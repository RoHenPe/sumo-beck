# -*- coding: utf-8 -*-
import logging
import subprocess
import time
import sys
import traci
from traci.exceptions import TraCIException

from tcc_sumo.utils.helpers import get_logger

logger = get_logger("tcc_sumo.simulation.traci_connection")

class TraciConnection:
    def __init__(self, sumo_executable: str, config_file: str, port: int):
        self.sumo_executable = sumo_executable
        self.config_file = config_file
        self.port = port
        self.sumo_process = None

    def start(self) -> None:
        sumo_cmd = [
            self.sumo_executable,
            "-c", self.config_file,
            "--remote-port", str(self.port),
            "--start",
            "--quit-on-end",
            "--time-to-teleport", "-1",
            "--no-warnings", "true"
        ]
        logger.info(f"Iniciando processo do SUMO: {' '.join(sumo_cmd)}")

        self.sumo_process = subprocess.Popen(sumo_cmd, stdout=sys.stdout, stderr=sys.stderr)

        retries = 10
        for i in range(retries):
            try:
                traci.init(self.port)
                logger.info(f"Conexão TraCI estabelecida na porta {self.port}.")
                return
            except TraCIException as e:
                if i < retries - 1:
                    logger.warning(f"Tentativa {i+1}/{retries}: Não foi possível conectar ao TraCI na porta {self.port}. Tentando novamente em 1s...")
                    time.sleep(1)
                else:
                    logger.critical(f"Falha ao estabelecer conexão TraCI após {retries} tentativas: {e}")
        
        self.close()
        raise RuntimeError("Não foi possível conectar ao SUMO via TraCI.")

    def close(self) -> None:
        try:
            traci.close()
            logger.info("Conexão TraCI encerrada.")
        except (TraCIException, AttributeError):
            logger.debug("Tentativa de fechar uma conexão TraCI já inexistente ou não inicializada.")
        finally:
            if self.sumo_process and self.sumo_process.poll() is None:
                try:
                    self.sumo_process.terminate()
                    self.sumo_process.wait(timeout=5)
                    logger.info("Processo do SUMO finalizado.")
                except subprocess.TimeoutExpired:
                    self.sumo_process.kill()
                    logger.warning("Processo do SUMO forçado a fechar (kill).")
                self.sumo_process = None
            else:
                 logger.debug("Processo SUMO já estava fechado.")