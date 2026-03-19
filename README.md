# League of Legends Solo Queue Match Prediction

Projeto de Data Science e MLOps para coleta, processamento e modelagem de partidas de **League of Legends** usando a API oficial da Riot Games.

O sistema coleta partidas da fila **Ranked Solo/Duo**, constrói um dataset longitudinal e treina modelos para prever o resultado das partidas.

---

# Objetivo do Projeto

Construir um pipeline completo de Data Science com:

- coleta contínua de dados via API
- armazenamento em data lake
- engenharia de features temporais
- treinamento de modelos preditivos
- monitoramento de performance

Pergunta principal do projeto:

**Quanto da vitória em uma partida é explicada pela habilidade dos jogadores vs a composição de campeões (draft)?**

---

# Arquitetura

Fluxo de dados:

Riot API  
↓  
Landing Zone (JSON comprimido)  
↓  
Bronze Layer  
↓  
Silver Layer (dados estruturados)  
↓  
Gold Layer (features)  
↓  
Model Training  
↓  
Model Monitoring

O pipeline segue o padrão de arquitetura **Bronze / Silver / Gold**.

---

# Coleta de Dados

Os dados são coletados da API da Riot com as seguintes regras:

- fila: Ranked Solo/Duo
- região: configurável
- respeito ao rate limit da API

Estratégia de amostragem:

1. coletar jogadores da ranked ladder
2. coletar partidas desses jogadores
3. expandir a coleta via jogadores encontrados nas partidas

Isso cria um dataset representativo do ecossistema competitivo.

---

# Estrutura do Dataset

## Matches

| campo | descrição |
|------|-----------|
| match_id | identificador da partida |
| patch_version | versão do jogo |
| match_datetime | data da partida |
| team_1_win | resultado da partida |

## Players

| campo | descrição |
|------|-----------|
| player_puuid | identificador do jogador |
| champion | campeão jogado |
| role | posição |
| kills | kills |
| deaths | mortes |
| assists | assistências |
| gold | ouro obtido |
| damage | dano causado |
| vision_score | controle de visão |

---

# Engenharia de Features

As features são baseadas no histórico recente dos jogadores.

Janela de histórico: **últimos 20 jogos por jogador**.

Exemplos de features:

- win rate recente
- média de KDA
- média de gold diff
- consistência de performance
- diversidade de campeões

Features de time:

- elo médio
- variância de elo
- diferença entre times

---

# Modelos

## Modelo Pré-Draft

Utiliza apenas informações disponíveis **antes da escolha dos campeões**.

Exemplos de features:

- histórico de desempenho
- elo dos jogadores
- consistência de performance

Objetivo: estimar vantagem baseada apenas em habilidade.

---

## Modelo Pós-Draft

Utiliza informações adicionais da composição de campeões.

Features adicionais:

- win rate do campeão
- histórico jogador + campeão
- sinergia entre campeões
- matchup de lanes
- balanceamento de dano

---

# Experimento Principal

Comparação entre modelos:

Delta_AUC = AUC_post_draft − AUC_pre_draft

Interpretação:

- Delta pequeno → habilidade domina
- Delta grande → draft domina

---

# Tecnologias

- Python
- Databricks
- Spark
- Parquet / Delta Lake
- Google Cloud Storage
- MLflow

---

# Estrutura do Projeto

collector/
    riot_api_collector.py

data_pipeline/
    bronze_to_silver.py
    feature_engineering.py

models/
    train_pre_draft_model.py
    train_post_draft_model.py

monitoring/
    model_metrics.py

notebooks/
    exploration.ipynb

---

# Roadmap

1. Implementar coletor da API
2. Construir data lake (bronze/silver)
3. Implementar engenharia de features
4. Treinar modelo pré-draft
5. Treinar modelo pós-draft
6. Comparar performance
7. Monitorar meta ao longo do tempo

---

# Licença

Projeto educacional para estudo de Data Science e Machine Learning.
