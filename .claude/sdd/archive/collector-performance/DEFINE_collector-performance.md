# DEFINE: Collector Performance Optimization

**Date**: 2026-03-18
**Status**: ✅ Shipped
**Source**: BRAINSTORM_collector-performance.md
**Clarity Score**: 14/15

**Shipped**: 2026-03-18 - Implementation complete, all tests passing

---

## Problem Statement

The current Riot API collector uses a single global rate limiter (20 req/sec) for all endpoints. This causes two issues:

1. **Underutilization**: Match-v5 endpoints can handle 200 req/sec but are throttled to 20 req/sec (10x waste)
2. **429 Errors**: League-v4 endpoints have stricter limits (3-5 req/sec) and hit rate limits when sharing the 20 req/sec bucket

**Impact**: 50k matches take ~90 minutes to collect, with intermittent 429 errors causing retries and delays.

---

## Users

| User | Description | Pain Points |
|------|-------------|-------------|
| Data Scientist | Runs collector to gather match data for ML model training | Collections take too long; 429 errors interrupt runs |
| ML Engineer | Maintains data pipeline and monitors collection health | Difficult to troubleshoot rate limit issues; no visibility into per-endpoint performance |

---

## Goals

| Goal | Description | Priority |
|------|-------------|----------|
| Scale throughput | Increase match fetch rate from 20 to 200 req/sec | P0 |
| Eliminate 429 errors | Respect per-endpoint rate limits to prevent rate limiting | P0 |
| Reduce API calls | Implement caching to eliminate 20-40% redundant requests | P1 |
| Maintain compatibility | All changes backward compatible with existing config | P1 |

---

## Success Criteria

| Metric | Before | After | Measurement Method |
|--------|--------|-------|-------------------|
| Match fetch throughput | ~20 req/sec | ~200 req/sec | Log rate during collection |
| Time for 10k matches | ~18 min | ~2 min | End-to-end timing |
| League ladder 429s | Occasional | 0 | Error log count |
| Cache hit rate | N/A | >20% | Cache statistics log |
| Time for 50k matches | ~90 min | ~10 min | End-to-end timing |
| Memory overhead | ~50MB | <250MB | Process monitoring |

---

## Functional Requirements

### FR-1: Per-Endpoint Rate Limiting

The collector SHALL implement per-endpoint rate limiting based on documented API limits:

| Endpoint Key | URL Pattern | Rate Limit |
|--------------|-------------|------------|
| `match_v5` | `/lol/match/v5/*` | 200 req/sec |
| `league_v4_elite` | `/lol/league/v4/challengerleagues/*`, `/grandmasterleagues/*`, `/masterleagues/*` | 3 req/sec |
| `league_v4_entries` | `/lol/league/v4/entries/*` | 5 req/sec |
| `summoner_v4` | `/lol/summoner/v4/*` | 27 req/sec |
| `default` | (fallback) | 20 req/sec |

**Acceptance Criteria**:
- [ ] `EndpointAwareRateLimiter` class routes URLs to correct limiter
- [ ] Each limiter operates independently (no cross-contamination)
- [ ] Unknown endpoints fall back to `default` limiter
- [ ] Limits are configurable via `config.yaml`

### FR-2: Response Caching

The collector SHALL cache match data to eliminate redundant API calls.

**Acceptance Criteria**:
- [ ] `LRUCache` class implemented with OrderedDict
- [ ] Cache automatically evicts oldest entries when `max_size` reached
- [ ] Cache tracks hits, misses, and calculates hit rate
- [ ] Cache is checked before API call in `fetch_match_details()`
- [ ] Cache can be disabled via config (`enable_response_cache: false`)

### FR-3: Configuration

All new behaviors SHALL be configurable via `config.yaml`.

**Acceptance Criteria**:
- [ ] `riot_api.endpoint_rate_limits` section exists with all 5 endpoint limits
- [ ] `performance.enable_response_cache` enables/disables caching
- [ ] `performance.cache_max_size` controls max cached matches (default: 10000)
- [ ] `performance.report_cache_stats` enables cache statistics logging
- [ ] Missing config values use sensible defaults

### FR-4: Observability

The collector SHALL report performance metrics at end of collection.

**Acceptance Criteria**:
- [ ] Cache hit rate logged as percentage
- [ ] Cache size and requests saved logged
- [ ] Per-endpoint token availability logged
- [ ] Overall throughput (matches/sec) calculated and logged

---

## Non-Functional Requirements

### NFR-1: Performance

- Match fetching throughput SHALL reach >=180 req/sec (90% of target 200)
- Cache lookup time SHALL be <1ms per operation
- Rate limiter overhead SHALL be <5ms per acquire()

### NFR-2: Memory

- Cache memory usage SHALL be <250MB for 10k matches
- Total process memory increase SHALL be <300MB

### NFR-3: Backward Compatibility

- Existing `config.yaml` without new sections SHALL use sensible defaults
- Disabling optimizations (`enable_response_cache: false`) SHALL fall back to current behavior
- No breaking changes to `RiotAPICollector` public API

### NFR-4: Reliability

- Rate limiter SHALL never exceed configured limits (even under concurrent load)
- Cache SHALL handle concurrent access safely (async-compatible)
- 429 errors on league-v4 endpoints SHALL be eliminated

---

## Constraints

| Constraint | Description |
|------------|-------------|
| API Rate Limits | Must respect per-endpoint limits documented in `api_rate_limits.md` |
| Existing Architecture | Must integrate with current batch processing (`asyncio.Semaphore`) |
| Python Version | Python 3.14+ with async/await |
| Dependencies | Use only existing dependencies (no new packages required) |
| Memory | Target environment has <1GB available for collector process |

---

## Out of Scope

The following features are explicitly excluded from MVP:

| Feature | Reason |
|---------|--------|
| Cache persistence to disk | Can add later if checkpoint/resume needed |
| Multi-region parallel collection | Out of scope for current scale (<50k matches) |
| Adaptive rate limiting | Over-engineering; documented limits are stable |
| Distributed caching (Redis) | Single-machine deployment |
| Per-endpoint request queuing | Not needed with token bucket approach |

---

## Dependencies

| Dependency | Type | Description |
|------------|------|-------------|
| `collector/rate_limiter.py` | Internal | Existing `RateLimiter` class used by new `EndpointAwareRateLimiter` |
| `collector/riot_api_collector.py` | Internal | Main collector to be modified |
| `config/config.yaml` | Config | Configuration file to be extended |
| `api_rate_limits.md` | Documentation | Source of truth for per-endpoint limits |

---

## Acceptance Tests

### Test-1: Endpoint Rate Limiter Routes Correctly

```python
def test_endpoint_aware_rate_limiter_routing():
    limiter = EndpointAwareRateLimiter(limits={'match_v5': 200, 'league_v4_elite': 3})

    # Match endpoint should use match_v5 limiter
    assert limiter._get_limiter_key("https://americas.api.riotgames.com/lol/match/v5/matches/xyz") == 'match_v5'

    # Challenger endpoint should use league_v4_elite limiter
    assert limiter._get_limiter_key("https://br1.api.riotgames.com/lol/league/v4/challengerleagues/by-queue/RANKED_SOLO_5x5") == 'league_v4_elite'
```

### Test-2: LRU Cache Eviction

```python
def test_lru_cache_eviction():
    cache = LRUCache(max_size=3)

    cache.set('key1', 'value1')
    cache.set('key2', 'value2')
    cache.set('key3', 'value3')
    assert len(cache.cache) == 3

    # Adding 4th item should evict oldest (key1)
    cache.set('key4', 'value4')
    assert len(cache.cache) == 3
    assert 'key1' not in cache.cache
    assert 'key4' in cache.cache
```

### Test-3: Cache Hit Rate Tracking

```python
def test_cache_hit_rate_tracking():
    cache = LRUCache(max_size=100)

    cache.set('key1', 'value1')
    cache.get('key1')  # hit
    cache.get('key2')  # miss

    stats = cache.get_stats()
    assert stats['hits'] == 1
    assert stats['misses'] == 1
    assert stats['hit_rate'] == '50.0%'
```

### Test-4: Configuration Defaults

```python
def test_config_defaults_when_missing():
    # Config without new sections should use defaults
    collector = RiotAPICollector(config_path="tests/fixtures/minimal_config.yaml")

    assert collector.rate_limiter.limiters['match_v5'].requests_per_second == 200
    assert collector.rate_limiter.limiters['league_v4_elite'].requests_per_second == 3
```

### Test-5: Backward Compatibility

```python
def test_backward_compatibility_when_cache_disabled():
    # Disable cache in config
    collector = RiotAPICollector(config_path="tests/config/no_cache.yaml")

    assert collector._match_cache is None
    # Should still function normally
    match_data = await collector.fetch_match_details('match_id')
```

---

## Definition of Done

- [ ] All Functional Requirements implemented
- [ ] All Acceptance Tests passing
- [ ] Performance targets met (>=180 req/sec match throughput)
- [ ] 429 errors eliminated on league-v4 endpoints
- [ ] Cache hit rate >20% in production run
- [ ] Memory overhead <300MB
- [ ] Documentation updated (collector/USAGE.md)
- [ ] CLAUDE.md updated with new patterns
- [ ] Backward compatibility verified

---

## Next Step

Run `/design .claude/sdd/features/DEFINE_collector-performance.md` to create technical specification.
