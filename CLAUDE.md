# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

League of Legends match prediction system using Bronze/Silver/Gold data lakehouse architecture. Core experiment: measure impact of player skill vs champion draft on match outcomes (ΔAUC = AUC_post_draft − AUC_pre_draft).

**Tech Stack**: Python 3.14+, uv package manager, Parquet/Delta Lake storage, LightGBM, MLflow, aiohttp for async API calls.

---

## Common Commands

### Data Collection
```bash
# Set up API key first
cp .env.example .env
# Edit .env and add RIOT_API_KEY=RGAPI-xxx

# Run collector (uses config/config.yaml settings)
uv run python collector/riot_api_collector.py
# Output: data/bronze/matches_{region}_TIMESTAMP.parquet
```

### Testing
```bash
# Run all tests
uv run pytest tests/ -v

# Run specific test file
uv run pytest tests/test_collector.py -v

# Run with coverage
uv run pytest tests/ --cov=collector
```

### Dependency Management
```bash
# Install dependencies
uv sync

# Add new dependency
uv add <package>
```

---

## Architecture & Data Flow

**Riot API → Landing Zone (JSON) → Bronze (raw Parquet) → Silver (structured) → Gold (features) → Models**

Each layer is **immutable** once written. Reprocessing requires new partitions/versions.

### Component Boundaries
- **`collector/`**: API interaction ONLY. Rate limiting via `RateLimiter` class (20 req/sec, 100 req/2min). No data transformation.
- **`data_pipeline/`**: Pure data transformations. Bronze→Silver schema enforcement, Silver→Gold feature engineering.
- **`models/`**: ML training/inference ONLY. Receives Gold layer features.
- **`monitoring/`**: Model evaluation, patch drift detection.

---

## Critical Anti-Patterns (MUST AVOID)

### Data Leakage - Temporal Violations
**NEVER** use information unavailable at prediction time. All feature functions MUST accept `before_datetime` parameter and filter chronologically.

```python
# ❌ WRONG - Uses in-game stats for pre-game prediction
features = {'player_kda': player.kda}  # Known only AFTER match

# ✅ CORRECT - Only historical data before match start
features = {'player_winrate_last_20': calculate_before_match(player, match.datetime)}
```

### Data Leakage - Train/Test Split
**ALWAYS** split by time, NEVER randomly.

```python
# ❌ WRONG
X_train, X_test = train_test_split(df, test_size=0.2, random_state=42)

# ✅ CORRECT - Temporal split
cutoff = df['match_datetime'].quantile(0.8)
X_train = df[df['match_datetime'] < cutoff]
X_test = df[df['match_datetime'] >= cutoff]
```

### Riot API Tier-Specific Endpoints
**Critical**: CHALLENGER, GRANDMASTER, MASTER return `{'entries': [...]}` object. DIAMOND and below return list directly `[...]`.

```python
# collector/riot_api_collector.py handles this correctly
if tier in ["CHALLENGER", "GRANDMASTER", "MASTER"]:
    entries = response["entries"]  # Extract from object
else:
    entries = response  # List directly
```

### Rate Limiting
All API calls MUST use rate limiter:
```python
async with self.rate_limiter:
    response = await session.get(url)
```

---

## Configuration

All settings in `config/config.yaml`. Loaded via:
```python
import yaml
with open('config/config.yaml') as f:
    config_str = f.read()
    config_str = config_str.replace("${RIOT_API_KEY}", os.getenv("RIOT_API_KEY"))
    config = yaml.safe_load(config_str)
```

**Key settings**:
- `riot_api.rate_limit_per_second`: 20 (production API)
- `riot_api.rate_limit_per_2min`: 100
- `riot_api.default_region`: "br1"
- `collection.queue_filter`: 420 (Ranked Solo/Duo only)
- `collection.min_patch_version`: "14.5"
- `performance.max_concurrent_requests`: 15 (keep below rate limit)

---

## Code Conventions

- **Async**: Use `async/await` for all I/O operations (API calls, file I/O)
- **Type hints**: Required for all public functions
- **Progress bars**: Use `tqdm` for loops > 100 iterations
- **Logging**: Configure via `config/config.yaml`, logs written to `logs/collector.log`
- **Storage format**: Parquet with snappy compression, partitioned by `patch_version` and `region`

### Feature Engineering Pattern
Default temporal window: **last 20 games per player** (configurable in config.yaml).

```python
def calculate_player_features(player_id: str, before_datetime: datetime, window: int = 20):
    """
    Calculate rolling statistics for a player.

    Args:
        player_id: Player PUUID
        before_datetime: Calculate features using ONLY games before this time
        window: Number of recent games to aggregate (default 20)

    Returns:
        dict: Feature dict with temporal correctness guaranteed
    """
    # Implementation must filter: games.datetime < before_datetime
```

---

## Data Schema Requirements

**Bronze Layer**: Raw API responses with metadata columns added:
- `_collected_at`: UTC timestamp
- `_region`: Region code
- `patch_version`: Extracted from gameVersion (e.g., "14.5")

**Silver Layer**: Structured tables, schema-enforced:
- `match_id`, `patch_version`, `match_datetime`, `player_puuid`, etc.

**Gold Layer**: Feature matrices for model training. Pre-draft (no champion info) vs Post-draft (includes champion data).
