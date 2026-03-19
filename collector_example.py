"""
Example: Using the Riot API Collector

This script demonstrates how to use the collector with custom configuration.
"""

import asyncio
import sys
from pathlib import Path
from collector.riot_api_collector import RiotAPICollector


async def example_basic_collection():
    """Basic collection example - uses default config."""
    print("=" * 60)
    print("Example 1: Basic Collection")
    print("=" * 60)

    collector = RiotAPICollector()
    await collector.run_collection()


async def example_custom_configuration():
    """Example with runtime configuration override."""
    print("=" * 60)
    print("Example 2: Custom Configuration")
    print("=" * 60)

    collector = RiotAPICollector()

    # Override collection settings for testing
    collector.config["collection"]["max_total_matches"] = 100
    collector.config["collection"]["initial_players_per_tier"] = 10
    collector.config["collection"]["max_iterations"] = 1

    print(f"Collection settings:")
    print(f"  Max matches: {collector.config['collection']['max_total_matches']}")
    print(
        f"  Players per tier: {collector.config['collection']['initial_players_per_tier']}"
    )
    print(f"  Snowball iterations: {collector.config['collection']['max_iterations']}")
    print()

    await collector.run_collection()


async def example_step_by_step():
    """Run collection steps individually for more control."""
    print("=" * 60)
    print("Example 3: Step-by-Step Collection")
    print("=" * 60)

    import aiohttp

    collector = RiotAPICollector()

    # Manually control each step with proper headers
    headers = {"X-Riot-Token": collector.api_key}
    async with aiohttp.ClientSession(headers=headers) as session:
        collector.session = session

        # Step 1: Get high-elo players
        print("\n[Step 1] Collecting ladder players...")
        player_puuids = await collector.step1_collect_ladder_players()
        print(f"✓ Found {len(player_puuids)} players")

        # Step 2: Get their matches (limit for demo)
        print("\n[Step 2] Collecting player matches...")
        limited_players = set(list(player_puuids)[:5])  # Just 5 players for demo
        await collector.step2_collect_player_matches(limited_players)
        print(f"✓ Collected {len(collector.collected_matches)} matches")

        # Step 3: Snowball (1 iteration only)
        print("\n[Step 3] Snowball expansion...")
        await collector.step3_snowball_expansion(iterations=1)
        print(f"✓ Total matches: {len(collector.collected_matches)}")

        # Save results
        print("\n[Saving] Writing to Bronze layer...")
        output_file = collector.save_to_bronze()
        print(f"✓ Saved to {output_file}")


async def example_rate_limiter_monitoring():
    """Monitor rate limiter status during collection."""
    print("=" * 60)
    print("Example 4: Rate Limiter Monitoring")
    print("=" * 60)

    from collector.rate_limiter import RateLimiter

    limiter = RateLimiter(requests_per_second=20, requests_per_2min=100)

    print("Making 30 requests with rate limiting...\n")

    for i in range(30):
        async with limiter:
            # Print stats every 5 requests
            if (i + 1) % 5 == 0:
                tokens_1s, tokens_2m = limiter.get_stats()
                print(
                    f"Request {i + 1:2d} | Tokens: 1s={tokens_1s:2d}/20 | 2m={tokens_2m:3d}/100"
                )

    print("\n✓ All requests completed with rate limiting")


async def main():
    """Run examples."""
    import sys

    if len(sys.argv) > 1:
        example = sys.argv[1]

        if example == "basic":
            await example_basic_collection()
        elif example == "custom":
            await example_custom_configuration()
        elif example == "step":
            await example_step_by_step()
        elif example == "rate":
            await example_rate_limiter_monitoring()
        else:
            print(f"Unknown example: {example}")
            print("Available: basic, custom, step, rate")
    else:
        print("Usage: python examples/collector_example.py <example>")
        print()
        print("Available examples:")
        print("  basic   - Basic collection with default settings")
        print("  custom  - Collection with custom configuration")
        print("  step    - Step-by-step collection with manual control")
        print("  rate    - Rate limiter monitoring demo")
        print()
        print("Example:")
        print("  uv run python examples/collector_example.py rate")


if __name__ == "__main__":
    asyncio.run(main())
