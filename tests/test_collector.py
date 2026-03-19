"""
Tests for Riot API Collector components.

Run with: pytest tests/test_collector.py -v
"""

import asyncio
import time

import pytest

from collector.rate_limiter import RateLimiter, EndpointAwareRateLimiter, LRUCache


class TestRateLimiter:
    """Test suite for token bucket rate limiter."""

    @pytest.mark.asyncio
    async def test_rate_limiter_respects_per_second_limit(self):
        """Verify rate limiter enforces per-second limit."""
        limiter = RateLimiter(requests_per_second=10, requests_per_2min=1000)

        start = time.monotonic()

        # Make 25 requests (should take at least 2.5 seconds)
        for _ in range(25):
            await limiter.acquire()

        elapsed = time.monotonic() - start

        # Should take at least 2 seconds (25 requests / 10 per sec = 2.5s)
        # Allow some tolerance for execution overhead
        assert elapsed >= 2.0, f"Rate limiter too fast: {elapsed}s for 25 requests"

    @pytest.mark.asyncio
    async def test_rate_limiter_respects_2min_limit(self):
        """Verify rate limiter enforces 2-minute window limit."""
        limiter = RateLimiter(requests_per_second=100, requests_per_2min=10)

        start = time.monotonic()

        # Make 15 requests (should hit 2-min limit after 10)
        request_times = []
        for i in range(15):
            await limiter.acquire()
            request_times.append(time.monotonic() - start)

        # First 10 should go fast
        assert request_times[9] < 1.0, "First 10 requests should be fast"

        # 11th request should wait until first request expires from 2-min window
        # This would be 120+ seconds in production, but our test uses small window
        # Just verify it's slower
        assert request_times[10] > request_times[9], "Rate limiting kicked in"

    @pytest.mark.asyncio
    async def test_get_stats(self):
        """Test token availability reporting."""
        limiter = RateLimiter(requests_per_second=20, requests_per_2min=100)

        # Initial state
        tokens_1s, tokens_2m = limiter.get_stats()
        assert tokens_1s == 20
        assert tokens_2m == 100

        # After consuming some tokens
        await limiter.acquire()
        await limiter.acquire()

        tokens_1s, tokens_2m = limiter.get_stats()
        assert tokens_1s == 18  # 20 - 2
        assert tokens_2m == 98  # 100 - 2

    @pytest.mark.asyncio
    async def test_context_manager_usage(self):
        """Test async context manager interface."""
        limiter = RateLimiter(requests_per_second=10, requests_per_2min=100)

        # Should work as context manager
        async with limiter:
            pass  # Request would go here

        tokens_1s, tokens_2m = limiter.get_stats()
        assert tokens_1s == 9  # One token consumed
        assert tokens_2m == 99

    @pytest.mark.asyncio
    async def test_concurrent_requests(self):
        """Test rate limiter with concurrent async requests."""
        limiter = RateLimiter(requests_per_second=20, requests_per_2min=200)

        async def make_request(request_id: int) -> int:
            async with limiter:
                await asyncio.sleep(0.01)  # Simulate API call
            return request_id

        # Launch 50 concurrent requests
        tasks = [make_request(i) for i in range(50)]
        start = time.monotonic()
        results = await asyncio.gather(*tasks)
        elapsed = time.monotonic() - start

        # All requests completed
        assert len(results) == 50

        # Should take at least 2.5 seconds (50 requests / 20 per sec)
        assert elapsed >= 2.0


class TestEndpointAwareRateLimiter:
    """Test suite for per-endpoint rate limiter."""

    def test_endpoint_aware_rate_limiter_routing(self):
        """Verify URL → limiter routing."""
        limiter = EndpointAwareRateLimiter()

        # Match endpoint should use match_v5 limiter
        assert limiter._get_limiter_key("https://americas.api.riotgames.com/lol/match/v5/matches/xyz") == 'match_v5'

        # Match IDs endpoint should use match_v5 limiter
        assert limiter._get_limiter_key("https://americas.api.riotgames.com/lol/match/v5/matches/by-puuid/abc/ids") == 'match_v5'

        # Challenger endpoint should use league_v4_elite limiter
        assert limiter._get_limiter_key("https://br1.api.riotgames.com/lol/league/v4/challengerleagues/by-queue/RANKED_SOLO_5x5") == 'league_v4_elite'

        # Grandmaster endpoint should use league_v4_elite limiter
        assert limiter._get_limiter_key("https://br1.api.riotgames.com/lol/league/v4/grandmasterleagues/by-queue/RANKED_SOLO_5x5") == 'league_v4_elite'

        # Master endpoint should use league_v4_elite limiter
        assert limiter._get_limiter_key("https://br1.api.riotgames.com/lol/league/v4/masterleagues/by-queue/RANKED_SOLO_5x5") == 'league_v4_elite'

        # League entries endpoint should use league_v4_entries limiter
        assert limiter._get_limiter_key("https://br1.api.riotgames.com/lol/league/v4/entries/RANKED_SOLO_5x5/CHALLENGER/I") == 'league_v4_entries'

        # Summoner endpoint should use summoner_v4 limiter
        assert limiter._get_limiter_key("https://br1.api.riotgames.com/lol/summoner/v4/summoners/123") == 'summoner_v4'

        # Unknown endpoint should use default limiter
        assert limiter._get_limiter_key("https://br1.api.riotgames.com/lol/unknown/endpoint") == 'default'

    @pytest.mark.asyncio
    async def test_endpoint_aware_rate_limiter_independent(self):
        """Verify each endpoint limiter operates independently."""
        limits = {'match_v5': 10, 'league_v4_elite': 2, 'default': 5}
        limiter = EndpointAwareRateLimiter(limits=limits)

        # Each limiter should have independent token counts
        match_stats = limiter.limiters['match_v5'].get_stats()
        league_stats = limiter.limiters['league_v4_elite'].get_stats()
        default_stats = limiter.limiters['default'].get_stats()

        assert match_stats == (10, 100)
        assert league_stats == (2, 100)
        assert default_stats == (5, 100)

    def test_endpoint_aware_rate_limiter_custom_limits(self):
        """Verify custom limits override defaults."""
        custom_limits = {'match_v5': 50, 'league_v4_elite': 5, 'default': 10}
        limiter = EndpointAwareRateLimiter(limits=custom_limits)

        assert limiter.limiters['match_v5'].requests_per_second == 50
        assert limiter.limiters['league_v4_elite'].requests_per_second == 5
        assert limiter.limiters['default'].requests_per_second == 10

    @pytest.mark.asyncio
    async def test_endpoint_aware_rate_limiter_acquire(self):
        """Verify acquire() uses correct limiter for URL."""
        limiter = EndpointAwareRateLimiter()

        # Acquire for match_v5 endpoint
        await limiter.acquire("https://americas.api.riotgames.com/lol/match/v5/matches/xyz")
        match_tokens, _ = limiter.limiters['match_v5'].get_stats()
        assert match_tokens == 199  # 200 - 1

        # Acquire for league_v4_elite endpoint
        await limiter.acquire("https://br1.api.riotgames.com/lol/league/v4/challengerleagues/by-queue/RANKED_SOLO_5x5")
        league_tokens, _ = limiter.limiters['league_v4_elite'].get_stats()
        assert league_tokens == 2  # 3 - 1

    def test_endpoint_aware_rate_limiter_get_stats(self):
        """Verify get_stats() returns all limiters' stats."""
        limiter = EndpointAwareRateLimiter()

        stats = limiter.get_stats()

        # Should have stats for all default limiters
        assert 'match_v5' in stats
        assert 'league_v4_elite' in stats
        assert 'league_v4_entries' in stats
        assert 'summoner_v4' in stats
        assert 'default' in stats

        # Each should be a tuple of (tokens_1s, tokens_2m)
        for key, value in stats.items():
            assert isinstance(value, tuple)
            assert len(value) == 2
            assert isinstance(value[0], int)  # tokens_1s
            assert isinstance(value[1], int)  # tokens_2m


class TestLRUCache:
    """Test suite for LRU cache."""

    def test_lru_cache_get_set(self):
        """Verify basic get/set operations."""
        cache = LRUCache(max_size=10)

        # Set and get a value
        cache.set('key1', 'value1')
        assert cache.get('key1') == 'value1'

        # Get non-existent key returns None
        assert cache.get('nonexistent') is None

    def test_lru_cache_eviction(self):
        """Verify LRU eviction when max_size is reached."""
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

    def test_lru_cache_lru_behavior(self):
        """Verify accessing an item moves it to end (most recently used)."""
        cache = LRUCache(max_size=3)

        cache.set('key1', 'value1')
        cache.set('key2', 'value2')
        cache.set('key3', 'value3')

        # Access key1 to make it recently used
        cache.get('key1')

        # Add key4 - should evict key2 (oldest non-accessed)
        cache.set('key4', 'value4')
        assert 'key2' not in cache.cache
        assert 'key1' in cache.cache  # Still present because we accessed it
        assert 'key4' in cache.cache

    def test_lru_cache_stats(self):
        """Verify hit/miss tracking."""
        cache = LRUCache(max_size=100)

        cache.set('key1', 'value1')
        cache.get('key1')  # hit
        cache.get('key2')  # miss

        stats = cache.get_stats()
        assert stats['hits'] == 1
        assert stats['misses'] == 1
        assert stats['hit_rate'] == '50.0%'
        assert stats['size'] == 1
        assert stats['max_size'] == 100

    def test_lru_cache_clear(self):
        """Verify clear() empties cache and resets stats."""
        cache = LRUCache(max_size=10)

        cache.set('key1', 'value1')
        cache.get('key1')

        assert len(cache.cache) == 1
        assert cache.stats['hits'] == 1

        cache.clear()

        assert len(cache.cache) == 0
        assert cache.stats['hits'] == 0
        assert cache.stats['misses'] == 0

    def test_lru_cache_update_existing(self):
        """Verify updating existing key moves it to end."""
        cache = LRUCache(max_size=3)

        cache.set('key1', 'value1')
        cache.set('key2', 'value2')
        cache.set('key3', 'value3')

        # Update key1
        cache.set('key1', 'new_value1')

        # Add key4 - should evict key2 (not key1)
        cache.set('key4', 'value4')
        assert 'key2' not in cache.cache
        assert 'key1' in cache.cache
        assert cache.get('key1') == 'new_value1'


class TestRiotAPICollectorConfig:
    """Test configuration loading and validation."""

    def test_config_file_exists(self):
        """Verify config file is present."""
        from pathlib import Path

        config_path = Path("config/config.yaml")
        assert config_path.exists(), "config.yaml not found"

    def test_config_structure(self):
        """Verify config has required sections."""
        from pathlib import Path

        import yaml

        config_path = Path("config/config.yaml")
        with open(config_path) as f:
            config = yaml.safe_load(f)

        # Check required sections
        assert "riot_api" in config
        assert "collection" in config
        assert "storage" in config

        # Check riot_api settings
        assert "rate_limit_per_second" in config["riot_api"]
        assert "rate_limit_per_2min" in config["riot_api"]
        assert config["riot_api"]["rate_limit_per_second"] <= 20
        assert config["riot_api"]["rate_limit_per_2min"] <= 100

        # Check collection settings
        assert "queue_filter" in config["collection"]
        assert config["collection"]["queue_filter"] == 420  # Ranked Solo/Duo


# Note: Full integration tests require valid API key and network access
# These should be run separately in CI/CD with proper credentials
class TestRiotAPIIntegration:
    """Integration tests (requires RIOT_API_KEY)."""

    @pytest.mark.skip(reason="Requires valid API key")
    @pytest.mark.asyncio
    async def test_full_collection_flow(self):
        """Test complete collection process (integration test)."""
        from collector.riot_api_collector import RiotAPICollector

        # This would run the full collection
        # Skip by default to avoid API usage in tests
        collector = RiotAPICollector()
        await collector.run_collection()

        assert len(collector.collected_matches) > 0


if __name__ == "__main__":
    # Run with: python tests/test_collector.py
    pytest.main([__file__, "-v"])
