"""
Shared utilities for the collector module.

Provides common functionality for config loading, logging,
and data transformations used across the project.
"""

import logging
import os
import re
from pathlib import Path
from typing import Any, Dict

import yaml


# Constants for tier classification
ELITE_TIERS = {"CHALLENGER", "GRANDMASTER", "MASTER"}


class EndpointKeys:
    """Constants for API endpoint keys to avoid stringly-typed code."""

    CHALLENGER_LEAGUE = "challenger_league"
    GRANDMASTER_LEAGUE = "grandmaster_league"
    MASTER_LEAGUE = "master_league"
    LEAGUE_ENTRIES = "league_entries"
    SUMMONER_BY_ID = "summoner_by_id"


def load_config_with_env_vars(config_path: str) -> Dict[str, Any]:
    """
    Load YAML config and replace environment variable placeholders.

    Supports ${VAR_NAME} syntax in config files.

    Args:
        config_path: Path to YAML configuration file

    Returns:
        Parsed configuration dictionary

    Raises:
        ValueError: If required environment variable is not found
        FileNotFoundError: If config file doesn't exist
    """
    with open(config_path) as f:
        config_str = f.read()

    # Replace all ${VAR_NAME} placeholders with environment variables
    for match in re.finditer(r'\$\{([^}]+)\}', config_str):
        var_name = match.group(1)
        value = os.getenv(var_name)
        if not value:
            raise ValueError(f"{var_name} not found in environment variables")
        config_str = config_str.replace(match.group(0), value)

    return yaml.safe_load(config_str)


def setup_logger(name: str, config: Dict[str, Any]) -> logging.Logger:
    """
    Set up logger with configuration.

    Args:
        name: Logger name (typically __name__)
        config: Logging configuration dict

    Returns:
        Configured logger instance
    """
    log_config = config["logging"]

    # Create logs directory if needed
    log_file = Path(log_config["file"])
    log_file.parent.mkdir(parents=True, exist_ok=True)

    # Configure logging (only once)
    if not logging.getLogger().handlers:
        logging.basicConfig(
            level=getattr(logging, log_config["level"]),
            format=log_config["format"],
            handlers=[
                logging.FileHandler(log_config["file"]),
                logging.StreamHandler()
            ],
        )

    return logging.getLogger(name)


def extract_patch_version(game_version: str) -> str:
    """
    Extract major.minor patch version from full game version string.

    Args:
        game_version: Full version string (e.g., "14.5.123.456")

    Returns:
        Patch version (e.g., "14.5") or "unknown" if invalid
    """
    if not isinstance(game_version, str):
        return "unknown"
    parts = game_version.split(".")
    if len(parts) >= 2:
        return f"{parts[0]}.{parts[1]}"
    return "unknown"


def decode_match_timestamp(match_id: str) -> int:
    """
    Decode timestamp from Riot match ID.

    Riot match v5 IDs encode timestamps that can be extracted for sorting.
    Format: {region}_{timestamp}_{increment} where timestamp is in milliseconds.

    Args:
        match_id: Match ID (e.g., "BR1_1234567890_1")

    Returns:
        Unix timestamp in milliseconds, or 0 if decoding fails
    """
    try:
        # Match IDs from Riot API are in readable format
        # Format: BR1_1234567890_1 or similar
        parts = match_id.split('_')

        if len(parts) >= 2:
            # Second part is the timestamp
            return int(parts[1])

        # Fallback: return 0 (oldest possible)
        return 0

    except (ValueError, IndexError):
        # Invalid format, return oldest possible
        return 0


def sort_match_ids_by_recency(match_ids: list) -> list:
    """
    Sort match IDs by recency (most recent first).

    Args:
        match_ids: List of match IDs

    Returns:
        Sorted list (most recent first)
    """
    return sorted(
        match_ids,
        key=lambda mid: decode_match_timestamp(mid),
        reverse=True
    )
