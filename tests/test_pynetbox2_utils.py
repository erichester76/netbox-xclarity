"""Unit tests for utility components in pynetbox2.py.

Covers:
* ``normalize_fk_fields`` – FK field normalisation for GET vs POST/PATCH
* ``RateLimiter`` – token-bucket throttle (no real sleeping for unit tests)
* ``NullCacheBackend`` – no-op cache contract
* ``SQLiteCacheBackend`` – SQLite-backed cache with TTL and prefix-delete
"""

from __future__ import annotations

import time
import os
import tempfile

import pytest

from pynetbox2 import (
    NullCacheBackend,
    RateLimiter,
    SQLiteCacheBackend,
    normalize_fk_fields,
)


# ===========================================================================
# normalize_fk_fields
# ===========================================================================

class TestNormalizeFkFields:
    """Tests for normalize_fk_fields(resource, payload, for_write)."""

    # ---- GET mode (for_write=False) ----------------------------------------

    def test_integer_fk_renamed_with_id_suffix_for_get(self):
        payload = {"manufacturer": 5}
        result = normalize_fk_fields("dcim.device_types", payload, for_write=False)
        assert "manufacturer_id" in result
        assert "manufacturer" not in result
        assert result["manufacturer_id"] == 5

    def test_dict_fk_unwrapped_to_id_for_get(self):
        payload = {"manufacturer": {"id": 7, "name": "Lenovo"}}
        result = normalize_fk_fields("dcim.device_types", payload, for_write=False)
        assert result["manufacturer_id"] == 7
        assert "manufacturer" not in result

    def test_object_with_id_attr_for_get(self):
        class FakeObj:
            id = 42

        payload = {"manufacturer": FakeObj()}
        result = normalize_fk_fields("dcim.device_types", payload, for_write=False)
        assert result["manufacturer_id"] == 42

    def test_non_fk_field_untouched_for_get(self):
        payload = {"name": "My Device", "slug": "my-device"}
        result = normalize_fk_fields("dcim.device_types", payload, for_write=False)
        assert result == {"name": "My Device", "slug": "my-device"}

    # ---- POST/PATCH mode (for_write=True) -----------------------------------

    def test_integer_fk_kept_as_plain_field_for_write(self):
        payload = {"manufacturer": 5}
        result = normalize_fk_fields("dcim.device_types", payload, for_write=True)
        # In write mode the field name should NOT be renamed to manufacturer_id
        assert result["manufacturer"] == 5
        assert "manufacturer_id" not in result

    def test_dict_fk_unwrapped_for_write(self):
        # Dict FK values are always resolved to integer _id regardless of for_write.
        # The for_write flag only prevents renaming plain integer values.
        payload = {"manufacturer": {"id": 9, "name": "Lenovo"}}
        result = normalize_fk_fields("dcim.device_types", payload, for_write=True)
        assert result.get("manufacturer_id") == 9

    # ---- Already-_id suffix ------------------------------------------------

    def test_field_already_has_id_suffix_untouched(self):
        payload = {"manufacturer_id": 3}
        result = normalize_fk_fields("dcim.device_types", payload, for_write=False)
        assert result["manufacturer_id"] == 3

    # ---- Unknown resource --------------------------------------------------

    def test_unknown_resource_leaves_payload_unchanged(self):
        payload = {"site": 1, "name": "Test"}
        result = normalize_fk_fields("unknown.resource", payload, for_write=False)
        # No FK mapping → payload unchanged (integer values stay as-is)
        assert result["site"] == 1


# ===========================================================================
# RateLimiter
# ===========================================================================

class TestRateLimiter:
    def test_zero_rate_returns_immediately(self):
        limiter = RateLimiter(calls_per_second=0)
        slept = limiter.acquire()
        assert slept == 0.0

    def test_first_call_within_burst_returns_immediately(self):
        limiter = RateLimiter(calls_per_second=100, burst=5)
        # With burst=5 and plenty of tokens available, should not sleep
        slept = limiter.acquire()
        assert slept == 0.0

    def test_tokens_decrease_after_acquire(self):
        limiter = RateLimiter(calls_per_second=10, burst=3)
        initial_tokens = limiter.tokens
        limiter.acquire()
        assert limiter.tokens < initial_tokens

    def test_burst_default_is_1(self):
        limiter = RateLimiter(calls_per_second=10)
        assert limiter.burst == 1

    def test_negative_rate_clamped_to_zero(self):
        limiter = RateLimiter(calls_per_second=-5)
        assert limiter.calls_per_second == 0.0
        assert limiter.acquire() == 0.0

    def test_negative_burst_clamped_to_1(self):
        limiter = RateLimiter(calls_per_second=1, burst=-2)
        assert limiter.burst == 1


# ===========================================================================
# NullCacheBackend
# ===========================================================================

class TestNullCacheBackend:
    @pytest.fixture
    def cache(self):
        return NullCacheBackend()

    def test_get_always_returns_none(self, cache):
        assert cache.get("any-key") is None

    def test_set_then_get_still_returns_none(self, cache):
        cache.set("key", "value", ttl_seconds=60)
        assert cache.get("key") is None

    def test_delete_is_noop(self, cache):
        cache.set("key", "value")
        cache.delete("key")
        assert cache.get("key") is None

    def test_delete_prefix_is_noop(self, cache):
        cache.delete_prefix("prefix:")

    def test_clear_is_noop(self, cache):
        cache.clear()

    def test_close_is_noop(self, cache):
        cache.close()


# ===========================================================================
# SQLiteCacheBackend
# ===========================================================================

class TestSQLiteCacheBackend:
    @pytest.fixture
    def cache(self, tmp_path):
        db_file = str(tmp_path / "test_cache.sqlite3")
        backend = SQLiteCacheBackend(db_path=db_file, default_ttl=300)
        yield backend
        backend.close()

    def test_set_and_get_simple_value(self, cache):
        cache.set("hello", "world")
        assert cache.get("hello") == "world"

    def test_get_missing_key_returns_none(self, cache):
        assert cache.get("nonexistent") is None

    def test_set_overwrites_existing(self, cache):
        cache.set("key", "first")
        cache.set("key", "second")
        assert cache.get("key") == "second"

    def test_delete_removes_entry(self, cache):
        cache.set("to-delete", 42)
        cache.delete("to-delete")
        assert cache.get("to-delete") is None

    def test_delete_prefix_removes_matching_entries(self, cache):
        cache.set("pfx:a", 1)
        cache.set("pfx:b", 2)
        cache.set("other:c", 3)
        cache.delete_prefix("pfx:")
        assert cache.get("pfx:a") is None
        assert cache.get("pfx:b") is None
        # 'other:c' should not be affected
        assert cache.get("other:c") == 3

    def test_clear_removes_all_entries(self, cache):
        cache.set("x", 1)
        cache.set("y", 2)
        cache.clear()
        assert cache.get("x") is None
        assert cache.get("y") is None

    def test_ttl_expiry(self, cache):
        """Entry with 1-second TTL should expire after that time."""
        cache.set("ephemeral", "value", ttl_seconds=1)
        assert cache.get("ephemeral") == "value"
        time.sleep(1.1)
        assert cache.get("ephemeral") is None

    def test_cleanup_expired_removes_stale_entries(self, cache):
        cache.set("stale", "old", ttl_seconds=1)
        time.sleep(1.1)
        removed = cache.cleanup_expired()
        assert removed >= 1
        assert cache.get("stale") is None

    def test_complex_value_round_trip(self, cache):
        value = {"devices": [1, 2, 3], "count": 3, "nested": {"a": True}}
        cache.set("complex", value)
        assert cache.get("complex") == value

    def test_key_prefix_applied(self, tmp_path):
        db_file = str(tmp_path / "prefix_test.sqlite3")
        backend = SQLiteCacheBackend(db_path=db_file, key_prefix="myapp:", default_ttl=60)
        backend.set("item", 99)
        # Verify the raw SQLite row uses the prefix
        import sqlite3
        conn = sqlite3.connect(db_file)
        rows = conn.execute("SELECT key FROM cache_entries").fetchall()
        conn.close()
        keys = [r[0] for r in rows]
        assert any(k.startswith("myapp:") for k in keys)
        backend.close()
