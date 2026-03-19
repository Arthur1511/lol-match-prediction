"""
Collector module for Riot API interaction.

This module handles all API calls to Riot Games for match and player data.
No data transformation should occur here - only API interaction.
"""

from collector.rate_limiter import RateLimiter
from collector.riot_api_collector import RiotAPICollector
from collector.utils import (
    ELITE_TIERS,
    EndpointKeys,
    extract_patch_version,
    load_config_with_env_vars,
    setup_logger,
)

__all__ = [
    "RiotAPICollector",
    "RateLimiter",
    "ELITE_TIERS",
    "EndpointKeys",
    "extract_patch_version",
    "load_config_with_env_vars",
    "setup_logger",
]

__version__ = "1.0.0"
