import xml.etree.ElementTree as ET
import pandas as pd
import json
import uuid
import argparse
import sys
from pathlib import Path
from datetime import datetime

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from tcc_sumo.utils.helpers import get_logger, setup_logging, PROJECT_ROOT

setup_logging()
logger = get_logger("LogAnalyzer")
LOGS_DIR = PROJECT_ROOT / "logs"
OUTPUT_DIR = PROJECT_ROOT / "output"

class LogAnalyzer:
    def __init__(self, mode="N/A"):
        self.mode = mode
        self.ticket_file = LOGS_DIR / "ticket.log"
        self.json_file = OUTPUT_DIR / "consolidated_data.json"
        self.scen_path = self._find_latest_scenario_path()
        self.trip_info = self.scen_path / "tripinfo.xml" if self.scen_path else None
        self.edge_data = self.scen_path / "edge_data.xml" if self.scen_path else None
        self.net_file = list(self.scen_path.glob("*.net.xml"))[0] if self.scen_path else None

    def _find_latest_scenario_path(self):
        api = PROJECT_ROOT / "scenarios" / "from_api"
        osm = PROJECT_ROOT / "scenarios" / "from_osm"
        api_t = api / "tripinfo.xml"
        osm_t = osm / "tripinfo.xml"
        if api_t.exists() and osm_t.exists():
            return api if api_t.stat().st_mtime > osm_t.stat().st_mtime else osm
        return api if api_t.exists() else (osm if osm_t.exists() else None)

    def run(self):
        if not self.trip_info or not self.trip_info.exists(): return
        metrics = self._calculate_metrics()
        tls_data = self._analyze_tls()
        self._write_ticket(metrics, tls_data)
        self._update_json(metrics)

    def _calculate_metrics(self):
        try:
            tree = ET.parse(self.trip_info)
            df = pd.DataFrame([c.attrib for c in tree.getroot().findall('tripinfo')])
            if df.empty: return {}
            for c in ['duration', 'waitingTime', 'timeLoss', 'routeLength']:
                df[c] = pd.to_numeric(df[c], errors='coerce')
            avg_speed = (df['routeLength'] / df['duration']).mean() * 3.6 if not df.empty else 0
            return {"count": len(df), "duration": df['duration'].mean(), "wait": df['waitingTime'].mean(), "loss": df['timeLoss'].mean(), "speed": avg_speed, "scenario": self.scen_path.name.replace("from_", "").upper(), "mode": self.mode}
        except: return {}

    def _analyze_tls(self):
        if not self.net_file or not self.edge_data or not self.edge_data.exists(): return []
        try:
            tls_map = {}
            for junc in ET.parse(self.net_file).getroot().findall("junction"):
                if junc.get("type") == "traffic_light":
                    edges = set([l.rsplit('_', 1)[0] for l in junc.get("incLanes", "").split() if '_' in l])
                    tls_map[junc.get("id")] = list(edges)
            edge_stats = {}
            for interval in ET.parse(self.edge_data).getroot().findall("interval"):
                for e in interval.findall("edge"):
                    eid = e.get("id")
                    if eid not in edge_stats: edge_stats[eid] = {'flow': 0, 'wait': 0}
                    edge_stats[eid]['flow'] += float(e.get("entered", 0))
                    edge_stats[eid]['wait'] += float(e.get("waitingTime", 0))
            results = []
            for tid, edges in tls_map.items():
                tf, tw = 0, 0
                for e in edges:
                    if e in edge_stats:
                        tf += edge_stats[e]['flow']
                        tw += edge_stats[e]['wait']
                if tf > 0: results.append({"id": tid, "flow": int(tf), "avg_wait": tw/tf})
            return sorted(results, key=lambda x: x['flow'], reverse=True)
        except: return []

    def _write_ticket(self, m, tls):
        tid = str(uuid.uuid4())[:8].upper()
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        txt = f"\n================================================================================\n[TICKET #{tid}] RELATÓRIO DE SIMULAÇÃO\nDATA: {now} | CENÁRIO: {m.get('scenario', 'N/A')}\n--------------------------------------------------------------------------------\n -> Veículos:      {m.get('count', 0)}\n -> Duração Média: {m.get('duration', 0):.2f} s\n -> Espera Média:  {m.get('wait', 0):.2f} s\n -> Velocidade:    {m.get('speed', 0):.2f} km/h\n"
        if tls:
            txt += "--------------------------------------------------------------------------------\n DESEMPENHO POR SEMÁFORO (Fluxo > 0):\n ID SEMÁFORO        | VEÍCULOS QUE PASSARAM | ESPERA MÉDIA (s)\n"
            for t in tls: txt += f" {t['id']:<18} | {t['flow']:<21} | {t['avg_wait']:.2f}\n"
        txt += f"\nSTATUS: {'SUCESSO' if m.get('count',0) > 0 else 'SEM DADOS'}\n================================================================================\n"
        with open(self.ticket_file, 'a', encoding='utf-8') as f: f.write(txt)
        print(txt)

    def _update_json(self, m):
        OUTPUT_DIR.mkdir(exist_ok=True)
        data = []
        if self.json_file.exists():
            try: 
                with open(self.json_file, 'r') as f: data = json.load(f)
            except: pass
        data.append({"timestamp": datetime.now().isoformat(), "metrics": m})
        try:
            with open(self.json_file, 'w') as f:
                json.dump(data, f, indent=4)
        except: pass

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument('--mode', default="MANUAL")
    args = parser.parse_args()
    LogAnalyzer(mode=args.mode).run()