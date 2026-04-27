"""Tests for graph.reindex_config.ReindexConfig."""
import pytest

from graph.reindex_config import ReindexConfig


class TestReindexConfigDefaults:
    """Default-construction behaviour."""

    def test_defaults(self):
        cfg = ReindexConfig()
        assert cfg.strategy == "auto"
        assert cfg.sweep_interval_minutes == 30
        assert cfg.sweep_on_session_start is True
        assert cfg.local_max_hops == 1
        assert cfg.force_full_after_runs == 50

    def test_validate_default_passes(self):
        ReindexConfig().validate()  # should not raise


class TestReindexConfigValidation:
    """Validate() rejects invalid values."""

    def test_invalid_strategy(self):
        cfg = ReindexConfig(strategy="bogus")
        with pytest.raises(ValueError, match="Invalid strategy"):
            cfg.validate()

    def test_zero_sweep_interval(self):
        cfg = ReindexConfig(sweep_interval_minutes=0)
        with pytest.raises(ValueError, match="sweep_interval_minutes"):
            cfg.validate()

    def test_zero_local_hops(self):
        cfg = ReindexConfig(local_max_hops=0)
        with pytest.raises(ValueError, match="local_max_hops"):
            cfg.validate()

    def test_zero_force_full(self):
        cfg = ReindexConfig(force_full_after_runs=0)
        with pytest.raises(ValueError, match="force_full_after_runs"):
            cfg.validate()

    @pytest.mark.parametrize("strategy", ["auto", "full", "resolve_full", "resolve_local"])
    def test_all_valid_strategies(self, strategy):
        ReindexConfig(strategy=strategy).validate()


class TestReindexConfigSerialization:
    """from_dict / to_dict roundtrip."""

    def test_to_dict_contains_all_fields(self):
        cfg = ReindexConfig()
        d = cfg.to_dict()
        assert set(d.keys()) == {
            "strategy",
            "sweep_interval_minutes",
            "sweep_on_session_start",
            "local_max_hops",
            "force_full_after_runs",
        }

    def test_from_dict_roundtrip(self):
        original = ReindexConfig(
            strategy="full",
            sweep_interval_minutes=10,
            sweep_on_session_start=False,
            local_max_hops=3,
            force_full_after_runs=20,
        )
        rebuilt = ReindexConfig.from_dict(original.to_dict())
        assert rebuilt == original

    def test_from_dict_ignores_unknown_keys(self):
        cfg = ReindexConfig.from_dict({"strategy": "full", "unknown_field": "x"})
        assert cfg.strategy == "full"

    def test_from_dict_handles_none(self):
        cfg = ReindexConfig.from_dict(None)
        assert cfg.strategy == "auto"


class TestEffectiveStrategy:
    """get_effective_strategy()."""

    def test_auto_foreground_picks_local(self):
        cfg = ReindexConfig(strategy="auto")
        assert cfg.get_effective_strategy(is_foreground=True) == "resolve_local"

    def test_auto_background_picks_full(self):
        cfg = ReindexConfig(strategy="auto")
        assert cfg.get_effective_strategy(is_foreground=False) == "resolve_full"

    def test_explicit_strategy_overrides_context(self):
        cfg = ReindexConfig(strategy="full")
        assert cfg.get_effective_strategy(is_foreground=True) == "full"
        assert cfg.get_effective_strategy(is_foreground=False) == "full"
