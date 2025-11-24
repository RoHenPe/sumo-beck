import traci
import random
from typing import List, Dict
from abc import ABC, abstractmethod
from tcc_sumo.utils.helpers import get_logger

logger = get_logger("TrafficController")

class BaseController(ABC):
    @abstractmethod
    def setup(self, tl_ids: List[str]): pass
    @abstractmethod
    def manage_traffic_lights(self, step: int): pass

class StaticController(BaseController):
    """
    Modo Estático: Ciclos Longos (Minutos) com Variação.
    Base: 300s (5 min). Variação: 120s (2 min).
    Resultado: Fases duram entre 3 e 7 minutos.
    """
    def __init__(self, base_duration: int = 300, variation: int = 120):
        self.tls_ids = []
        self.states = {}
        self.BASE_DURATION = base_duration
        self.VARIATION = variation

    def setup(self, tl_ids: List[str]):
        self.tls_ids = tl_ids
        logger.info(f"Modo Estático: Ciclos de {self.BASE_DURATION/60:.1f} min (+/- {self.VARIATION/60:.1f} min).")
        
        for tid in self.tls_ids:
            try:
                start_phase = 0
                logics = traci.trafficlight.getAllProgramLogics(tid)
                if logics:
                    start_phase = random.randint(0, len(logics[0].phases) - 1)
                    traci.trafficlight.setPhase(tid, start_phase)
                    
                    # Duração inicial aleatória (em minutos)
                    dur = self._get_random_duration()
                    
                    self.states[tid] = {
                        'last_switch': 0,
                        'current_phase': start_phase,
                        'current_duration': dur
                    }
            except: pass

    def manage_traffic_lights(self, step: int):
        for tid in self.tls_ids:
            try:
                state = self.states[tid]
                time_in_phase = step - state['last_switch']
                
                # Troca apenas se atingiu o tempo alvo (que é > 1 min)
                if time_in_phase >= state['current_duration']:
                    self._switch_phase(tid, step, state)
            except: pass

    def _switch_phase(self, tid, step, state):
        program = traci.trafficlight.getAllProgramLogics(tid)[0]
        next_phase = (state['current_phase'] + 1) % len(program.phases)
        
        traci.trafficlight.setPhase(tid, next_phase)
        
        # Define o tempo da PRÓXIMA fase (ex: fechou 5min, agora abre 7min)
        new_duration = self._get_random_duration()
        
        state['last_switch'] = step
        state['current_phase'] = next_phase
        state['current_duration'] = new_duration

    def _get_random_duration(self):
        # Garante "acima de minutos" (mínimo 60s)
        min_dur = max(60, self.BASE_DURATION - self.VARIATION)
        max_dur = self.BASE_DURATION + self.VARIATION
        return random.randint(min_dur, max_dur)


class AdaptiveController(BaseController):
    """
    Modo Adaptativo: Sincronização por Demanda.
    Tempo Mínimo: 1 min (60s). Tempo Máximo: 10 min (600s).
    """
    def __init__(self, threshold: int = 5, min_time: int = 60, max_time: int = 600):
        self.tls_ids = []
        self.states = {}
        self.THRESHOLD = threshold
        self.MIN_TIME = min_time
        self.MAX_TIME = max_time

    def setup(self, tl_ids: List[str]):
        self.tls_ids = tl_ids
        for tid in self.tls_ids:
            self.states[tid] = {'last_switch': 0}
        logger.info(f"Modo Adaptativo: Ciclos dinâmicos ({self.MIN_TIME}s a {self.MAX_TIME}s).")

    def manage_traffic_lights(self, step: int):
        # Avalia todos os semáforos para decidir o "melhor momento"
        for tid in self.tls_ids:
            try:
                self._evaluate_best_timing(tid, step)
            except: pass

    def _evaluate_best_timing(self, tid, step):
        # 1. Soma da Via (Todos os carros chegando)
        total_queue = 0
        links = traci.trafficlight.getControlledLinks(tid)
        unique_lanes = set()
        for lgroup in links:
            for link in lgroup:
                if link: unique_lanes.add(link[0])
        
        for lane in unique_lanes:
            total_queue += traci.lane.getLastStepHaltingNumber(lane)

        # 2. Decisão Sincronizada
        last_switch = self.states[tid]['last_switch']
        time_in_phase = step - last_switch
        
        # Verifica se é fase Amarela (Transição Rápida)
        current_phase_idx = traci.trafficlight.getPhase(tid)
        program = traci.trafficlight.getAllProgramLogics(tid)[0]
        state_str = program.phases[current_phase_idx].state.lower()
        
        if 'y' in state_str:
            # Amarelo dura pouco (4s), não obedece regra de minutos
            if time_in_phase >= 4:
                self._advance_phase(tid, step, current_phase_idx, program)
            return

        should_switch = False
        
        # Se fila acumulou E já passou 1 minuto -> Abre
        if total_queue >= self.THRESHOLD and time_in_phase > self.MIN_TIME:
            should_switch = True
            
        # Se segurou demais (10 min) -> Abre forçado
        if time_in_phase > self.MAX_TIME:
            should_switch = True

        if should_switch:
            self._advance_phase(tid, step, current_phase_idx, program)

    def _advance_phase(self, tid, step, current_idx, program):
        next_phase = (current_idx + 1) % len(program.phases)
        traci.trafficlight.setPhase(tid, next_phase)
        self.states[tid]['last_switch'] = step