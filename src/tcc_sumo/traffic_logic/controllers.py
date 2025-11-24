import traci
import random
from typing import List
from abc import ABC, abstractmethod
from tcc_sumo.utils.helpers import get_logger

logger = get_logger("TrafficController")

class BaseController(ABC):
    @abstractmethod
    def setup(self, tl_ids: List[str]): pass
    @abstractmethod
    def manage_traffic_lights(self, step: int): pass

class StaticController(BaseController):
    def __init__(self):
        self.tls_ids = []
        self.states = {}

    def setup(self, tl_ids: List[str]):
        self.tls_ids = tl_ids
        logger.info("Modo Estático: Ciclos de Minutos (Dependência R/G).")
        
        for tid in self.tls_ids:
            try:
                logics = traci.trafficlight.getAllProgramLogics(tid)
                if logics:
                    # Tenta iniciar em fase vermelha para facilitar lógica
                    start_phase = 0
                    for i, p in enumerate(logics[0].phases):
                        if 'r' in p.state.lower() and 'g' not in p.state.lower():
                            start_phase = i
                            break
                    
                    traci.trafficlight.setPhase(tid, start_phase)
                    
                    # Define um "último vermelho" inicial fictício (ex: 300s)
                    last_red = 300
                    curr_dur = self._calc_duration(logics[0].phases[start_phase], last_red)
                    
                    # Offset aleatório para dessincronizar
                    offset = random.randint(0, 60)

                    self.states[tid] = {
                        'last_switch': -offset,
                        'current_phase': start_phase,
                        'current_duration': curr_dur + offset,
                        'last_red_duration': last_red
                    }
            except: pass

    def manage_traffic_lights(self, step: int):
        for tid in self.tls_ids:
            try:
                state = self.states[tid]
                time_in_phase = step - state['last_switch']
                
                if time_in_phase >= state['current_duration']:
                    self._switch_phase(tid, step, state)
            except: pass

    def _switch_phase(self, tid, step, state):
        program = traci.trafficlight.getAllProgramLogics(tid)[0]
        next_idx = (state['current_phase'] + 1) % len(program.phases)
        traci.trafficlight.setPhase(tid, next_idx)
        
        # Se a fase que acabou era Vermelha, salva a duração real
        prev_def = program.phases[state['current_phase']]
        state_str = prev_def.state.lower()
        if 'r' in state_str and 'g' not in state_str:
             duration = step - state['last_switch']
             if duration > 60: # Valida se foi um vermelho significativo
                state['last_red_duration'] = duration

        new_dur = self._calc_duration(program.phases[next_idx], state['last_red_duration'])
        
        state['last_switch'] = step
        state['current_phase'] = next_idx
        state['current_duration'] = new_dur

    def _calc_duration(self, phase, last_red):
        s = phase.state.lower()
        # Amarelo: 1 a 2 minutos
        if 'y' in s: return random.randint(60, 120)
        # Verde: Vermelho Anterior + (2 a 3 minutos)
        if 'g' in s: return last_red + random.randint(120, 180)
        # Vermelho: 3 a 5 minutos
        if 'r' in s: return random.randint(180, 300)
        return 60

class AdaptiveController(BaseController):
    def __init__(self, threshold: int = 3, min_time: int = 60, max_time: int = 600):
        self.tls_ids = []
        self.states = {}
        self.THRESHOLD = threshold
        self.MIN_TIME = min_time
        self.MAX_TIME = max_time

    def setup(self, tl_ids: List[str]):
        self.tls_ids = tl_ids
        for tid in self.tls_ids:
            self.states[tid] = {'last_switch': 0, 'yellow_duration': 0}
        logger.info("Modo Adaptativo: Sincronização Global Ativa.")

    def manage_traffic_lights(self, step: int):
        for tid in self.tls_ids:
            try: self._evaluate(tid, step)
            except: pass

    def _evaluate(self, tid, step):
        current_idx = traci.trafficlight.getPhase(tid)
        program = traci.trafficlight.getAllProgramLogics(tid)[0]
        phase_def = program.phases[current_idx]
        state_str = phase_def.state.lower()
        
        last_switch = self.states[tid]['last_switch']
        time_in_phase = step - last_switch
        
        # Lógica Amarelo (1 a 2 min)
        if 'y' in state_str:
            if self.states[tid]['yellow_duration'] == 0:
                self.states[tid]['yellow_duration'] = random.randint(60, 120)
            
            if time_in_phase >= self.states[tid]['yellow_duration']:
                self._advance(tid, step, current_idx, program)
            return

        # Lógica Verde/Vermelho (Demanda Total)
        total_queue = 0
        links = traci.trafficlight.getControlledLinks(tid)
        unique_lanes = set(l[0] for group in links for l in group if l)
        for l in unique_lanes: total_queue += traci.lane.getLastStepHaltingNumber(l)

        should_switch = False
        if total_queue >= self.THRESHOLD and time_in_phase > self.MIN_TIME: should_switch = True
        if time_in_phase > self.MAX_TIME: should_switch = True

        if should_switch:
            self._advance(tid, step, current_idx, program)

    def _advance(self, tid, step, idx, program):
        next_idx = (idx + 1) % len(program.phases)
        traci.trafficlight.setPhase(tid, next_idx)
        self.states[tid]['last_switch'] = step
        self.states[tid]['yellow_duration'] = 0