import os
import sys
import json
import subprocess
import shutil
import yaml
import random
import urllib.request
import urllib.parse
import ssl
import re
import argparse
import xml.etree.ElementTree as ET
from pathlib import Path
from dotenv import load_dotenv

# --- CONFIGURAÇÃO DE AMBIENTE ---
load_dotenv()
site_env = Path(__file__).parents[4] / "site-web" / ".env.local"
if site_env.exists():
    load_dotenv(site_env)

SB_URL = os.getenv("SUPABASE_URL") or os.getenv("NEXT_PUBLIC_SUPABASE_URL")
SB_KEY = os.getenv("SUPABASE_KEY") or os.getenv("NEXT_PUBLIC_SUPABASE_ANON_KEY")

if 'SUMO_HOME' in os.environ:
    tools = os.path.join(os.environ['SUMO_HOME'], 'tools')
    if tools not in sys.path: sys.path.append(tools)
else:
    sys.exit("ERRO: SUMO_HOME não definido.")

import sumolib

try:
    from github import Github, Auth
    HAS_GITHUB = True
except ImportError: HAS_GITHUB = False

try:
    from supabase import create_client
    HAS_SUPABASE = True
except ImportError: HAS_SUPABASE = False

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from tcc_sumo.utils.helpers import get_logger, setup_logging, PROJECT_ROOT

setup_logging()
logger = get_logger("ScenarioGeneratorAPI")

REPO_NAME = "RoHenPe/plataforma-trafego-web"
REPO_MAP_PATH = "public/maps/api_mapa_validacao.html"
WEB_PLATFORM_PATH = PROJECT_ROOT.parent / "site-web/public/maps" 

class ScenarioGeneratorAPI:
    def __init__(self, config: dict):
        self.config = config
        self.settings = {}
        self.device_manifest = []
        self.detectors_config = []
        self.traffic_lights_config = []
        self.generated_macs = set()

    def _run_command(self, command):
        try:
            subprocess.run(command, cwd=PROJECT_ROOT, check=True, capture_output=True, text=True)
        except subprocess.CalledProcessError as e:
            logger.error(f"CMD Error: {e.stderr}")
            raise

    def _gen_mac(self):
        while True:
            mac = ":".join([f"{random.randint(0, 255):02X}" for _ in range(6)])
            if mac not in self.generated_macs:
                self.generated_macs.add(mac)
                return mac

    def generate(self, input_file_name, num_vehicles, duration):
        logger.info(f"=== GERAÇÃO DE CENÁRIO (Pool: {num_vehicles} veics, Base: {duration/3600:.1f}h) ===")
        
        # 1. Configuração e Localização
        base_file = PROJECT_ROOT / "scenarios" / "base_files" / input_file_name
        self.settings = self._load_config(base_file, num_vehicles, duration)
        
        # Limpa o banco apenas dos DISPOSITIVOS (mantém a rede viária se quiser cachear no futuro)
        self._clear_devices_db()
        
        output_dir = PROJECT_ROOT / "scenarios" / "from_api"
        validation_dir = PROJECT_ROOT / "output"
        validation_dir.mkdir(exist_ok=True)
        
        if output_dir.exists(): shutil.rmtree(output_dir)
        output_dir.mkdir(parents=True)
        
        osm_full = output_dir / "map_full.osm.xml"
        osm_sumo = output_dir / "map_sumo.osm.xml"
        net_file = output_dir / "api.net.xml"
        
        lat, lon = self.settings['LOC']['lat'], self.settings['LOC']['lon']
        bbox = self._get_bbox(lat, lon, self.settings['LOC']['radius'])
        
        # 2. Download e Processamento (OSM -> SUMO)
        logger.info("Baixando e processando mapa OSM...")
        self._download_map(bbox, osm_full)
        self._clean_xml(osm_full)
        self._filter_map_sumo(osm_full, osm_sumo)
        self._build_net(osm_sumo, net_file, bbox, keep_names=True)
        
        # 3. Extração da Rede Viária (Source of Truth)
        logger.info("Extraindo e Classificando Rede Viária...")
        roads_data = self._extract_sumo_geometry(net_file)
        
        # 4. Dispositivos e Rotas
        logger.info("Configurando dispositivos e tráfego...")
        self._generate_devices(net_file)
        self._gen_trips(output_dir, net_file)
        
        # 5. Exportação Local (JSONs para o site ler rápido)
        logger.info("Exportando arquivos...")
        self._export_json(validation_dir / "api_road_network.json", roads_data)
        self._export_json(validation_dir / "api_devices_manifest.json", self.device_manifest)
        self._export_json(validation_dir / "api_traffic_lights_config.json", self.traffic_lights_config)
        self._convert_trips_to_json(output_dir / "trips.xml", validation_dir / "api_vehicle_routes.json")
        
        # Gera HTML estático apenas como fallback/visualização rápida
        local_html = validation_dir / "api_mapa_validacao.html"
        self._gen_web_map_fidelity(lat, lon, bbox, roads_data, local_html)
        
        # 6. Sincronização com o Banco de Dados (Supabase)
        logger.info("Sincronizando com Supabase (Banco de Dados)...")
        self._sync_devices_db() # Dispositivos
        self._sync_roads_to_supabase(roads_data) # NOVA FUNÇÃO: Salva as ruas
        
        # 7. Deploy para pasta do Site
        self._deploy_files(validation_dir, local_html)
        
        logger.info("=== GERAÇÃO CONCLUÍDA ===")

    # --- LÓGICA DE CLASSIFICAÇÃO DE VIAS (IGUAL AO FRONTEND) ---
    def _identify_road_type(self, speed):
        # Classificação baseada na velocidade da via (m/s para km/h aprox)
        # w=4 (Rápida > 72km/h), w=3 (Arterial > 47km/h), w=2 (Local)
        if speed > 20: return 'rodovia', 4
        if speed > 13: return 'primaria', 3
        return 'secundaria', 2 # Inclui locais/terciárias

    def _extract_sumo_geometry(self, net_file):
        """Extrai geometria do SUMO e aplica a classificação de tipos/cores"""
        net = sumolib.net.readNet(str(net_file))
        roads = []
        
        for edge in net.getEdges():
            if edge.getFunction() == "internal": continue # Ignora conexões internas de cruzamento
            
            edge_id = edge.getID()
            edge_name = edge.getName() or edge_id # Usa ID se não tiver nome
            
            # Geometria
            shape = edge.getShape()
            geo_shape = []
            for x, y in shape:
                lon, lat = net.convertXY2LonLat(x, y)
                geo_shape.append([lat, lon])
            
            # Classificação
            speed = edge.getSpeed()
            road_type, weight = self._identify_road_type(speed)
            
            # Cores (Backend define o padrão, Frontend pode sobrescrever com tema)
            color = '#000000' # Padrão Preto
            if road_type == 'primaria': color = '#22c55e' # Verde
            elif road_type == 'secundaria': color = '#eab308' # Amarelo
            
            roads.append({
                'id': edge_id,
                'name': edge_name,
                'points': geo_shape, # Novo padrão
                'type': road_type,   # Tipo explícito para o Frontend
                'style': {'c': color, 'w': weight, 'z': weight}
            })
            
        roads.sort(key=lambda x: x['style']['w']) # Renderiza vias menores primeiro
        return roads

    def _sync_roads_to_supabase(self, roads_data):
        """Salva a rede viária gerada no Supabase para persistência"""
        if not HAS_SUPABASE or not SB_URL or not SB_KEY: return
        
        try:
            client = create_client(SB_URL, SB_KEY)
            
            # Opcional: Limpar tabela antes de inserir (Full Refresh)
            # Se quiser manter cache de outros lugares, precisaria filtrar por localização
            client.table("rede_viaria").delete().neq("id", "0").execute()
            
            logger.info(f"Enviando {len(roads_data)} segmentos de via para o banco...")
            
            # Inserção em lotes para não estourar limite de request
            batch_size = 100
            for i in range(0, len(roads_data), batch_size):
                batch = roads_data[i:i+batch_size]
                db_payload = []
                for r in batch:
                    db_payload.append({
                        "id": r['id'],
                        "name": r['name'],
                        "type": r['type'],
                        "points": r['points'],
                        "style": r['style']
                    })
                client.table("rede_viaria").upsert(db_payload).execute()
                
            logger.info("Rede viária salva no Supabase com sucesso!")
            
        except Exception as e:
            logger.error(f"Erro ao salvar rede no banco: {e}")

    # --- MÉTODOS EXISTENTES (AJUSTADOS) ---

    def _generate_devices(self, net_file):
        net = sumolib.net.readNet(str(net_file))
        for tls in net.getTrafficLights():
            tid = tls.getID()
            programs = tls.getPrograms()
            conns = tls.getConnections()
            if not conns: continue
            
            # Geometria
            lane = conns[0][0]
            shape = lane.getShape()
            if not shape: continue
            sx, sy = shape[-1]
            lon, lat = net.convertXY2LonLat(sx, sy)
            
            tls_mac = self._gen_mac()
            self.device_manifest.append({
                "source": "sumo_net", "sumo_id": tid, "id": tls_mac, "type": "SEMAFARO",
                "geo": {"lat": lat, "lon": lon}, "status": "active"
            })
            
            # Config do Semáforo (Fases)
            current_prog = None
            if '0' in programs: current_prog = programs['0']
            elif len(programs) > 0: current_prog = programs[list(programs.keys())[0]]
            
            if current_prog:
                phases = [{'duration': p.duration, 'state': p.state} for p in current_prog.getPhases()]
                self.traffic_lights_config.append({
                    "tls_id": tid, "mac_address": tls_mac, "phases": phases, "lat": lat, "lon": lon
                })

            # Detectores
            incoming = set(c[0].getID() for c in conns)
            for lane_id in incoming:
                l_obj = net.getLane(lane_id)
                pos = max(0, l_obj.getLength() - 15)
                self.detectors_config.append({
                    'id': f"e2_{tid}_{lane_id}", 'lane': lane_id, 'pos': pos, 'len': 8
                })
        self._write_detectors(PROJECT_ROOT / "scenarios" / "from_api" / "detectors.add.xml")

    def _build_net(self, osm, net, bbox, keep_names=True):
        s, w, n, e = bbox
        cmd = [str(Path(os.environ["SUMO_HOME"]) / 'bin' / 'netconvert'), '--osm-files', str(osm), '-o', str(net), '--keep-edges.in-geo-boundary', f"{w},{s},{e},{n}", '--geometry.remove', 'false', '--tls.guess', 'true', '--tls.join', 'true', '--proj.utm', 'true']
        if keep_names: cmd.append('--output.street-names')
        self._run_command(cmd)

    def _gen_web_map_fidelity(self, lat, lon, bbox, roads, fp):
        js_roads = []
        for r in roads:
            pts = ",".join([f"[{p[0]},{p[1]}]" for p in r['points']])
            popup = f"<b>{r['name']}</b><br><span style='font-size:9px;color:#666'>ID: {r['id']}</span>"
            line_js = f"var line = L.polyline([{pts}], {{color: '{r['style']['c']}', weight: {r['style']['w']}, opacity: 0.8, lineCap: 'round'}}).bindPopup(\"{popup}\"); roadLayer.addLayer(line);"
            js_roads.append(line_js)
        html = f"""<!DOCTYPE html><html><head><meta charset="utf-8"><title>Fidelity Map</title><meta name="viewport" content="width=device-width, initial-scale=1.0" /><link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css"/><script src="https://cdn.jsdelivr.net/npm/@supabase/supabase-js@2"></script><script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script><link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;600&family=Material+Icons&display=swap" rel="stylesheet"><style>body {{ margin:0; font-family:'Inter', sans-serif; background:#1e1e1e; overflow:hidden; }} #map {{ width:100vw; height:100vh; }} .leaflet-popup-content {{ font-size: 13px; }}</style></head><body><div id="map"></div><script>const sbUrl = '{SB_URL}'; const sbKey = '{SB_KEY}'; const client = supabase.createClient(sbUrl, sbKey); var map = L.map('map', {{ zoomControl: false }}).setView([{lat}, {lon}], 15); L.tileLayer('https://{{s}}.basemaps.cartocdn.com/dark_all/{{z}}/{{x}}/{{y}}{{r}}.png', {{maxZoom:20}}).addTo(map); var roadLayer = L.layerGroup().addTo(map); {"".join(js_roads)} async function loadDevices() {{ const {{ data }} = await client.from('dispositivos').select('*'); if (!data) return; data.forEach(d => {{ if(!d.latitude) return; let color = d.tipo === 'SEMAFARO' ? '#ef4444' : '#3b82f6'; L.circleMarker([d.latitude, d.longitude], {{ radius: 6, color: color, fillColor: color, fillOpacity: 0.8 }}).addTo(map).bindPopup(d.tipo + '<br>' + d.mac_address); }}); }} loadDevices();</script></body></html>"""
        with open(fp, 'w', encoding='utf-8') as f: f.write(html)

    def _clear_devices_db(self):
        if not HAS_SUPABASE or not SB_URL or not SB_KEY: return
        try:
            client = create_client(SB_URL, SB_KEY)
            client.table("dispositivos").delete().neq("mac_address", "0").execute()
        except: pass

    def _sync_devices_db(self):
        # Usa script existente ou lógica direta
        sync = PROJECT_ROOT / "src" / "sync_db.py"
        if sync.exists():
            env_vars = os.environ.copy()
            if SB_URL: env_vars["SUPABASE_URL"] = SB_URL
            if SB_KEY: env_vars["SUPABASE_KEY"] = SB_KEY
            try: subprocess.run([sys.executable, str(sync)], check=True, env=env_vars)
            except: pass

    def _export_json(self, fp, data):
        with open(fp, 'w', encoding='utf-8') as f: json.dump(data, f, indent=4)

    def _deploy_files(self, source_dir, main_html):
        if WEB_PLATFORM_PATH.parent.parent.exists():
            if not WEB_PLATFORM_PATH.exists(): WEB_PLATFORM_PATH.mkdir(parents=True, exist_ok=True)
            try: 
                shutil.copy2(main_html, WEB_PLATFORM_PATH / "api_mapa_validacao.html")
                shutil.copy2(source_dir / "api_road_network.json", WEB_PLATFORM_PATH / "api_road_network.json")
                shutil.copy2(source_dir / "api_vehicle_routes.json", WEB_PLATFORM_PATH / "api_vehicle_routes.json")
                shutil.copy2(source_dir / "api_devices_manifest.json", WEB_PLATFORM_PATH / "api_devices_manifest.json")
                shutil.copy2(source_dir / "api_traffic_lights_config.json", WEB_PLATFORM_PATH / "api_traffic_lights_config.json")
            except: pass

    def _upload_to_github(self, local_path):
        if not HAS_GITHUB: return
        token = os.getenv("GITHUB_TOKEN")
        if not token: return
        try:
            g = Github(auth=Auth.Token(token))
            repo = g.get_repo(REPO_NAME)
            with open(local_path, 'r', encoding='utf-8') as f: content = f.read()
            try:
                c = repo.get_contents(REPO_MAP_PATH)
                repo.update_file(c.path, "Update Map", content, c.sha)
            except:
                repo.create_file(REPO_MAP_PATH, "Create Map", content)
        except: pass

    # ... (Métodos de download, clean_xml, gen_trips, convert_trips, etc. permanecem inalterados) ...
    def _load_config(self, fpath, vehs, dur):
        with open(fpath, encoding='utf-8') as f: data = json.load(f)
        loc = data.get('location_settings', {})
        try:
            q = urllib.parse.quote(loc.get('center_point_query', "Alphaville"))
            url = f"https://nominatim.openstreetmap.org/search?q={q}&format=json&limit=1"
            req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
            ctx = ssl.create_default_context(); ctx.check_hostname=False; ctx.verify_mode=ssl.CERT_NONE
            with urllib.request.urlopen(req, context=ctx, timeout=5) as r:
                res = json.loads(r.read())
                lat, lon = (float(res[0]['lat']), float(res[0]['lon'])) if res else (loc['fallback_lat'], loc['fallback_lon'])
        except:
            lat, lon = loc['fallback_lat'], loc['fallback_lon']
        return {
            'LOC': {'lat': lat, 'lon': lon, 'radius': loc.get('search_radius_km', 1.0)},
            'SIM': {'vehs': vehs, 'dur': dur},
            'DEV': {'offset': 15, 'len': 8}
        }

    def _get_bbox(self, lat, lon, r):
        d = r / 111.0
        return (lat - d, lon - d, lat + d, lon + d)

    def _download_map(self, bbox, target):
        s, w, n, e = bbox
        servers = [
            f"https://overpass-api.de/api/map?bbox={w},{s},{e},{n}",
            f"https://api.openstreetmap.org/api/0.6/map?bbox={w},{s},{e},{n}"
        ]
        for url in servers:
            try:
                req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
                ctx = ssl.create_default_context(); ctx.check_hostname=False; ctx.verify_mode=ssl.CERT_NONE
                with urllib.request.urlopen(req, context=ctx, timeout=60) as r, open(target, 'wb') as f:
                    shutil.copyfileobj(r, f)
                if target.stat().st_size > 1000: return
            except: pass
        raise RuntimeError("Falha download OSM.")

    def _clean_xml(self, fp):
        try:
            with open(fp, 'r', encoding='utf-8') as f: c = f.read()
            c = re.sub(r'\sxmlns="[^"]+"', '', c, count=1)
            with open(fp, 'w', encoding='utf-8') as f: f.write(c)
        except: pass

    def _filter_map_sumo(self, inp, out):
        tree = ET.parse(inp); root = tree.getroot()
        allowed = {'motorway', 'motorway_link', 'primary', 'primary_link', 'secondary', 'secondary_link', 'tertiary', 'residential'}
        rem = []
        for w in root.findall('way'):
            if not any(t.get('k')=='highway' and t.get('v') in allowed for t in w.findall('tag')):
                rem.append(w)
        for r in rem: root.remove(r)
        tree.write(out, encoding='utf-8')

    def _gen_trips(self, out, net):
        rou = out / "api.rou.xml"
        subprocess.run([
            "python3", str(Path(os.environ["SUMO_HOME"]) / "tools" / "randomTrips.py"),
            "-n", str(net), "-r", str(rou), "-o", str(out / "trips.xml"),
            "-e", str(self.settings['SIM']['dur']), "-p", "2.5", "--validate"
        ], check=True)
        with open(out / "api.sumocfg", 'w') as f:
            f.write(f"""<configuration><input><net-file value="{net.name}"/><route-files value="{rou.name}"/></input><time><begin value="0"/><end value="{int(self.settings['SIM']['dur'])}"/></time></configuration>""")

    def _convert_trips_to_json(self, trips_xml, json_out):
        try:
            tree = ET.parse(trips_xml)
            root = tree.getroot()
            vehicles = []
            for trip in root.findall('trip'):
                vehicles.append({
                    "id": trip.get("id"),
                    "depart": float(trip.get("depart")),
                    "from_edge": trip.get("from"),
                    "to_edge": trip.get("to"),
                    "speed_factor": random.uniform(0.8, 1.2) 
                })
            with open(json_out, 'w', encoding='utf-8') as f:
                json.dump(vehicles, f, separators=(',', ':'))
        except: pass

    def _write_detectors(self, fp):
        with open(fp, 'w') as f:
            f.write("<additional>\n")
            for d in self.detectors_config:
                f.write(f' <e2Detector id="{d["id"]}" lane="{d["lane"]}" pos="{d["pos"]:.2f}" length="{d["len"]:.2f}" file="traffic.xml" freq="60"/>\n')
            f.write("</additional>")

# --- MENU INTERATIVO ---
def interactive_config():
    print("\n" + "="*40)
    print(" CONFIGURAÇÃO DO CENÁRIO (BASE DE DADOS)")
    print("="*40)
    print("\n[1] Densidade de Veículos (Tamanho do Pool):")
    print("   1. Baixa  (1.000 veículos)  - Recomendado p/ testes rápidos")
    print("   2. Média  (5.000 veículos)  - Padrão")
    print("   3. Alta   (20.000 veículos) - Para estresse do navegador")
    d_choice = input("   >>> Escolha (1-3) [Enter=1]: ").strip()
    vehicles = 1000
    if d_choice == '2': vehicles = 5000
    elif d_choice == '3': vehicles = 20000
    
    print("\n[2] Duração da Base de Rotas:")
    print("   Obs: A simulação web será infinita, mas precisa de rotas base.")
    print("   1. Curta  (1 hora de rotas únicas)")
    print("   2. Média  (3 horas de rotas únicas)")
    t_choice = input("   >>> Escolha (1-2) [Enter=1]: ").strip()
    duration = 3600.0
    if t_choice == '2': duration = 10800.0
    
    print("\n" + "-"*40)
    print(f" RESUMO: {vehicles} veículos | {duration/3600:.0f}h de base")
    print("-"*40 + "\n")
    return vehicles, duration

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument('--input', help='Arquivo de input')
    parser.add_argument('--vehicles', type=int)
    parser.add_argument('--duration', type=float)
    parser.add_argument('--no-menu', action='store_true')
    args = parser.parse_args()

    if args.no_menu or (args.vehicles and args.duration):
        final_vehicles = args.vehicles or 1000
        final_duration = args.duration or 3600.0
        logger.info("Modo CLI (sem interação)")
    else:
        final_vehicles, final_duration = interactive_config()

    input_file = args.input if args.input else "dados_api.json"
    cfg_path = PROJECT_ROOT / 'config' / 'config.yaml'
    config = {}
    if cfg_path.exists():
        with open(cfg_path) as f: config = yaml.safe_load(f) or {}

    try:
        gen = ScenarioGeneratorAPI(config)
        gen.generate(input_file, final_vehicles, final_duration)
    except Exception as e:
        logger.critical(f"Erro API: {e}")
        sys.exit(1)