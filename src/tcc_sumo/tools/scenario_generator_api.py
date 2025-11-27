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
from pathlib import Path
import xml.etree.ElementTree as ET
from dotenv import load_dotenv

load_dotenv()
site_env = Path(__file__).parents[4] / "site-web" / ".env.local"
if site_env.exists():
    load_dotenv(site_env)

SB_URL = os.getenv("SUPABASE_URL") or os.getenv("NEXT_PUBLIC_SUPABASE_URL") or "https://rumhqljidmwkctjojqdw.supabase.co"
SB_KEY = os.getenv("SUPABASE_KEY") or os.getenv("NEXT_PUBLIC_SUPABASE_ANON_KEY") or "sb_publishable_KYQSvBlqUw9hrC0zeB-3Tg_SIdo84So"

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
from tcc_sumo.utils.helpers import get_logger, setup_logging, ensure_sumo_home, PROJECT_ROOT

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
        logger.info("=== GERAÇÃO API (COLORED PINS) ===")
        
        self._clear_database()
        
        base_file = PROJECT_ROOT / "scenarios" / "base_files" / input_file_name
        output_dir = PROJECT_ROOT / "scenarios" / "from_api"
        validation_dir = PROJECT_ROOT / "output"
        validation_dir.mkdir(exist_ok=True)
        
        if output_dir.exists(): shutil.rmtree(output_dir)
        output_dir.mkdir(parents=True)
        
        osm_full = output_dir / "map_full.osm.xml"
        osm_sumo = output_dir / "map_sumo.osm.xml"
        net_file = output_dir / "api.net.xml"
        
        self.settings = self._load_config(base_file, num_vehicles, duration)
        lat, lon = self.settings['LOC']['lat'], self.settings['LOC']['lon']
        bbox = self._get_bbox(lat, lon, self.settings['LOC']['radius'])
        
        self._download_map(bbox, osm_full)
        self._clean_xml(osm_full)
        self._filter_map_sumo(osm_full, osm_sumo)
        self._build_net(osm_sumo, net_file, bbox)
        
        tls_data, roads_data = self._analyze_net_geo_priority(net_file, osm_full)
        
        self._export_manifest(validation_dir / "api_devices_manifest.json")
        self._export_coords(validation_dir / "api_traffic_lights.json")
        self._write_detectors(output_dir / "detectors.add.xml")
        
        local_html = validation_dir / "api_mapa_validacao.html"
        self._gen_web_map_dynamic(lat, lon, bbox, roads_data, local_html)
        
        self._deploy_local(local_html)
        self._upload_to_github(local_html)
        
        self._create_view(output_dir)
        self._gen_trips(output_dir, net_file)
        self._update_cfg()
        
        self._run_db_sync()
        logger.info("=== SUCESSO ===")

    def _clear_database(self):
        if not HAS_SUPABASE: return
        try:
            client = create_client(SB_URL, SB_KEY)
            client.table("dispositivos").delete().neq("mac_address", "0").execute()
            logger.info("Banco limpo.")
        except: pass

    def _deploy_local(self, source):
        if WEB_PLATFORM_PATH.parent.exists():
            if not WEB_PLATFORM_PATH.exists(): WEB_PLATFORM_PATH.mkdir(parents=True)
            try: shutil.copy2(source, WEB_PLATFORM_PATH / "api_mapa_validacao.html")
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

    def _run_db_sync(self):
        sync = PROJECT_ROOT / "src" / "sync_db.py"
        if sync.exists():
            env_vars = os.environ.copy()
            env_vars["SUPABASE_URL"] = SB_URL
            env_vars["SUPABASE_KEY"] = SB_KEY
            try: 
                subprocess.run([sys.executable, str(sync)], check=True, env=env_vars)
                logger.info("Sync DB OK")
            except: logger.error("Sync DB Failed")

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
        allowed = {'motorway', 'motorway_link', 'primary', 'primary_link', 'secondary', 'secondary_link'}
        rem = []
        for w in root.findall('way'):
            if not any(t.get('k')=='highway' and t.get('v') in allowed for t in w.findall('tag')):
                rem.append(w)
        for r in rem: root.remove(r)
        tree.write(out, encoding='utf-8')

    def _build_net(self, osm, net, bbox):
        s, w, n, e = bbox
        cmd = [
            str(Path(os.environ["SUMO_HOME"]) / 'bin' / 'netconvert'),
            '--osm-files', str(osm), '-o', str(net),
            '--keep-edges.in-geo-boundary', f"{w},{s},{e},{n}",
            '--geometry.remove', 'false', '--keep-edges.components', '1',
            '--ramps.guess', 'true', '--tls.guess', 'true', '--tls.join', 'true',
            '--proj.utm', 'true'
        ]
        self._run_command(cmd)

    def _analyze_net_geo_priority(self, net_file, osm_visual):
        net = sumolib.net.readNet(str(net_file))
        osm_nodes = {}
        try:
            tree = ET.parse(osm_visual); root = tree.getroot()
            for n in root.findall('node'):
                osm_nodes[n.get('id')] = (float(n.get('lat')), float(n.get('lon')))
        except: pass

        def resolve_geo(tid, x_sumo, y_sumo):
            if tid in osm_nodes: return osm_nodes[tid]
            sub_ids = re.findall(r'\d+', tid)
            found = [osm_nodes[i] for i in sub_ids if i in osm_nodes]
            if found:
                lat = sum(c[0] for c in found) / len(found)
                lon = sum(c[1] for c in found) / len(found)
                return (lat, lon)
            try: 
                lon, lat = net.convertXY2LonLat(x_sumo, y_sumo)
                return (lat, lon)
            except: pass
            return (self.settings['LOC']['lat'], self.settings['LOC']['lon'])

        for tls in net.getTrafficLights():
            tid = tls.getID()
            conns = tls.getConnections()
            if not conns: continue
            ref_lane = conns[0][0]
            shape = ref_lane.getShape()
            if not shape: continue
            sx, sy = shape[-1]
            lat, lon = resolve_geo(tid, sx, sy)
            
            tls_mac = self._gen_mac()
            cam_mac = self._gen_mac()
            
            incoming_lanes = set()
            for c in conns: incoming_lanes.add(c[0].getID())
            for lane_id in incoming_lanes:
                l_len = net.getLane(lane_id).getLength()
                pos = max(0, l_len - self.settings['DEV']['offset'])
                self.detectors_config.append({
                    'id': f"e2_{tid}_{lane_id}", 'lane': lane_id, 
                    'pos': pos, 'len': self.settings['DEV']['len']
                })

            self.device_manifest.append({
                "source": "from_api",
                "sumo_id": tid, "id": tls_mac, "type": "SEMAFARO",
                "geo": {"lat": lat, "lon": lon}, "status": "active"
            })
            
            self.device_manifest.append({
                "source": "from_api",
                "sumo_id": None, "id": cam_mac, "type": "CAMERA",
                "geo": {"lat": lat + 0.0001, "lon": lon + 0.0001},
                "status": "active", "linked_to": tls_mac
            })

        visuals = []
        try:
            for w in root.findall('way'):
                tags = {t.get('k'):t.get('v') for t in w.findall('tag')}
                coords = [osm_nodes[nd.get('ref')] for nd in w.findall('nd') if nd.get('ref') in osm_nodes]
                if len(coords) < 2: continue
                ht = tags.get('highway')
                st = None
                if ht in ['motorway', 'motorway_link']: st = {'c': '#B3261E', 'w': 3, 'z': 4, 'o': 0.9}
                elif ht in ['primary', 'primary_link']: st = {'c': '#2E7D32', 'w': 3, 'z': 3, 'o': 0.9}
                elif ht in ['secondary', 'secondary_link']: st = {'c': '#F9A825', 'w': 2, 'z': 2, 'o': 0.9}
                elif ht in ['service', 'residential', 'living_street']: st = {'c': '#E0E0E0', 'w': 1, 'z': 1, 'o': 0.5}
                if st: visuals.append({'p': coords, 's': st})
            visuals.sort(key=lambda x: x['s']['z'])
        except: pass
        return None, visuals

    def _export_manifest(self, fp):
        with open(fp, 'w', encoding='utf-8') as f: json.dump(self.device_manifest, f, indent=4)

    def _export_coords(self, fp):
        data = [{'id': d['id'], 'lat': d['geo']['lat'], 'lon': d['geo']['lon'], 'source': 'from_api'} for d in self.device_manifest]
        with open(fp, 'w', encoding='utf-8') as f: json.dump(data, f, indent=4)

    def _write_detectors(self, fp):
        with open(fp, 'w') as f:
            f.write("<additional>\n")
            for d in self.detectors_config:
                f.write(f' <e2Detector id="{d["id"]}" lane="{d["lane"]}" pos="{d["pos"]:.2f}" length="{d["len"]:.2f}" file="traffic.xml" freq="60"/>\n')
            f.write("</additional>")

    def _gen_web_map_dynamic(self, lat, lon, bbox, roads, fp):
        lines = []
        for r in roads:
            pts = ",".join([f"[{p[0]},{p[1]}]" for p in r['p']])
            lines.append(f"L.polyline([{pts}], {{color:'{r['s']['c']}', weight:{r['s']['w']}, opacity:{r['s']['o']}, lineCap:'round'}}).addTo(map);")

        html = f"""<!DOCTYPE html><html><head><meta charset="utf-8"><title>Mapa F.org</title>
<meta name="viewport" content="width=device-width, initial-scale=1.0" />
<link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css"/>
<link rel="stylesheet" href="https://unpkg.com/leaflet.markercluster@1.4.1/dist/MarkerCluster.css" />
<link rel="stylesheet" href="https://unpkg.com/leaflet.markercluster@1.4.1/dist/MarkerCluster.Default.css" />
<script src="https://cdn.jsdelivr.net/npm/@supabase/supabase-js@2"></script>
<script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>
<script src="https://unpkg.com/leaflet.markercluster@1.4.1/dist/leaflet.markercluster.js"></script>
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&family=Material+Icons&display=swap" rel="stylesheet">
<style>
  body {{ margin:0; font-family:'Inter', sans-serif; background:#f8fafc; overflow:hidden; }} 
  #map {{ width:100vw; height:100vh; }}
  .elegant-card {{ width: 240px; background: #ffffff; border-radius: 12px; overflow: hidden; box-shadow: 0 4px 20px rgba(0,0,0,0.1); }}
  .card-header {{ padding: 12px 16px; display: flex; align-items: center; justify-content: space-between; background: #f1f5f9; border-bottom: 1px solid #e2e8f0; }}
  .card-type {{ font-size: 11px; font-weight: 700; text-transform: uppercase; letter-spacing: 0.5px; color: #64748b; }}
  .card-status {{ width: 10px; height: 10px; border-radius: 50%; }}
  .card-body {{ padding: 16px; }}
  .card-mac {{ font-family: monospace; font-size: 14px; font-weight: 600; color: #334155; margin-bottom: 8px; display: block; }}
  .card-coords {{ font-size: 11px; color: #94a3b8; display: flex; gap: 8px; }}
  .legend {{ position: absolute; bottom: 24px; right: 24px; z-index: 1000; background: rgba(255,255,255,0.95); padding: 16px; border-radius: 12px; box-shadow: 0 4px 12px rgba(0,0,0,0.08); width: 140px; }}
  .l-item {{ display: flex; align-items: center; margin-bottom: 8px; font-size: 12px; color: #475569; font-weight: 500; }}
  .dot {{ width: 8px; height: 8px; border-radius: 50%; margin-right: 10px; }}
  .marker-pin {{ width: 30px; height: 30px; border-radius: 50%; display: flex; justify-content: center; align-items: center; color: white; box-shadow: 0 2px 5px rgba(0,0,0,0.2); border: 2px solid white; }}
  .marker-pin span {{ font-size: 18px; }}
  .bg-active {{ background-color: #10b981; }} 
  .bg-inactive {{ background-color: #ef4444; }} 
  .bg-maintenance {{ background-color: #f59e0b; }}
</style></head><body><div id="map"></div>
<div class="legend">
  <div style="margin-bottom:10px;font-weight:700;color:#1e293b">Legenda</div>
  <div class="l-item"><div class="dot" style="background:#10b981"></div>Ativo</div>
  <div class="l-item"><div class="dot" style="background:#ef4444"></div>Inativo</div>
  <div class="l-item"><div class="dot" style="background:#f59e0b"></div>Manutenção</div>
  <div style="margin: 8px 0; height:1px; background:#e2e8f0"></div>
  <div class="l-item"><span class="material-icons" style="font-size:14px; margin-right:6px; color:#333">videocam</span>Câmera</div>
  <div class="l-item"><span class="material-icons" style="font-size:14px; margin-right:6px; color:#333">traffic</span>Semáforo</div>
</div>
<script>
function getQueryParam(name) {{ const urlParams = new URLSearchParams(window.location.search); return urlParams.get(name); }}
const sbUrl = getQueryParam('sbUrl') || '{SB_URL}';
const sbKey = getQueryParam('sbKey') || '{SB_KEY}';
if (!sbUrl || !sbKey || sbUrl === 'None') alert('Erro: Chaves Supabase não configuradas.');
const client = supabase.createClient(sbUrl, sbKey);

var map = L.map('map', {{ zoomControl: false }}).setView([{lat}, {lon}], 14);
L.tileLayer('https://{{s}}.basemaps.cartocdn.com/light_all/{{z}}/{{x}}/{{y}}{{r}}.png', {{subdomains:'abcd',maxZoom:20}}).addTo(map);
L.control.zoom({{ position: 'topright' }}).addTo(map);
{chr(10).join(lines)}

var markers = L.markerClusterGroup({{
    showCoverageOnHover: false, zoomToBoundsOnClick: true, spiderfyOnMaxZoom: true, maxClusterRadius: 40
}});
map.addLayer(markers);

async function loadDevices() {{
    const {{ data, error }} = await client.from('dispositivos').select('id, mac_address, tipo, status, latitude, longitude');
    if (error || !data) return;
    markers.clearLayers();
    
    data.forEach(dev => {{
        if (!dev.latitude || !dev.longitude) return;
        
        let status = dev.status || 'active';
        let bgColor = '#10b981';
        if (status === 'inactive') bgColor = '#ef4444';
        if (status === 'maintenance') bgColor = '#f59e0b';
        
        let iconName = (dev.tipo === 'CAMERA') ? 'videocam' : 'traffic';
        
        const customIcon = L.divIcon({{
            className: 'custom-pin',
            html: `<div class="marker-pin" style="background-color: ${{bgColor}}"><span class="material-icons">${{iconName}}</span></div>`,
            iconSize: [30, 30], iconAnchor: [15, 15]
        }});
        
        let statusClass = (status === 'inactive') ? 'bg-inactive' : (status === 'maintenance' ? 'bg-maintenance' : 'bg-active');
        const popupContent = `<div class="elegant-card"><div class="card-header"><span class="card-type">${{dev.tipo}}</span><div class="card-status ${{statusClass}}"></div></div><div class="card-body"><span class="card-mac">${{dev.mac_address}}</span><div class="card-coords"><span>Lat: ${{dev.latitude.toFixed(4)}}</span><span>Lon: ${{dev.longitude.toFixed(4)}}</span></div></div></div>`;
        
        const m = L.marker([dev.latitude, dev.longitude], {{ icon: customIcon }});
        m.bindPopup(popupContent, {{ minWidth: 240, closeButton: false }});
        markers.addLayer(m);
    }});
}}
loadDevices();
setInterval(loadDevices, 10000);
map.fitBounds([[{bbox[0]},{bbox[1]}],[{bbox[2]},{bbox[3]}]], {{padding:[50,50]}});
</script></body></html>"""
        with open(fp, 'w', encoding='utf-8') as f: f.write(html)

    def _create_view(self, out):
        with open(out / "gui-settings.xml", 'w') as f:
            f.write('<viewsettings><scheme name="real_world"><opengl><background value="white"/><draw-junction-shape value="true"/></opengl></scheme></viewsettings>')

    def _gen_trips(self, out, net):
        rou = out / "api.rou.xml"
        subprocess.run([
            "python3", str(Path(os.environ["SUMO_HOME"]) / "tools" / "randomTrips.py"),
            "-n", str(net), "-r", str(rou), "-o", str(out / "trips.xml"),
            "-e", str(self.settings['SIM']['dur']), "-p", "2.5", "--validate"
        ], check=True)
        with open(out / "api.sumocfg", 'w') as f:
            f.write(f"""<configuration><input><net-file value="{net.name}"/><route-files value="{rou.name}"/><additional-files value="detectors.add.xml"/><gui-settings-file value="gui-settings.xml"/></input><time><begin value="0"/><end value="{int(self.settings['SIM']['dur'])}"/></time></configuration>""")

    def _update_cfg(self):
        with open(PROJECT_ROOT / 'config' / 'config.yaml', 'w') as f:
            self.config.setdefault('scenarios', {})['api'] = "scenarios/from_api/api.sumocfg"
            yaml.dump(self.config, f)

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument('--input', required=True)
    parser.add_argument('--vehicles', type=int, default=1000)
    parser.add_argument('--duration', type=float, default=3600)
    args = parser.parse_args()

    cfg_path = PROJECT_ROOT / 'config' / 'config.yaml'
    config = {}
    if cfg_path.exists():
        with open(cfg_path) as f: config = yaml.safe_load(f) or {}

    try:
        gen = ScenarioGeneratorAPI(config)
        gen.generate(args.input, args.vehicles, args.duration)
    except Exception as e:
        logger.critical(f"Erro API: {e}")
        sys.exit(1)