# DEFINE: Collector Batch Optimization

**Date:** 2026-03-19
**Status:** ✅ Ready for Design
**Source:** BRAINSTORM_collector_batch_optimization.md
**Clarity Score:** 15/15

---

## Problem Statement

The Riot API collector's batch processing is **ineffective with 0% cache hit rate**, wasting computational resources and time:

### Current Issues

1. **Cache Inefficiency**: 0% cache hit rate because the collector never attempts to fetch the same match twice
2. **Wasted Discovery**: Discovers 20,000+ new players via snowball but cannot use them due to hardcoded 10,000 match limit
3. **Sequential Workflow**: Fetches match details BEFORE discovering all available match IDs, preventing effective deduplication
4. **Time Inefficiency**: Takes 3-5 hours to collect 10,000 matches with significant redundant work

### Root Cause

The current workflow fetches detailed match data immediately upon discovery:

```
200 players → 10,000 matches (with details) → Hit limit
→ Discovers 20,000 new players → Limit reached, work discarded
```

Each iteration discovers **completely different matches** (low overlap in high-tier ranked play), so the LRU cache never has hits.

### Evidence

**From `logs/collector.log` (2026-03-18):**
```
STEP 1: 200 initial players
STEP 2: 10,000 matches collected (3+ hours)
STEP 3: 19,900-21,232 new players discovered (cannot use them)
STEP 2: 10,000 matches (limit already reached)

Cache: 0.0% hit rate | 10000/10000 entries | 0 requests saved
```

---

## Users & Personas

### Primary User: Data Scientist / ML Engineer

**Pain Points:**
- Wastes 3-5 hours per collection waiting for match data
- Frustrated seeing "20,000 players discovered" but not used
- Needs high-quality recent match data for model training
- Limited by Riot API rate limits (production key: 20 req/sec, 100 req/2min)

**Goals:**
- Collect maximum number of unique high-quality matches per session
- Minimize collection time while respecting rate limits
- Leverage caching to reduce redundant API calls
- Maintain temporal correctness for feature engineering

### Secondary User: System Maintainer

**Pain Points:**
- Hardcoded limits prevent experimentation with collection strategies
- No visibility into cache effectiveness
- Difficult to tune collection parameters

**Goals:**
- Configurable collection strategy via YAML
- Clear performance metrics and logging
- Maintainable, testable code

---

## Goals & Objectives

### Primary Goals

1. **Improve Cache Effectiveness**: Achieve **>30% cache hit rate** (vs. 0% currently)
2. **Increase Match Discovery**: Discover **30,000+ unique match IDs** (vs. 10,000 currently)
3. **Reduce Collection Time**: Complete collection in **<3 hours** (vs. 3-5 hours currently)
4. **Maintain Output Quality**: Commit **10,000 highest-quality matches** (same as current)

### Secondary Goals

1. **Utilize Snowball Discovery**: Use 100% of discovered players (vs. 0% currently)
2. **Configurable Strategy**: Allow tuning of discovery vs. selection trade-offs
3. **Better Observability**: Log detailed metrics per phase (IDs discovered, cache hits, time spent)

---

## Success Criteria

### Functional Requirements

| ID | Requirement | Acceptance Test | Priority |
|----|-------------|-----------------|----------|
| **FR-1** | Discover match IDs in Phase 1 | Collect 30,000+ unique match IDs before selection | Must |
| **FR-2** | Select best matches in Phase 2 | Commit 10,000 most recent matches to Bronze layer | Must |
| **FR-3** | Achieve effective caching | Cache hit rate > 30% (measured in performance summary) | Must |
| **FR-4** | Complete collection in <3 hours | Total time from start to save < 3 hours for 10,000 matches | Should |
| **FR-5** | No breaking API changes | Existing `run_collection()` method works without modification | Must |
| **FR-6** | Configurable limits | Phase 1 and Phase 2 limits configurable via config.yaml | Should |

### Non-Functional Requirements

| ID | Requirement | Acceptance Test | Priority |
|----|-------------|-----------------|----------|
| **NFR-1** | Respect rate limits | No 429 errors with production API key (20 req/sec) | Must |
| **NFR-2** | Memory efficiency | Cache 50,000 matches using <100MB RAM | Should |
| **NFR-3** | Backward compatibility | Existing config.yaml works without modification | Must |
| **NFR-4** | Test coverage | Unit tests for new methods, integration test for flow | Should |
| **NFR-5** | Logging clarity | Performance summary shows per-phase metrics | Should |

---

## Solution Overview

### Two-Phase Collection Strategy

**Phase 1: Aggressive Discovery with Progressive Caching**
- Discover match IDs through multiple snowball iterations
- Fetch match details immediately but **don't commit** to collection
- Cache all fetched details (LRU cache with 50,000 capacity)
- Limit: 50,000 total match IDs discovered (configurable)

**Phase 2: Select and Commit**
- Select best 10,000 matches from cached pool
- Prioritize by recency (match IDs encode timestamps)
- Commit selected matches to Bronze layer
- Benefit: High cache hit rate during selection (duplicates filtered in Phase 1)

### Workflow Comparison

**Current Flow:**
```
200 players → 10,000 matches (committed)
→ Discover 20,000 players → Cannot use (limit reached)

Time: 3-5 hours
Cache: 0% hit rate
Matches: 10,000 committed
```

**Proposed Flow:**
```
200 players → 5,000 matches (cached)
→ Discover 15,000 players → Select 50 → 5,000 more matches (cached)
→ Discover 15,000 players → Select 50 → 5,000 more matches (cached)
Total cached: 15,000 matches

Phase 2: Select best 10,000 from 15,000 cached

Time: 2-3 hours
Cache: 33% hit rate (5,000/15,000)
Matches: 10,000 committed (highest quality)
```

---

## Configuration

### New Configuration Parameters

```yaml
# config/config.yaml
collection:
  # Phase 1: Discovery
  phase1_max_match_ids: 50000          # Total match IDs to discover
  phase1_max_iterations: 3              # Snowball iterations
  phase1_players_per_iteration: 50      # Players to process per iteration

  # Phase 2: Selection
  phase2_max_matches: 10000             # Matches to commit to Bronze

  # Legacy (deprecated, backward compatible)
  max_total_matches: 10000              # Mapped to phase2_max_matches
  max_iterations: 5                     # Mapped to phase1_max_iterations
```

### Default Values

| Parameter | Default | Range | Description |
|-----------|---------|-------|-------------|
| `phase1_max_match_ids` | 50,000 | 10,000-100,000 | Total match IDs to discover in Phase 1 |
| `phase1_max_iterations` | 3 | 1-10 | Snowball iterations in Phase 1 |
| `phase1_players_per_iteration` | 50 | 10-100 | Players to process per iteration |
| `phase2_max_matches` | 10,000 | 1,000-50,000 | Matches to commit in Phase 2 |

---

## Architecture & Integration

### New Methods

```python
# collector/riot_api_collector.py

async def phase1_discover_and_cache(
    self,
    initial_players: Set[str],
    max_total_match_ids: int,
    max_iterations: int,
    players_per_iteration: int
) -> int:
    """
    Discover match IDs through snowball iterations.
    Fetch and cache details progressively (not committed).

    Returns: Number of unique matches cached
    """

async def phase2_select_and_commit(
    self,
    max_matches: int
) -> List[Dict]:
    """
    Select best matches from cache and commit to collection.
    Prioritizes by recency (match ID timestamp).

    Returns: List of committed matches
    """

def _select_best_matches(
    self,
    cached_ids: List[str],
    limit: int
) -> List[str]:
    """
    Sort match IDs by recency and select top N.
    Match IDs encode timestamps as base64.

    Returns: List of selected match IDs
    """

def _extract_players_from_cached_matches(
    self,
    match_ids: Set[str]
) -> Set[str]:
    """
    Extract unique player PUUIDs from cached match data.

    Returns: Set of player PUUIDs
    """
```

### Modified Methods

```python
# Existing method, updated to use two-phase flow
async def run_collection(self) -> None:
    """
    Execute two-phase collection process:
    1. Discover and cache match IDs
    2. Select and commit best matches
    """
```

---

## Data Flow

### Phase 1: Discovery Flow

```
┌─────────────────────────────────────────────────────────────┐
│                    PHASE 1: DISCOVERY                        │
├─────────────────────────────────────────────────────────────┤
│                                                              │
│  Initial Players (200)                                       │
│       ↓                                                      │
│  ┌─────────────────────────────────────────┐                │
│  │ Iteration 1 (50 players)                │                │
│  │  • Fetch match IDs (~5,000)             │                │
│  │  • Fetch details (cached, not committed)│                │
│  │  • Extract players (~15,000 new)        │                │
│  └─────────────────────────────────────────┘                │
│       ↓                                                      │
│  ┌─────────────────────────────────────────┐                │
│  │ Iteration 2 (50 players from pool)      │                │
│  │  • Fetch match IDs (~5,000 NEW)         │                │
│  │  • Fetch details (cached, not committed)│                │
│  │  • Extract players (~15,000 more)       │                │
│  └─────────────────────────────────────────┘                │
│       ↓                                                      │
│  Repeat until:                                               │
│  • Reached phase1_max_match_ids (50,000) OR                  │
│  • Completed phase1_max_iterations (3) OR                    │
│  • No new players discovered                                 │
│                                                              │
│  Output: 15,000 matches in cache                             │
└─────────────────────────────────────────────────────────────┘
```

### Phase 2: Selection Flow

```
┌─────────────────────────────────────────────────────────────┐
│                   PHASE 2: SELECTION                         │
├─────────────────────────────────────────────────────────────┤
│                                                              │
│  Cached Match IDs (15,000)                                   │
│       ↓                                                      │
│  Sort by Recency (match ID timestamp)                        │
│       ↓                                                      │
│  Select Top N (10,000)                                       │
│       ↓                                                      │
│  Commit to Collection:                                       │
│  • self.match_data.append(match_data)                        │
│  • self.collected_matches.add(match_id)                      │
│       ↓                                                      │
│  Save to Bronze Layer                                        │
│                                                              │
│  Output: 10,000 committed matches                             │
└─────────────────────────────────────────────────────────────┘
```

---

## Out of Scope

### Explicitly Excluded (YAGNI)

1. **Progress Bars Per Iteration**: Single progress bar for each phase is sufficient
2. **Disk-Based Match ID Cache**: In-memory set is sufficient for 50,000 IDs (~5MB)
3. **Time-Based Early Stop**: Not critical for MVP, iteration-based stop is sufficient
4. **Advanced Match Selection**: Simple recency-based selection for MVP (future: quality scoring)
5. **Parallel Phase 1 Iterations**: Sequential iterations prevent overwhelming rate limiter

### Deferred to Future Releases

1. **Quality-Based Match Selection**: Prioritize by MMR, patch relevance, champion diversity
2. **Adaptive Iteration Limits**: Auto-tune based on overlap rate
3. **Incremental Bronze Writes**: Stream matches to disk instead of holding in memory
4. **Multi-Region Collection**: Parallel collection across regions

---

## Dependencies & Constraints

### Dependencies

| Component | Version | Purpose |
|-----------|---------|---------|
| `collector.rate_limiter` | Existing | Respect Riot API rate limits |
| `collector.rate_limiter.LRUCache` | Existing | Cache match details (50K capacity) |
| `config/config.yaml` | Existing | Configuration parameters |
| `aiohttp` | Existing | Async HTTP client |
| `pandas` | Existing | Parquet export |

### Constraints

1. **Rate Limits**: Must respect Riot API limits (20 req/sec, 100 req/2min per production key)
2. **Memory**: Cache size limited to available RAM (target: <100MB for 50,000 matches)
3. **Backward Compatibility**: Existing config.yaml must work without modification
4. **No Breaking Changes**: Public API methods must maintain same signatures

### Assumptions

1. **Match IDs Encode Timestamps**: Riot match v5 IDs contain timestamps for sorting
2. **Cache Persistence**: LRU cache can hold 50,000 matches without eviction
3. **Network Stability**: No extended outages during collection (3-hour window)
4. **API Key Validity**: Production API key remains valid for entire collection

---

## Risks & Mitigation

| Risk | Probability | Impact | Mitigation |
|------|-------------|--------|------------|
| **Match exceeds cache capacity** | Low (20K vs 50K limit) | Medium | Monitor cache size, log warnings if approaching limit |
| **Rate limit still hit** | Low (calculated loads) | High | Add configurable delay between iterations |
| **Match ID timestamp assumption wrong** | Low (documented behavior) | High | Fall back to random selection if decoding fails |
| **Memory exceeds 100MB** | Low (1KB/match estimate) | Medium | Add memory monitoring, reduce cache size if needed |
| **Backward compatibility broken** | Medium | High | Add migration logic, support legacy config keys |

---

## Testing Strategy

### Unit Tests

```python
# tests/test_collector_phases.py

def test_phase1_discover_and_cache():
    """Test Phase 1 discovers and caches correct number of matches"""

def test_phase1_stops_at_max_match_ids():
    """Test Phase 1 stops when reaching phase1_max_match_ids"""

def test_phase1_stops_at_max_iterations():
    """Test Phase 1 stops after phase1_max_iterations"""

def test_phase1_extract_players_from_cache():
    """Test player extraction from cached matches"""

def test_phase2_select_and_commit():
    """Test Phase 2 commits correct number of matches"""

def test_phase2_sorts_by_recency():
    """Test Phase 2 prioritizes recent matches"""

def test_select_best_matches_handles_limit():
    """Test selection when cached > limit"""

def test_integration_two_phase_flow():
    """Test complete two-phase collection flow"""
```

### Integration Tests

```python
# tests/test_collector_integration.py

async def test_full_two_phase_collection():
    """Test complete collection with mock API"""

async def test_cache_effectiveness():
    """Verify cache hit rate > 30% under realistic conditions"""

async def test_no_rate_limit_violations():
    """Verify rate limiter not exceeded during collection"""

async def test_backward_compatible_config():
    """Test old config.yaml still works"""
```

### Performance Tests

```python
# tests/test_collector_performance.py

async def test_collection_time_under_3_hours():
    """Test 10,000 matches collected in <3 hours"""

async def test_memory_usage_under_100mb():
    """Test cache uses <100MB for 50,000 matches"""

async def test_cache_hit_rate_above_30_percent():
    """Test cache effectiveness threshold"""
```

---

## Acceptance Tests

### Scenario 1: Successful Two-Phase Collection

```gherkin
Given: Valid Riot API key and config.yaml
When: Collector runs two-phase collection
Then:
  • Phase 1 discovers 30,000+ unique match IDs
  • Phase 1 caches all discovered match details
  • Phase 2 selects 10,000 most recent matches
  • All 10,000 matches saved to Bronze layer
  • Performance summary shows cache hit rate > 30%
  • Total time < 3 hours
```

### Scenario 2: Early Stop at Match ID Limit

```gherkin
Given: phase1_max_match_ids = 50,000
When: Discovery reaches 50,000 match IDs in 2 iterations
Then:
  • Phase 1 stops (does not start 3rd iteration)
  • Proceeds to Phase 2 with 50,000 cached matches
  • Phase 2 selects 10,000 best matches
```

### Scenario 3: Early Stop at Iteration Limit

```gherkin
Given: phase1_max_iterations = 3
When: Discovery completes 3 iterations with 15,000 match IDs
Then:
  • Phase 1 stops after 3rd iteration
  • Proceeds to Phase 2 with 15,000 cached matches
  • Phase 2 commits 10,000 best matches
```

### Scenario 4: No New Players Discovered

```gherkin
Given: Phase 1 iteration discovers 0 new players
When: No new players available
Then:
  • Phase 1 stops early
  • Proceeds to Phase 2 with current cached matches
  • Logs "No new players found, stopping discovery"
```

### Scenario 5: Backward Compatibility

```gherkin
Given: Old config.yaml with max_total_matches and max_iterations
When: Collector initialized with legacy config
Then:
  • Maps max_total_matches → phase2_max_matches
  • Maps max_iterations → phase1_max_iterations
  • Collection completes without errors
  • Logs "Using legacy configuration keys"
```

---

## Open Questions

### Resolved

| Question | Answer | Source |
|----------|--------|--------|
| How to sort matches by recency without fetching details? | Riot match IDs encode timestamps (base64) | Riot API documentation |
| Can we discover players without fetching details? | No, must fetch details to extract participants | API constraint |
| Memory usage for 50,000 cached matches? | ~50MB (1KB per match estimate) | Calculated |
| Will rate limits be exceeded? | No: 15,000 light calls = ~12.5 min at 20/sec | Calculated |

### To Be Validated

| Question | Validation Method |
|----------|-------------------|
| Actual match ID timestamp encoding? | Implement and test with real data |
| Real-world cache hit rate? | Measure in integration test |
| Actual memory per match? | Profile with real match data |
| Optimal iteration limit? | Tune based on overlap rate |

---

## Success Metrics

### Primary Metrics

| Metric | Current | Target | Measurement Method |
|--------|---------|--------|---------------------|
| **Cache Hit Rate** | 0% | >30% | Performance summary logging |
| **Match IDs Discovered** | ~10,000 | >30,000 | Phase 1 completion log |
| **Collection Time** | 3-5 hours | <3 hours | Start/end timestamps |
| **Snowball Utilization** | 0% | 100% | Players discovered vs. used |

### Secondary Metrics

| Metric | Target | Measurement Method |
|--------|--------|---------------------|
| API calls (heavy) | 10,000 | Request counter |
| API calls (light) | ~15,000 | Request counter |
| Memory usage | <100MB | Memory profiler |
| Rate limit errors | 0 | Error logs |

---

## Next Steps

1. ✅ **DEFINE document created** (this file)
2. ⏭️ **Run `/design`** to create technical specification
3. ⏭️ **Run `/build`** to implement the solution
4. ⏭️ **Run tests** to validate acceptance criteria
5. ⏭️ **Deploy and monitor** first production run

---

## Related Artifacts

- **BRAINSTORM**: `.claude/sdd/features/BRAINSTORM_collector_batch_optimization.md`
- **Evidence**: `logs/collector.log` (2026-03-18 execution)
- **Configuration**: `config/config.yaml`
- **Implementation**: `collector/riot_api_collector.py`

---

## Change History

| Date | Version | Changes |
|------|---------|---------|
| 2026-03-19 | 1.0 | Initial DEFINE from BRAINSTORM |
