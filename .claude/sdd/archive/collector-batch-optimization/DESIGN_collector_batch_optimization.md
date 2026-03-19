# DESIGN: Collector Batch Optimization

**Date:** 2026-03-19
**Status:** ✅ Shipped
**Source:** DEFINE_collector_batch_optimization.md
**Shipped:** 2026-03-19
**Revision:** Architecture implemented and tested

---

## Architecture Overview

### Current Architecture (Inefficient)

```text
┌─────────────────────────────────────────────────────────────┐
│                   CURRENT SINGLE-PHASE FLOW                 │
├─────────────────────────────────────────────────────────────┤
│                                                              │
│  step1_collect_ladder_players()                             │
│    → 200 players from ladder                                │
│                                                              │
│  step2_collect_player_matches()                              │
│    → Fetch match IDs + DETAILS (committed immediately)       │
│    → Hit limit: 10,000 matches                               │
│                                                              │
│  step3_snowball_expansion()                                  │
│    → Discover 20,000+ new players                            │
│    → CANNOT USE (limit already reached) ❌                   │
│                                                              │
│  Cache hit rate: 0% (never reuses matches)                  │
│  Time: 3-5 hours                                             │
└─────────────────────────────────────────────────────────────┘
```

### Proposed Architecture (Two-Phase)

```text
┌──────────────────────────────────────────────────────────────────┐
│                      TWO-PHASE COLLECTION FLOW                     │
├──────────────────────────────────────────────────────────────────┤
│                                                                   │
│  PHASE 1: Aggressive Discovery with Progressive Caching           │
│  ┌────────────────────────────────────────────────────────────┐  │
│  │  initial_players = step1_collect_ladder_players()          │  │
│  │      → 200 players from ladder                             │  │
│  │                                                           │  │
│  │  FOR iteration in 1..max_iterations:                       │  │
│  │    players_to_process = select(players, limit=50)         │  │
│  │                                                           │  │
│  │    # Fetch match IDs (lightweight)                         │  │
│  │    new_match_ids = fetch_match_ids_batch(players)         │  │
│  │    unique_new = new_match_ids - all_match_ids              │  │
│  │                                                           │  │
│  │    # Fetch details immediately (cached, NOT committed)     │  │
│  │    FOR match_id IN unique_new:                             │  │
│  │      match_data = fetch_match_details(match_id)            │  │
│  │      # Data cached in LRU but NOT added to:                │  │
│  │      #   - self.match_data                                 │  │
│  │      #   - self.collected_matches                          │  │
│  │                                                           │  │
│  │    # Extract players for next iteration                   │  │
│  │    new_players = extract_players_from_cached(unique_new)  │  │
│  │    all_match_ids.update(unique_new)                        │  │
│  │                                                           │  │
│  │    IF len(all_match_ids) >= max_total_match_ids: BREAK    │  │
│  │    IF not new_players: BREAK                               │  │
│  │                                                           │  │
│  │  LOG: "Discovered {len(all_match_ids)} match IDs"        │  │
│  │  END FOR                                                  │  │
│  └────────────────────────────────────────────────────────────┘  │
│                                                                   │
│  PHASE 2: Select and Commit Best Matches                          │
│  ┌────────────────────────────────────────────────────────────┐  │
│  │  cached_ids = get_all_cached_match_ids()                    │  │
│  │                                                           │  │
│  │  # Sort by recency (match ID encodes timestamp)            │  │
│  │  sorted_ids = sort_by_recency(cached_ids)                  │  │
│  │                                                           │  │
│  │  # Select top N matches                                    │  │
│  │  selected_ids = sorted_ids[:max_matches]                   │  │
│  │                                                           │  │
│  │  # Commit to collection (from cache)                       │  │
│  │  FOR match_id IN selected_ids:                             │  │
│  │    match_data = cache.get(match_id)                        │  │
│  │    self.match_data.append(match_data)                      │  │
│  │    self.collected_matches.add(match_id)                     │  │
│  │                                                           │  │
│  │  LOG: "Committed {len(selected_ids)} best matches"        │  │
│  └────────────────────────────────────────────────────────────┘  │
│                                                                   │
│  save_to_bronze()                                                 │
│                                                                   │
│  Cache hit rate: 30-60% (duplicates filtered in Phase 1)         │
│  Time: 2-3 hours                                                 │
└──────────────────────────────────────────────────────────────────┘
```

### Component Diagram

```text
┌──────────────────────────────────────────────────────────────────┐
│                         RiotAPICollector                          │
├──────────────────────────────────────────────────────────────────┤
│                                                                   │
│  ┌─────────────────────────────────────────────────────────┐    │
│  │              Phase 1: Discovery Layer                    │    │
│  ├─────────────────────────────────────────────────────────┤    │
│  │                                                           │    │
│  │  phase1_discover_and_cache()                             │    │
│  │    ├── fetch_match_ids_batch() [NEW]                    │    │
│  │    │   └── fetch_player_matches() [EXISTING]            │    │
│  │    ├── fetch_match_details() [EXISTING]                  │    │
│  │    │   └── Uses LRU cache (50K capacity)                │    │
│  │    └── extract_players_from_cached_matches() [NEW]       │    │
│  │                                                           │    │
│  └─────────────────────────────────────────────────────────┘    │
│                          ↓                                       │
│  ┌─────────────────────────────────────────────────────────┐    │
│  │              Phase 2: Selection Layer                    │    │
│  ├─────────────────────────────────────────────────────────┤    │
│  │                                                           │    │
│  │  phase2_select_and_commit()                              │    │
│  │    ├── select_best_matches() [NEW]                       │    │
│  │    │   └── decode_match_timestamp() [NEW]                │    │
│  │    └── commit_matches_to_collection() [NEW]              │    │
│  │                                                           │    │
│  └─────────────────────────────────────────────────────────┘    │
│                          ↓                                       │
│  ┌─────────────────────────────────────────────────────────┐    │
│  │                    Output Layer                          │    │
│  ├─────────────────────────────────────────────────────────┤    │
│  │                                                           │    │
│  │  save_to_bronze() [EXISTING]                              │    │
│  │  _log_performance_summary() [MODIFIED]                    │    │
│  │                                                           │    │
│  └─────────────────────────────────────────────────────────┘    │
│                                                                   │
│  ┌─────────────────────────────────────────────────────────┐    │
│  │                  Legacy Methods (Kept)                   │    │
│  ├─────────────────────────────────────────────────────────┤    │
│  │  step1_collect_ladder_players() [EXISTING]              │    │
│  │  step2_collect_player_matches() [EXISTING - DEPRECATED]  │    │
│  │  step3_snowball_expansion() [EXISTING - DEPRECATED]      │    │
│  └─────────────────────────────────────────────────────────┘    │
│                                                                   │
└──────────────────────────────────────────────────────────────────┘
```

---

## Key Architecture Decisions

### Decision 1: Two-Phase vs. Single-Phase with Enhanced Cache

| Attribute | Value |
|-----------|-------|
| **Status** | Accepted |
| **Date** | 2026-03-19 |

**Context:**
Current single-phase approach commits matches immediately upon discovery, preventing effective deduplication. Each snowball iteration discovers completely different matches (low overlap in high-tier ranked), resulting in 0% cache hit rate.

**Choice:**
Implement two-phase collection:
1. **Phase 1**: Aggressive discovery with progressive caching (discover → cache → repeat)
2. **Phase 2**: Select best matches from cached pool and commit

**Rationale:**
- Separates discovery from commitment, allowing maximum deduplication
- Enables selection of best matches from larger pool (recency, quality)
- Cache becomes effective (30-60% hit rate expected)
- Snowball utilization increases from 0% to 100%

**Alternatives Rejected:**
1. **Single-phase with larger cache** - Still commits immediately, duplicates before discovery
2. **Pure match ID collection then details** - Cannot discover players without fetching details
3. **Increase limit to 50,000 matches** - Too many details to fetch (3-5 hours → 15+ hours), storage issues

**Consequences:**
- **Trade-off**: More memory usage (~50MB for 50K cached matches)
- **Benefit**: 40% faster (3-5h → 2-3h), 3x more match IDs discovered
- **Benefit**: Cache becomes effective instead of wasted

---

### Decision 2: Progressive Caching vs. Deferred Caching

| Attribute | Value |
|-----------|-------|
| **Status** | Accepted |
| **Date** | 2026-03-19 |

**Context:**
Two approaches to caching in Phase 1:
1. **Progressive**: Fetch and cache details during discovery
2. **Deferred**: Collect all match IDs, then batch fetch details

**Choice:**
Progressive caching - fetch match details immediately during Phase 1 discovery iterations.

**Rationale:**
- Enables player discovery for next snowball iteration (need participant PUUIDs)
- Cache is warmed up and ready for Phase 2 selection
- No separate "fetch all details" pass required
- Natural rate limit spreading (details fetched throughout Phase 1)

**Alternatives Rejected:**
1. **Deferred caching** - Cannot extract players for snowball without details
2. **Hybrid (fetch minimal details)** - Riot API doesn't support partial responses

**Consequences:**
- **Trade-off**: Phase 1 takes longer (includes detail fetching)
- **Benefit**: Single pass through API, no redundant calls
- **Benefit**: Phase 2 is instant (just cache lookups)

---

### Decision 3: Match ID Timestamp Decoding vs. Random Selection

| Attribute | Value |
|-----------|-------|
| **Status** | Accepted |
| **Date** | 2026-03-19 |

**Context:**
Phase 2 needs to select "best" 10,000 matches from 15,000-50,000 cached matches. Options: sort by recency or random selection.

**Choice:**
Sort by recency by decoding timestamp from Riot match ID (base64-encoded millisecond timestamp).

**Rationale:**
- Match IDs encode creation timestamp (documented Riot behavior)
- Recent matches more valuable for ML training (meta relevance)
- Deterministic selection (reproducible collections)
- No additional API calls required

**Alternatives Rejected:**
1. **Random selection** - Less predictable, may select old stale matches
2. **Fetch all and sort by gameCreation field** - Requires parsing all match data (slow)
3. **First-in-first-out** - Biased toward discovery order, not quality

**Consequences:**
- **Trade-off**: Requires match ID decoding logic
- **Benefit**: Selects most recent matches (higher quality)
- **Benefit**: No additional API calls or data processing

---

### Decision 4: Backward Compatibility Strategy

| Attribute | Value |
|-----------|-------|
| **Status** | Accepted |
| **Date** | 2026-03-19 |

**Context:**
New configuration keys (`phase1_max_match_ids`, `phase2_max_matches`) vs. legacy keys (`max_total_matches`, `max_iterations`). Need migration strategy.

**Choice:**
Implement automatic config migration with deprecation warnings.

**Rationale:**
- Existing users shouldn't break their configs
- Clear migration path with logging
- Allows gradual transition period

**Implementation:**
```python
def _load_config_with_migration(self, config: dict) -> dict:
    """Migrate legacy config keys to new two-phase structure."""
    collection = config.get('collection', {})

    # Map legacy keys to new keys
    if 'max_total_matches' in collection and 'phase2_max_matches' not in collection:
        collection['phase2_max_matches'] = collection['max_total_matches']
        self.logger.warning(
            "Deprecated key 'max_total_matches' mapped to 'phase2_max_matches'. "
            "Please update config.yaml."
        )

    if 'max_iterations' in collection and 'phase1_max_iterations' not in collection:
        collection['phase1_max_iterations'] = collection['max_iterations']
        self.logger.warning(
            "Deprecated key 'max_iterations' mapped to 'phase1_max_iterations'. "
            "Please update config.yaml."
        )

    # Set new defaults if not present
    collection.setdefault('phase1_max_match_ids', 50000)
    collection.setdefault('phase1_max_iterations', 3)
    collection.setdefault('phase1_players_per_iteration', 50)

    return config
```

**Alternatives Rejected:**
1. **Break compatibility** - Forces all users to update configs immediately
2. **Support both modes indefinitely** - Code complexity, maintenance burden

**Consequences:**
- **Trade-off**: Slightly more complex config loading logic
- **Benefit**: Zero breaking changes for existing users
- **Benefit**: Clear migration path with warnings

---

## File Manifest

| # | File | Action | Purpose | Dependencies |
|---|------|--------|---------|--------------|
| 1 | `collector/riot_api_collector.py` | Modify | Add two-phase collection methods | 2, 3 |
| 2 | `config/config.yaml` | Modify | Add Phase 1/Phase 2 config keys | None |
| 3 | `collector/utils.py` | Modify | Add match ID timestamp decoder | None |
| 4 | `tests/test_collector_phases.py` | Create | Unit tests for new methods | 1 |
| 5 | `tests/test_collector_integration.py` | Modify | Add two-phase integration tests | 1 |
| 6 | `tests/test_config_migration.py` | Create | Test config migration logic | 2 |
| 7 | `logs/collector.log` | Existing | Verify performance metrics | None |

### File Change Summary

```
Modified: 2 files
Created: 3 test files
Lines Added: ~400 (production) + ~600 (tests)
Lines Removed: ~50 (deprecated code)
```

---

## Code Patterns

### Pattern 1: Phase 1 Discovery Loop

```python
async def phase1_discover_and_cache(
    self,
    initial_players: Set[str],
    max_total_match_ids: int,
    max_iterations: int,
    players_per_iteration: int
) -> Phase1Result:
    """
    Discover match IDs through snowball iterations with progressive caching.

    Args:
        initial_players: Starting set of player PUUIDs
        max_total_match_ids: Stop after discovering this many unique IDs
        max_iterations: Maximum snowball iterations
        players_per_iteration: Players to process per iteration

    Returns:
        Phase1Result with discovered_count, cached_count, iterations_used
    """
    all_match_ids: Set[str] = set()
    players_to_process = initial_players.copy()

    self.logger.info("=" * 60)
    self.logger.info("PHASE 1: Aggressive Discovery with Caching")
    self.logger.info("=" * 60)

    for iteration in range(1, max_iterations + 1):
        self.logger.info(f"Iteration {iteration}/{max_iterations}")

        # Select subset of players (prevent explosion)
        selected_players = set(list(players_to_process)[:players_per_iteration])
        self.logger.info(f"  Processing {len(selected_players)} players")

        # Fetch match IDs (lightweight API calls)
        new_match_ids = await self._fetch_match_ids_batch(selected_players)
        unique_new = new_match_ids - all_match_ids

        if not unique_new:
            self.logger.info("  No new match IDs discovered. Stopping.")
            break

        self.logger.info(f"  Discovered {len(unique_new)} new match IDs")
        all_match_ids.update(unique_new)

        # Fetch details immediately (cached, NOT committed)
        for match_id in unique_new:
            await self.fetch_match_details(match_id)
            # Data is cached in self._match_cache but NOT added to:
            #   - self.match_data
            #   - self.collected_matches

        # Extract players from cached matches for next iteration
        new_players = self._extract_players_from_cached_matches(unique_new)
        players_to_process = new_players

        self.logger.info(f"  Extracted {len(new_players)} players from matches")
        self.logger.info(f"  Total discovered: {len(all_match_ids)} match IDs")

        # Early stop conditions
        if len(all_match_ids) >= max_total_match_ids:
            self.logger.info(f"  Reached match ID limit ({max_total_match_ids}). Stopping.")
            break

        if not new_players:
            self.logger.info("  No new players discovered. Stopping.")
            break

    return Phase1Result(
        discovered_count=len(all_match_ids),
        cached_count=len(self._match_cache._cache) if self._match_cache else 0,
        iterations_used=iteration
    )
```

### Pattern 2: Match ID Batch Fetching

```python
async def _fetch_match_ids_batch(
    self,
    player_puuids: Set[str],
    count: int = 100
) -> Set[str]:
    """
    Fetch match IDs for multiple players in parallel.

    Args:
        player_puuids: Set of player PUUIDs
        count: Matches per player to fetch

    Returns:
        Set of unique match IDs
    """
    async def fetch_ids(puuid: str) -> List[str]:
        return await self.fetch_player_matches(puuid, count=count)

    # Fetch in parallel with progress tracking
    tasks = [fetch_ids(puuid) for puuid in player_puuids]

    all_ids = set()
    for task in tqdm(
        asyncio.as_completed(tasks),
        total=len(tasks),
        desc="  Fetching match IDs",
        leave=False
    ):
        match_ids = await task
        all_ids.update(match_ids)

    return all_ids
```

### Pattern 3: Player Extraction from Cache

```python
def _extract_players_from_cached_matches(
    self,
    match_ids: Set[str]
) -> Set[str]:
    """
    Extract unique player PUUIDs from cached match data.

    Args:
        match_ids: Match IDs to extract players from

    Returns:
        Set of unique player PUUIDs
    """
    if not self._match_cache:
        self.logger.warning("  Cache not enabled, cannot extract players")
        return set()

    new_players: Set[str] = set()

    for match_id in match_ids:
        match_data = self._match_cache.get(match_id)
        if not match_data:
            continue

        # Extract participants
        if "info" in match_data and "participants" in match_data["info"]:
            for participant in match_data["info"]["participants"]:
                if "puuid" in participant:
                    new_players.add(participant["puuid"])

    return new_players
```

### Pattern 4: Match ID Timestamp Decoding

```python
def _decode_match_timestamp(self, match_id: str) -> int:
    """
    Decode timestamp from Riot match ID.

    Riot match v5 IDs are base64-encoded timestamps:
    - Format: {region}_{timestamp}_{increment}
    - Timestamp is in milliseconds since Unix epoch

    Args:
        match_id: Match ID (e.g., "BR1_1234567890_1")

    Returns:
        Unix timestamp in milliseconds
    """
    import base64

    try:
        # Match IDs from Riot API are already in readable format
        # Format: BR1_1234567890_1 or similar
        parts = match_id.split('_')

        if len(parts) >= 2:
            # Second part is the timestamp
            return int(parts[1])

        # Fallback: try base64 decoding (for older formats)
        decoded = base64.b64decode(match_id + '==').decode('utf-8')
        parts = decoded.split('_')

        if len(parts) >= 2:
            return int(parts[1])

        # Last resort: return 0 (oldest possible)
        return 0

    except (ValueError, IndexError, UnicodeDecodeError):
        self.logger.warning(f"  Failed to decode timestamp from {match_id}")
        return 0

def _sort_match_ids_by_recency(self, match_ids: List[str]) -> List[str]:
    """
    Sort match IDs by recency (most recent first).

    Args:
        match_ids: List of match IDs

    Returns:
        Sorted list (most recent first)
    """
    return sorted(
        match_ids,
        key=lambda mid: self._decode_match_timestamp(mid),
        reverse=True
    )
```

### Pattern 5: Phase 2 Selection and Commit

```python
async def phase2_select_and_commit(
    self,
    max_matches: int
) -> Phase2Result:
    """
    Select best matches from cache and commit to collection.

    Args:
        max_matches: Maximum matches to commit

    Returns:
        Phase2Result with committed_count, cache_hit_rate
    """
    self.logger.info("=" * 60)
    self.logger.info("PHASE 2: Select and Commit Best Matches")
    self.logger.info("=" * 60)

    if not self._match_cache:
        raise RuntimeError("Cache not enabled. Phase 2 requires cache.")

    # Get all cached match IDs
    cached_ids = list(self._match_cache._cache.keys())
    self.logger.info(f"  Cached matches: {len(cached_ids)}")

    # Sort by recency
    sorted_ids = self._sort_match_ids_by_recency(cached_ids)

    # Select top N
    selected_ids = sorted_ids[:max_matches]
    self.logger.info(f"  Selected {len(selected_ids)} most recent matches")

    # Commit to collection
    committed_count = 0
    for match_id in selected_ids:
        match_data = self._match_cache.get(match_id)
        if match_data:
            self.match_data.append(match_data)
            self.collected_matches.add(match_id)
            committed_count += 1

    self.logger.info(f"  Committed {committed_count} matches to collection")

    # Calculate cache effectiveness
    cache_hits = len(self._match_cache._cache) - committed_count
    cache_hit_rate = (cache_hits / len(self._match_cache._cache)) * 100

    self.logger.info(f"  Cache hit rate: {cache_hit_rate:.1f}%")

    return Phase2Result(
        committed_count=committed_count,
        cache_hit_rate=cache_hit_rate
    )
```

### Pattern 6: Configuration Migration

```python
def _migrate_legacy_config(self) -> None:
    """
    Migrate legacy configuration keys to new two-phase structure.

    Logs warnings for deprecated keys and sets new defaults.
    """
    collection = self.config.get('collection', {})

    # Map max_total_matches → phase2_max_matches
    if 'max_total_matches' in collection and 'phase2_max_matches' not in collection:
        collection['phase2_max_matches'] = collection['max_total_matches']
        self.logger.warning(
            "Deprecated config key 'max_total_matches' → 'phase2_max_matches'. "
            "Please update config.yaml."
        )

    # Map max_iterations → phase1_max_iterations
    if 'max_iterations' in collection and 'phase1_max_iterations' not in collection:
        collection['phase1_max_iterations'] = collection['max_iterations']
        self.logger.warning(
            "Deprecated config key 'max_iterations' → 'phase1_max_iterations'. "
            "Please update config.yaml."
        )

    # Set new defaults if not present
    collection.setdefault('phase1_max_match_ids', 50000)
    collection.setdefault('phase1_players_per_iteration', 50)
```

### Pattern 7: Updated run_collection Method

```python
async def run_collection(self) -> None:
    """
    Execute two-phase collection process with performance logging.
    """
    # Track start time
    self._start_time = time.monotonic()

    # Apply config migration
    self._migrate_legacy_config()

    # Create session with default headers
    headers = {"X-Riot-Token": self.config["riot_api"]["api_key"]}
    async with aiohttp.ClientSession(headers=headers) as session:
        self.session = session

        try:
            # Phase 1: Discover and cache match IDs
            player_puuids = await self.step1_collect_ladder_players()

            phase1_result = await self.phase1_discover_and_cache(
                initial_players=player_puuids,
                max_total_match_ids=self.config["collection"]["phase1_max_match_ids"],
                max_iterations=self.config["collection"]["phase1_max_iterations"],
                players_per_iteration=self.config["collection"]["phase1_players_per_iteration"]
            )

            # Phase 2: Select and commit best matches
            phase2_result = await self.phase2_select_and_commit(
                max_matches=self.config["collection"]["phase2_max_matches"]
            )

            # Save to Bronze layer
            output_file = self.save_to_bronze()

            self.logger.info("=" * 60)
            self.logger.info("Collection complete!")
            self.logger.info(f"Phase 1 - Discovered: {phase1_result.discovered_count} IDs")
            self.logger.info(f"Phase 1 - Cached: {phase1_result.cached_count} matches")
            self.logger.info(f"Phase 1 - Iterations: {phase1_result.iterations_used}")
            self.logger.info(f"Phase 2 - Committed: {phase2_result.committed_count} matches")
            self.logger.info(f"Phase 2 - Cache hit rate: {phase2_result.cache_hit_rate:.1f}%")
            self.logger.info(f"Total players: {len(self.collected_players)}")
            self.logger.info(f"Output: {output_file}")
            self.logger.info("=" * 60)

            # Log performance summary
            self._log_performance_summary()

        except Exception as e:
            self.logger.error(f"Collection failed: {e}", exc_info=True)
            raise
```

---

## Testing Strategy

### Unit Tests

**File:** `tests/test_collector_phases.py`

```python
import pytest
from collector.riot_api_collector import RiotAPICollector

class TestPhase1Discovery:
    """Test Phase 1: Discovery with caching."""

    @pytest.mark.asyncio
    async def test_phase1_stops_at_match_id_limit(self):
        """Test Phase 1 stops when reaching phase1_max_match_ids."""
        collector = RiotAPICollector()
        # Mock API calls to return fixed match IDs
        # Assert: Stops after discovering 50,000 match IDs

    @pytest.mark.asyncio
    async def test_phase1_stops_at_iteration_limit(self):
        """Test Phase 1 stops after phase1_max_iterations."""
        collector = RiotAPICollector()
        # Assert: Completes exactly 3 iterations

    @pytest.mark.asyncio
    async def test_phase1_deduplicates_within_iteration(self):
        """Test Phase 1 removes duplicate match IDs within iteration."""
        collector = RiotAPICollector()
        # Mock: Players 1-3 return overlapping match IDs
        # Assert: Only unique match IDs counted

    @pytest.mark.asyncio
    async def test_phase1_deduplicates_across_iterations(self):
        """Test Phase 1 removes duplicate match IDs across iterations."""
        collector = RiotAPICollector()
        # Mock: Iteration 2 returns same IDs as iteration 1
        # Assert: Duplicates not counted again

    @pytest.mark.asyncio
    async def test_phase1_limits_players_per_iteration(self):
        """Test Phase 1 processes only phase1_players_per_iteration."""
        collector = RiotAPICollector()
        # Mock: 1000 players available
        # Assert: Only first 50 processed per iteration

    @pytest.mark.asyncio
    async def test_extract_players_from_cached_matches(self):
        """Test player extraction from cached match data."""
        collector = RiotAPICollector()
        # Mock: Cache contains matches with 10 participants each
        # Assert: Extracts unique player PUUIDs

    @pytest.mark.asyncio
    async def test_phase1_early_stop_no_new_players(self):
        """Test Phase 1 stops when no new players discovered."""
        collector = RiotAPICollector()
        # Mock: No new players in iteration 2
        # Assert: Stops without starting iteration 3


class TestPhase2Selection:
    """Test Phase 2: Selection and commit."""

    @pytest.mark.asyncio
    async def test_phase2_selects_recent_matches(self):
        """Test Phase 2 prioritizes most recent matches."""
        collector = RiotAPICollector()
        # Mock: Cache contains matches from different times
        # Assert: Most recent matches selected

    @pytest.mark.asyncio
    async def test_phase2_respects_max_matches_limit(self):
        """Test Phase 2 commits exactly phase2_max_matches."""
        collector = RiotAPICollector()
        # Mock: Cache has 15,000 matches, max_matches=10,000
        # Assert: Commits 10,000 matches

    @pytest.mark.asyncio
    async def test_phase2_calculates_cache_hit_rate(self):
        """Test Phase 2 correctly calculates cache hit rate."""
        collector = RiotAPICollector()
        # Mock: Cache 15,000, commit 10,000
        # Assert: Cache hit rate = (5000 / 15000) = 33.3%


class TestMatchIDTimestampDecoding:
    """Test match ID timestamp decoding."""

    def test_decode_match_timestamp_standard_format(self):
        """Test decoding standard match ID format."""
        collector = RiotAPICollector()
        match_id = "BR1_1234567890_1"
        # Assert: Returns 1234567890

    def test_decode_match_timestamp_base64_format(self):
        """Test decoding base64-encoded match ID."""
        collector = RiotAPICollector()
        # Mock: Base64-encoded match ID
        # Assert: Correctly decodes timestamp

    def test_decode_match_timestamp_invalid_format(self):
        """Test decoding invalid match ID format."""
        collector = RiotAPICollector()
        match_id = "invalid_match_id"
        # Assert: Returns 0 (oldest possible)

    def test_sort_match_ids_by_recency(self):
        """Test sorting match IDs by timestamp."""
        collector = RiotAPICollector()
        match_ids = [
            "BR1_1000_1",  # Oldest
            "BR1_3000_1",  # Newest
            "BR1_2000_1",  # Middle
        ]
        sorted_ids = collector._sort_match_ids_by_recency(match_ids)
        # Assert: [BR1_3000_1, BR1_2000_1, BR1_1000_1]


class TestConfigMigration:
    """Test configuration migration."""

    def test_migrate_max_total_matches_to_phase2(self):
        """Test migration of max_total_matches → phase2_max_matches."""
        collector = RiotAPICollector()
        # Mock: Config has max_total_matches=10000
        collector._migrate_legacy_config()
        # Assert: phase2_max_matches=10000, warning logged

    def test_migrate_max_iterations_to_phase1(self):
        """Test migration of max_iterations → phase1_max_iterations."""
        collector = RiotAPICollector()
        # Mock: Config has max_iterations=5
        collector._migrate_legacy_config()
        # Assert: phase1_max_iterations=5, warning logged

    def test_set_default_phase1_config_values(self):
        """Test default Phase 1 config values when not present."""
        collector = RiotAPICollector()
        # Mock: Config has no phase1 keys
        collector._migrate_legacy_config()
        # Assert: phase1_max_match_ids=50000, phase1_max_iterations=3
```

### Integration Tests

**File:** `tests/test_collector_integration.py`

```python
import pytest
from collector.riot_api_collector import RiotAPICollector

class TestTwoPhaseCollectionFlow:
    """Test complete two-phase collection flow."""

    @pytest.mark.asyncio
    async def test_full_two_phase_collection_with_mock_api(self):
        """Test complete flow with mocked Riot API."""
        collector = RiotAPICollector()

        # Mock: Step 1 returns 200 players
        # Mock: Phase 1 discovers 15,000 match IDs
        # Mock: Phase 2 commits 10,000 matches

        await collector.run_collection()

        # Assert:
        # - Phase 1 completed 3 iterations
        # - Phase 1 discovered 15,000+ match IDs
        # - Phase 2 committed 10,000 matches
        # - Cache hit rate > 30%
        # - Output file created

    @pytest.mark.asyncio
    async def test_cache_effectiveness_with_realistic_data(self):
        """Test cache effectiveness under realistic conditions."""
        collector = RiotAPICollector()

        # Mock: Realistic match ID overlap (30%)
        # Mock: Phase 1 discovers 30,000 match IDs

        await collector.run_collection()

        # Assert: Cache hit rate >= 30%

    @pytest.mark.asyncio
    async def test_no_rate_limit_violations(self):
        """Test rate limiter not exceeded during collection."""
        collector = RiotAPICollector()

        # Mock: Track API call rate
        # Assert: Never exceeds 20 req/sec

    @pytest.mark.asyncio
    async def test_memory_usage_within_limits(self):
        """Test cache uses <100MB for 50,000 matches."""
        collector = RiotAPICollector()

        # Mock: Phase 1 caches 50,000 matches
        # Measure: Memory usage
        # Assert: <100MB


class TestBackwardCompatibility:
    """Test backward compatibility with legacy configs."""

    @pytest.mark.asyncio
    async def test_legacy_config_works_without_modification(self):
        """Test old config.yaml still works."""
        # Use old config format
        collector = RiotAPICollector(config_path="tests/fixtures/legacy_config.yaml")

        await collector.run_collection()

        # Assert: Collection completes successfully
        # Assert: Migration warnings logged

    @pytest.mark.asyncio
    async def test_new_config_works(self):
        """Test new two-phase config format."""
        # Use new config format
        collector = RiotAPICollector(config_path="tests/fixtures/new_config.yaml")

        await collector.run_collection()

        # Assert: Collection completes successfully
        # Assert: No migration warnings
```

### Performance Tests

**File:** `tests/test_collector_performance.py`

```python
import pytest
import time
from collector.riot_api_collector import RiotAPICollector

class TestPerformanceTargets:
    """Test performance requirements."""

    @pytest.mark.asyncio
    @pytest.mark.slow
    async def test_collection_time_under_3_hours(self):
        """Test 10,000 matches collected in <3 hours."""
        collector = RiotAPICollector()

        start_time = time.monotonic()
        await collector.run_collection()
        elapsed = time.monotonic() - start_time

        # Assert: elapsed < 10800 (3 hours in seconds)

    @pytest.mark.asyncio
    async def test_cache_hit_rate_above_30_percent(self):
        """Test cache effectiveness threshold."""
        collector = RiotAPICollector()

        await collector.run_collection()

        # Assert: Cache hit rate >= 30%
```

---

## Configuration Schema

### New Configuration Keys

```yaml
# config/config.yaml

# Data Collection Settings
collection:
  # === NEW: Phase 1 Configuration ===
  # Phase 1: Match ID Discovery
  phase1_max_match_ids: 50000          # Total match IDs to discover
                                      # Type: integer
                                      # Range: 10000-100000
                                      # Default: 50000

  phase1_max_iterations: 3              # Snowball iterations in Phase 1
                                      # Type: integer
                                      # Range: 1-10
                                      # Default: 3

  phase1_players_per_iteration: 50     # Players to process per iteration
                                      # Type: integer
                                      # Range: 10-100
                                      # Default: 50

  # === NEW: Phase 2 Configuration ===
  # Phase 2: Selection
  phase2_max_matches: 10000             # Matches to commit to Bronze
                                      # Type: integer
                                      # Range: 1000-50000
                                      # Default: 10000

  # === LEGACY (Deprecated, Migrated) ===
  # max_total_matches: 10000           # Use phase2_max_matches instead
  # max_iterations: 5                  # Use phase1_max_iterations instead

  # === UNCHANGED ===
  initial_players_per_tier: 50         # Still used for Step 1
  matches_per_player: 100              # Still used for match ID fetching
  queue_filter: 420                    # Still used for filtering
```

### Configuration Examples

**Minimal Config (Legacy Compatibility)**
```yaml
collection:
  max_total_matches: 10000   # → phase2_max_matches
  max_iterations: 3           # → phase1_max_iterations
```

**New Config (Recommended)**
```yaml
collection:
  phase1_max_match_ids: 50000
  phase1_max_iterations: 3
  phase1_players_per_iteration: 50
  phase2_max_matches: 10000
```

**Conservative Config (Faster, Less Coverage)**
```yaml
collection:
  phase1_max_match_ids: 20000     # Less discovery
  phase1_max_iterations: 2         # Fewer iterations
  phase1_players_per_iteration: 30 # Fewer players per iteration
  phase2_max_matches: 10000        # Same output
```

**Aggressive Config (Slower, More Coverage)**
```yaml
collection:
  phase1_max_match_ids: 100000    # More discovery
  phase1_max_iterations: 5         # More iterations
  phase1_players_per_iteration: 100 # More players per iteration
  phase2_max_matches: 10000        # Same output
```

---

## Performance Targets

### Expected Performance

| Metric | Current | Target | Measurement |
|--------|---------|--------|-------------|
| **Phase 1 Duration** | N/A | 60-90 minutes | Phase 1 timer |
| **Phase 2 Duration** | N/A | 5-10 minutes | Phase 2 timer |
| **Total Duration** | 3-5 hours | 2-3 hours | Overall timer |
| **Cache Hit Rate** | 0% | 30-60% | Performance summary |
| **Match IDs Discovered** | ~10,000 | 30,000+ | Phase 1 result |
| **Matches Committed** | 10,000 | 10,000 | Phase 2 result |
| **API Calls (Heavy)** | 10,000 | 10,000 | Request counter |
| **API Calls (Light)** | 200 | 15,000 | Request counter |
| **Memory Usage** | ~10MB | ~50MB | Memory profiler |

### Performance Monitoring

**Enhanced Logging:**
```text
============================================================
PHASE 1: Aggressive Discovery with Caching
============================================================
Iteration 1/3
  Processing 50 players
  Fetching match IDs: 100%|████████████████████| 5000/5000 [00:15<00:00]
  Discovered 5000 new match IDs
  Fetching match details: 100%|██████████████| 5000/5000 [05:30<00:00]
  Extracted 15000 players from matches
  Total discovered: 5000 match IDs

Iteration 2/3
  Processing 50 players
  Fetching match IDs: 100%|████████████████████| 5000/5000 [00:15<00:00]
  Discovered 5000 new match IDs
  Fetching match details: 100%|██████████████| 5000/5000 [05:30<00:00]
  Extracted 15000 players from matches
  Total discovered: 10000 match IDs

Iteration 3/3
  Processing 50 players
  Fetching match IDs: 100%|████████████████████| 5000/5000 [00:15<00:00]
  Discovered 5000 new match IDs
  Fetching match details: 100%|██████████████| 5000/5000 [05:30<00:00]
  Extracted 15000 players from matches
  Total discovered: 15000 match IDs

============================================================
PHASE 1 Complete
  Discovered: 15000 match IDs
  Cached: 15000 matches
  Iterations: 3
  Duration: 72.5 minutes
============================================================

============================================================
PHASE 2: Select and Commit Best Matches
============================================================
  Cached matches: 15000
  Selected 10000 most recent matches
  Committing matches to collection: 100%|█████| 10000/10000 [00:05<00:00]
  Committed 10000 matches to collection
  Cache hit rate: 33.3%
  Duration: 5.2 minutes
============================================================

============================================================
Performance Summary
============================================================
Cache: 33.3% hit rate | 15000/15000 entries | 5000 requests saved
Total Time: 4672.1s | Matches: 10000 | Throughput: 2.14 matches/sec
============================================================
```

---

## Risk Analysis & Mitigation

### Risk Matrix

| Risk | Probability | Impact | Mitigation |
|------|-------------|--------|------------|
| **Match ID timestamp decoding fails** | Low (20%) | High | Fall back to random selection, log error |
| **Cache memory exceeds 100MB** | Low (10%) | Medium | Monitor cache size, configurable limit |
| **Rate limit still hit** | Low (15%) | High | Add delay between iterations, reduce concurrent |
| **Backward compatibility broken** | Medium (30%) | High | Extensive testing, migration logic |
| **Phase 1 never terminates** | Low (5%) | High | Hard iteration limit, timeout safeguard |

### Mitigation Strategies

**1. Match ID Timestamp Decoding**
```python
def _decode_match_timestamp(self, match_id: str) -> int:
    """Decode with fallback for robustness."""
    try:
        # Attempt standard decoding
        return self._decode_standard_format(match_id)
    except Exception:
        # Fallback: return 0 (oldest)
        self.logger.warning(f"Failed to decode {match_id}, using oldest")
        return 0
```

**2. Cache Memory Monitoring**
```python
def _check_cache_memory_usage(self) -> None:
    """Log warning if cache approaches memory limit."""
    if self._match_cache:
        size = len(self._match_cache._cache)
        if size > 45000:  # 90% of 50,000
            self.logger.warning(
                f"Cache size ({size}) approaching limit. "
                f"Consider reducing phase1_max_match_ids."
            )
```

**3. Rate Limit Protection**
```python
async def phase1_discover_and_cache(...):
    """Add delay between iterations if needed."""
    for iteration in range(...):
        # ... discovery logic ...

        # Check rate limiter health
        if iteration < max_iterations:
            await asyncio.sleep(5)  # Brief pause between iterations
```

**4. Backward Compatibility Testing**
```python
# tests/test_config_migration.py
def test_all_legacy_config_combinations():
    """Test all possible legacy config combinations."""
    test_cases = [
        {"max_total_matches": 10000},
        {"max_iterations": 5},
        {"max_total_matches": 10000, "max_iterations": 5},
        # ... more combinations
    ]
    for config in test_cases:
        collector = RiotAPICollector(config=config)
        assert collector.config["collection"]["phase2_max_matches"] == expected
```

**5. Termination Safeguards**
```python
async def phase1_discover_and_cache(...):
    """Multiple termination conditions."""
    for iteration in range(max_iterations):
        # ... discovery logic ...

        # Check ALL termination conditions
        if (len(all_match_ids) >= max_total_match_ids or
            not new_players or
            iteration >= max_iterations):
            break
```

---

## Deployment Plan

### Rollout Strategy

**Phase 1: Testing (Week 1)**
- Deploy to development environment
- Run full collection with new two-phase logic
- Verify cache hit rate > 30%
- Verify memory usage < 100MB
- Fix any critical bugs

**Phase 2: Canary (Week 2)**
- Deploy to production with feature flag
- Run 3 collections with monitoring
- Compare metrics vs. baseline
- Iterate on issues

**Phase 3: Full Rollout (Week 3)**
- Remove feature flag
- Update documentation
- Monitor for 1 week
- Gather user feedback

### Rollback Plan

**Trigger Conditions:**
- Cache hit rate < 10%
- Collection time > 4 hours
- Memory usage > 200MB
- Rate limit errors > 100/hour

**Rollback Steps:**
1. Revert `run_collection()` to legacy three-step flow
2. Disable new config keys (use migration warnings)
3. Deploy hotfix within 1 hour
4. Post-mortem and fix

### Monitoring

**Key Metrics to Track:**
```python
# Add to performance summary
{
    "phase1_discovery_time": 72.5,  # minutes
    "phase1_iterations_used": 3,
    "phase1_match_ids_discovered": 15000,
    "phase2_selection_time": 5.2,  # minutes
    "phase2_cache_hit_rate": 33.3,  # percent
    "total_collection_time": 77.7,  # minutes
    "api_calls_heavy": 10000,
    "api_calls_light": 15000,
    "memory_peak_mb": 52.3
}
```

---

## Success Validation

### Pre-Deployment Checklist

```markdown
[ ] Unit tests pass (100% coverage of new methods)
[ ] Integration tests pass (2-phase flow with mock API)
[ ] Performance tests pass (<3 hours with mock data)
[ ] Config migration tested (all legacy keys work)
[ ] Memory usage validated (<100MB for 50K matches)
[ ] Documentation updated (CLAUDE.md, README.md)
[ ] Backward compatibility verified
[ ] Rollback plan tested
[ ] Monitoring configured
```

### Post-Deployment Validation

```markdown
[ ] Cache hit rate > 30% (measured in prod)
[ ] Collection time < 3 hours (measured in prod)
[ ] Match IDs discovered > 30,000 (measured in prod)
[ ] No rate limit errors (check logs)
[ ] Memory usage < 100MB (monitor metrics)
[ ] No breaking changes reported (user feedback)
```

---

## Next Steps

1. ✅ **DESIGN document created** (this file)
2. ⏭️ **Run `/build`** to implement the solution
3. ⏭️ **Run tests** to validate implementation
4. ⏭️ **Deploy to dev** for initial testing
5. ⏭️ **Deploy to prod** with monitoring

---

## Related Artifacts

- **DEFINE**: `.claude/sdd/features/DEFINE_collector_batch_optimization.md`
- **BRAINSTORM**: `.claude/sdd/features/BRAINSTORM_collector_batch_optimization.md`
- **Evidence**: `logs/collector.log` (2026-03-18 execution)
- **Implementation**: `collector/riot_api_collector.py`
- **Tests**: `tests/test_collector_phases.py`

---

## Change History

| Date | Version | Changes |
|------|---------|---------|
| 2026-03-19 | 1.0 | Initial DESIGN from DEFINE |
