"""Tests for apollo.projects.reindex (ReindexHistory + ReindexOrchestrator)."""
import json
import time

import networkx as nx
import pytest

from apollo.projects.reindex import ReindexHistory, ReindexOrchestrator
from apollo.graph.reindex_config import ReindexConfig
from apollo.graph.incremental import GraphDiff, IncrementalResult, ReindexStats


def _make_stats(strategy: str = "resolve_local", **kw) -> ReindexStats:
    base = dict(
        strategy=strategy,
        started_at=time.time(),
        duration_ms=100,
        files_total=5,
        files_parsed=5,
        files_skipped=0,
        affected_files=1,
        edges_resolved=10,
        edges_added=2,
        edges_removed=1,
        bytes_written=1024,
    )
    base.update(kw)
    return ReindexStats(**base)


class TestReindexHistoryEmpty:
    """Empty / fresh project state."""

    def test_load_returns_empty_when_missing(self, tmp_path):
        h = ReindexHistory(tmp_path)
        assert h.load() == []

    def test_get_last_returns_none(self, tmp_path):
        h = ReindexHistory(tmp_path)
        assert h.get_last() is None

    def test_summary_when_empty(self, tmp_path):
        h = ReindexHistory(tmp_path)
        s = h.get_summary()
        assert s == {"total_runs": 0, "avg_duration_ms": 0, "total_files_indexed": 0}


class TestReindexHistoryAppend:
    """Append + persistence + truncation."""

    def test_append_persists_to_disk(self, tmp_path):
        h = ReindexHistory(tmp_path)
        h.append(_make_stats())

        assert h.history_file.exists()
        loaded = h.load()
        assert len(loaded) == 1
        assert loaded[0].strategy == "resolve_local"

    def test_append_multiple_preserves_order(self, tmp_path):
        h = ReindexHistory(tmp_path)
        h.append(_make_stats(strategy="full"))
        h.append(_make_stats(strategy="resolve_local"))

        loaded = h.load()
        assert [s.strategy for s in loaded] == ["full", "resolve_local"]

    def test_truncates_to_max_history(self, tmp_path, monkeypatch):
        h = ReindexHistory(tmp_path)
        monkeypatch.setattr(ReindexHistory, "MAX_HISTORY_SIZE", 3)

        for i in range(5):
            h.append(_make_stats(duration_ms=i))

        loaded = h.load()
        assert len(loaded) == 3
        # Newest 3 (durations 2,3,4) retained
        assert [s.duration_ms for s in loaded] == [2, 3, 4]

    def test_get_last_returns_most_recent(self, tmp_path):
        h = ReindexHistory(tmp_path)
        h.append(_make_stats(duration_ms=10))
        h.append(_make_stats(duration_ms=20))
        assert h.get_last().duration_ms == 20

    def test_corrupt_file_returns_empty(self, tmp_path):
        h = ReindexHistory(tmp_path)
        h.history_file.parent.mkdir(parents=True, exist_ok=True)
        h.history_file.write_text("{not valid json")
        assert h.load() == []


class TestReindexHistorySummary:
    """get_summary() aggregations."""

    def test_summary_aggregates_stats(self, tmp_path):
        h = ReindexHistory(tmp_path)
        h.append(_make_stats(duration_ms=100, files_total=10, edges_added=2, edges_removed=1, strategy="full"))
        h.append(_make_stats(duration_ms=200, files_total=20, edges_added=4, edges_removed=2, strategy="resolve_local"))

        s = h.get_summary()
        assert s["total_runs"] == 2
        assert s["avg_duration_ms"] == 150
        assert s["total_files_indexed"] == 30
        assert s["total_edges_added"] == 6
        assert s["total_edges_removed"] == 3
        assert set(s["strategies"]) == {"full", "resolve_local"}


class TestReindexOrchestrator:
    """ReindexOrchestrator behaviour."""

    def test_init_uses_default_config(self, tmp_path):
        orch = ReindexOrchestrator(tmp_path)
        assert orch.config.strategy == "auto"

    def test_init_validates_provided_config(self, tmp_path):
        bad = ReindexConfig(strategy="bogus")
        with pytest.raises(ValueError):
            ReindexOrchestrator(tmp_path, bad)

    def test_get_effective_strategy_proxies_to_config(self, tmp_path):
        orch = ReindexOrchestrator(tmp_path, ReindexConfig(strategy="auto"))
        assert orch.get_effective_strategy(is_foreground=True) == "resolve_local"
        assert orch.get_effective_strategy(is_foreground=False) == "resolve_full"

    def test_record_run_appends_to_history(self, tmp_path):
        orch = ReindexOrchestrator(tmp_path)
        result = IncrementalResult(
            graph_out=nx.DiGraph(),
            new_hashes={},
            new_dep_index={},
            diff=GraphDiff(),
            stats=_make_stats(),
        )
        orch.record_run(result)
        assert len(orch.history.load()) == 1

    def test_should_force_full_rebuild_false_when_few_runs(self, tmp_path):
        cfg = ReindexConfig(force_full_after_runs=5)
        orch = ReindexOrchestrator(tmp_path, cfg)
        for _ in range(3):
            orch.record_run(
                IncrementalResult(nx.DiGraph(), {}, {}, GraphDiff(), _make_stats(strategy="resolve_local"))
            )
        assert orch.should_force_full_rebuild() is False

    def test_should_force_full_rebuild_true_after_threshold(self, tmp_path):
        cfg = ReindexConfig(force_full_after_runs=3)
        orch = ReindexOrchestrator(tmp_path, cfg)
        for _ in range(3):
            orch.record_run(
                IncrementalResult(nx.DiGraph(), {}, {}, GraphDiff(), _make_stats(strategy="resolve_local"))
            )
        assert orch.should_force_full_rebuild() is True

    def test_full_runs_reset_threshold(self, tmp_path):
        """A 'full' strategy in window prevents triggering force_full."""
        cfg = ReindexConfig(force_full_after_runs=3)
        orch = ReindexOrchestrator(tmp_path, cfg)
        # 2 incremental + 1 full = 2 incremental in last 3 runs (< 3)
        orch.record_run(IncrementalResult(nx.DiGraph(), {}, {}, GraphDiff(), _make_stats(strategy="resolve_local")))
        orch.record_run(IncrementalResult(nx.DiGraph(), {}, {}, GraphDiff(), _make_stats(strategy="full")))
        orch.record_run(IncrementalResult(nx.DiGraph(), {}, {}, GraphDiff(), _make_stats(strategy="resolve_local")))
        assert orch.should_force_full_rebuild() is False

    def test_get_last_reindex_info_no_runs(self, tmp_path):
        orch = ReindexOrchestrator(tmp_path)
        info = orch.get_last_reindex_info()
        assert info["has_run"] is False
        assert info["display"] == "No reindex yet"

    def test_get_last_reindex_info_with_data(self, tmp_path):
        orch = ReindexOrchestrator(tmp_path)
        orch.record_run(
            IncrementalResult(nx.DiGraph(), {}, {}, GraphDiff(), _make_stats(strategy="resolve_local"))
        )
        info = orch.get_last_reindex_info()
        assert info["has_run"] is True
        assert info["strategy"] == "resolve_local"
        assert "ms" in info["display"]
        assert "timestamp" in info
