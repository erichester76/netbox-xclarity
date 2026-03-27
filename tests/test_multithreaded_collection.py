"""Tests for the multithreaded collection logic in Collector.

These tests verify that _collect_nodes, _collect_chassis, _collect_switches,
and _collect_storage dispatch work via a ThreadPoolExecutor and correctly
handle per-device errors without aborting the whole collection run.
"""

from __future__ import annotations

import os
from unittest.mock import MagicMock, patch, call

import pytest

from collector import Collector


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_collector(max_workers: int = 4) -> Collector:
    """Return a Collector with fully-mocked xc and nb_sync."""
    xc = MagicMock()
    nb_sync = MagicMock()
    with patch.dict(os.environ, {"COLLECTOR_MAX_WORKERS": str(max_workers)}):
        return Collector(xc, nb_sync)


# ---------------------------------------------------------------------------
# COLLECTOR_MAX_WORKERS env var is read correctly
# ---------------------------------------------------------------------------

class TestMaxWorkersConfig:
    def test_default_is_10(self):
        env = {k: v for k, v in os.environ.items() if k != "COLLECTOR_MAX_WORKERS"}
        with patch.dict(os.environ, env, clear=True):
            c = Collector(MagicMock(), MagicMock())
        assert c._max_workers == 10

    def test_custom_value_is_read(self):
        with patch.dict(os.environ, {"COLLECTOR_MAX_WORKERS": "5"}):
            c = Collector(MagicMock(), MagicMock())
        assert c._max_workers == 5

    def test_single_worker(self):
        with patch.dict(os.environ, {"COLLECTOR_MAX_WORKERS": "1"}):
            c = Collector(MagicMock(), MagicMock())
        assert c._max_workers == 1


# ---------------------------------------------------------------------------
# _device_name helper
# ---------------------------------------------------------------------------

class TestDeviceName:
    def test_prefers_name(self):
        assert Collector._device_name({"name": "srv-01", "hostname": "h", "uuid": "u"}) == "srv-01"

    def test_falls_back_to_hostname(self):
        assert Collector._device_name({"hostname": "h", "uuid": "u"}) == "h"

    def test_falls_back_to_uuid(self):
        assert Collector._device_name({"uuid": "u"}) == "u"

    def test_unknown_when_all_missing(self):
        assert Collector._device_name({}) == "unknown"


# ---------------------------------------------------------------------------
# _run_parallel helper
# ---------------------------------------------------------------------------

class TestRunParallel:
    def test_all_items_processed(self):
        c = _make_collector(max_workers=4)
        items = [{"uuid": f"u{i}"} for i in range(5)]
        func = MagicMock()

        c._run_parallel(items, func, "device")

        assert func.call_count == 5
        func.assert_has_calls([call(item) for item in items], any_order=True)

    def test_empty_list(self):
        c = _make_collector()
        func = MagicMock()
        c._run_parallel([], func, "device")
        func.assert_not_called()

    def test_exception_in_one_item_does_not_abort_others(self):
        c = _make_collector(max_workers=4)
        items = [{"uuid": f"u{i}"} for i in range(4)]
        call_log: list[str] = []

        def _func(item):
            if item["uuid"] == "u1":
                raise RuntimeError("boom")
            call_log.append(item["uuid"])

        c._run_parallel(items, _func, "device")

        assert sorted(call_log) == ["u0", "u2", "u3"]

    def test_uses_max_workers(self):
        c = _make_collector(max_workers=7)
        with patch("collector.concurrent.futures.ThreadPoolExecutor") as mock_tpe:
            mock_executor = MagicMock()
            mock_tpe.return_value.__enter__ = MagicMock(return_value=mock_executor)
            mock_tpe.return_value.__exit__ = MagicMock(return_value=False)
            mock_future = MagicMock()
            mock_future.result.return_value = None
            mock_executor.submit.return_value = mock_future
            with patch("collector.concurrent.futures.as_completed", return_value=[mock_future]):
                c._run_parallel([{"uuid": "x"}], MagicMock(), "device")

        mock_tpe.assert_called_once_with(max_workers=7)


# ---------------------------------------------------------------------------
# _collect_nodes
# ---------------------------------------------------------------------------

class TestCollectNodesMultithreaded:
    def test_sync_called_for_each_node(self):
        c = _make_collector(max_workers=4)
        nodes = [{"uuid": f"uuid-{i}", "hostname": f"node-{i}"} for i in range(5)]
        c.xc.get_nodes.return_value = nodes
        c._sync_node = MagicMock()

        c._collect_nodes()

        assert c._sync_node.call_count == 5
        c._sync_node.assert_has_calls(
            [call(node) for node in nodes], any_order=True
        )

    def test_empty_node_list_no_error(self):
        c = _make_collector()
        c.xc.get_nodes.return_value = []
        c._sync_node = MagicMock()

        c._collect_nodes()

        c._sync_node.assert_not_called()

    def test_xclarity_fetch_error_is_handled(self):
        c = _make_collector()
        c.xc.get_nodes.side_effect = RuntimeError("network failure")
        c._sync_node = MagicMock()

        c._collect_nodes()  # must not raise

        c._sync_node.assert_not_called()

    def test_per_node_error_does_not_abort(self):
        """An exception in _sync_node for one node must not prevent others from running."""
        c = _make_collector(max_workers=4)
        nodes = [{"uuid": f"uuid-{i}", "hostname": f"node-{i}"} for i in range(4)]
        c.xc.get_nodes.return_value = nodes

        call_log: list[str] = []

        def _sync(node):
            name = node["hostname"]
            if name == "node-1":
                raise ValueError("deliberate failure")
            call_log.append(name)

        c._sync_node = _sync

        c._collect_nodes()  # must not raise

        # The three successful nodes must have been processed
        assert sorted(call_log) == ["node-0", "node-2", "node-3"]

    def test_delegates_to_run_parallel(self):
        """_collect_nodes must dispatch work via _run_parallel."""
        c = _make_collector(max_workers=3)
        nodes = [{"uuid": "u1", "hostname": "n1"}]
        c.xc.get_nodes.return_value = nodes
        c._sync_node = MagicMock()

        with patch.object(c, "_run_parallel") as mock_run:
            c._collect_nodes()

        mock_run.assert_called_once_with(nodes, c._sync_node, "node")


# ---------------------------------------------------------------------------
# _collect_chassis
# ---------------------------------------------------------------------------

class TestCollectChassisMultithreaded:
    def test_sync_called_for_each_chassis(self):
        c = _make_collector(max_workers=2)
        chassis_list = [{"uuid": f"c-{i}", "hostname": f"chassis-{i}"} for i in range(3)]
        c.xc.get_chassis.return_value = chassis_list
        c._sync_chassis = MagicMock()

        c._collect_chassis()

        assert c._sync_chassis.call_count == 3
        c._sync_chassis.assert_has_calls(
            [call(ch) for ch in chassis_list], any_order=True
        )

    def test_per_chassis_error_does_not_abort(self):
        c = _make_collector(max_workers=2)
        chassis_list = [{"uuid": f"c-{i}", "hostname": f"chassis-{i}"} for i in range(3)]
        c.xc.get_chassis.return_value = chassis_list

        call_log: list[str] = []

        def _sync(ch):
            if ch["hostname"] == "chassis-0":
                raise RuntimeError("boom")
            call_log.append(ch["hostname"])

        c._sync_chassis = _sync
        c._collect_chassis()

        assert sorted(call_log) == ["chassis-1", "chassis-2"]


# ---------------------------------------------------------------------------
# _collect_switches
# ---------------------------------------------------------------------------

class TestCollectSwitchesMultithreaded:
    def test_sync_called_for_each_switch(self):
        c = _make_collector(max_workers=2)
        switches = [{"uuid": f"s-{i}", "hostname": f"sw-{i}"} for i in range(3)]
        c.xc.get_switches.return_value = switches
        c._sync_switch = MagicMock()

        c._collect_switches()

        assert c._sync_switch.call_count == 3

    def test_per_switch_error_does_not_abort(self):
        c = _make_collector(max_workers=2)
        switches = [{"uuid": f"s-{i}", "hostname": f"sw-{i}"} for i in range(3)]
        c.xc.get_switches.return_value = switches

        call_log: list[str] = []

        def _sync(sw):
            if sw["hostname"] == "sw-2":
                raise RuntimeError("boom")
            call_log.append(sw["hostname"])

        c._sync_switch = _sync
        c._collect_switches()

        assert sorted(call_log) == ["sw-0", "sw-1"]


# ---------------------------------------------------------------------------
# _collect_storage
# ---------------------------------------------------------------------------

class TestCollectStorageMultithreaded:
    def test_sync_called_for_each_storage(self):
        c = _make_collector(max_workers=2)
        storage_list = [{"uuid": f"st-{i}", "hostname": f"storage-{i}"} for i in range(3)]
        c.xc.get_storage.return_value = storage_list
        c._sync_storage = MagicMock()

        c._collect_storage()

        assert c._sync_storage.call_count == 3

    def test_per_storage_error_does_not_abort(self):
        c = _make_collector(max_workers=2)
        storage_list = [{"uuid": f"st-{i}", "hostname": f"storage-{i}"} for i in range(3)]
        c.xc.get_storage.return_value = storage_list

        call_log: list[str] = []

        def _sync(st):
            if st["hostname"] == "storage-1":
                raise RuntimeError("boom")
            call_log.append(st["hostname"])

        c._sync_storage = _sync
        c._collect_storage()

        assert sorted(call_log) == ["storage-0", "storage-2"]
