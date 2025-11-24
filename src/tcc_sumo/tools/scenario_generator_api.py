import os
import sys
import json
import subprocess
import shutil
import yaml
import math
import argparse
import urllib.request
import urllib.parse
import ssl
import re
import random
import time
from pathlib import Path
import xml.etree.ElementTree as ET
from dotenv import load_dotenv

load_dotenv()

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

SB_URL = os.getenv("SUPABASE_URL")
SB_KEY = os.getenv("SUPABASE_KEY")
REPO_NAME = "RoHenPe/plataforma-trafego-web"
REPO_MAP_PATH = "public/maps/api_mapa_validacao.html"
WEB_PLATFORM_PATH = PROJECT_ROOT.parent / "rohenpe/plataforma-trafego-web/RoHenPe-plataforma-trafego-web-41ccc66874af228613cf7a2473b2caf94ef0e0c2/public/maps"

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
        logger.info("=== GERAÇÃO API (UPDATED) ===")
        
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
        self._gen_web_map_supabase(lat, lon, bbox, roads_data, local_html)
        
        self._deploy_local(local_html)
        self._upload_to_github(local_html)
        
        self._create_view(output_dir)
        self._gen_trips(output_dir, net_file)
        self._update_cfg()
        
        self._run_db_sync()
        logger.info("=== SUCESSO ===")

    def _clear_database(self):
        if not HAS_SUPABASE or not SB_URL or not SB_KEY: return
        try:
            client = create_client(SB_URL, SB_KEY)
            client.table("dispositivos").delete().neq("mac_address", "0").execute()
        except: pass

    def _deploy_local(self, source):
        if WEB_PLATFORM_PATH.exists():
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
            try: subprocess.run([sys.executable, str(sync)], check=True)
            except: pass

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
                "sumo_id": tid, "id": tls_mac, "type": "traffic_control_unit",
                "camera": {"id": cam_mac, "status": "active", "source": "from_api"},
                "geo": {"lat": lat, "lon": lon}, "status": "active"
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

    def _gen_web_map_supabase(self, lat, lon, bbox, roads, fp):
        lines = []
        for r in roads:
            pts = ",".join([f"[{p[0]},{p[1]}]" for p in r['p']])
            lines.append(f"L.polyline([{pts}], {{color:'{r['s']['c']}', weight:{r['s']['w']}, opacity:{r['s']['o']}, lineCap:'round'}}).addTo(map);")

        markers_data = []
        for i, d in enumerate(self.device_manifest):
            pop = f"""<div class='elegant-card' id='card-{d['id']}'><div class='card-top'><div class='icon-circle'><span class='material-icons'>traffic</span></div><div class='header-text'><div class='card-title'>Traffic Light</div><div class='card-subtitle'>{d['id']}</div></div><div class='status-indicator' id='status-dot-{d['id']}'></div></div><div class='card-split'></div><div class='card-bottom'><div class='data-row'><span class='material-icons row-icon'>videocam</span><div class='row-content'><div class='row-label'>Camera</div><div class='row-value'>{d['camera']['id']}</div></div></div><div class='copy-row'><span class='copy-label'>STATUS:</span><span class='copy-val' id='status-text-{d['id']}'>Loading...</span></div></div></div>"""
            markers_data.append({'lat': d['geo']['lat'], 'lon': d['geo']['lon'], 'pop': pop, 'mac': d['id']})

        html = f"""<!DOCTYPE html><html><head><meta charset="utf-8"><title>Monitoramento API</title>
<meta name="viewport" content="width=device-width, initial-scale=1.0" />
<link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css"/>
<link rel="stylesheet" href="https://unpkg.com/leaflet.markercluster@1.4.1/dist/MarkerCluster.css" />
<link rel="stylesheet" href="https://unpkg.com/leaflet.markercluster@1.4.1/dist/MarkerCluster.Default.css" />
<script src="https://cdn.jsdelivr.net/npm/@supabase/supabase-js@2"></script>
<script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>
<script src="https://unpkg.com/leaflet.markercluster@1.4.1/dist/leaflet.markercluster.js"></script>
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&family=Material+Icons&display=swap" rel="stylesheet">
<style>
  body {{ margin:0; font-family:'Inter', sans-serif; background:#f4f4f4; overflow:hidden; }} #map {{ width:100vw; height:100vh; }}
  .elegant-card {{ width: 260px; background: #ffffff; border-radius: 16px; overflow: hidden; font-family: 'Inter', sans-serif; }}
  .card-top {{ padding: 16px 20px; display: flex; align-items: center; background: #ffffff; }}
  .icon-circle {{ width: 40px; height: 40px; background: #f0f2f5; border-radius: 50%; display: flex; justify-content: center; align-items: center; margin-right: 12px; color: #333; }}
  .header-text {{ flex: 1; }}
  .card-title {{ font-size: 14px; font-weight: 700; color: #111; }}
  .card-subtitle {{ font-size: 12px; color: #666; font-weight: 500; font-family: monospace; letter-spacing: 0.5px; margin-top: 2px; }}
  .status-indicator {{ width: 12px; height: 12px; border-radius: 50%; background: #ccc; box-shadow: 0 0 0 2px white; }}
  .status-indicator.active {{ background: #10b981; }} .status-indicator.inactive {{ background: #ef4444; }} .status-indicator.maintenance {{ background: #f59e0b; }}
  .card-split {{ height: 1px; background: #f0f0f0; margin: 0 20px; }}
  .card-bottom {{ padding: 16px 20px; background: #ffffff; }}
  .data-row {{ display: flex; align-items: flex-start; margin-bottom: 16px; }}
  .row-icon {{ font-size: 20px; color: #9ca3af; margin-right: 12px; margin-top: 2px; }}
  .row-content {{ display: flex; flex-direction: column; }}
  .row-label {{ font-size: 11px; color: #9ca3af; font-weight: 600; text-transform: uppercase; letter-spacing: 0.5px; }}
  .row-value {{ font-size: 13px; color: #374151; font-weight: 500; font-family: monospace; margin-top: 2px; }}
  .copy-row {{ margin-top: 12px; padding-top: 12px; border-top: 1px dashed #eee; font-size: 10px; color: #999; display: flex; justify-content: space-between; }}
  .copy-val {{ font-family: monospace; color: #555; font-weight: bold; text-transform: uppercase; }}
  .pin-dot {{ width: 14px; height: 14px; border-radius: 50%; border: 3px solid white; box-shadow: 0 2px 5px rgba(0,0,0,0.2); background: #ef4444; transition: background 0.3s; }}
  .pin-dot.active {{ background: #10b981; }} .pin-dot.maintenance {{ background: #f59e0b; }}
  .pin-wrap {{ display: flex; justify-content: center; align-items: center; }}
  .legend {{ position: absolute; bottom: 24px; left: 24px; z-index: 1000; background: rgba(255,255,255,0.95); padding: 16px; border-radius: 12px; box-shadow: 0 4px 12px rgba(0,0,0,0.08); width: 160px; }}
  .l-item {{ display: flex; align-items: center; margin-bottom: 8px; font-size: 12px; color: #444; font-weight: 500; }}
  .dot {{ width: 8px; height: 8px; border-radius: 50%; margin-right: 10px; }}
</style></head><body><div id="map"></div>
<div class="legend">
<div style="margin-bottom:10px;font-weight:700;color:#111">Legenda (API)</div>
<div class="l-item"><div class="dot" style="background:#B3261E"></div>Rodovia</div>
<div class="l-item"><div class="dot" style="background:#2E7D32"></div>Primária</div>
<div class="l-item"><div class="dot" style="background:#F9A825"></div>Secundária</div>
<div class="l-item"><div class="dot" style="background:#E0E0E0"></div>Outros</div>
<div class="l-item" style="margin-top:8px"><div class="dot" style="background:#10b981;border:1px solid #ddd"></div>Ativo</div>
<div class="l-item"><div class="dot" style="background:#ef4444;border:1px solid #ddd"></div>Inativo</div>
<div class="l-item"><div class="dot" style="background:#f59e0b;border:1px solid #ddd"></div>Manutenção</div>
</div>
<script>
const supabaseUrl = '{SB_URL}'; const supabaseKey = '{SB_KEY}';
const client = supabase.createClient(supabaseUrl, supabaseKey);
var map=L.map('map',{{zoomControl:false}}).setView([{lat},{lon}],14);
L.tileLayer('https://{{s}}.basemaps.cartocdn.com/light_all/{{z}}/{{x}}/{{y}}{{r}}.png',{{subdomains:'abcd',maxZoom:20}}).addTo(map);
L.control.zoom({{position:'topright'}}).addTo(map);
{chr(10).join(lines)}
var markers = L.markerClusterGroup({{
    showCoverageOnHover: false, zoomToBoundsOnClick: true, spiderfyOnMaxZoom: true, maxClusterRadius: 45,
    iconCreateFunction: function(cluster) {{
        return L.divIcon({{ html: '<div style="background:#333;color:white;border-radius:50%;width:32px;height:32px;display:flex;justify-content:center;align-items:center;font-weight:bold;border:3px solid white;font-size:12px;box-shadow:0 4px 8px rgba(0,0,0,0.15)">' + cluster.getChildCount() + '</div>', className: 'cluster-icon', iconSize: [36, 36] }});
    }}
}});
var data = {json.dumps(markers_data)};
data.forEach(function(d) {{
    var icon = L.divIcon({{ className: 'pin-wrap', html: `<div class='pin-dot' id='pin-${{d.mac}}'></div>`, iconSize: [16, 16], iconAnchor: [8, 8] }});
    var m = L.marker([d.lat, d.lon], {{icon: icon}});
    m.bindPopup(d.pop, {{closeButton: false, minWidth: 260}});
    markers.addLayer(m);
}});
map.addLayer(markers);
map.fitBounds([[{bbox[0]},{bbox[1]}],[{bbox[2]},{bbox[3]}]], {{padding:[60,60]}});
async function syncStatus() {{
    const {{ data, error }} = await client.from('dispositivos').select('mac_address, status');
    if (!error && data) {{
        data.forEach(dev => {{
            const mac = dev.mac_address;
            const status = dev.status || 'active';
            const dot = document.getElementById(`status-dot-${{mac}}`);
            const txt = document.getElementById(`status-text-${{mac}}`);
            if (dot) dot.className = `status-indicator ${{status}}`;
            if (txt) {{
                txt.innerText = status.toUpperCase();
                txt.style.color = (status === 'active') ? '#10b981' : ((status === 'maintenance') ? '#f59e0b' : '#ef4444');
            }}
            const pin = document.getElementById(`pin-${{mac}}`);
            if (pin) pin.className = `pin-dot ${{status}}`;
        }});
    }}
}}
syncStatus();
setInterval(syncStatus, 10000);
map.on('popupopen', syncStatus);
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
            f.write(f"""<configuration>
            <input>
                <net-file value="{net.name}"/>
                <route-files value="{rou.name}"/>
                <additional-files value="detectors.add.xml"/>
                <gui-settings-file value="gui-settings.xml"/>
            </input>
            <time><begin value="0"/><end value="{int(self.settings['SIM']['dur'])}"/></time>
            </configuration>""")

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