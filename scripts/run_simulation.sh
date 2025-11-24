#!/bin/bash
SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" &> /dev/null && pwd )"
cd "$SCRIPT_DIR/.." || exit
PROJECT_ROOT_DIR="$(pwd)"
export PYTHONPATH="${PROJECT_ROOT_DIR}/src:${PYTHONPATH}"
LOG_DIR="${PROJECT_ROOT_DIR}/logs"
mkdir -p "$LOG_DIR"
MAIN_LOG="${LOG_DIR}/simulation.log"
if [ -f ".venv/bin/activate" ]; then source .venv/bin/activate; fi
touch "$MAIN_LOG"
VEHICLE_COUNT=1000
INSERTION_DURATION=3600
TARGET_TL_ID=""

execute_task() {
    local cmd="$1"
    local msg="$2"
    echo "----------------------------------------------------------------" >> "$MAIN_LOG"
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] [DEBUG   ] [ShellScript  ] : INICIO: $msg" >> "$MAIN_LOG"
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] [DEBUG   ] [ShellScript  ] : CMD: $cmd" >> "$MAIN_LOG"
    echo -n "$msg "
    eval "$cmd" >> "$MAIN_LOG" 2>&1 &
    local pid=$!
    local spin='|/-\'; local i=0
    while kill -0 $pid 2>/dev/null; do i=$(( (i+1) %4 )); printf "\b${spin:$i:1}"; sleep .1; done
    wait $pid; local code=$?
    printf "\b"
    if [ $code -eq 0 ]; then
        echo "[ OK ]"
        echo "[$(date '+%Y-%m-%d %H:%M:%S')] [DEBUG   ] [ShellScript  ] : FIM: SUCESSO" >> "$MAIN_LOG"
    else
        echo "[ERRO]"
        echo "[$(date '+%Y-%m-%d %H:%M:%S')] [ERROR   ] [ShellScript  ] : FIM: ERRO ($code)" >> "$MAIN_LOG"
    fi
}

prompt_vars() {
    echo "--- Configuração ---"
    echo "1. Baixa (1000) | 2. Média (5000) | 3. Alta (20000)"
    read -p "Densidade: " d; case $d in 1) V=1000;; 2) V=5000;; 3) V=20000;; *) V=1000;; esac
    echo "1. Curta (1h) | 2. Média (3h)"
    read -p "Duração: " t; case $t in 1) D=3600;; 2) D=10800;; *) D=3600;; esac
    export VEHICLE_COUNT=$V; export INSERTION_DURATION=$D
}

prompt_target() {
    read -p "ID do Semáforo (Enter para pular): " TID
    export TARGET_TL_ID="$TID"
}

while true; do
    clear
    cat << "EOF"
================================================================
       TCC - SIMULADOR DE TRÁFEGO URBANO COM SUMO
================================================================
 Geração:
    1. Cenário OpenStreetMap (OSM)
    2. Cenário API

 Simulação (OSM):
    3. Estático
    4. Adaptativo

 Simulação (API):
    5. Estático
    6. Adaptativo

 Resultados:
    7. Dashboard Logs
    8. Dashboard Tráfego
    9. Limpar Tudo
    0. Sair
================================================================
EOF
    read -p "Opção: " opt
    case $opt in
        1) prompt_vars; execute_task "python3 -m tcc_sumo.tools.scenario_generator_osm --input osm_bbox.osm.xml --vehicles $VEHICLE_COUNT --duration $INSERTION_DURATION" "Gerando OSM...";;
        2) prompt_vars; execute_task "python3 -m tcc_sumo.tools.scenario_generator_api --input dados_api.json --vehicles $VEHICLE_COUNT --duration $INSERTION_DURATION" "Gerando API...";;
        3) prompt_target; execute_task "python3 -m main --scenario osm --mode STATIC --target-tl-id '$TARGET_TL_ID'" "Simulando OSM (Static)...";;
        4) prompt_target; execute_task "python3 -m main --scenario osm --mode ADAPTIVE --target-tl-id '$TARGET_TL_ID'" "Simulando OSM (Adaptive)...";;
        5) prompt_target; execute_task "python3 -m main --scenario api --mode STATIC --target-tl-id '$TARGET_TL_ID'" "Simulando API (Static)...";;
        6) prompt_target; execute_task "python3 -m main --scenario api --mode ADAPTIVE --target-tl-id '$TARGET_TL_ID'" "Simulando API (Adaptive)...";;
        7) execute_task "python3 -m tcc_sumo.tools.traffic_analyzer --source logs" "Gerando Dashboard Logs...";;
        8) execute_task "python3 -m tcc_sumo.tools.traffic_analyzer --source traffic" "Gerando Dashboard Tráfego...";;
        9) echo "Limpando..."; rm -rf logs/* output/* scenarios/from_api/* scenarios/from_osm/*; touch "$MAIN_LOG"; echo "[$(date '+%Y-%m-%d %H:%M:%S')] [WARNING ] [ShellScript  ] : SISTEMA LIMPO PELO USUÁRIO" >> "$MAIN_LOG"; sleep 1;;
        0) exit 0;;
        *) echo "Inválido."; sleep 1;;
    esac
    if [[ "$opt" != "0" ]]; then read -n 1 -s -r -p "Pressione Enter..."; fi
done