# -*- coding: utf-8 -*-
import logging
import logging.config
import os
import json
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[3]

def setup_logging(config_path: Path = PROJECT_ROOT / "config" / "logging_config.json", default_level=logging.INFO):
    if config_path.exists():
        with open(config_path, 'rt', encoding='utf-8') as f:
            config = json.load(f)
        log_dir = PROJECT_ROOT / "logs"
        log_dir.mkdir(exist_ok=True)
        
        for handler in config.get('handlers', {}).values():
            if 'filename' in handler:
                handler['filename'] = str(PROJECT_ROOT / handler['filename'])
                
        logging.config.dictConfig(config)
    else:
        logging.basicConfig(level=default_level, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
        logging.warning(f"Ficheiro 'logging_config.json' não encontrado. A usar configuração de log básica.")

def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)

def task_start(message: str):
    print(f"[ ] {message}...")

def task_success(message: str):
    print(f"[✓] {message}")

def task_fail(message: str):
    print(f"[✗] {message}")

def ensure_sumo_home():
    if 'SUMO_HOME' not in os.environ:
        logger = get_logger("SUMO_CHECK")
        error_msg = "A variável de ambiente SUMO_HOME não está definida."
        logger.critical(error_msg)
        raise EnvironmentError(error_msg)

def format_time(seconds: float) -> str:
    seconds = int(seconds)
    hours, remainder = divmod(seconds, 3600)
    minutes, seconds = divmod(remainder, 60)
    return f"{hours:02d}h {minutes:02d}m {seconds:02d}s"