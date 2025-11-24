from pathlib import Path
from datetime import datetime
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from tcc_sumo.utils.helpers import get_logger, setup_logging, PROJECT_ROOT

setup_logging()
logger = get_logger("Reporter")

class Reporter:
    def generate_report(self, metrics):
        report_file = PROJECT_ROOT / "logs" / "simulation_report.log"
        content = f"""
=======================================================
RELATÓRIO DE PERFORMANCE
Data: {datetime.now()}
Cenário: {metrics.get('scenario')}
-------------------------------------------------------
Total de Veículos: {metrics.get('count')}
Tempo Médio: {metrics.get('duration'):.2f}s
Perda Média: {metrics.get('loss'):.2f}s
=======================================================
"""
        with open(report_file, 'a') as f: f.write(content)
        logger.info("Relatório anexado ao simulation_report.log")

if __name__ == "__main__":
    pass