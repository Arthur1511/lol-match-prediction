# League of Legends Match Prediction - Workspace Instructions

> **Project Type**: Machine Learning (Time-Series Prediction) + MLOps  
> **Domain**: League of Legends competitive match outcome prediction  
> **Architecture**: Bronze/Silver/Gold Data Lakehouse Pattern

---

## 🎯 Project Mission

Build an end-to-end ML system to predict Solo Queue match outcomes and measure the impact of **player skill vs champion draft** on win probability.

**Core Experiment**: ΔAUC = AUC_post_draft − AUC_pre_draft

---

## 🏗️ Architecture Principles

### Data Flow (Strictly Ordered)
```
Riot API → Landing Zone (compressed JSON)
    ↓
Bronze Layer (raw, immutable)
    ↓
Silver Layer (structured, cleaned)
    ↓  
Gold Layer (features with temporal correctness)
    ↓
Models (Pre-Draft → Post-Draft)
    ↓
Monitoring (patch drift detection)
```

**Critical Rule**: Each layer is immutable once written. Reprocessing requires new partitions/versions.

### Component Boundaries

- **`collector/`**: API interaction ONLY. No data transformation.
- **`data_pipeline/`**: Pure data transformations. No ML logic.
- **`models/`**: ML training/inference ONLY. Receives Gold layer features.
- **`monitoring/`**: Model evaluation. No training logic.

---

## ⚠️ Critical Anti-Patterns (MUST AVOID)

### 🔴 Data Leakage - Temporal Violations

**NEVER** use information unavailable at prediction time:

```python
# ❌ WRONG - Uses in-game stats for pre-game prediction
features = {
    'player_avg_kda': player.kda,  # Known only AFTER match
    'team_gold_diff': match.gold_diff_15min  # In-game data
}

# ✅ CORRECT - Only historical data before match start
features = {
    'player_winrate_last_20': calculate_before_match(player, match.datetime),
    'player_kda_avg_last_20': historical_kda(player, before=match.datetime)
}
```

**Enforce**: All feature functions must accept `before_datetime` parameter and filter data chronologically.

### 🔴 Data Leakage - Future Data in Training

```python
# ❌ WRONG - Random train/test split
X_train, X_test = train_test_split(df, test_size=0.2, random_state=42)

# ✅ CORRECT - Temporal split
cutoff_date = df['match_datetime'].quantile(0.8)
X_train = df[df['match_datetime'] < cutoff_date]
X_test = df[df['match_datetime'] >= cutoff_date]
```

**Rule**: ALWAYS split by time, NEVER randomly.

### 🔴 Riot API Rate Limits

**Production Key Limits**: 20 req/sec, 100 req/2min

```python
# ❌ WRONG - No rate limiting
for match_id in match_ids:
    response = requests.get(f'/match/{match_id}')

# ✅ CORRECT - Rate limiter with exponential backoff
async with rate_limiter.acquire():
    response = await session.get(f'/match/{match_id}')
```

**Requirements**:
- Implement token bucket algorithm in `collector/rate_limiter.py`
- Cache all API responses to avoid duplicate calls
- Use `tqdm` for progress tracking on long collections

### 🔴 Riot API Tier-Specific Endpoints

**Critical**: CHALLENGER, GRANDMASTER, and MASTER tiers use different endpoints than DIAMOND and below.

```python
# ✅ CORRECT - Different endpoints for elite tiers
endpoints = {
    # Elite tiers (return object with 'entries' field)
    'challenger_league': '/lol/league/v4/challengerleagues/by-queue/{queue}',
    'grandmaster_league': '/lol/league/v4/grandmasterleagues/by-queue/{queue}',
    'master_league': '/lol/league/v4/masterleagues/by-queue/{queue}',
    
    # DIAMOND and below (return list directly)
    'league_entries': '/lol/league/v4/entries/{queue}/{tier}/{division}'
}

# Elite tiers response: {'entries': [...], 'tier': 'CHALLENGER', ...}
# DIAMOND+ response: [...]
```

**Response Format Differences**:
- **Elite tiers** (CHALLENGER/GRANDMASTER/MASTER): Return object `{entries: [...], tier: '...', ...}`
- **Standard tiers** (DIAMOND/PLATINUM/etc): Return list directly `[...]`

**Implementation**:
```python
if tier in ['CHALLENGER', 'GRANDMASTER', 'MASTER']:
    response = await get_special_league(tier)
    entries = response['entries']  # Extract from object
else:
    entries = await get_league_entries(tier, division)  # List directly
```

### 🔴 Champion Meta Drift

**Issue**: Game patches change champion balance → features become stale

```python
# ✅ ALWAYS track patch version
match_data = {
    'match_id': '...',
    'patch_version': '14.5',  # Required field
    'match_datetime': datetime(...),
    # ...
}

# ✅ Filter training data by patch recency
recent_patches = df[df['patch_version'].isin(['14.4', '14.5'])]
model.fit(recent_patches[features], recent_patches['target'])
```

**Monitoring**: Alert if model AUC drops >2% week-over-week (likely meta shift).

---

## 📊 Feature Engineering Rules

### Temporal Window Standard

**Default**: Last 20 games per player (configurable in `config/config.yaml`)

```python
def calculate_player_features(player_id: str, before_datetime: datetime, window: int = 20):
    """
    Calculate rolling statistics for a player.
    
    Args:
        player_id: Player PUUID
        before_datetime: Calculate features using ONLY games before this time
        window: Number of recent games to aggregate (default 20)
    
    Returns:
        dict: {
            'player_winrate_last_N': float,
            'player_kda_avg': float,
            'player_games_played': int,
            ...
        }
    """
    # Implementation must filter: games.datetime < before_datetime
```

### Feature Categories

**Pre-Draft Features** (Player Skill Only):
- `player_winrate_last_20`
- `player_kda_avg`
- `player_gold_diff_avg`
- `player_damage_share_avg`
- `player_vision_score_avg`
- `team_avg_elo`
- `team_elo_variance`

**Post-Draft Features** (Adds Champion Data):
- All pre-draft features +
- `champion_winrate_overall`
- `player_champion_mastery` (player's history on specific champ)
- `champion_synergy_score` (TBD - requires champion interaction matrix)
- `lane_matchup_advantage` (TBD - requires role-specific data)

### Minimum Data Requirements

```python
# ✅ Handle players with insufficient history
if player_games_count < 5:
    # Option 1: Exclude from training
    continue
    
    # Option 2: Use league average imputation
    features['player_winrate_last_20'] = league_avg_winrate
```

**Rule**: Document imputation strategy in `data_pipeline/feature_engineering.py` docstring.

---

## 🔧 Tech Stack Conventions

### Python Style

- **Version**: Python 3.14+ (uses `uv` for package management)
- **Type Hints**: Required for all public functions
- **Async**: Use `async/await` for all I/O operations (API calls, file I/O)
- **Progress**: Use `tqdm` for long-running loops

```python
from typing import Dict, List
import asyncio

async def fetch_matches(match_ids: List[str]) -> List[Dict]:
    """Fetch match data from Riot API."""
    results = []
    for match_id in tqdm(match_ids, desc="Fetching matches"):
        async with rate_limiter.acquire():
            match_data = await api_client.get_match(match_id)
            results.append(match_data)
    return results
```

### Data Storage

- **Format**: Parquet (compressed, columnar)
- **Partitioning**: By `patch_version` and `region`
- **Schema**: Enforce strict schemas at Bronze → Silver transition

```python
# Bronze: Save raw JSON as-is
df_bronze.to_parquet('bronze/matches.parquet', compression='snappy')

# Silver: Enforce schema
silver_schema = {
    'match_id': 'string',
    'patch_version': 'string',
    'match_datetime': 'datetime64[ns]',
    'player_puuid': 'string',
    # ...
}
df_silver = df_bronze.astype(silver_schema)
```

### Configuration

**File**: `config/config.yaml`

```yaml
riot_api:
  api_key: ${RIOT_API_KEY}  # From environment variable
  rate_limit_per_second: 20
  rate_limit_per_2min: 100
  regions: ['br1', 'na1', 'euw1']
  
  # Tier-specific endpoints
  endpoints:
    # Elite tiers (return object with 'entries' field)
    challenger_league: "/lol/league/v4/challengerleagues/by-queue/{queue}"
    grandmaster_league: "/lol/league/v4/grandmasterleagues/by-queue/{queue}"
    master_league: "/lol/league/v4/masterleagues/by-queue/{queue}"
    # Standard tiers (return list directly)
    league_entries: "/lol/league/v4/entries/{queue}/{tier}/{division}"

data_pipeline:
  bronze_path: 'data/bronze/'
  silver_path: 'data/silver/'
  gold_path: 'data/gold/'
  feature_window: 20  # games

models:
  random_state: 42
  test_split_method: 'temporal'  # Never 'random'
  pre_draft:
    model_type: 'lightgbm'
    hyperparameters: {...}
  post_draft:
    model_type: 'lightgbm'
    hyperparameters: {...}
```

**Access Pattern**:
```python
import yaml
with open('config/config.yaml') as f:
    config = yaml.safe_load(f)
```

### MLflow Integration

```python
import mlflow

with mlflow.start_run(run_name=f"pre_draft_{patch_version}"):
    mlflow.log_param("patch_version", patch_version)
    mlflow.log_param("feature_window", 20)
    mlflow.log_param("model_type", "lightgbm")
    
    model.fit(X_train, y_train)
    
    auc = roc_auc_score(y_test, model.predict_proba(X_test)[:, 1])
    mlflow.log_metric("test_auc", auc)
    mlflow.sklearn.log_model(model, "model")
```

---

## 🚀 Development Workflow

### Phase 1: Data Collection
```bash
# 1. Configure API key
echo "RIOT_API_KEY=RGAPI-xxx" > .env

# 2. Run collector
uv run python collector/riot_api_collector.py --region br1 --num-matches 1000

# Expected output: data/bronze/matches_YYYYMMDD.parquet
```

### Phase 2: Data Pipeline
```bash
# 1. Bronze → Silver (parse + validate)
uv run python data_pipeline/bronze_to_silver.py

# 2. Silver → Gold (feature engineering)
uv run python data_pipeline/feature_engineering.py

# Expected outputs:
#   data/silver/matches.parquet
#   data/silver/players.parquet
#   data/gold/pre_draft_features.parquet
#   data/gold/post_draft_features.parquet
```

### Phase 3: Model Training
```bash
# 1. Train pre-draft model
uv run python models/train_pre_draft_model.py

# 2. Train post-draft model
uv run python models/train_post_draft_model.py

# 3. Compare results
uv run python monitoring/compare_models.py
# Expected: ΔAUC metric, feature importances
```

### Phase 4: Monitoring
```bash
# Launch MLflow UI
mlflow ui --backend-store-uri ./mlruns

# Monitor patch drift
uv run python monitoring/drift_detection.py --patch 14.6
```

---

## 🧪 Testing Strategy

**Critical Tests**:

1. **Temporal Leakage Test**
   ```python
   def test_no_future_data_in_features():
       """Ensure features don't use future information."""
       match = get_test_match()
       features = calculate_features(match.player, match.datetime)
       
       # All source games must be BEFORE match datetime
       assert all(g.datetime < match.datetime for g in features.source_games)
   ```

2. **Rate Limiter Test**
   ```python
   async def test_rate_limiter_respects_limits():
       limiter = RateLimiter(requests_per_sec=20)
       start = time.time()
       
       async def make_requests():
           for _ in range(100):
               await limiter.acquire()
       
       await make_requests()
       elapsed = time.time() - start
       
       # Should take at least 5 seconds (100 requests / 20 per sec)
       assert elapsed >= 5.0
   ```

3. **Schema Validation Test**
   ```python
   def test_silver_schema_compliance():
       df = pd.read_parquet('data/silver/matches.parquet')
       required_cols = ['match_id', 'patch_version', 'match_datetime', ...]
       assert all(col in df.columns for col in required_cols)
   ```

---

## 📝 Code Review Checklist

Before merging any PR:

- [ ] No temporal data leakage (features calculated with `before_datetime`)
- [ ] Train/test split is temporal, not random
- [ ] API calls use rate limiter
- [ ] All Parquet files include `patch_version` column
- [ ] Type hints on all public functions
- [ ] MLflow logging for model experiments
- [ ] Progress bars (`tqdm`) for loops > 100 iterations
- [ ] Config values loaded from `config.yaml`, not hardcoded

---

## 🎓 Learning Resources

- [Riot Games API Docs](https://developer.riotgames.com/)
- [Medallion Architecture (Databricks)](https://www.databricks.com/glossary/medallion-architecture)
- [Preventing Data Leakage (Kaggle)](https://www.kaggle.com/discussions/getting-started/263129)
- [LightGBM Documentation](https://lightgbm.readthedocs.io/)

---

## ❓ When to Ask for Clarification

- **Storage location**: Local filesystem or cloud? (impacts collector implementation)
- **Imputation strategy**: How to handle players with < 20 games?
- **Champion synergy**: Use pre-computed matrix or calculate on-the-fly?
- **Patch recency**: How many patches back to include in training?

---

**Last Updated**: 2026-03-10  
**Maintained By**: Project team
