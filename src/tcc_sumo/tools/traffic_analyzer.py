import argparse
import re
import pandas as pd
from pathlib import Path
from jinja2 import Environment, FileSystemLoader
import sys
import json

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from tcc_sumo.utils.helpers import get_logger, setup_logging, PROJECT_ROOT

setup_logging()
logger = get_logger("TrafficAnalyzer")

LEVEL_COLORS = {'CRITICAL': '#000000', 'ERROR': '#dc3545', 'WARNING': '#ffc107', 'INFO': '#007bff', 'DEBUG': '#6c757d', 'NOTSET': '#6c757d'}

class TrafficAnalyzer:
    def __init__(self):
        self.logs_dir = PROJECT_ROOT / "logs"
        self.output_dir = PROJECT_ROOT / "output"
        self.output_dir.mkdir(exist_ok=True)
        self.templates_dir = PROJECT_ROOT / "src" / "tcc_sumo" / "templates"
        self.env = Environment(loader=FileSystemLoader(str(self.templates_dir)))

    def generate_log_dashboard(self):
        logger.info("Gerando Dashboard Logs...")
        log_file = self.logs_dir / "simulation.log"
        if not log_file.exists(): return
        df = self._parse_log(log_file)
        if df.empty: return
        summary = {"total_logs": len(df), "all_level_counts": [], "modules": sorted(df['module'].unique().tolist()), "sources": ["System"]}
        for level, count in df['level'].value_counts().items():
            summary["all_level_counts"].append({"level": level, "count": count, "color": LEVEL_COLORS.get(level, '#6c757d')})
        html = self.env.get_template("log_dashboard.html").render(generation_time=pd.Timestamp.now().strftime('%d/%m/%Y %H:%M:%S'), summary=summary, all_logs=df.to_dict('records'))
        with open(self.output_dir / "log_dashboard.html", 'w', encoding='utf-8') as f: f.write(html)

    def generate_traffic_dashboard(self):
        logger.info("Gerando Dashboard Tráfego...")
        json_file = self.output_dir / "consolidated_data.json"
        if not json_file.exists(): return
        try:
            with open(json_file, 'r') as f: data = json.load(f)
        except: return
        if not data: return
        latest = data[-1]
        for k in ["metrics", "pollution", "queue_metrics"]:
            if k not in latest: latest[k] = {}
        html = self.env.get_template("traffic_dashboard.html").render(generation_time=pd.Timestamp.now().strftime('%d/%m/%Y %H:%M:%S'), data=latest, all_data=data, metrics=latest.get("metrics", {}), pollution=latest.get("pollution", {}), queue_metrics=latest.get("queue_metrics", {}), vehicle_count=latest.get("metrics", {}).get("count", 0))
        with open(self.output_dir / "traffic_dashboard.html", 'w', encoding='utf-8') as f: f.write(html)

    def _parse_log(self, path):
        regex = re.compile(r"^\[(.*?)\] \[(.*?)\] \[(.*?)\] : (.*)$")
        rows = []
        try:
            with open(path, 'r', encoding='utf-8') as f:
                for line in f:
                    m = regex.match(line.strip())
                    if m: rows.append({"timestamp": m.group(1), "level": m.group(2).strip(), "module": m.group(3).strip(), "message": m.group(4).strip(), "level_class": m.group(2).strip().lower()})
        except: pass
        return pd.DataFrame(rows)

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument('--source', required=True, choices=['logs', 'traffic'])
    args = parser.parse_args()
    an = TrafficAnalyzer()
    if args.source == 'logs': an.generate_log_dashboard()
    else: an.generate_traffic_dashboard()