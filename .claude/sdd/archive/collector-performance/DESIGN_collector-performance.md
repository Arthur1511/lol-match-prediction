# DESIGN: Collector Performance Optimization

**Date**: 2026-03-18
**Status**: ✅ Shipped
**Source**: DEFINE_collector-performance.md

**Shipped**: 2026-03-18 - Implementation complete, all tests passing

---

## Architecture Overview

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                              RiotAPICollector                               │
│                                                                              │
│  ┌──────────────────────────────────────────────────────────────────────┐  │
│  │                    EndpointAwareRateLimiter                          │  │
│  │  ┌──────────────┐  ┌──────────────────┐  ┌─────────────────────┐   │  │
│  │  │  match_v5    │  │ league_v4_elite  │  │  league_v4_entries │   │  │
│  │  │  200 req/s   │  │    3 req/s       │  │      5 req/s       │   │  │
│  │  └──────────────┘  └──────────────────┘  └─────────────────────┘   │  │
│  │  ┌──────────────┐  ┌──────────────────┐                            │  │
│  │  │ summoner_v4  │  │     default      │                            │  │
│  │  │   27 req/s   │  │    20 req/s      │                            │  │
│  │  └──────────────┘  └──────────────────┘                            │  │
│  └──────────────────────────────────────────────────────────────────────┘  │
│                                    │                                        │
│                                    ▼                                        │
│  ┌──────────────────────────────────────────────────────────────────────┐  │
│  │                              LRUCache                                 │  │
│  │  ┌─────────────────────────────────────────────────────────────┐    │  │
│  │  │  OrderedDict<match_id, match_data>                           │    │  │
│  │  │  max_size: 10000 | hits/misses tracking | auto-eviction      │    │  │
│  │  └─────────────────────────────────────────────────────────────┘    │  │
│  └──────────────────────────────────────────────────────────────────────┘  │
│                                    │                                        │
│                                    ▼                                        │
│  ┌──────────────────────────────────────────────────────────────────────┐  │
│  │                         _make_request()                               │  │
│  │  1. Check cache → return if hit                                      │  │
│  │  2. EndpointAwareRateLimiter.acquire(url) → wait if needed           │  │
│  │  3. Execute HTTP request                                             │  │
│  │  4. Cache response if successful                                      │  │
│  └──────────────────────────────────────────────────────────────────────┘  │
└─────────────────────────────────────────────────────────────────────────────┘
```

---

## Components

### Component 1: EndpointAwareRateLimiter

**Purpose**: Manages multiple independent rate limiters, routing each API request to the appropriate limiter based on URL pattern.

**Responsibilities**:
- Route URLs to correct limiter based on endpoint patterns
- Manage independent token buckets per endpoint
- Support configurable limits via config.yaml
- Provide per-endpoint statistics for observability

**File**: `collector/rate_limiter.py` (new class, same file as existing `RateLimiter`)

### Component 2: LRUCache

**Purpose**: In-memory cache for match data to eliminate redundant API calls.

**Responsibilities**:
- Store match data with O(1) lookup/insert
- Track hits/misses and calculate hit rate
- Automatically evict oldest entries when max_size reached
- Thread-safe for async operations

**File**: `collector/rate_limiter.py` (new class, same file as existing `RateLimiter`)

### Component 3: Collector Integration

**Purpose**: Integrate new rate limiting and caching into existing `RiotAPICollector`.

**Responsibilities**:
- Initialize `EndpointAwareRateLimiter` instead of single `RateLimiter`
- Add cache check before API calls in `fetch_match_details()`
- Log performance statistics at end of collection
- Maintain backward compatibility

**File**: `collector/riot_api_collector.py` (modifications to existing class)

### Component 4: Configuration

**Purpose**: Add new configuration sections for per-endpoint limits and cache settings.

**File**: `config/config.yaml` (additions to existing file)

---

## Data Flow

### Request Flow with Caching and Per-Endpoint Rate Limiting

```
fetch_match_details(match_id)
         │
         ▼
┌─────────────────────────┐
│  Check cache            │
│  ┌───────────────────┐  │
│  │ match_id in cache?│  │
│  └───────────────────┘  │
│         │               │
│    ┌────┴────┐          │
│    │         │          │
│   YES       NO          │
│    │         │          │
│    ▼         ▼          │
│  return    Determine   │
│  cached   endpoint key │
│  data      from URL    │
│            │            │
│            ▼            │
│  ┌────────────────────┐ │
│  │ acquire(url)       │ │
│  │ EndpointAware      │ │
│  │ RateLimiter        │ │
│  │ blocks if needed   │ │
│  └────────┬───────────┘ │
│           │             │
│           ▼             │
│  ┌────────────────────┐ │
│  │ HTTP request to    │ │
│  │ Riot API           │ │
│  └────────┬───────────┘ │
│           │             │
│           ▼             │
│  ┌────────────────────┐ │
│  │ Cache response     │ │
│  └────────┬───────────┘ │
└───────────┼─────────────┘
            │
            ▼
        return data
```

---

## Architecture Decisions (ADRs)

### Decision 1: OrderedDict for LRU Cache

| Attribute | Value |
|-----------|-------|
| **Status** | Accepted |
| **Date** | 2026-03-18 |

**Context**: Need an async-compatible LRU cache with full control and minimal overhead.

**Choice**: Use Python's `collections.OrderedDict` for LRU cache implementation.

**Rationale**:
- Built-in type, no external dependencies
- O(1) operations for get/set/move_to_end
- Maintains insertion order for LRU tracking
- Works seamlessly with async (no decorator workarounds)
- Full control over eviction behavior and statistics

**Alternatives Rejected**:
1. `@lru_cache` decorator - Requires wrapper pattern for async, less control, can't inspect cached values
2. `cachetools.LRUCache` - External dependency, not needed for simple use case
3. Custom doubly-linked list - More complex, OrderedDict provides same functionality

**Consequences**:
- Trade-off: Slightly more code than decorator (~30 lines)
- Benefit: Full observability and control
- Benefit: No external dependencies
- Benefit: Async-native implementation

---

### Decision 2: Composition Over Inheritance for EndpointAwareRateLimiter

| Attribute | Value |
|-----------|-------|
| **Status** | Accepted |
| **Date** | 2026-03-18 |

**Context**: Need to manage multiple `RateLimiter` instances without duplicating token bucket logic.

**Choice**: `EndpointAwareRateLimiter` contains multiple `RateLimiter` instances (composition), not extends them.

**Rationale**:
- Each endpoint has independent rate limits (no shared state)
- Existing `RateLimiter` class is battle-tested
- Clear separation of concerns (routing vs rate limiting)
- Easier to test and maintain

**Alternatives Rejected**:
1. Inheritance - Would require modifying `RateLimiter` to support multiple buckets, breaking existing API
2. Single limiter with variable rates - Cannot independently enforce per-endpoint limits
3. Global limiter registry - Adds unnecessary complexity

**Consequences**:
- Trade-off: Slightly more complex initialization (5 limiters vs 1)
- Benefit: Independent enforcement of per-endpoint limits
- Benefit: No changes to existing `RateLimiter` class
- Benefit: Each limiter can be queried independently for stats

---

### Decision 3: URL Pattern Matching for Endpoint Routing

| Attribute | Value |
|-----------|-------|
| **Status** | Accepted |
| **Date** | 2026-03-18 |

**Context**: Need to route arbitrary URLs to correct rate limiter.

**Choice**: Use substring matching on URL paths with predefined patterns.

**Rationale**:
- Simple and fast (O(n) where n = number of patterns)
- No regex complexity
- Easy to add new endpoints
- Riot's API structure is stable and hierarchical

**Alternatives Rejected**:
1. Regex matching - Overkill for Riot's predictable URL structure
2. Full URL parsing - Unnecessary, path matching is sufficient
3. Configuration-driven patterns - Harder to maintain, patterns are stable

**Consequences**:
- Trade-off: New endpoints require code update (not config)
- Benefit: Fast and simple
- Benefit: Riot's API structure changes infrequently
- Benefit: Easy to debug (log shows matched pattern)

---

### Decision 4: Cache Only Match Details (Not Match IDs)

| Attribute | Value |
|-----------|-------|
| **Status** | Accepted |
| **Date** | 2026-03-18 |

**Context**: Caching can apply to multiple endpoints; need to decide scope.

**Choice**: Cache only `fetch_match_details()` responses, not `fetch_player_matches()` or ladder endpoints.

**Rationale**:
- Match details are the primary source of redundancy (high-elo players share matches)
- Match ID lists are small (100 items) and change frequently
- Ladder data changes too frequently for effective caching
- Match details are large payloads (~10KB each) where caching saves most

**Alternatives Rejected**:
1. Cache all endpoints - Ladder data expires too quickly, adds complexity
2. Configurable cache scope - YAGNI for MVP
3. Time-based expiration - Adds complexity without clear benefit

**Consequences**:
- Trade-off: Other endpoints still hit API for every request
- Benefit: 80% of benefit with 20% of complexity
- Benefit: Match IDs and ladder requests are relatively cheap

---

## File Manifest

| # | File | Action | Purpose | Dependencies |
|---|------|--------|---------|--------------|
| 1 | `collector/rate_limiter.py` | Modify | Add `EndpointAwareRateLimiter` and `LRUCache` classes | Existing `RateLimiter` |
| 2 | `collector/riot_api_collector.py` | Modify | Integrate new limiter and cache; add performance logging | #1 |
| 3 | `config/config.yaml` | Modify | Add `endpoint_rate_limits` and cache settings | #2 |
| 4 | `tests/test_collector.py` | Modify | Add tests for new classes and integration | #1, #2 |
| 5 | `tests/fixtures/minimal_config.yaml` | Create | Test fixture for backward compatibility | #3 |
| 6 | `collector/USAGE.md` | Modify | Document new features and configuration | #1, #2, #3 |

---

## Code Patterns

### Pattern 1: EndpointAwareRateLimiter

```python
"""
Add to collector/rate_limiter.py
"""

from typing import Dict, Optional
from collections import OrderedDict


class EndpointAwareRateLimiter:
    """
    Manages multiple rate limiters per API endpoint.

    Routes each request to the appropriate limiter based on URL pattern.
    """

    # URL pattern → limiter key mapping
    _PATTERNS: Dict[str, str] = {
        '/lol/match/v5/matches/': 'match_v5',
        '/lol/match/v5/matches/by-puuid/': 'match_v5',
        '/lol/league/v4/challengerleagues/': 'league_v4_elite',
        '/lol/league/v4/grandmasterleagues/': 'league_v4_elite',
        '/lol/league/v4/masterleagues/': 'league_v4_elite',
        '/lol/league/v4/entries/': 'league_v4_entries',
        '/lol/summoner/v4/': 'summoner_v4',
    }

    # Default rate limits (requests per second)
    _DEFAULT_LIMITS: Dict[str, int] = {
        'match_v5': 200,
        'league_v4_elite': 3,
        'league_v4_entries': 5,
        'summoner_v4': 27,
        'default': 20,
    }

    def __init__(self, limits: Optional[Dict[str, int]] = None):
        """
        Initialize per-endpoint rate limiters.

        Args:
            limits: Optional dict of limiter_key → requests_per_second.
                    Uses defaults if not provided.
        """
        limits = limits or self._DEFAULT_LIMITS

        self.limiters: Dict[str, RateLimiter] = {}
        for key, rate in limits.items():
            # Use default 2min limit (100) for all, could be configurable
            self.limiters[key] = RateLimiter(
                requests_per_second=rate,
                requests_per_2min=100
            )

    def _get_limiter_key(self, url: str) -> str:
        """
        Determine which limiter to use based on URL.

        Args:
            url: Full API URL

        Returns:
            Limiter key (e.g., 'match_v5', 'league_v4_elite', 'default')
        """
        for pattern, key in self._PATTERNS.items():
            if pattern in url:
                return key
        return 'default'

    async def acquire(self, url: str) -> None:
        """
        Acquire permission for the specific endpoint.

        Args:
            url: Full API URL to determine which limiter to use
        """
        limiter_key = self._get_limiter_key(url)
        await self.limiters[limiter_key].acquire()

    def get_stats(self) -> Dict[str, Tuple[int, int]]:
        """
        Get token availability for all limiters.

        Returns:
            Dict of limiter_key → (tokens_1s, tokens_2m)
        """
        return {
            key: limiter.get_stats()
            for key, limiter in self.limiters.items()
        }

    async def __aenter__(self):
        """Support async context manager with URL."""
        # Note: This requires URL to be set before use
        # Prefer explicit acquire(url) for clarity
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        """Support async context manager."""
        pass
```

### Pattern 2: LRUCache

```python
"""
Add to collector/rate_limiter.py
"""

class LRUCache:
    """
    LRU cache with async compatibility and statistics tracking.

    Uses OrderedDict for O(1) operations and automatic eviction.
    """

    def __init__(self, max_size: int = 10000):
        """
        Initialize LRU cache.

        Args:
            max_size: Maximum number of entries to cache
        """
        self.cache: OrderedDict = OrderedDict()
        self.max_size = max_size
        self.stats = {'hits': 0, 'misses': 0}

    def get(self, key: str) -> Optional[Any]:
        """
        Get value and move to end (most recently used).

        Args:
            key: Cache key

        Returns:
            Cached value or None if not found
        """
        if key in self.cache:
            self.stats['hits'] += 1
            # Move to end to mark as recently used
            self.cache.move_to_end(key)
            return self.cache[key]
        self.stats['misses'] += 1
        return None

    def set(self, key: str, value: Any) -> None:
        """
        Set value and evict oldest if over limit.

        Args:
            key: Cache key
            value: Value to cache
        """
        if key in self.cache:
            # Move to end if updating existing
            self.cache.move_to_end(key)
        self.cache[key] = value

        # Evict oldest if over limit
        if len(self.cache) > self.max_size:
            self.cache.popitem(last=False)

    def get_stats(self) -> Dict[str, Any]:
        """
        Get cache performance statistics.

        Returns:
            Dict with hits, misses, hit_rate, and size
        """
        total = self.stats['hits'] + self.stats['misses']
        hit_rate = self.stats['hits'] / total if total > 0 else 0

        return {
            'hits': self.stats['hits'],
            'misses': self.stats['misses'],
            'hit_rate': f"{hit_rate:.1%}",
            'size': len(self.cache),
            'max_size': self.max_size
        }

    def clear(self) -> None:
        """Clear all cached entries and reset stats."""
        self.cache.clear()
        self.stats = {'hits': 0, 'misses': 0}
```

### Pattern 3: Collector Integration

```python
"""
Modifications to collector/riot_api_collector.py
"""

from collector.rate_limiter import RateLimiter, EndpointAwareRateLimiter, LRUCache

class RiotAPICollector:
    def __init__(self, config_path: str = "config/config.yaml"):
        # ... existing config loading ...

        # NEW: Use EndpointAwareRateLimiter if configured
        if 'endpoint_rate_limits' in self.config.get('riot_api', {}):
            limits = self.config['riot_api']['endpoint_rate_limits']
            self.rate_limiter = EndpointAwareRateLimiter(limits=limits)
        else:
            # Backward compatible: use single RateLimiter
            self.rate_limiter = RateLimiter(
                requests_per_second=self.config["riot_api"]["rate_limit_per_second"],
                requests_per_2min=self.config["riot_api"]["rate_limit_per_2min"],
            )

        # NEW: Initialize cache if enabled
        perf_config = self.config.get('performance', {})
        if perf_config.get('enable_response_cache', False):
            self._match_cache = LRUCache(
                max_size=perf_config.get('cache_max_size', 10000)
            )
        else:
            self._match_cache = None

        # Track start time for performance logging
        self._start_time = None

    async def fetch_match_details(self, match_id: str) -> Optional[Dict]:
        """
        Fetch detailed match data with caching.

        Args:
            match_id: Match ID

        Returns:
            Match data dictionary or None
        """
        # NEW: Check cache first
        if self._match_cache:
            cached = self._match_cache.get(match_id)
            if cached is not None:
                return cached

        # Build URL
        routing = self.region_routing.get(self.region.lower(), "americas")
        url = f"https://{routing}.api.riotgames.com/lol/match/v5/matches/{match_id}"

        # Fetch from API
        match_data = await self._make_request(url)

        if match_data:
            # Add metadata
            match_data["_collected_at"] = datetime.now(UTC).isoformat()
            match_data["_region"] = self.region

            # NEW: Cache the response
            if self._match_cache:
                self._match_cache.set(match_id, match_data)

            return match_data

        return None

    async def run_collection(self) -> None:
        """Execute full three-step collection process with performance logging."""
        # Track start time
        self._start_time = time.monotonic()

        headers = {"X-Riot-Token": self.api_key}
        async with aiohttp.ClientSession(headers=headers) as session:
            self.session = session

            try:
                # ... existing collection steps ...

                # NEW: Log performance summary
                self._log_performance_summary()

            except Exception as e:
                self.logger.error(f"Collection failed: {e}", exc_info=True)
                raise

    def _log_performance_summary(self) -> None:
        """Log performance metrics at end of collection."""
        elapsed = time.monotonic() - self._start_time if self._start_time else 0

        self.logger.info("=" * 60)
        self.logger.info("Performance Summary")
        self.logger.info("=" * 60)

        # Cache stats
        if self._match_cache:
            stats = self._match_cache.get_stats()
            self.logger.info(
                f"Cache: {stats['hit_rate']} hit rate | "
                f"{stats['size']}/{stats['max_size']} entries | "
                f"{stats['hits']} requests saved"
            )

        # Rate limiter stats (only for EndpointAwareRateLimiter)
        if isinstance(self.rate_limiter, EndpointAwareRateLimiter):
            self.logger.info("Endpoint Token Availability:")
            for endpoint, (tokens_1s, tokens_2m) in self.rate_limiter.get_stats().items():
                self.logger.info(f"  {endpoint}: {tokens_1s} tokens/sec available")

        # Overall throughput
        if elapsed > 0:
            throughput = len(self.collected_matches) / elapsed
            self.logger.info(
                f"Total Time: {elapsed:.1f}s | "
                f"Matches: {len(self.collected_matches)} | "
                f"Throughput: {throughput:.2f} matches/sec"
            )
```

### Pattern 4: Configuration

```yaml
# Additions to config/config.yaml

riot_api:
  # Existing rate_limit_per_second and rate_limit_per_2min kept for backward compatibility
  rate_limit_per_second: 20
  rate_limit_per_2min: 100

  # NEW: Per-endpoint rate limits
  endpoint_rate_limits:
    match_v5: 200        # /lol/match/v5/* endpoints (2000/10s)
    league_v4_elite: 3    # challenger/grandmaster/master leagues (30/10s)
    league_v4_entries: 5  # league entries by tier/division (50/10s)
    summoner_v4: 27       # summoner lookup (1600/1m ≈ 27/s)
    default: 20           # fallback for unknown endpoints

performance:
  max_concurrent_requests: 15
  enable_batch_processing: true

  # NEW: Response cache settings
  enable_response_cache: true        # Enable/disable caching
  cache_max_size: 10000              # Max matches to cache
  report_cache_stats: true           # Log cache performance
```

---

## Testing Strategy

### Unit Tests

| Test | File | Purpose |
|------|------|---------|
| `test_endpoint_aware_rate_limiter_routing` | `tests/test_collector.py` | Verify URL → limiter routing |
| `test_endpoint_aware_rate_limiter_independent` | `tests/test_collector.py` | Verify limiters don't interfere |
| `test_lru_cache_eviction` | `tests/test_collector.py` | Verify LRU eviction behavior |
| `test_lru_cache_stats` | `tests/test_collector.py` | Verify hit/miss tracking |
| `test_config_defaults` | `tests/test_collector.py` | Verify default values when missing |

### Integration Tests

| Test | File | Purpose |
|------|------|---------|
| `test_collector_with_endpoint_limiter` | `tests/test_collector.py` | End-to-end with new limiter |
| `test_collector_with_cache` | `tests/test_collector.py` | Verify cache reduces API calls |
| `test_backward_compatibility` | `tests/test_collector.py` | Old config still works |

### Performance Tests

| Test | Method | Success Criteria |
|------|--------|------------------|
| Match throughput | Time 1000 match fetches | >=180 req/sec |
| Cache hit rate | Run full collection | >20% hit rate |
| Memory overhead | Monitor process memory | <300MB increase |

---

## Implementation Order

1. **Phase 1: Core Classes** (Low risk, high value)
   - Add `LRUCache` to `rate_limiter.py`
   - Add `EndpointAwareRateLimiter` to `rate_limiter.py`
   - Unit tests for both classes

2. **Phase 2: Collector Integration** (Medium risk)
   - Modify `RiotAPICollector.__init__()` to use new limiter
   - Add cache check to `fetch_match_details()`
   - Add performance logging to `run_collection()`

3. **Phase 3: Configuration** (Low risk)
   - Update `config/config.yaml` with new sections
   - Create test fixtures for backward compatibility

4. **Phase 4: Testing & Documentation** (Low risk)
   - Add integration tests
   - Update `collector/USAGE.md`
   - Update `CLAUDE.md` with new patterns

---

## Rollback Plan

If issues arise:

1. **Disable caching**: Set `enable_response_cache: false` in config
2. **Disable per-endpoint limiting**: Remove `endpoint_rate_limits` from config, falls back to single limiter
3. **Full rollback**: Revert `rate_limiter.py` and `riot_api_collector.py` to previous versions

All changes are backward compatible and feature-flagged via configuration.

---

## Next Step

Run `/build .claude/sdd/features/DESIGN_collector-performance.md` to execute implementation.
