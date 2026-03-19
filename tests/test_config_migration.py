"""
Tests for configuration migration and backward compatibility.

Tests legacy config key migration to new two-phase structure.
"""

import os
import tempfile
from pathlib import Path

import pytest
import yaml

from collector.riot_api_collector import RiotAPICollector


class TestConfigMigration:
    """Test configuration migration logic."""

    def test_migrate_max_total_matches_to_phase2(self):
        """Test migration of max_total_matches → phase2_max_matches."""
        # Create temporary config with legacy key
        with tempfile.NamedTemporaryFile(mode='w', suffix='.yaml', delete=False) as f:
            config = {
                'riot_api': {
                    'api_key': 'test_key',
                    'rate_limit_per_second': 20,
                    'rate_limit_per_2min': 100,
                    'default_region': 'br1',
                    'regions': ['br1'],
                    'region_routing': {'br1': 'americas'},
                    'endpoints': {},
                    'queue_types': {'ranked_solo': 'RANKED_SOLO_5x5'},
                    'target_tiers': [],
                    'timeout_seconds': 30,
                    'max_retries': 3,
                },
                'collection': {
                    'max_total_matches': 5000,  # Legacy key
                    'matches_per_player': 100,
                    'queue_filter': 420,
                },
                'performance': {
                    'enable_response_cache': True,
                    'cache_max_size': 100,
                },
                'logging': {
                    'level': 'INFO',
                    'format': '%(message)s',
                    'file': 'test.log',
                },
                'storage': {
                    'bronze_path': 'test_bronze',
                    'landing_path': 'test_landing',
                    'compression': 'snappy',
                },
            }
            yaml.dump(config, f)
            temp_path = f.name

        try:
            # Set API key env var
            old_env = os.environ.get('RIOT_API_KEY')
            os.environ['RIOT_API_KEY'] = 'test_key'

            collector = RiotAPICollector(temp_path)

            # Verify migration occurred
            assert collector.config['collection']['phase2_max_matches'] == 5000
            assert collector.config['collection']['max_total_matches'] == 5000  # Original preserved

            if old_env:
                os.environ['RIOT_API_KEY'] = old_env
            else:
                os.environ.pop('RIOT_API_KEY', None)
        finally:
            os.unlink(temp_path)

    def test_migrate_max_iterations_to_phase1(self):
        """Test migration of max_iterations → phase1_max_iterations."""
        with tempfile.NamedTemporaryFile(mode='w', suffix='.yaml', delete=False) as f:
            config = {
                'riot_api': {
                    'api_key': 'test_key',
                    'rate_limit_per_second': 20,
                    'rate_limit_per_2min': 100,
                    'default_region': 'br1',
                    'regions': ['br1'],
                    'region_routing': {'br1': 'americas'},
                    'endpoints': {},
                    'queue_types': {'ranked_solo': 'RANKED_SOLO_5x5'},
                    'target_tiers': [],
                    'timeout_seconds': 30,
                    'max_retries': 3,
                },
                'collection': {
                    'max_iterations': 7,  # Legacy key
                    'matches_per_player': 100,
                    'queue_filter': 420,
                },
                'performance': {
                    'enable_response_cache': True,
                    'cache_max_size': 100,
                },
                'logging': {
                    'level': 'INFO',
                    'format': '%(message)s',
                    'file': 'test.log',
                },
                'storage': {
                    'bronze_path': 'test_bronze',
                    'landing_path': 'test_landing',
                    'compression': 'snappy',
                },
            }
            yaml.dump(config, f)
            temp_path = f.name

        try:
            old_env = os.environ.get('RIOT_API_KEY')
            os.environ['RIOT_API_KEY'] = 'test_key'

            collector = RiotAPICollector(temp_path)

            # Verify migration occurred
            assert collector.config['collection']['phase1_max_iterations'] == 7
            assert collector.config['collection']['max_iterations'] == 7  # Original preserved

            if old_env:
                os.environ['RIOT_API_KEY'] = old_env
            else:
                os.environ.pop('RIOT_API_KEY', None)
        finally:
            os.unlink(temp_path)

    def test_migrate_both_legacy_keys(self):
        """Test migration when both legacy keys present."""
        with tempfile.NamedTemporaryFile(mode='w', suffix='.yaml', delete=False) as f:
            config = {
                'riot_api': {
                    'api_key': 'test_key',
                    'rate_limit_per_second': 20,
                    'rate_limit_per_2min': 100,
                    'default_region': 'br1',
                    'regions': ['br1'],
                    'region_routing': {'br1': 'americas'},
                    'endpoints': {},
                    'queue_types': {'ranked_solo': 'RANKED_SOLO_5x5'},
                    'target_tiers': [],
                    'timeout_seconds': 30,
                    'max_retries': 3,
                },
                'collection': {
                    'max_total_matches': 8000,  # Legacy key
                    'max_iterations': 4,  # Legacy key
                    'matches_per_player': 100,
                    'queue_filter': 420,
                },
                'performance': {
                    'enable_response_cache': True,
                    'cache_max_size': 100,
                },
                'logging': {
                    'level': 'INFO',
                    'format': '%(message)s',
                    'file': 'test.log',
                },
                'storage': {
                    'bronze_path': 'test_bronze',
                    'landing_path': 'test_landing',
                    'compression': 'snappy',
                },
            }
            yaml.dump(config, f)
            temp_path = f.name

        try:
            old_env = os.environ.get('RIOT_API_KEY')
            os.environ['RIOT_API_KEY'] = 'test_key'

            collector = RiotAPICollector(temp_path)

            # Verify both migrations occurred
            assert collector.config['collection']['phase2_max_matches'] == 8000
            assert collector.config['collection']['phase1_max_iterations'] == 4

            if old_env:
                os.environ['RIOT_API_KEY'] = old_env
            else:
                os.environ.pop('RIOT_API_KEY', None)
        finally:
            os.unlink(temp_path)

    def test_no_migration_when_new_keys_present(self):
        """Test no migration when new keys already present."""
        with tempfile.NamedTemporaryFile(mode='w', suffix='.yaml', delete=False) as f:
            config = {
                'riot_api': {
                    'api_key': 'test_key',
                    'rate_limit_per_second': 20,
                    'rate_limit_per_2min': 100,
                    'default_region': 'br1',
                    'regions': ['br1'],
                    'region_routing': {'br1': 'americas'},
                    'endpoints': {},
                    'queue_types': {'ranked_solo': 'RANKED_SOLO_5x5'},
                    'target_tiers': [],
                    'timeout_seconds': 30,
                    'max_retries': 3,
                },
                'collection': {
                    'phase1_max_match_ids': 30000,
                    'phase1_max_iterations': 2,
                    'phase1_players_per_iteration': 40,
                    'phase2_max_matches': 5000,
                    'matches_per_player': 100,
                    'queue_filter': 420,
                },
                'performance': {
                    'enable_response_cache': True,
                    'cache_max_size': 100,
                },
                'logging': {
                    'level': 'INFO',
                    'format': '%(message)s',
                    'file': 'test.log',
                },
                'storage': {
                    'bronze_path': 'test_bronze',
                    'landing_path': 'test_landing',
                    'compression': 'snappy',
                },
            }
            yaml.dump(config, f)
            temp_path = f.name

        try:
            old_env = os.environ.get('RIOT_API_KEY')
            os.environ['RIOT_API_KEY'] = 'test_key'

            collector = RiotAPICollector(temp_path)

            # Verify new keys are used as-is
            assert collector.config['collection']['phase1_max_match_ids'] == 30000
            assert collector.config['collection']['phase1_max_iterations'] == 2
            assert collector.config['collection']['phase2_max_matches'] == 5000

            if old_env:
                os.environ['RIOT_API_KEY'] = old_env
            else:
                os.environ.pop('RIOT_API_KEY', None)
        finally:
            os.unlink(temp_path)

    def test_default_values_when_no_keys_present(self):
        """Test default values set when no keys present."""
        with tempfile.NamedTemporaryFile(mode='w', suffix='.yaml', delete=False) as f:
            config = {
                'riot_api': {
                    'api_key': 'test_key',
                    'rate_limit_per_second': 20,
                    'rate_limit_per_2min': 100,
                    'default_region': 'br1',
                    'regions': ['br1'],
                    'region_routing': {'br1': 'americas'},
                    'endpoints': {},
                    'queue_types': {'ranked_solo': 'RANKED_SOLO_5x5'},
                    'target_tiers': [],
                    'timeout_seconds': 30,
                    'max_retries': 3,
                },
                'collection': {
                    'matches_per_player': 100,
                    'queue_filter': 420,
                    # No phase1/phase2 keys, no legacy keys
                },
                'performance': {
                    'enable_response_cache': True,
                    'cache_max_size': 100,
                },
                'logging': {
                    'level': 'INFO',
                    'format': '%(message)s',
                    'file': 'test.log',
                },
                'storage': {
                    'bronze_path': 'test_bronze',
                    'landing_path': 'test_landing',
                    'compression': 'snappy',
                },
            }
            yaml.dump(config, f)
            temp_path = f.name

        try:
            old_env = os.environ.get('RIOT_API_KEY')
            os.environ['RIOT_API_KEY'] = 'test_key'

            collector = RiotAPICollector(temp_path)

            # Verify defaults are set
            assert collector.config['collection']['phase1_max_match_ids'] == 50000
            assert collector.config['collection']['phase1_max_iterations'] == 5
            assert collector.config['collection']['phase1_players_per_iteration'] == 50

            if old_env:
                os.environ['RIOT_API_KEY'] = old_env
            else:
                os.environ.pop('RIOT_API_KEY', None)
        finally:
            os.unlink(temp_path)


class TestBackwardCompatibility:
    """Test backward compatibility with legacy configs."""

    @pytest.mark.asyncio
    async def test_legacy_config_works_without_modification(self):
        """Test old config.yaml format still works."""
        # Test that collector can be instantiated with legacy config
        with tempfile.NamedTemporaryFile(mode='w', suffix='.yaml', delete=False) as f:
            config = {
                'riot_api': {
                    'api_key': 'test_key',
                    'rate_limit_per_second': 20,
                    'rate_limit_per_2min': 100,
                    'default_region': 'br1',
                    'regions': ['br1'],
                    'region_routing': {'br1': 'americas'},
                    'endpoints': {},
                    'queue_types': {'ranked_solo': 'RANKED_SOLO_5x5'},
                    'target_tiers': [],
                    'timeout_seconds': 30,
                    'max_retries': 3,
                },
                'collection': {
                    'max_total_matches': 10000,
                    'max_iterations': 5,
                    'matches_per_player': 100,
                    'queue_filter': 420,
                },
                'performance': {
                    'enable_response_cache': True,
                    'cache_max_size': 100,
                },
                'logging': {
                    'level': 'INFO',
                    'format': '%(message)s',
                    'file': 'test.log',
                },
                'storage': {
                    'bronze_path': 'test_bronze',
                    'landing_path': 'test_landing',
                    'compression': 'snappy',
                },
            }
            yaml.dump(config, f)
            temp_path = f.name

        try:
            old_env = os.environ.get('RIOT_API_KEY')
            os.environ['RIOT_API_KEY'] = 'test_key'

            # Should not raise any errors
            collector = RiotAPICollector(temp_path)

            # Should have migrated config
            assert 'phase2_max_matches' in collector.config['collection']
            assert 'phase1_max_iterations' in collector.config['collection']

            if old_env:
                os.environ['RIOT_API_KEY'] = old_env
            else:
                os.environ.pop('RIOT_API_KEY', None)
        finally:
            os.unlink(temp_path)
