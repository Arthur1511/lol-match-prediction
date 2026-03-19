"""
Token Bucket Rate Limiter for Riot API.

Implements async rate limiting to respect Riot's API constraints:
- 20 requests per second
- 100 requests per 2 minutes

Uses token bucket algorithm with async/await support.
"""

import asyncio
import time
from collections import OrderedDict, deque
from typing import Any, Dict, Optional, Tuple


class RateLimiter:
    """
    Async token bucket rate limiter.

    Supports multiple rate limits simultaneously (per-second and per-2min).
    Thread-safe for async operations.
    """

    def __init__(self, requests_per_second: int = 20, requests_per_2min: int = 100):
        """
        Initialize rate limiter with two token buckets.

        Args:
            requests_per_second: Maximum requests allowed per second
            requests_per_2min: Maximum requests allowed per 2 minutes
        """
        self.requests_per_second = requests_per_second
        self.requests_per_2min = requests_per_2min

        # Token buckets
        self.tokens_1s = requests_per_second
        self.tokens_2m = requests_per_2min

        # Timestamps
        self.last_update_1s = time.monotonic()
        self.last_update_2m = time.monotonic()

        # Request history for 2-minute window
        self.request_history: deque[float] = deque()

        # Lock for async safety
        self._lock = asyncio.Lock()

    def _refill_tokens(self) -> None:
        """Refill token buckets based on elapsed time."""
        now = time.monotonic()

        # Refill 1-second bucket
        elapsed_1s = now - self.last_update_1s
        if elapsed_1s >= 1.0:
            self.tokens_1s = self.requests_per_second
            self.last_update_1s = now

        # Clean up old requests from 2-minute window
        cutoff_time = now - 120.0  # 2 minutes
        while self.request_history and self.request_history[0] < cutoff_time:
            self.request_history.popleft()

        self.tokens_2m = self.requests_per_2min - len(self.request_history)

    def _calculate_wait_time(self) -> float:
        """
        Calculate how long to wait before next request is allowed.

        Returns:
            Wait time in seconds (0 if request can proceed immediately)
        """
        self._refill_tokens()

        # Check 1-second limit
        if self.tokens_1s <= 0:
            now = time.monotonic()
            wait_1s = 1.0 - (now - self.last_update_1s)
            wait_1s = max(0, wait_1s)
        else:
            wait_1s = 0

        # Check 2-minute limit
        if self.tokens_2m <= 0:
            # Need to wait until oldest request expires
            now = time.monotonic()
            oldest_request = self.request_history[0]
            wait_2m = 120.0 - (now - oldest_request)
            wait_2m = max(0, wait_2m)
        else:
            wait_2m = 0

        return max(wait_1s, wait_2m)

    async def acquire(self) -> None:
        """
        Acquire permission to make a request.

        Blocks until rate limit allows the request to proceed.
        Call this before each API request:

        Example:
            async with rate_limiter.acquire():
                response = await session.get(url)
        """
        async with self._lock:
            wait_time = self._calculate_wait_time()

            if wait_time > 0:
                await asyncio.sleep(wait_time)
                # Refill after waiting
                self._refill_tokens()

            # Consume tokens
            self.tokens_1s -= 1
            now = time.monotonic()
            self.request_history.append(now)
            self.tokens_2m -= 1

    def get_stats(self) -> Tuple[int, int]:
        """
        Get current token availability.

        Returns:
            Tuple of (tokens_available_1s, tokens_available_2m)
        """
        self._refill_tokens()
        return (self.tokens_1s, self.tokens_2m)

    async def __aenter__(self):
        """Support async context manager usage."""
        await self.acquire()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        """Support async context manager usage."""
        pass


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
