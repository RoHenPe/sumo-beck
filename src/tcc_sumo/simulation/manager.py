import sys
import os
import subprocess
import time
import json
import traci
from collections import defaultdict
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

if 'SUMO_HOME' in os.environ:
    tools = os.path.join(os.environ['SUMO_HOME'], 'tools')
    if tools not in sys.path: sys.path.append(tools)

from tcc_sumo.utils.helpers import get_logger, setup_logging, PROJECT_ROOT
from tcc_sumo.traffic_logic.controllers import StaticController, AdaptiveController
from tcc_sumo.tools.log_analyzer import LogAnalyzer

setup_logging()
logger = get_logger("SimulationManager")

SB_URL = os.getenv("SUPABASE_URL")
SB_KEY = os.getenv("SUPABASE_KEY")

try:
    from supabase import create_client
    HAS_SUPABASE = True
except ImportError:
    HAS_SUPABASE = False

class SimulationManager:
    def __init__(self, config, scenario_name, mode_name, target_tl_id=None):
        self.scenario_name = scenario_name
        
        if scenario_name == 'osm':
            self.cfg_path = PROJECT_ROOT / "scenarios/from_osm/osm.sumocfg"
            self.manifest_path = PROJECT_ROOT / "output" / "osm_devices_manifest.json"
        else:
            self.cfg_path = PROJECT_ROOT / "scenarios/from_api/api.sumocfg"
            self.manifest_path = PROJECT_ROOT / "output" / "api_devices_manifest.json"
            
        self.scenario_dir = self.cfg_path.parent
        self.cfg_file_name = self.cfg_path.name
        self.mode = mode_name.upper()
        self.target = target_tl_id
        self.ctrl = AdaptiveController() if self.mode == 'ADAPTIVE' else StaticController()
        
        self.device_map = {} 
        self.global_stats = defaultdict(lambda: {'total_cars': set(), 'max_q': 0, 'sum_q': 0, 'samples': 0})
        
        self._load_device_states()

    def _load_device_states(self):
        local_data = []
        if self.manifest_path.exists():
            with open(self.manifest_path, 'r') as f: local_data = json.load(f)
        
        mac_to_sumo = {d['id']: d['sumo_id'] for d in local_data if 'sumo_id' in d}
        self.device_map = {d['sumo_id']: d for d in local_data if 'sumo_id' in d}

        if HAS_SUPABASE and self.scenario_name == 'api' and SB_URL and SB_KEY:
            try:
                client = create_client(SB_URL, SB_KEY)
                res = client.from_("dispositivos").select("mac_address, status, tipo").execute()
                for r in res.data:
                    mac, st, tp = r['mac_address'], r['status'], r['tipo']
                    if tp == 'SEMAFARO' and mac in mac_to_sumo:
                        sid = mac_to_sumo[mac]
                        if sid in self.device_map: self.device_map[sid]['status'] = st
                    if tp == 'CAMERA':
                        for sid, dev in self.device_map.items():
                            if dev.get('camera', {}).get('id') == mac:
                                dev['camera']['status'] = st
                logger.info("Estados sincronizados via Supabase.")
            except Exception as e:
                logger.warning(f"Erro Supabase: {e}")
        else:
            logger.info("Modo OSM (Offline): Usando estados padrão.")

    def _kill_existing_sumo(self):
        try:
            subprocess.run(["pkill", "-9", "sumo-gui"], stderr=subprocess.DEVNULL)
            time.sleep(0.5)
        except: pass

    def run(self):
        logger.info(f"Iniciando Simulação [{self.mode}] ({self.scenario_name})...")
        self._kill_existing_sumo()
        
        cmd = ["sumo-gui", "-c", self.cfg_file_name, "--start", "--quit-on-end", "--no-warnings"]
        original_cwd = Path.cwd()
        os.chdir(self.scenario_dir)
        
        try:
            traci.start(cmd)
            
            active_ids = []
            for tid in traci.trafficlight.getIDList():
                dev = self.device_map.get(tid)
                if dev and dev.get('status') in ['inactive', 'maintenance']:
                    traci.trafficlight.setProgram(tid, "off")
                else:
                    active_ids.append(tid)

            self.ctrl.setup(active_ids)
            
            if self.target and self.target in active_ids:
                try:
                    x, y = traci.junction.getPosition(self.target)
                    traci.gui.setSchema("View #0", "real_world")
                    traci.gui.setZoom("View #0", 2500)
                    traci.gui.setOffset("View #0", x, y)
                except: pass

            self._loop()
            
        except Exception as e:
            logger.critical(f"Erro Simulação: {e}")
        finally:
            os.chdir(original_cwd)
            self._generate_tickets()
            try: LogAnalyzer(mode=self.mode).run()
            except: pass

    def _loop(self):
        step = 0
        try:
            while traci.simulation.getMinExpectedNumber() > 0:
                traci.simulationStep()
                self.ctrl.manage_traffic_lights(step)
                self._collect_stats(step)
                step += 1
        except: pass
        finally:
            try: traci.close()
            except: pass

    def _collect_stats(self, step):
        for tid in self.global_stats.keys() if self.global_stats else traci.trafficlight.getIDList():
            dev = self.device_map.get(tid)
            if dev and dev.get('camera', {}).get('status') != 'active': continue

            try:
                lanes = traci.trafficlight.getControlledLanes(tid)
                q = 0
                for l in set(lanes):
                    q += traci.lane.getLastStepHaltingNumber(l)
                    vehs = traci.lane.getLastStepVehicleIDs(l)
                    for v in vehs: self.global_stats[tid]['total_cars'].add(v)
                
                self.global_stats[tid]['sum_q'] += q
                self.global_stats[tid]['samples'] += 1
                if q > self.global_stats[tid]['max_q']:
                    self.global_stats[tid]['max_q'] = q
            except: pass

    def _generate_tickets(self):
        tickets = []
        for tid, data in self.global_stats.items():
            if data['samples'] == 0: continue
            avg_q = data['sum_q'] / data['samples']
            dev = self.device_map.get(tid, {})
            
            tickets.append({
                "sumo_id": tid,
                "tls_mac": dev.get('id', 'N/A'),
                "camera_mac": dev.get('camera', {}).get('id', 'N/A'),
                "source": f"from_{self.scenario_name}",
                "metrics": {
                    "flow_count": len(data['total_cars']),
                    "max_queue": data['max_q'],
                    "avg_queue": round(avg_q, 2)
                },
                "mode": self.mode,
                "timestamp": time.strftime("%Y-%m-%d %H:%M:%S")
            })
        
        out_file = PROJECT_ROOT / "output" / f"{self.scenario_name}_simulation_tickets.json"
        with open(out_file, 'w') as f: json.dump(tickets, f, indent=4)
        logger.info(f"Tickets gerados: {out_file}")