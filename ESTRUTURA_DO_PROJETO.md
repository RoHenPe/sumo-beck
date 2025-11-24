Projeto de Simulação de Tráfego Inteligente com SUMO (TCC)
1. Visão Geral
Este projeto implementa e valida um sistema de controlo de tráfego adaptativo de alta fidelidade, utilizando a plataforma SUMO (Simulation of Urban MObility). A missão é comparar empiricamente a eficiência de um controlador de semáforos de tempo fixo (modo STATIC) com um agente de controlo dinâmico (modo ADAPTIVE) que utiliza dados da simulação em tempo real para tomar decisões.

O objetivo final é criar um sistema robusto que não apenas funcione, mas que também sirva como uma plataforma educacional e de pesquisa clara e bem documentada para o estudo de sistemas inteligentes, teoria de filas, engenharia de software e otimização de fluxo em redes complexas.

2. Funcionalidades Principais
Dois Modos de Controlo: Execute simulações no modo STATIC (tempos de semáforo fixos, geridos pelo SUMO) ou ADAPTIVE (IA que ajusta os tempos de semáforo em tempo real).

Geração de Cenários Flexível: Crie malhas viárias e fluxos de veículos a partir de duas fontes distintas: dados geográficos do OpenStreetMap (OSM) ou a partir de uma estrutura de dados de uma API (JSON), permitindo alta flexibilidade nos testes.

Controlo Inteligente Adaptativo: A lógica do modo ADAPTIVE monitoriza as filas de veículos em cada via e decide se deve estender a fase verde de um semáforo para otimizar o fluxo e reduzir congestionamentos, baseando-se em limiares configuráveis.

Orquestração via Linha de Comando: Um script interativo (run_simulation.sh) guia o utilizador através de todas as etapas: geração de cenário, escolha da densidade de tráfego, execução da simulação e análise de resultados, tornando o sistema acessível e fácil de usar.

Análise de Resultados Abrangente: O sistema gera múltiplos outputs para análise:

Relatórios em "Ticket": Ficheiros de texto (human_analysis_report.log) com um resumo executivo claro dos KPIs (Key Performance Indicators) de cada simulação.

Dados Consolidados: Um ficheiro consolidated_data.json que armazena os resultados de todas as simulações, criando uma base de dados histórica para análises comparativas.

Dashboards Interativos: Geração de relatórios HTML (log_dashboard.html, traffic_dashboard.html) com gráficos e tabelas interativas para uma análise visual profunda dos logs e dos resultados de tráfego.

Estrutura Profissional: O código é organizado como um pacote Python modular, separando configuração, lógica de negócio, ferramentas e execução, seguindo as melhores práticas de engenharia de software para garantir alta manutenibilidade e extensibilidade.

3. Arquitetura do Projeto
A estrutura foi desenhada para ser modular e escalável, garantindo baixo acoplamento e alta coesão entre os componentes.

TCC_SUMO/
│
├── config/
│   ├── config.yaml
│   └── logging_config.json
│
├── logs/
│   ├── consolidated_data.json
│   ├── generation.log
│   ├── human_analysis_report.log
│   └── simulation.log
│
├── output/
│   ├── log_dashboard.html
│   └── traffic_dashboard.html
│
├── scenarios/
│   ├── base_files/
│   │   ├── dados_api.json
│   │   └── osm_bbox.osm.xml
│   ├── from_api/
│   └── from_osm/
│
├── scripts/
│   └── run_simulation.sh
│
└── src/
    ├── main.py
    └── tcc_sumo/
        ├── __init__.py
        ├── simulation/
        │   ├── __init__.py
        │   ├── manager.py
        │   └── traci_connection.py
        ├── templates/
        │   ├── log_dashboard.html
        │   └── traffic_dashboard.html
        ├── tools/
        │   ├── __init__.py
        │   ├── log_analyzer.py
        │   ├── scenario_generator.py
        │   └── traffic_analyzer.py
        ├── traffic_logic/
        │   ├── __init__.py
        │   └── controllers.py
        └── utils/
            ├── __init__.py
            └── helpers.py
/config: Centraliza todas as configurações. config.yaml para parâmetros da simulação e logging_config.json para o formato dos logs.

/scripts: Contém o orquestrador run_simulation.sh, a interface de linha de comando para o utilizador final.

/src: Abriga todo o código-fonte Python.

main.py: Ponto de entrada que interpreta os argumentos da linha de comando e inicializa o SimulationManager.

/tcc_sumo: O coração do projeto, estruturado como um pacote Python.

/simulation: Módulos que gerem a interação com o SUMO. traci_connection.py lida com a conexão e manager.py orquestra o ciclo de vida da simulação.

/traffic_logic: Onde reside a inteligência artificial do sistema. controllers.py contém as classes StaticController e AdaptiveController que definem o comportamento dos semáforos.

/tools: Ferramentas de suporte. scenario_generator.py cria os cenários, log_analyzer.py processa os outputs do SUMO, e traffic_analyzer.py gera os dashboards HTML.

/templates: Contém os templates HTML (com Jinja2) para a geração dos dashboards interativos.

/utils: Funções de suporte (helpers.py) para tarefas como configuração de logs, formatação de tempo e verificação de ambiente.

O Papel do __init__.py
Você notará que cada subdiretório dentro de src/tcc_sumo contém um arquivo __init__.py. Este arquivo é fundamental: ele diz ao Python que a pasta deve ser tratada como um "pacote". Isso permite a importação estruturada de módulos (from tcc_sumo.simulation.manager import SimulationManager), tornando o código organizado, modular e reutilizável.

4. Princípios de Operação do Controlo Adaptativo (IA)
O núcleo da inovação deste projeto reside no AdaptiveController dentro de src/tcc_sumo/traffic_logic/controllers.py. A sua lógica opera sob os seguintes princípios:

Segurança em Primeiro Lugar (Safety-First Principle): O ciclo de fases (VERDE -> AMARELO -> VERMELHO) é inviolável. A IA nunca tentará saltar a fase amarela ou criar um estado inseguro. A duração das fases amarela e vermelha é fixa e controlada pelo SUMO.

Decisões Baseadas em Dados (Data-Driven Decisions): A decisão de estender um sinal verde é baseada em dados em tempo real. O controlador monitoriza as lanes associadas a uma fase verde ativa, utilizando a função traci.lane.getLastStepHaltingNumber() para obter o número de veículos parados.

Extensão Adaptativa com Limiar (Threshold-Based Adaptive Extension): Se uma fase verde está prestes a terminar, mas a telemetria indica veículos em fila, a IA avalia se a procura na próxima fase é significativamente maior. Apenas se a fila na próxima fase verde exceder a fila atual por um limiar (SWITCH_THRESHOLD), a transição ocorre. Caso contrário, a fase verde atual é estendida para dissipar a sua fila.

Eficiência de Recursos (Resource Efficiency): Se não há veículos à espera (getLastStepHaltingNumber == 0), estender a fase verde seria um desperdício. Nesse caso, a IA permite que o ciclo de semáforos prossiga normalmente, libertando o cruzamento para o próximo fluxo de tráfego.

5. Pré-requisitos
SUMO: A plataforma de simulação de tráfego. Garanta que o executável esteja no PATH do sistema ou que a variável de ambiente $SUMO_HOME esteja configurada.

Verificação: sumo-gui

Python: Versão 3.8 ou superior.

Verificação: python3 --version

Bibliotecas Python: Instale as dependências necessárias.

Bash

pip install pyyaml pandas jinja2
6. Instalação
Clone o repositório ou descompacte os arquivos.

Verifique o arquivo config/config.yaml e ajuste os parâmetros (especialmente os caminhos e as lanes de cada semáforo) para corresponder ao seu cenário.

Dê permissão de execução ao script principal:

Bash

chmod +x scripts/run_simulation.sh
7. Como Executar
A execução é totalmente gerida pelo script interativo run_simulation.sh.

Abra um terminal na pasta raiz do projeto (TCC_SUMO/).

Execute o script:

Bash

./scripts/run_simulation.sh
O script irá guiá-lo com um menu interativo completo:

Geração de Cenários: Opções 1 e 2 para criar cenários OSM ou API, com seleção de densidade de tráfego (ex: 5.000, 50.000, 150.000 veículos).

Simulação: Opções 3 a 6 para executar a simulação nos cenários OSM ou API, nos modos STATIC ou ADAPTIVE.

Análise de Resultados: Opções 7 e 8 para gerar os dashboards interativos de Logs e Tráfego.

Manutenção: Opção 9 para limpar todos os ficheiros gerados.

A janela do sumo-gui será aberta, e a simulação começará. Os logs serão exibidos no terminal e salvos em logs/simulation.log.

8. Análise de Resultados
A eficácia da IA é medida pela análise detalhada dos múltiplos outputs gerados:

Relatório Rápido (Ticket): Para uma visão geral, consulte o ficheiro logs/human_analysis_report.log. Ele fornece um resumo executivo, KPIs como taxa de conclusão de viagens, tempo médio perdido e emissões de CO2.

Base de Dados Histórica: O ficheiro logs/consolidated_data.json armazena os resultados agregados de cada simulação. É a fonte de dados principal para comparações de performance entre diferentes modos e cenários.

Análise Visual (Dashboards): Para uma análise aprofundada, abra os ficheiros em output/. O traffic_dashboard.html mostra gráficos sobre a performance do tráfego, enquanto o log_dashboard.html permite filtrar e analisar os logs do sistema, o que é crucial para depuração e diagnóstico de comportamento.

Para comparar os modos, execute a simulação uma vez em cada modo (STATIC e ADAPTIVE) com o mesmo cenário e densidade. Depois, compare os resultados nos dashboards e relatórios. Uma redução significativa nas métricas Tempo Médio Perdido (s) e Veículos Removidos (Não Concluídos) no modo ADAPTIVE comprova a eficiência do controlo inteligente.