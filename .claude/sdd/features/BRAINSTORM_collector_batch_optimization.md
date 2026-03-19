# BRAINSTORM: Collector Batch Optimization

**Date:** 2026-03-19
**Author:** Claude + User
**Status:** ✅ Approved
**Next Step:** `/define .claude/sdd/features/BRAINSTORM_collector_batch_optimization.md`

---

## Problem Statement

The Riot API collector's batch processing is ineffective:
- **Cache hit rate: 0%** (never attempts to fetch same match twice)
- **Snowball explosion**: Discovers 20,000+ new players but ignores them
- **Inefficient workflow**: Fetches details before discovering all available matches

### Root Cause Analysis

**Current Flow:**
```text
200 players → 10,000 matches (with details) → Hit limit
→ Discovers 20,000 new players → Cannot use them (limit reached)
```

**Problems:**
1. Match details are fetched BEFORE discovering all available match IDs
2. Hardcoded limit of 10,000 matches prevents snowball from continuing
3. Cache is never utilized (always fetching new matches)
4. Work wasted discovering players that cannot be used

### Evidence from Logs

```
STEP 1: 200 initial players
STEP 2: 10,000 matches collected (3+ hours)
STEP 3: 19,900-21,232 new players discovered
STEP 2: 10,000 matches (limit already reached - ignores new players)

Cache: 0.0% hit rate | 10000/10000 entries | 0 requests saved
```

---

## Proposed Solution: Two-Phase Collection

### Phase 1: Lightweight Match ID Collection

**Goal:** Discover ALL possible match IDs without fetching details

```python
async def phase1_collect_match_ids(
    initial_players: Set[str],
    max_iterations: int = 3,
    max_total_match_ids: int = 50000
) -> Set[str]:
    """
    Collect match IDs only (no details).

    Returns: Set of unique match IDs
    """
    all_match_ids = set()
    players_to_process = initial_players.copy()

    for iteration in range(max_iterations):
        # Fetch ONLY match IDs (fast)
        new_match_ids = await self._fetch_match_ids_batch(
            players_to_process,
            limit=50  # 50 players per iteration
        )

        # Aggressive deduplication
        unique_new = new_match_ids - all_match_ids
        all_match_ids.update(unique_new)

        # Early stop if reached total limit
        if len(all_match_ids) >= max_total_match_ids:
            break

        # Snowball: discover next batch of players
        players_to_process = await self._discover_players_from_ids(unique_new)

        if not players_to_process:
            break

    return all_match_ids
```

**Benefits:**
- Fast API calls (match IDs endpoint is lightweight)
- Discover complete pool of available matches
- No wasted work fetching details upfront

### Phase 2: Detail Collection with Effective Caching

**Goal:** Fetch details only for unique matches, high cache hit rate

```python
async def phase2_fetch_details(
    match_ids: Set[str],
    max_matches: int = 10000
) -> List[Dict]:
    """
    Fetch match details with effective caching.

    Returns: List of matches with details
    """
    # Sort by recency (most recent first)
    # Assuming match IDs contain timestamp
    sorted_ids = sorted(match_ids, reverse=True)[:max_matches]

    self.logger.info(f"Fetching details for {len(sorted_ids)} unique matches")

    # Batch processing with EFFECTIVE cache
    await self._fetch_matches_batch(sorted_ids, max_matches)

    return self.match_data
```

**Benefits:**
- Cache hit rate: 60-80% (aggressive deduplication in Phase 1)
- Only fetch details for matches we actually need
- Better use of rate limits

---

## Expected Impact

### Performance Comparison

| Metric | Current | Proposed | Improvement |
|--------|---------|----------|-------------|
| **Match IDs Discovered** | ~10,000 | ~50,000 | 5x coverage |
| **Cache Hit Rate** | 0% | 60-80% | Effective caching |
| **API Calls (Heavy)** | 10,000 | 10,000 | Same |
| **API Calls (Light)** | 200 | 15,000 | Trade-off for coverage |
| **Total Time** | 3-5 hours | 2-3 hours | 40% faster |
| **Snowball Utilization** | 0% | 100% | Uses discovered players |

### Work Distribution

**Before:**
```text
Heavy API calls: 10,000 (match details)
Light API calls: 200 (player lists)
Cache hits: 0
```

**After:**
```text
Heavy API calls: 10,000 (match details)
Light API calls: 15,000 (match ID lists)
Cache hits: 6,000-8,000 (from deduplication)
```

---

## Implementation Approach

### New Configuration

```yaml
# config/config.yaml
collection:
  # Phase 1: Match ID Discovery
  phase1_max_match_ids: 50000      # Total match IDs to discover
  phase1_max_iterations: 3          # Snowball iterations
  phase1_players_per_iteration: 50  # Players per iteration

  # Phase 2: Detail Collection
  phase2_max_matches: 10000         # Matches to fetch details for

  # Legacy (deprecated)
  # max_total_matches: 10000        # Replaced by phase2_max_matches
```

### New Methods

```python
# collector/riot_api_collector.py

async def _fetch_match_ids_batch(
    self,
    player_puuids: Set[str],
    count: int = 100
) -> Set[str]:
    """Fetch match IDs for multiple players (no details)."""
    all_ids = set()
    for puuid in player_puuids[:50]:  # Limit per batch
        match_ids = await self.fetch_player_matches(puuid, count)
        all_ids.update(match_ids)
    return all_ids

async def _discover_players_from_ids(
    self,
    match_ids: Set[str]
) -> Set[str]:
    """Extract unique players from match IDs (without fetching details)."""
    # This is tricky - we need SOME details to extract players
    # Options:
    # 1. Fetch minimal details (just participant PUUIDs)
    # 2. Use a different API endpoint
    # 3. Accept that we need to fetch some details
    pass
```

### Challenge: Player Discovery Without Details

**Problem:** How to discover new players from matches without fetching details?

**Options:**

| Option | Approach | Pros | Cons |
|--------|----------|------|------|
| **A. Minimal Fetch** | Fetch only participant PUUIDs | Less data transfer | Still requires API call |
| **B. Bulk Fetch** | Fetch all details, cache for Phase 2 | One pass, effective cache | More memory upfront |
| **C. Hybrid** | Fetch details for subset, use for discovery | Balance speed/coverage | Complex logic |

**Recommendation:** Option B (Bulk Fetch)
- Fetch all match details in Phase 1
- Cache them for Phase 2
- Eliminates separate Phase 2 API calls
- **Actually, this reveals a flaw in the design...**

---

## Design Revision: Hybrid Approach

### Revised Two-Phase Strategy

**Phase 1: Aggressive Discovery with Caching**
```python
async def phase1_discover_and_cache(
    initial_players: Set[str],
    max_total_match_ids: int = 50000,
    max_iterations: int = 3
) -> int:
    """
    Discover match IDs and cache details progressively.

    Returns: Number of unique matches cached
    """
    all_match_ids = set()
    players_to_process = initial_players.copy()

    for iteration in range(max_iterations):
        # Fetch match IDs
        new_match_ids = await self._fetch_match_ids_batch(players_to_process)
        unique_new = new_match_ids - all_match_ids
        all_match_ids.update(unique_new)

        # Fetch details IMMEDIATELY (with caching)
        # But don't add to collected_matches yet
        for match_id in unique_new:
            match_data = await self.fetch_match_details(match_id)
            # Data is cached, but we don't count it yet

        # Discover next players from cached data
        new_players = await self._extract_players_from_cached_matches(unique_new)
        players_to_process = new_players

        if len(all_match_ids) >= max_total_match_ids:
            break

    return len(all_match_ids)
```

**Phase 2: Select and Commit**
```python
async def phase2_select_and_commit(
    max_matches: int = 10000
) -> List[Dict]:
    """
    Select best matches from cache and commit to collection.

    Returns: List of committed matches
    """
    # Get all cached match IDs
    cached_ids = list(self._match_cache._cache.keys())

    # Select best matches (most recent, high quality, etc.)
    selected_ids = self._select_best_matches(cached_ids, max_matches)

    # Commit to collected_matches
    for match_id in selected_ids:
        match_data = self._match_cache.get(match_id)
        self.match_data.append(match_data)
        self.collected_matches.add(match_id)

    return self.match_data
```

**This approach:**
- ✅ Leverages cache effectively (60-80% hit rate expected)
- ✅ Discovers all available matches before committing
- ✅ Single pass through API (no redundant calls)
- ✅ Can select best matches from larger pool

---

## YAGNI Applied (Features Removed for MVP)

| Feature | Status | Reason |
|---------|--------|--------|
| Progress bars per iteration | ❌ Removed | Single progress bar sufficient |
| Disk-based match ID cache | ❌ Removed | In-memory set is sufficient |
| Time-based early stop | ❌ Removed | Not critical for MVP |
| Per-iteration statistics | ✅ Kept | Useful for debugging |
| 50-player limit per iteration | ✅ Kept | Prevents explosion |
| Configurable iteration limit | ✅ Kept | User requested |

---

## Sample Data Analysis

**From:** `logs/collector.log` (2026-03-18 execution)

**Observations:**
- 200 initial players → 5,515 matches collected
- Snowball: 14,083 new players discovered
- Second iteration: 9,986 matches collected
- Snowball: 21,232 new players discovered
- Cache: 0% hit rate (never reused)

**Key Insight:**
The collector is working correctly but inefficiently. Each iteration discovers NEW players with DIFFERENT matches, so cache never hits.

**Proposed Flow (based on evidence):**
```
Initial: 200 players → ~5,000 matches
Cache them all (not counted yet)
Discover 15,000 new players from those 5,000 matches

Iteration 2: 50 players (selected from 15,000) → ~5,000 NEW matches
Cache them all (now have 10,000 cached)
Discover 15,000 more players

Iteration 3: 50 players → ~5,000 NEW matches
Cache them all (now have 15,000 cached)

Final: Select best 10,000 from 15,000 cached
Cache hit rate: ~33% (5,000/15,000 were cached during discovery)
```

---

## Open Questions

1. **Match ID Sorting:** How to sort matches by recency without fetching details?
   - **Answer:** Riot match IDs contain timestamps (encoded as base64)

2. **Player Discovery:** Can we discover players without fetching full match details?
   - **Answer:** No, Riot API doesn't provide this. We need fetch details.

3. **Memory Usage:** Caching 50,000 matches in memory?
   - **Answer:** ~50MB (assuming 1KB per match). Acceptable.

4. **Rate Limits:** Will fetching 15,000 light calls + 10,000 heavy calls hit limits?
   - **Answer:** 15,000 light calls = ~12 minutes at 20/sec. Fine.

---

## Decision Record

### Approach Selection

**Chosen:** Hybrid Two-Phase with Progressive Caching

**Rationale:**
- Leverages cache effectively (unlike current)
- Discovers maximum coverage (unlike current)
- Single API pass (no redundant calls)
- Selects best matches from larger pool

**Rejected Alternatives:**
- Pure two-phase (can't discover players without details)
- Increase limit to 50,000 (too many details to fetch)
- Reduce iterations (less coverage)

---

## Success Criteria

- [ ] Cache hit rate > 30% (vs. 0% currently)
- [ ] Discover 30,000+ unique match IDs (vs. 10,000 currently)
- [ ] Commit 10,000 best matches (same as current)
- [ ] Total time < 3 hours (vs. 3-5 hours currently)
- [ ] No breaking changes to public API

---

## Next Steps

1. ✅ **Create BRAINSTORM document** (this file)
2. ⏭️ **Run `/define`** to capture formal requirements
3. ⏭️ **Run `/design`** to create technical specification
4. ⏭️ **Run `/build`** to implement the solution

---

## Related

- Issue: Cache hit rate 0% in collector
- CLAUDE.md: Collector architecture guidelines
- config.yaml: Collection settings
- logs/collector.log: Evidence of problem
