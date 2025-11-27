#!/bin/bash

# Obtém o diretório onde o script está e define a raiz do projeto
SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" &> /dev/null && pwd )"
cd "$SCRIPT_DIR/.." || exit
PROJECT_ROOT_DIR="$(pwd)"

# Configura PYTHONPATH para encontrar os módulos src
export PYTHONPATH="${PROJECT_ROOT_DIR}/src:${PYTHONPATH}"

# Configura diretório de logs
LOG_DIR="${PROJECT_ROOT_DIR}/logs"
mkdir -p "$LOG_DIR"
MAIN_LOG="${LOG_DIR}/simulation.log"

# Ativa venv se existir
if [ -f ".venv/bin/activate" ]; then source .venv/bin/activate; fi

# Cria/Limpa log inicial
touch "$MAIN_LOG"

# Variáveis Globais Padrão
VEHICLE_COUNT=1000
INSERTION_DURATION=3600
TARGET_TL_ID=""

# Função para executar comandos com Spinner e Log
execute_task() {
    local cmd="$1"
    local msg="$2"
    
    echo "----------------------------------------------------------------" >> "$MAIN_LOG"
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] [DEBUG    ] [ShellScript  ] : INICIO: $msg" >> "$MAIN_LOG"
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] [DEBUG    ] [ShellScript  ] : CMD: $cmd" >> "$MAIN_LOG"
    
    echo -n "$msg "
    
    # Executa em background redirecionando tudo para o log
    eval "$cmd" >> "$MAIN_LOG" 2>&1 &
    local pid=$!
    
    # Spinner visual enquanto o PID existe
    local spin='|/-\'
    local i=0
    while kill -0 $pid 2>/dev/null; do 
        i=$(( (i+1) %4 ))
        printf "\b${spin:$i:1}"
        sleep .1
    done
    
    # Captura código de saída
    wait $pid
    local code=$?
    
    printf "\b"
    
    if [ $code -eq 0 ]; then
        echo "[ OK ]"
        echo "[$(date '+%Y-%m-%d %H:%M:%S')] [DEBUG    ] [ShellScript  ] : FIM: SUCESSO" >> "$MAIN_LOG"
    else
        echo "[ERRO]"
        echo "[$(date '+%Y-%m-%d %H:%M:%S')] [ERROR    ] [ShellScript  ] : FIM: ERRO ($code)" >> "$MAIN_LOG"
        echo "Verifique os detalhes em: $MAIN_LOG"
    fi
}

# Função para configurar densidade e duração
prompt_vars() {
    echo "--- Configuração da Base de Dados para o Site ---"
    echo "A simulação no navegador será infinita, mas precisamos gerar"
    echo "um banco de dados de rotas válidas para o Javascript usar."
    echo ""
    echo "[1] Tamanho do Pool de Veículos (Densidade):"
    echo "   1. Baixa  (1.000 rotas únicas)  - Carregamento rápido"
    echo "   2. Média  (5.000 rotas únicas)  - Recomendado"
    echo "   3. Alta   (20.000 rotas únicas) - Alta variedade"
    read -p "   Opção [1-3]: " d
    case $d in 
        1) V=1000;; 
        2) V=5000;; 
        3) V=20000;; 
        *) V=1000;; 
    esac
    
    echo ""
    echo "[2] Diversidade de Horários (Base de Tempo):"
    echo "   Isso define quantos minutos de tráfego único o SUMO deve calcular"
    echo "   antes que o site comece a reciclar/repetir padrões."
    echo "   1. Curta (Calcula 1h de tráfego único)"
    echo "   2. Média (Calcula 3h de tráfego único)"
    read -p "   Opção [1-2]: " t
    case $t in 
        1) D=3600.0;; 
        2) D=10800.0;; 
        *) D=3600.0;; 
    esac
    
    export VEHICLE_COUNT=$V
    export INSERTION_DURATION=$D
}

# Função para definir alvo (Semáforo)
prompt_target() {
    echo "Digite o ID do Semáforo para focar a otimização (Ex: 4238640565)"
    read -p "ID (Pressione Enter para rodar na rede toda): " TID
    export TARGET_TL_ID="$TID"
}

# Loop Principal do Menu
while true; do
    clear
    cat << "EOF"
================================================================
       TCC - SIMULADOR DE TRÁFEGO URBANO (WEB INTEGRATION)
================================================================
 Geração de Cenários (Infra + Dados Estáticos):
    1. Cenário OpenStreetMap (OSM)
    2. Cenário API (Fidelidade SUMO + Web)

 Simulação Backend (Opcional / Depuração):
    3. Rodar OSM (Estático)
    4. Rodar OSM (Adaptativo IA)
    5. Rodar API (Estático)
    6. Rodar API (Adaptativo IA)

 Ferramentas:
    7. Gerar Dashboard de Logs
    8. Gerar Dashboard de Tráfego
    9. Limpar Tudo (Reset)
    0. Sair
================================================================
EOF
    read -p "Selecione uma opção: " opt
    case $opt in
        1) 
            prompt_vars
            execute_task "python3 -m tcc_sumo.tools.scenario_generator_osm --input osm_bbox.osm.xml --vehicles $VEHICLE_COUNT --duration $INSERTION_DURATION" "Gerando Cenário OSM..."
            ;;
        2) 
            prompt_vars
            # Passa os argumentos para o Python pular o menu interativo dele
            execute_task "python3 -m tcc_sumo.tools.scenario_generator_api --input dados_api.json --vehicles $VEHICLE_COUNT --duration $INSERTION_DURATION" "Gerando Cenário API & Web..."
            ;;
        3) 
            prompt_target
            execute_task "python3 -m main --scenario osm --mode STATIC --target-tl-id '$TARGET_TL_ID'" "Simulando OSM (Static)..."
            ;;
        4) 
            prompt_target
            execute_task "python3 -m main --scenario osm --mode ADAPTIVE --target-tl-id '$TARGET_TL_ID'" "Simulando OSM (IA)..."
            ;;
        5) 
            prompt_target
            execute_task "python3 -m main --scenario api --mode STATIC --target-tl-id '$TARGET_TL_ID'" "Simulando API (Static)..."
            ;;
        6) 
            prompt_target
            execute_task "python3 -m main --scenario api --mode ADAPTIVE --target-tl-id '$TARGET_TL_ID'" "Simulando API (IA)..."
            ;;
        7) 
            execute_task "python3 -m tcc_sumo.tools.traffic_analyzer --source logs" "Processando Logs..."
            ;;
        8) 
            execute_task "python3 -m tcc_sumo.tools.traffic_analyzer --source traffic" "Analisando Tráfego..."
            ;;
        9) 
            echo "Limpando arquivos gerados..."
            rm -rf logs/* output/* scenarios/from_api/* scenarios/from_osm/*
            touch "$MAIN_LOG"
            echo "[$(date '+%Y-%m-%d %H:%M:%S')] [WARNING ] [ShellScript  ] : LIMPEZA COMPLETA REALIZADA" >> "$MAIN_LOG"
            sleep 1
            ;;
        0) 
            echo "Saindo..."
            exit 0
            ;;
        *) 
            echo "Opção inválida."
            sleep 1
            ;;
    esac
    
    if [[ "$opt" != "0" ]]; then 
        read -n 1 -s -r -p "Pressione qualquer tecla para continuar..."
    fi
done