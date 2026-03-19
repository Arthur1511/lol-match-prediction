# BRAINSTORM: Collector Performance Optimization

**Date**: 2026-03-18
**Status**: Ready for DEFINE phase
**Goal**: Improve collector execution time through per-endpoint rate limiting and response caching

---

## Problem Statement

The current Riot API collector uses a single global rate limiter (20 req/sec) for all endpoints. This causes two issues:

1. **Underutilization**: Match-v5 endpoints can handle 200 req/sec but are throttled to 20 req/sec (10x waste)
2. **429 Errors**: League-v4 endpoints have stricter limits (3-5 req/sec) and hit rate limits when sharing the 20 req/sec bucket

**User Goals**:
- Collect more data faster (moderate scale: < 50k matches per run)
- Reduce costs/retries (eliminate 429 errors)

---

## Discovery Summary

**Questions Asked**:
1. Primary goals: Scale throughput + improve reliability
2. Target scale: Moderate (< 50k matches per collection run)
3. Pain point: Rate limit issues (429 errors)
4. Affected endpoints: Unsure (indicates need for per-endpoint monitoring)

**Key Finding**: User provided `api_rate_limits.md` with detailed per-endpoint limits, revealing the single global limiter is misaligned with actual API capacities.

---

## Proposed Solution: Per-Endpoint Rate Limiting + Response Caching

### Approach A: Per-Endpoint Rate Limiter ⭐ Selected

Create an `EndpointAwareRateLimiter` class that manages multiple token buckets:

| Endpoint | Current Limit | Actual Capacity | New Limit |
|----------|---------------|-----------------|-----------|
| Match-v5 (details, IDs) | 20 req/sec | 200 req/sec | **200 req/sec** |
| League-v4 elite tiers | 20 req/sec | 3 req/sec | **3 req/sec** |
| League-v4 entries | 20 req/sec | 5 req/sec | **5 req/sec** |
| Summoner-v4 | 20 req/sec | 27 req/sec | **27 req/sec** |

**Benefits**:
- 10x faster match fetching (20 → 200 req/sec)
- Eliminates 429s on league endpoints (respects actual limits)
- Config-driven for easy adjustment

### Approach B: Response Caching with LRU Eviction ⭐ Selected

In-memory LRU cache using `OrderedDict` to eliminate redundant match requests:

```python
class LRUCache:
    """Simple LRU cache with full control and async compatibility."""
```

**Benefits**:
- 20-40% fewer API calls (high-elo players share matches)
- Full control and observability (hit rate stats)
- Async-native (no decorator workaround needed)
- Memory-safe with automatic eviction

---

## Expected Performance Impact

| Metric | Before | After | Improvement |
|--------|--------|-------|-------------|
| Match fetch throughput | ~20 req/sec | ~200 req/sec | **10x** |
| Time for 10k matches | ~18 min | ~2 min | **9x faster** |
| League ladder 429s | Occasional | Eliminated | **100% reduction** |
| Redundant API calls | 20-40% | Near 0% | **~30% fewer requests** |
| Time for 50k matches | ~90 min | ~10 min | **9x faster** |

---

## Technical Design

### Component 1: EndpointAwareRateLimiter

**File**: `collector/rate_limiter.py` (new class)

```python
class EndpointAwareRateLimiter:
    """Manages multiple rate limiters per API endpoint."""

    def __init__(self, limits: Dict[str, int]):
        self.limiters = {
            'match_v5': RateLimiter(requests_per_second=limits.get('match_v5', 200)),
            'league_v4_elite': RateLimiter(requests_per_second=limits.get('league_v4_elite', 3)),
            'league_v4_entries': RateLimiter(requests_per_second=limits.get('league_v4_entries', 5)),
            'summoner_v4': RateLimiter(requests_per_second=limits.get('summoner_v4', 27)),
            'default': RateLimiter(requests_per_second=limits.get('default', 20)),
        }
        self._endpoint_mapping = {...}  # URL → limiter_key mapping

    async def acquire(self, url: str):
        """Acquire permit for specific endpoint."""
        limiter_key = self._get_limiter_key(url)
        await self.limiters[limiter_key].acquire()
```

### Component 2: LRUCache

**File**: `collector/rate_limiter.py` (new class)

```python
from collections import OrderedDict

class LRUCache:
    """LRU cache with full control and async compatibility."""

    def __init__(self, max_size: int = 10000):
        self.cache = OrderedDict()
        self.max_size = max_size
        self.stats = {'hits': 0, 'misses': 0}

    def get(self, key: str):
        """Get value and move to end (most recently used)."""
        if key in self.cache:
            self.stats['hits'] += 1
            self.cache.move_to_end(key)
            return self.cache[key]
        self.stats['misses'] += 1
        return None

    def set(self, key: str, value):
        """Set value and evict oldest if over limit."""
        if key in self.cache:
            self.cache.move_to_end(key)
        self.cache[key] = value
        if len(self.cache) > self.max_size:
            self.cache.popitem(last=False)

    def get_stats(self) -> Dict:
        """Return cache performance stats."""
        total = self.stats['hits'] + self.stats['misses']
        return {
            'hits': self.stats['hits'],
            'misses': self.stats['misses'],
            'hit_rate': f"{self.stats['hits'] / total:.1%}" if total else "0%",
            'size': len(self.cache)
        }
```

### Component 3: Collector Integration

**File**: `collector/riot_api_collector.py` (modifications)

```python
class RiotAPICollector:
    def __init__(self, config_path: str = "config/config.yaml"):
        # Replace single RateLimiter with EndpointAwareRateLimiter
        self.rate_limiter = EndpointAwareRateLimiter(
            limits=self.config['riot_api']['endpoint_rate_limits']
        )

        # Add cache if enabled
        if self.config['performance']['enable_response_cache']:
            self._match_cache = LRUCache(
                max_size=self.config['performance']['cache_max_size']
            )
        else:
            self._match_cache = None

    async def fetch_match_details(self, match_id: str) -> Optional[Dict]:
        """Fetch match details with caching."""
        # Check cache first
        if self._match_cache:
            cached = self._match_cache.get(match_id)
            if cached:
                return cached

        # Fetch from API
        match_data = await self._make_request(url)

        # Cache the result
        if match_data and self._match_cache:
            self._match_cache.set(match_id, match_data)

        return match_data
```

### Component 4: Configuration

**File**: `config/config.yaml` (additions)

```yaml
riot_api:
  # Per-endpoint rate limits (requests per second)
  endpoint_rate_limits:
    match_v5: 200        # /lol/match/v5/* endpoints
    league_v4_elite: 3    # challenger/grandmaster/master leagues
    league_v4_entries: 5  # league entries by tier/division
    summoner_v4: 27       # summoner lookup
    default: 20           # fallback for unknown endpoints

performance:
  max_concurrent_requests: 15
  enable_batch_processing: true
  enable_response_cache: true        # NEW
  cache_max_size: 10000              # NEW
  report_cache_stats: true           # NEW
```

### Component 5: Enhanced Logging

Add performance summary at end of collection showing:
- Cache hit rate and size
- Requests saved
- Per-endpoint token availability
- Overall throughput

---

## Features Removed (YAGNI)

The following features were considered but excluded from MVP:

| Feature | Reason Excluded |
|---------|-----------------|
| Cache persistence to disk | Can add later if checkpoint/resume needed |
| Multi-region parallel collection | Out of scope for current scale |
| Adaptive rate limiting | Over-engineering; documented limits are stable |
| Distributed caching (Redis) | Single-machine deployment |

---

## Implementation Approach

**Recommended Strategy**:
1. Implement `EndpointAwareRateLimiter` first (addresses 429 errors)
2. Add `LRUCache` second (reduces API calls)
3. Integrate both into `RiotAPICollector`
4. Add enhanced logging for observability
5. Update tests and documentation

**Risk Mitigation**:
- All changes are backward compatible (feature flags in config)
- Falls back to existing behavior if optimizations disabled
- Can tune limits via config without code changes

---

## Open Questions

None - requirements are clear based on documented API limits and user goals.

---

## Next Step

Run `/define .claude/sdd/features/BRAINSTORM_collector-performance.md` to capture formal requirements.
