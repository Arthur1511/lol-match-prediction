"""
Riot API Data Collector for League of Legends Match Prediction.

Three-step sampling strategy:
1. Collect players from ranked ladder (high elo)
2. Collect match histories for those players
3. Expand collection via players discovered in matches (snowball)

Respects Riot API rate limits and saves raw data to Bronze layer.
"""

import asyncio
import logging
import os
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

import aiohttp
import pandas as pd
import yaml
from dotenv import load_dotenv
from tqdm import tqdm

from collector.rate_limiter import RateLimiter, EndpointAwareRateLimiter, LRUCache
from collector.utils import (
    ELITE_TIERS,
    EndpointKeys,
    extract_patch_version,
    load_config_with_env_vars,
    setup_logger,
)

# Load environment variables
load_dotenv()


class RiotAPICollector:
    """
    Async collector for League of Legends match data.

    Uses three-step sampling:
    1. Fetch high-elo players from ranked ladder
    2. Fetch match histories for those players
    3. Discover new players from matches and repeat
    """

    def __init__(self, config_path: str = "config/config.yaml"):
        """
        Initialize the collector with configuration.

        Args:
            config_path: Path to YAML configuration file
        """
        # Load configuration
        self.config = load_config_with_env_vars(config_path)

        # Setup logging
        self._setup_logging()

        # Migrate legacy config keys to new two-phase structure
        self._migrate_legacy_config()

        # Initialize rate limiter
        if 'endpoint_rate_limits' in self.config.get('riot_api', {}):
            limits = self.config['riot_api']['endpoint_rate_limits']
            self.rate_limiter = EndpointAwareRateLimiter(limits=limits)
        else:
            # Backward compatible: use single RateLimiter
            self.rate_limiter = RateLimiter(
                requests_per_second=self.config["riot_api"]["rate_limit_per_second"],
                requests_per_2min=self.config["riot_api"]["rate_limit_per_2min"],
            )

        # Initialize cache if enabled
        perf_config = self.config.get('performance', {})
        if perf_config.get('enable_response_cache', False):
            self._match_cache = LRUCache(
                max_size=perf_config.get('cache_max_size', 10000)
            )
            # Track cache performance
            self._cache_hits: int = 0
            self._cache_misses: int = 0
        else:
            self._match_cache = None
            self._cache_hits: int = 0
            self._cache_misses: int = 0

        # Region
        self.region = self.config["riot_api"]["default_region"]

        # Track start time for performance logging
        self._start_time = None

        # Session will be created in async context
        self.session: Optional[aiohttp.ClientSession] = None

        # Tracking
        self.collected_players: Set[str] = set()  # PUUIDs
        self.collected_matches: Set[str] = set()  # Match IDs
        self.match_data: List[Dict] = []
        self.parsed_match_index: int = 0  # For incremental snowball parsing

        # Load region routing from config or use defaults
        self.region_routing = self.config["riot_api"].get(
            "region_routing",
            {
                "br1": "americas",
                "na1": "americas",
                "la1": "americas",
                "la2": "americas",
                "euw1": "europe",
                "eun1": "europe",
                "tr1": "europe",
                "ru": "europe",
                "kr": "asia",
                "jp1": "asia",
                "oc1": "sea",
            },
        )

        # Precompute routing URLs for efficiency
        routing = self.region_routing.get(self.region.lower(), "americas")
        self._match_routing_base = f"https://{routing}.api.riotgames.com"

        # Create output directories
        self._create_directories()

        self.logger.info(f"Initialized RiotAPICollector for region: {self.region}")

    def _setup_logging(self) -> None:
        """Configure logging."""
        self.logger = setup_logger(__name__, self.config)

    def _create_directories(self) -> None:
        """Create necessary data directories."""
        storage = self.config["storage"]
        Path(storage["bronze_path"]).mkdir(parents=True, exist_ok=True)
        Path(storage["landing_path"]).mkdir(parents=True, exist_ok=True)

    def _build_url(self, endpoint_key: str, **kwargs) -> str:
        """
        Build full API URL from endpoint template.

        Args:
            endpoint_key: Key in config['riot_api']['endpoints']
            **kwargs: Values to format into the endpoint template

        Returns:
            Complete API URL
        """
        base_url = self.config["riot_api"]["base_url"].format(region=self.region)
        endpoint = self.config["riot_api"]["endpoints"][endpoint_key]

        # Format endpoint with provided kwargs
        formatted_endpoint = endpoint.format(**kwargs)

        return base_url + formatted_endpoint

    def _build_routing_url(self, endpoint_path: str) -> str:
        """
        Build routing URL for match endpoints.

        Args:
            endpoint_path: Endpoint path (e.g., "/lol/match/v5/matches/{match_id}")

        Returns:
            Complete routing URL
        """
        return self._match_routing_base + endpoint_path

    def _calculate_retry_delay(
        self, attempt: int, status_code: int, headers: Dict[str, str]
    ) -> float:
        """
        Calculate retry delay using exponential backoff or Retry-After header.

        Args:
            attempt: Current retry attempt (0-indexed)
            status_code: HTTP status code
            headers: Response headers

        Returns:
            Delay in seconds
        """
        # For 429, respect Retry-After header if present
        if status_code == 429 and "Retry-After" in headers:
            return float(headers["Retry-After"])

        # Use exponential backoff for other errors if enabled
        if self.config["riot_api"]["retry_strategy"]["exponential_backoff"]:
            base_delay = self.config["riot_api"]["retry_strategy"][
                "base_delay_seconds"
            ]
            max_delay = self.config["riot_api"]["retry_strategy"]["max_delay_seconds"]
            delay = min(max_delay, base_delay * (2**attempt))
            return delay

        # Fallback to legacy fixed delay
        return self.config["riot_api"]["retry_delay_seconds"]

    async def _make_request(
        self, url: str, params: Optional[Dict] = None
    ) -> Optional[Dict]:
        """
        Make rate-limited API request with retries and exponential backoff.

        Args:
            url: Full API URL
            params: Query parameters

        Returns:
            JSON response or None if failed
        """
        max_retries = self.config["riot_api"]["max_retries"]
        timeout = self.config["riot_api"]["timeout_seconds"]

        for attempt in range(max_retries):
            try:
                async with self.rate_limiter:
                    async with self.session.get(
                        url, params=params, timeout=aiohttp.ClientTimeout(total=timeout)
                    ) as response:
                        if response.status == 200:
                            return await response.json()
                        elif response.status == 429:
                            # Rate limited - calculate delay with Retry-After priority
                            delay = self._calculate_retry_delay(
                                attempt, 429, dict(response.headers)
                            )
                            self.logger.warning(
                                f"Rate limited. Waiting {delay}s... (attempt {attempt + 1}/{max_retries})"
                            )
                            await asyncio.sleep(delay)
                            continue
                        elif response.status == 404:
                            # Not found - valid response, return None
                            return None
                        else:
                            # Other errors - use exponential backoff
                            self.logger.error(
                                f"Request failed: {response.status} - {url}"
                            )
                            if attempt < max_retries - 1:
                                delay = self._calculate_retry_delay(
                                    attempt, response.status, dict(response.headers)
                                )
                                self.logger.info(
                                    f"Retrying in {delay}s... (attempt {attempt + 1}/{max_retries})"
                                )
                                await asyncio.sleep(delay)
                                continue
                            return None

            except asyncio.TimeoutError:
                self.logger.error(f"Request timeout: {url}")
                if attempt < max_retries - 1:
                    delay = self._calculate_retry_delay(attempt, 0, {})
                    self.logger.info(
                        f"Retrying in {delay}s... (attempt {attempt + 1}/{max_retries})"
                    )
                    await asyncio.sleep(delay)
                    continue
                return None

            except Exception as e:
                self.logger.error(f"Request error: {e}")
                if attempt < max_retries - 1:
                    delay = self._calculate_retry_delay(attempt, 0, {})
                    self.logger.info(
                        f"Retrying in {delay}s... (attempt {attempt + 1}/{max_retries})"
                    )
                    await asyncio.sleep(delay)
                    continue
                return None

        return None

    async def fetch_ladder_players(
        self, tier: str, division: str = "I"
    ) -> List[Dict[str, Any]]:
        """
        Fetch players from ranked ladder.

        Args:
            tier: Tier name (e.g., CHALLENGER, MASTER, DIAMOND)
            division: Division within tier (I, II, III, IV) - not used for CHALLENGER/GRANDMASTER/MASTER

        Returns:
            List of player entries from ladder
        """
        queue = self.config["riot_api"]["queue_types"]["ranked_solo"]

        # CHALLENGER, GRANDMASTER, MASTER use different endpoints
        if tier == "CHALLENGER":
            url = self._build_url(EndpointKeys.CHALLENGER_LEAGUE, queue=queue)
        elif tier == "GRANDMASTER":
            url = self._build_url(EndpointKeys.GRANDMASTER_LEAGUE, queue=queue)
        elif tier == "MASTER":
            url = self._build_url(EndpointKeys.MASTER_LEAGUE, queue=queue)
        else:
            # DIAMOND and below use the standard entries endpoint
            url = self._build_url(
                EndpointKeys.LEAGUE_ENTRIES, queue=queue, tier=tier, division=division
            )

        self.logger.info(
            f"Fetching {tier} {division if tier not in ELITE_TIERS else ''} players..."
        )

        response = await self._make_request(url)

        # Special tiers return an object with 'entries' field
        if tier in ELITE_TIERS:
            if response and isinstance(response, dict) and "entries" in response:
                entries = response["entries"]
                self.logger.info(f"Found {len(entries)} players in {tier}")
                return entries
            else:
                self.logger.warning(f"No players found in {tier}")
                return []
        else:
            # DIAMOND and below return list directly
            if response and isinstance(response, list):
                self.logger.info(f"Found {len(response)} players in {tier} {division}")
                return response
            else:
                self.logger.warning(f"No players found in {tier} {division}")
                return []

    async def fetch_player_matches(self, puuid: str, count: int = 100) -> List[str]:
        """
        Fetch match IDs for a player.

        Args:
            puuid: Player PUUID
            count: Number of matches to fetch

        Returns:
            List of match IDs
        """
        # Use precomputed routing URL
        url = self._build_routing_url(f"/lol/match/v5/matches/by-puuid/{puuid}/ids")

        # Filter by queue (Ranked Solo/Duo only)
        queue_id = self.config["collection"]["queue_filter"]
        params = {"queue": queue_id, "count": count}

        match_ids = await self._make_request(url, params=params)

        if match_ids and isinstance(match_ids, list):
            return match_ids
        return []

    async def fetch_match_details(self, match_id: str) -> Optional[Dict]:
        """
        Fetch detailed match data with caching.

        Args:
            match_id: Match ID

        Returns:
            Match data dictionary or None
        """
        # Check cache first
        if self._match_cache:
            cached = self._match_cache.get(match_id)
            if cached is not None:
                self._cache_hits += 1
                return cached
            self._cache_misses += 1

        # Use precomputed routing URL
        url = self._build_routing_url(f"/lol/match/v5/matches/{match_id}")

        match_data = await self._make_request(url)

        if match_data:
            # Add collection metadata
            match_data["_collected_at"] = datetime.now(UTC).isoformat()
            match_data["_region"] = self.region

            # Cache the response
            if self._match_cache:
                self._match_cache.set(match_id, match_data)

            return match_data

        return None

    async def fetch_puuid_from_summoner_id(self, summoner_id: str) -> Optional[str]:
        """
        Convert summonerId to PUUID via Summoner-V4 API.

        Args:
            summoner_id: Summoner ID from league entries

        Returns:
            PUUID or None if failed
        """
        url = self._build_url(EndpointKeys.SUMMONER_BY_ID, summoner_id=summoner_id)
        summoner_data = await self._make_request(url)

        if summoner_data and "puuid" in summoner_data:
            return summoner_data["puuid"]
        return None

    async def step1_collect_ladder_players(self) -> Set[str]:
        """
        Step 1: Collect high-elo players from ranked ladder in parallel.

        Returns:
            Set of player PUUIDs
        """
        self.logger.info("=" * 60)
        self.logger.info("STEP 1: Collecting players from ranked ladder")
        self.logger.info("=" * 60)

        player_puuids: Set[str] = set()
        target_tiers = self.config["riot_api"]["target_tiers"]
        players_per_tier = self.config["collection"]["initial_players_per_tier"]

        # Fetch all tiers in parallel (improved efficiency)
        tasks = [self.fetch_ladder_players(tier) for tier in target_tiers]
        results = await asyncio.gather(*tasks)

        for entries in results:
            for entry in entries[:players_per_tier]:
                player_puuids.add(entry["puuid"])

        self.logger.info(f"Collected {len(player_puuids)} unique players from ladder")
        self.collected_players.update(player_puuids)

        return player_puuids

    async def step2_collect_player_matches(self, player_puuids: Set[str]) -> None:
        """
        Step 2: Collect match histories for players.

        Args:
            player_puuids: Set of player PUUIDs to collect matches from
        """
        self.logger.info("=" * 60)
        self.logger.info("STEP 2: Collecting matches from players")
        self.logger.info("=" * 60)

        matches_per_player = self.config["collection"]["matches_per_player"]
        max_total_matches = self.config["collection"]["max_total_matches"]

        # Create tasks for fetching match IDs
        async def fetch_player_match_ids(puuid: str):
            match_ids = await self.fetch_player_matches(puuid, count=matches_per_player)
            return match_ids

        # Fetch match IDs for all players with progress bar
        tasks = [fetch_player_match_ids(puuid) for puuid in player_puuids]

        all_match_ids = []
        # Process tasks as they complete
        for task in tqdm(
            asyncio.as_completed(tasks), total=len(tasks), desc="Fetching match lists"
        ):
            match_ids = await task
            all_match_ids.extend(match_ids)

            # Stop if we've reached the limit
            if len(self.collected_matches) >= max_total_matches:
                break

        # Deduplicate
        unique_match_ids = set(all_match_ids) - self.collected_matches
        unique_match_ids = list(unique_match_ids)[
            : max_total_matches - len(self.collected_matches)
        ]

        self.logger.info(f"Found {len(unique_match_ids)} new unique matches to collect")

        # Fetch match details with batch processing
        if self.config["performance"]["enable_batch_processing"]:
            await self._fetch_matches_batch(unique_match_ids, max_total_matches)
        else:
            # Legacy sequential processing
            for match_id in tqdm(unique_match_ids, desc="Fetching match details"):
                if len(self.collected_matches) >= max_total_matches:
                    break

                match_data = await self.fetch_match_details(match_id)

                if match_data:
                    self.match_data.append(match_data)
                    self.collected_matches.add(match_id)

        self.logger.info(f"Collected {len(self.collected_matches)} total matches")

    async def _fetch_matches_batch(
        self, match_ids: List[str], max_total_matches: int
    ) -> None:
        """
        Fetch match details in parallel batches with concurrency control.

        Args:
            match_ids: List of match IDs to fetch
            max_total_matches: Maximum total matches to collect
        """
        max_concurrent = self.config["performance"]["max_concurrent_requests"]
        semaphore = asyncio.Semaphore(max_concurrent)

        async def fetch_with_limit(match_id: str) -> tuple[str, Optional[Dict]]:
            """Fetch single match with semaphore limit."""
            async with semaphore:
                match_data = await self.fetch_match_details(match_id)
                return match_id, match_data

        # Create all tasks
        tasks = [fetch_with_limit(mid) for mid in match_ids]

        # Process with progress bar
        with tqdm(total=len(match_ids), desc="Fetching match details (batch)") as pbar:
            for coro in asyncio.as_completed(tasks):
                if len(self.collected_matches) >= max_total_matches:
                    break

                match_id, match_data = await coro

                if match_data:
                    self.match_data.append(match_data)
                    self.collected_matches.add(match_id)

                pbar.update(1)

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
        collection.setdefault('phase1_max_iterations', 5)
        collection.setdefault('phase1_players_per_iteration', 50)

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

        # Create tasks for all players
        tasks = [fetch_ids(puuid) for puuid in player_puuids]

        # Fetch in parallel
        all_ids = set()
        for task in asyncio.as_completed(tasks):
            match_ids = await task
            all_ids.update(match_ids)

        return all_ids

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
            self.logger.warning("Cache not enabled, cannot extract players")
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

    async def phase1_discover_and_cache(
        self,
        initial_players: Set[str],
        max_total_match_ids: int,
        max_iterations: int,
        players_per_iteration: int
    ) -> Dict[str, int]:
        """
        Discover match IDs through snowball iterations with progressive caching.

        Args:
            initial_players: Starting set of player PUUIDs
            max_total_match_ids: Stop after discovering this many unique IDs
            max_iterations: Maximum snowball iterations
            players_per_iteration: Players to process per iteration

        Returns:
            Dict with discovered_count, cached_count, iterations_used
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

        cached_count = len(self._match_cache._cache) if self._match_cache else 0

        self.logger.info("=" * 60)
        self.logger.info("PHASE 1 Complete")
        self.logger.info(f"  Discovered: {len(all_match_ids)} match IDs")
        self.logger.info(f"  Cached: {cached_count} matches")
        self.logger.info(f"  Iterations: {iteration}")
        self.logger.info("=" * 60)

        return {
            "discovered_count": len(all_match_ids),
            "cached_count": cached_count,
            "iterations_used": iteration
        }

    async def phase2_select_and_commit(
        self,
        max_matches: int
    ) -> Dict[str, any]:
        """
        Select best matches from cache and commit to collection.

        Args:
            max_matches: Maximum matches to commit

        Returns:
            Dict with committed_count, cache_hit_rate
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
        from collector.utils import sort_match_ids_by_recency
        sorted_ids = sort_match_ids_by_recency(cached_ids)

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

        # Calculate actual cache hit rate from Phase 1 lookups
        total_cache_lookups = self._cache_hits + self._cache_misses
        cache_hit_rate = (self._cache_hits / total_cache_lookups * 100) if total_cache_lookups > 0 else 0.0

        self.logger.info(f"  Cache hits during Phase 1: {self._cache_hits}")
        self.logger.info(f"  Cache misses during Phase 1: {self._cache_misses}")
        self.logger.info(f"  Cache hit rate: {cache_hit_rate:.1f}%")
        self.logger.info("=" * 60)

        return {
            "committed_count": committed_count,
            "cache_hit_rate": cache_hit_rate
        }

    async def step3_snowball_expansion(self, iterations: int = 3) -> None:
        """
        Step 3: Expand collection by discovering players in collected matches.

        Args:
            iterations: Number of snowball iterations
        """
        self.logger.info("=" * 60)
        self.logger.info("STEP 3: Snowball expansion via match participants")
        self.logger.info("=" * 60)

        max_total_matches = self.config["collection"]["max_total_matches"]
        max_iterations = min(iterations, self.config["collection"]["max_iterations"])

        for iteration in range(max_iterations):
            self.logger.info(f"Iteration {iteration + 1}/{max_iterations}")

            # Extract new player PUUIDs from collected matches
            # Only parse NEW matches since last iteration (incremental)
            new_players: Set[str] = set()

            # Process only matches added since last iteration
            for match in self.match_data[self.parsed_match_index :]:
                if "info" in match and "participants" in match["info"]:
                    for participant in match["info"]["participants"]:
                        if "puuid" in participant:
                            puuid = participant["puuid"]
                            if puuid not in self.collected_players:
                                new_players.add(puuid)

            # Update parsed index to current position
            self.parsed_match_index = len(self.match_data)

            if not new_players:
                self.logger.info("No new players found. Stopping expansion.")
                break

            self.logger.info(f"Discovered {len(new_players)} new players")

            # Limit new players to avoid explosion
            new_players = set(list(new_players)[:50])

            # Collect matches from new players
            await self.step2_collect_player_matches(new_players)

            # Update collected players
            self.collected_players.update(new_players)

            # Check if we've hit the match limit
            if len(self.collected_matches) >= max_total_matches:
                self.logger.info(
                    f"Reached max matches ({max_total_matches}). Stopping expansion."
                )
                break

    def save_to_bronze(self) -> str:
        """
        Save collected matches to Bronze layer as Parquet.

        Returns:
            Path to saved Parquet file
        """
        self.logger.info("=" * 60)
        self.logger.info("Saving to Bronze layer")
        self.logger.info("=" * 60)

        if not self.match_data:
            self.logger.warning("No data to save")
            return ""

        # Convert to DataFrame
        df = pd.DataFrame(self.match_data)

        # Extract patch version for partitioning using shared utility
        df["patch_version"] = df["info"].apply(
            lambda x: (
                extract_patch_version(x.get("gameVersion", ""))
                if isinstance(x, dict)
                else "unknown"
            )
        )
        df["region"] = self.region

        # Create filename with timestamp
        timestamp = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
        bronze_path = self.config["storage"]["bronze_path"]
        filename = f"{bronze_path}/matches_{self.region}_{timestamp}.parquet"

        # Save as Parquet
        df.to_parquet(
            filename, compression=self.config["storage"]["compression"], index=False
        )

        self.logger.info(f"Saved {len(df)} matches to {filename}")
        self.logger.info(f"Patches: {df['patch_version'].value_counts().to_dict()}")

        return filename

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

    async def run_collection(self) -> None:
        """Execute full three-step collection process with performance logging."""
        # Track start time
        self._start_time = time.monotonic()

        # Create session with default headers
        headers = {"X-Riot-Token": self.config["riot_api"]["api_key"]}
        async with aiohttp.ClientSession(headers=headers) as session:
            self.session = session

            try:
                # Step 1: Collect ladder players
                player_puuids = await self.step1_collect_ladder_players()

                # Phase 1: Discover and cache match IDs
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
                self.logger.info(f"Phase 1 - Discovered: {phase1_result['discovered_count']} IDs")
                self.logger.info(f"Phase 1 - Cached: {phase1_result['cached_count']} matches")
                self.logger.info(f"Phase 1 - Iterations: {phase1_result['iterations_used']}")
                self.logger.info(f"Phase 2 - Committed: {phase2_result['committed_count']} matches")
                self.logger.info(f"Phase 2 - Cache hit rate: {phase2_result['cache_hit_rate']:.1f}%")
                self.logger.info(f"Total players: {len(self.collected_players)}")
                self.logger.info(f"Output: {output_file}")
                self.logger.info("=" * 60)

                # Log performance summary
                self._log_performance_summary()

            except Exception as e:
                self.logger.error(f"Collection failed: {e}", exc_info=True)
                raise


async def main():
    """Main entry point."""
    collector = RiotAPICollector()
    await collector.run_collection()


if __name__ == "__main__":
    asyncio.run(main())
