import argparse
import sys
import yaml
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from tcc_sumo.utils.helpers import setup_logging, get_logger, PROJECT_ROOT
from tcc_sumo.simulation.manager import SimulationManager

setup_logging()
logger = get_logger("Main")

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--scenario', required=True, choices=['osm', 'api'])
    parser.add_argument('--mode', required=True, choices=['STATIC', 'ADAPTIVE'])
    parser.add_argument('--target-tl-id', default=None)
    args = parser.parse_args()

    cfg_path = PROJECT_ROOT / 'config' / 'config.yaml'
    with open(cfg_path) as f: config = yaml.safe_load(f)

    try:
        manager = SimulationManager(config, args.scenario, args.mode, args.target_tl_id)
        manager.run()
    except Exception as e:
        logger.critical(f"Erro Fatal: {e}")
        sys.exit(1)

if __name__ == "__main__":
    main()