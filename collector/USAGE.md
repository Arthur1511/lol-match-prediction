# Riot API Collector - Quick Start Guide

## 🚀 Setup

### 1. Get Your Riot API Key

1. Visit [Riot Developer Portal](https://developer.riotgames.com/)
2. Sign in with your Riot account
3. Generate a new API key
4. Copy the key (starts with `RGAPI-`)

### 2. Configure Environment

Create a `.env` file in the project root:

```bash
cp .env.example .env
```

Edit `.env` and add your API key:

```env
RIOT_API_KEY=RGAPI-your-actual-key-here
```

### 3. Configure Collection Settings

Edit `config/config.yaml` to customize:

- **Region**: Change `default_region` (br1, na1, euw1, etc.)
- **Target tiers**: Which elos to collect from (CHALLENGER, MASTER, DIAMOND)
- **Collection limits**:
  - `initial_players_per_tier`: Players to fetch from each tier
  - `matches_per_player`: Match history depth
  - `max_total_matches`: Total matches to collect
  - `max_iterations`: Snowball expansion iterations

## 📊 Running the Collector

### Basic Usage

```bash
uv run python collector/riot_api_collector.py
```

This will:

1. ✅ Collect high-elo players from ranked ladder (BR1 by default)
2. ✅ Fetch match histories for those players
3. ✅ Expand via snowball sampling (discover new players in matches)
4. ✅ Save to `data/bronze/matches_br1_TIMESTAMP.parquet`

### Expected Output

```
============================================================
STEP 1: Collecting players from ranked ladder
============================================================
Fetching CHALLENGER I players...
Found 300 players in CHALLENGER I
Fetching MASTER I players...
Found 500 players in MASTER I
Collected 100 unique players from ladder

============================================================
STEP 2: Collecting matches from players
============================================================
Fetching match lists: 100%|████████████| 100/100
Found 8532 new unique matches to collect
Fetching match details: 100%|████████████| 1000/1000
Collected 1000 total matches

============================================================
STEP 3: Snowball expansion via match participants
============================================================
Iteration 1/3
Discovered 500 new players
Fetching match details: 100%|████████████| 2000/2000
...

============================================================
Saving to Bronze layer
============================================================
Saved 10000 matches to data/bronze/matches_br1_20260311_143022.parquet
Patches: {'14.5': 8234, '14.4': 1766}

============================================================
Collection complete!
Total players: 650
Total matches: 10000
Output: data/bronze/matches_br1_20260311_143022.parquet
============================================================
============================================================
Performance Summary
============================================================
Cache: 28.3% hit rate | 7150/10000 entries | 2850 requests saved
Endpoint Token Availability:
  match_v5: 180 tokens/sec available
  league_v4_elite: 2 tokens/sec available
Total Time: 125.3s | Matches: 10000 | Throughput: 79.8 matches/sec
============================================================
```

## ⚙️ Configuration Options

### Performance Optimizations

The collector includes two performance optimizations enabled by default:

#### Per-Endpoint Rate Limiting

Instead of a single global rate limiter, the collector uses **per-endpoint rate limiting** based on Riot's documented API limits:

| Endpoint | Rate Limit | Description |
|----------|------------|-------------|
| `match_v5` | 200 req/sec | Match details and match IDs |
| `league_v4_elite` | 3 req/sec | Challenger/Grandmaster/Master leagues |
| `league_v4_entries` | 5 req/sec | League entries by tier/division |
| `summoner_v4` | 27 req/sec | Summoner lookups |
| `default` | 20 req/sec | Fallback for unknown endpoints |

**Benefits**:
- **10x faster** match fetching (200 req/sec vs 20 req/sec)
- **Eliminates 429 errors** on league endpoints by respecting actual limits

#### Response Caching

The collector caches match data in memory to eliminate redundant API calls:

- **LRU eviction** when cache reaches `cache_max_size` (default: 10,000 matches)
- **20-40% reduction** in API calls (high-elo players share matches)
- **Automatic hit rate tracking** in performance summary

**Benefits**:
- Faster collection when matches are shared across players
- Reduced API quota usage
- Memory-safe with automatic eviction

#### Configuration

Configure optimizations in `config/config.yaml`:

```yaml
riot_api:
  # Per-endpoint rate limits (enabled by default)
  endpoint_rate_limits:
    match_v5: 200        # /lol/match/v5/* endpoints
    league_v4_elite: 3    # challenger/grandmaster/master leagues
    league_v4_entries: 5  # league entries by tier/division
    summoner_v4: 27       # summoner lookup
    default: 20           # fallback for unknown endpoints

performance:
  # Response cache settings
  enable_response_cache: true        # Enable/disable caching
  cache_max_size: 10000              # Max matches to cache
  report_cache_stats: true           # Log cache performance
```

#### Performance Summary

At the end of each collection, a performance summary is logged:

```
============================================================
Performance Summary
============================================================
Cache: 28.3% hit rate | 7150/10000 entries | 2850 requests saved
Endpoint Token Availability:
  match_v5: 180 tokens/sec available
  league_v4_elite: 2 tokens/sec available
  league_v4_entries: 4 tokens/sec available
  summoner_v4: 25 tokens/sec available
  default: 18 tokens/sec available
Total Time: 125.3s | Matches: 10000 | Throughput: 79.8 matches/sec
============================================================
```

### Legacy Rate Limiting

For backward compatibility, the collector supports a single global rate limiter:

- **Production key**: 20 requests/second, 100 requests/2 minutes
- Uses token bucket algorithm with automatic backoff

To use legacy mode, remove `endpoint_rate_limits` from `config/config.yaml`.

### Queue Filtering

By default, only **Ranked Solo/Duo** matches are collected (queue ID 420).

Edit `config/config.yaml` to change:

```yaml
collection:
  queue_filter: 420  # Ranked Solo/Duo only
```

### Patch Filtering

Optionally filter by minimum patch version:

```yaml
collection:
  min_patch_version: "14.5"  # Only collect from 14.5+
  # OR set to null to collect all patches
  min_patch_version: null
```

## 🏗️ Three-Step Sampling Strategy

### Step 1: Ladder Collection

- Fetches players from `CHALLENGER`, `MASTER`, `DIAMOND` tiers
- Uses League-V4 API `/lol/league/v4/entries`
- Returns summoner IDs and basic stats

### Step 2: Match Histories

- For each player, fetches up to 100 recent Ranked Solo/Duo matches
- Uses Match-V5 API `/lol/match/v5/matches/by-puuid/{puuid}/ids`
- Deduplicates match IDs across players

### Step 3: Snowball Expansion

- Parses collected matches to discover new players
- Fetches match histories for newly discovered players
- Repeats for configured number of iterations
- Creates representative sample of the competitive ecosystem

## 📂 Output Structure

### Bronze Layer Format

Parquet file with columns:

- `metadata`: Full match metadata (game mode, duration, patch)
- `info`: Match details (participants, teams, timeline)
- `patch_version`: Extracted patch (e.g., "14.5")
- `region`: Region code (e.g., "br1")
- `_collected_at`: UTC timestamp of collection
- `_region`: Region where match occurred

### Data Flow

```
Riot API
    ↓
Landing Zone (temp JSON)
    ↓
Bronze Layer (data/bronze/*.parquet)
    ↓
[Next: Bronze → Silver transformation]
```

## 🛠️ Troubleshooting

### API Key Issues

```
ValueError: RIOT_API_KEY not found in environment variables
```

**Solution**: Ensure `.env` file exists with valid key

### Rate Limit Errors

```
Rate limited. Waiting 120s...
```

**Solution**: Normal behavior. Collector automatically waits and retries.

### No Matches Found

```
No players found in DIAMOND I
```

**Solution**:

- Check if region is correct
- Verify API key has proper permissions
- Try different tier (MASTER, CHALLENGER)

### Import Errors

```
ModuleNotFoundError: No module named 'aiohttp'
```

**Solution**: Run `uv sync` to install dependencies

## 📊 Next Steps

After collection, proceed to data pipeline:

```bash
# Transform Bronze → Silver
uv run python data_pipeline/bronze_to_silver.py

# Generate features (Silver → Gold)
uv run python data_pipeline/feature_engineering.py

# Train models
uv run python models/train_pre_draft_model.py
```

## 🔍 Monitoring

Check logs at:

```
logs/collector.log
```

Rate limiter stats available during collection:

- Current tokens available per second
- Current tokens available per 2 minutes

## 💡 Tips

- Start with small limits for testing (`max_total_matches: 100`)
- Monitor API key usage on Riot Developer Portal
- Development keys expire after 24 hours - use Production keys for large collections
- **Peak collection rate**: ~180 matches/second = ~650,000 matches/hour (with optimizations)
- **Without optimizations**: ~20 matches/second = ~72,000 matches/hour
- Plan collection windows accordingly for large datasets
- Disable caching for one-time collections (set `enable_response_cache: false`)
- Cache hit rate >20% indicates significant overlap in player match histories
