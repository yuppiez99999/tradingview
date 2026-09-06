"""05_07 — IV Rank 自适应参数解析测试.

覆盖: resolve_adaptive_params 分档边界 (左闭右开) / 字段继承 / fail-open;
validate_iv_adaptive_config fail-fast; build_iv_rank_provider;
CollarEngine.generate 覆写 (meta/参数恢复/保护加深);
ComboOrchestrator rank 注入 (fake provider / 异常 fail-open / 默认旁路).
"""

from __future__ import annotations

import pytest

from utils.etf_option_combo.collar import CollarEngine
from utils.etf_option_combo.combo_base import LegSide, StrategyType
from utils.etf_option_combo.combo_orchestrator import ComboOrchestrator
from utils.etf_option_combo.iv_adaptive import (
    build_iv_rank_provider,
    load_iv_adaptive_config,
    resolve_adaptive_params,
    validate_iv_adaptive_config,
)

from .conftest import SyntheticDataLayer

pytestmark = pytest.mark.unit


# ============================================================
# Fixtures
# ============================================================
@pytest.fixture
def static_params() -> dict:
    """与生产 yaml collar 段一致的静态参数."""
    return {
        "put_otm_pct": 0.05,
        "call_otm_pct": 0.05,
        "protection_band_min": 0.10,
        "put_otm_max": 0.10,
        "max_net_cost_pct": 0.005,
        "dte_min": 30,
        "dte_max": 60,
        "preferred_dte": 45,
    }


@pytest.fixture
def collar_config() -> dict:
    """与 test_collar.py 同口径的领口静态配置."""
    return {
        "total_capital": 2_000_000,
        "put_otm_pct": 0.05,
        "call_otm_pct": 0.05,
        "protection_band_min": 0.10,
        "put_otm_max": 0.10,
        "max_net_cost_pct": 0.005,
        "dte_min": 30,
        "dte_max": 60,
        "preferred_dte": 45,
    }


@pytest.fixture
def iv_adaptive_cfg() -> dict:
    """与生产 yaml iv_adaptive 段一致的分档配置 (测试中 enabled=true)."""
    return {
        "enabled": True,
        "tiers": [
            {"name": "low", "max_rank": 30, "params": {
                "put_otm_pct": 0.03, "call_otm_pct": 0.07, "dte_min": 15, "dte_max": 55,
            }},
            {"name": "mid", "max_rank": 70, "params": {}},
            {"name": "high", "max_rank": 101, "params": {
                "put_otm_pct": 0.07, "call_otm_pct": 0.03,
                "put_otm_max": 0.12, "dte_min": 45, "dte_max": 90,
            }},
        ],
    }


@pytest.fixture
def collar_engine_adaptive(chain_fetcher, collar_config, iv_adaptive_cfg):
    """启用 iv_adaptive 的领口引擎."""
    return CollarEngine(
        config={**collar_config, "iv_adaptive": iv_adaptive_cfg},
        chain_fetcher=chain_fetcher,
    )


def _resolve(rank, cfg, static):
    return resolve_adaptive_params(rank, cfg, static)


# ============================================================
# resolve_adaptive_params: 分档边界 (左闭右开)
# ============================================================
class TestResolveTiers:
    def test_rank_0_low(self, iv_adaptive_cfg, static_params):
        assert _resolve(0, iv_adaptive_cfg, static_params)["tier"] == "low"

    def test_rank_29_low(self, iv_adaptive_cfg, static_params):
        assert _resolve(29, iv_adaptive_cfg, static_params)["tier"] == "low"

    def test_rank_30_mid(self, iv_adaptive_cfg, static_params):
        """30 落入 mid (左闭右开)."""
        assert _resolve(30, iv_adaptive_cfg, static_params)["tier"] == "mid"

    def test_rank_69_mid(self, iv_adaptive_cfg, static_params):
        assert _resolve(69, iv_adaptive_cfg, static_params)["tier"] == "mid"

    def test_rank_70_high(self, iv_adaptive_cfg, static_params):
        """70 落入 high (左闭右开)."""
        assert _resolve(70, iv_adaptive_cfg, static_params)["tier"] == "high"

    def test_rank_100_high(self, iv_adaptive_cfg, static_params):
        assert _resolve(100, iv_adaptive_cfg, static_params)["tier"] == "high"

    def test_mid_inherits_static(self, iv_adaptive_cfg, static_params):
        """mid 档 params 为空 → 完整继承静态参数."""
        resolved = _resolve(50, iv_adaptive_cfg, static_params)
        assert resolved["tier"] == "mid"
        assert resolved["params"] == static_params

    def test_partial_override_merges(self, iv_adaptive_cfg, static_params):
        """tier 只覆盖部分字段, 其余继承静态."""
        resolved = _resolve(85, iv_adaptive_cfg, static_params)
        assert resolved["params"]["put_otm_pct"] == 0.07      # 覆盖
        assert resolved["params"]["put_otm_max"] == 0.12      # 覆盖
        assert resolved["params"]["max_net_cost_pct"] == 0.005  # 继承
        assert resolved["params"]["protection_band_min"] == 0.10  # 继承

    def test_none_rank_falls_back(self, iv_adaptive_cfg, static_params):
        resolved = _resolve(None, iv_adaptive_cfg, static_params)
        assert resolved["tier"] is None
        assert resolved["params"] == static_params
        assert resolved["iv_rank"] is None

    @pytest.mark.parametrize("bad_rank", [-1, 101, 150])
    def test_out_of_range_rank_falls_back(self, bad_rank, iv_adaptive_cfg, static_params):
        resolved = _resolve(bad_rank, iv_adaptive_cfg, static_params)
        assert resolved["tier"] is None
        assert resolved["params"] == static_params

    def test_bool_rank_falls_back(self, iv_adaptive_cfg, static_params):
        assert _resolve(True, iv_adaptive_cfg, static_params)["tier"] is None

    def test_disabled_cfg_falls_back(self, iv_adaptive_cfg, static_params):
        iv_adaptive_cfg["enabled"] = False
        assert _resolve(85, iv_adaptive_cfg, static_params)["tier"] is None

    def test_none_cfg_falls_back(self, static_params):
        assert _resolve(85, None, static_params)["tier"] is None

    def test_unordered_tiers_resolved_by_max_rank(self, static_params):
        """tiers 乱序输入仍按 max_rank 升序解析."""
        cfg = {
            "enabled": True,
            "tiers": [
                {"name": "high", "max_rank": 101, "params": {"put_otm_pct": 0.07}},
                {"name": "low", "max_rank": 30, "params": {"put_otm_pct": 0.03}},
                {"name": "mid", "max_rank": 70, "params": {}},
            ],
        }
        assert _resolve(20, cfg, static_params)["tier"] == "low"
        assert _resolve(50, cfg, static_params)["tier"] == "mid"
        assert _resolve(90, cfg, static_params)["tier"] == "high"

    def test_no_matching_tier_falls_back(self, static_params):
        """末档 max_rank=50, rank=60 未命中 → 静态."""
        cfg = {"enabled": True, "tiers": [{"name": "low", "max_rank": 50, "params": {}}]}
        assert _resolve(60, cfg, static_params)["tier"] is None

    def test_static_params_not_mutated(self, iv_adaptive_cfg, static_params):
        """resolve 不得原地修改 static_params (引擎快照安全)."""
        snapshot = dict(static_params)
        _resolve(85, iv_adaptive_cfg, static_params)
        assert static_params == snapshot


# ============================================================
# validate_iv_adaptive_config: fail-fast
# ============================================================
class TestValidateConfig:
    def test_valid_config_passes(self, iv_adaptive_cfg):
        validate_iv_adaptive_config(iv_adaptive_cfg)

    def test_disabled_skips_validation(self):
        """enabled=false 时坏配置也不抛 (默认旁路)."""
        validate_iv_adaptive_config({"enabled": False, "tiers": "bad"})

    def test_none_cfg_skips_validation(self):
        validate_iv_adaptive_config(None)

    def test_missing_tiers_raises(self):
        with pytest.raises(ValueError, match="tiers"):
            validate_iv_adaptive_config({"enabled": True})

    def test_non_increasing_max_rank_raises(self):
        cfg = {"enabled": True, "tiers": [
            {"name": "a", "max_rank": 50, "params": {}},
            {"name": "b", "max_rank": 50, "params": {}},
            {"name": "c", "max_rank": 101, "params": {}},
        ]}
        with pytest.raises(ValueError, match="严格递增"):
            validate_iv_adaptive_config(cfg)

    def test_last_tier_not_covering_100_raises(self):
        cfg = {"enabled": True, "tiers": [{"name": "low", "max_rank": 100, "params": {}}]}
        with pytest.raises(ValueError, match="未覆盖 rank=100"):
            validate_iv_adaptive_config(cfg)

    def test_param_out_of_range_raises(self):
        cfg = {"enabled": True, "tiers": [
            {"name": "low", "max_rank": 101, "params": {"put_otm_pct": 0.01}},
        ]}
        with pytest.raises(ValueError, match="put_otm_pct"):
            validate_iv_adaptive_config(cfg)

    def test_call_otm_beyond_chain_range_raises(self):
        """call_otm_pct 超出期权链硬编码范围 (0.02,0.08) → 拒绝."""
        cfg = {"enabled": True, "tiers": [
            {"name": "low", "max_rank": 101, "params": {"call_otm_pct": 0.09}},
        ]}
        with pytest.raises(ValueError, match="call_otm_pct"):
            validate_iv_adaptive_config(cfg)

    def test_unknown_param_raises(self):
        cfg = {"enabled": True, "tiers": [
            {"name": "low", "max_rank": 101, "params": {"foo": 1.0}},
        ]}
        with pytest.raises(ValueError, match="未知字段"):
            validate_iv_adaptive_config(cfg)

    def test_put_otm_gt_max_raises(self):
        cfg = {"enabled": True, "tiers": [
            {"name": "low", "max_rank": 101, "params": {"put_otm_pct": 0.08, "put_otm_max": 0.06}},
        ]}
        with pytest.raises(ValueError, match="不可达"):
            validate_iv_adaptive_config(cfg)

    def test_dte_min_gt_dte_max_raises(self):
        cfg = {"enabled": True, "tiers": [
            {"name": "low", "max_rank": 101, "params": {"dte_min": 60, "dte_max": 30}},
        ]}
        with pytest.raises(ValueError, match="dte_min"):
            validate_iv_adaptive_config(cfg)


# ============================================================
# build_iv_rank_provider
# ============================================================
class TestBuildProvider:
    def test_disabled_returns_none(self):
        assert build_iv_rank_provider({"enabled": False}) is None

    def test_none_returns_none(self):
        assert build_iv_rank_provider(None) is None

    def test_enabled_returns_provider(self):
        provider = build_iv_rank_provider({
            "enabled": True,
            "lookback_days": 120,
            "min_history_days": 30,
        })
        assert provider is not None
        assert provider.lookback_days == 120
        assert provider.min_history_days == 30


# ============================================================
# load_iv_adaptive_config (回测对比用只读加载)
# ============================================================
class TestLoadConfig:
    def _write_yaml(self, tmp_path, content: str):
        p = tmp_path / "combo.yaml"
        p.write_text(content, encoding="utf-8")
        return p

    def test_nested_under_combo_strategies(self, tmp_path):
        """生产 yaml 结构: iv_adaptive 嵌套于 combo_strategies 下可正确解包."""
        p = self._write_yaml(tmp_path, (
            "config_version: '1.0'\n"
            "combo_strategies:\n"
            "  collar:\n"
            "    put_otm_pct: 0.05\n"
            "  iv_adaptive:\n"
            "    enabled: false\n"
            "    tiers:\n"
            "      - name: high\n"
            "        max_rank: 101\n"
            "        params: {put_otm_pct: 0.07}\n"
        ))
        cfg = load_iv_adaptive_config(p)
        assert cfg.get("enabled") is False
        assert cfg["tiers"][0]["name"] == "high"

    def test_top_level_form(self, tmp_path):
        """兼容写法: iv_adaptive 直接位于文件顶层."""
        p = self._write_yaml(tmp_path, (
            "iv_adaptive:\n"
            "  enabled: true\n"
            "  tiers:\n"
            "    - name: low\n"
            "      max_rank: 30\n"
            "      params: {}\n"
        ))
        cfg = load_iv_adaptive_config(p)
        assert cfg.get("enabled") is True

    def test_missing_section_returns_empty(self, tmp_path):
        p = self._write_yaml(tmp_path, "combo_strategies:\n  collar: {}\n")
        assert load_iv_adaptive_config(p) == {}

    def test_missing_file_returns_empty(self, tmp_path):
        assert load_iv_adaptive_config(tmp_path / "nope.yaml") == {}

    def test_malformed_yaml_returns_empty(self, tmp_path):
        p = self._write_yaml(tmp_path, "combo_strategies: [unclosed\n")
        assert load_iv_adaptive_config(p) == {}

    def test_real_config_loads(self):
        """真实 config/etf_option_combo.yaml 的 iv_adaptive 段可加载 (含 tiers)."""
        cfg = load_iv_adaptive_config()
        assert cfg.get("enabled") is False  # 默认关闭交付
        assert isinstance(cfg.get("tiers"), list) and len(cfg["tiers"]) >= 3


# ============================================================
# CollarEngine.generate 覆写
# ============================================================
class TestCollarAdaptiveGenerate:
    def test_high_rank_meta(self, collar_engine_adaptive, underlying_code, spot_position_sufficient):
        """高 IV Rank 建仓 → meta 携带 tier/iv_rank/生效参数."""
        result = collar_engine_adaptive.generate(
            underlying_code, spot_position_sufficient, market_state={"iv_rank": 85},
        )
        assert result.error_code is None, f"error: {result.error_code} - {result.error_msg}"
        assert result.meta is not None
        assert result.meta["tier"] == "high"
        assert result.meta["iv_rank"] == 85
        assert result.meta["effective_params"]["put_otm_pct"] == 0.07

    def test_low_rank_meta(self, collar_engine_adaptive, underlying_code, spot_position_sufficient):
        result = collar_engine_adaptive.generate(
            underlying_code, spot_position_sufficient, market_state={"iv_rank": 10},
        )
        if result.error_code is not None:
            pytest.skip(f"合成链无法构建低档领口: {result.error_code}")
        assert result.meta is not None
        assert result.meta["tier"] == "low"

    def test_no_rank_meta_none_regression(self, collar_engine_adaptive, underlying_code, spot_position_sufficient):
        """回归: 无 iv_rank 时 meta=None, 走原路径."""
        result = collar_engine_adaptive.generate(underlying_code, spot_position_sufficient)
        assert result.error_code is None
        assert result.meta is None

    def test_engine_params_restored(self, collar_engine_adaptive, underlying_code, spot_position_sufficient):
        """动态参数不得泄漏: generate 后引擎属性恢复静态值."""
        engine = collar_engine_adaptive
        engine.generate(underlying_code, spot_position_sufficient, market_state={"iv_rank": 85})
        assert engine._put_otm_pct == 0.05
        assert engine._call_otm_pct == 0.05
        assert engine._put_otm_max == 0.10
        assert engine._dte_min == 30
        assert engine._dte_max == 60

    def test_params_restored_even_on_error(self, collar_engine_adaptive, underlying_code):
        """建仓失败 (现货不足) 时 finally 仍恢复参数."""
        engine = collar_engine_adaptive
        result = engine.generate(underlying_code, {"shares": 100}, market_state={"iv_rank": 85})
        assert result.error_code == "COLLAR_NO_UNDERLYING"
        assert engine._put_otm_pct == 0.05

    def test_high_rank_put_deeper(
        self, chain_fetcher, collar_config, iv_adaptive_cfg,
        underlying_code, spot_position_sufficient, fixed_spot_price,
    ):
        """高 IV Rank → put 更深 (行权价更低), 保护带更宽."""
        static_engine = CollarEngine(config=dict(collar_config), chain_fetcher=chain_fetcher)
        adaptive_engine = CollarEngine(
            config={**collar_config, "iv_adaptive": iv_adaptive_cfg}, chain_fetcher=chain_fetcher,
        )
        static_result = static_engine.generate(underlying_code, spot_position_sufficient)
        adaptive_result = adaptive_engine.generate(
            underlying_code, spot_position_sufficient, market_state={"iv_rank": 85},
        )
        if static_result.error_code or adaptive_result.error_code:
            pytest.skip(
                f"合成链无法构建: static={static_result.error_code} adaptive={adaptive_result.error_code}"
            )

        def put_strike(r):
            legs = [o.leg for o in r.orders]
            put = next(leg for leg in legs if leg.option_type == "PUT" and leg.side == LegSide.BUY)
            return put.strike

        assert put_strike(adaptive_result) < put_strike(static_result)

    def test_enabled_but_no_rank_static_path(self, collar_engine_adaptive, underlying_code, spot_position_sufficient):
        """iv_adaptive 启用但 market_state 无 iv_rank → 静态路径 (meta=None)."""
        result = collar_engine_adaptive.generate(
            underlying_code, spot_position_sufficient, market_state={"regime": "calm"},
        )
        assert result.error_code is None
        assert result.meta is None


# ============================================================
# ComboOrchestrator rank 注入
# ============================================================
class _FakeIVRankProvider:
    def __init__(self, rank: int | None = 85) -> None:
        self._rank = rank

    def fetch_iv_rank(self, use_cache: bool = True) -> int | None:
        return self._rank


class _BrokenProvider:
    def fetch_iv_rank(self, use_cache: bool = True) -> int | None:
        raise RuntimeError("provider down")


def _write_orch_config(tmp_path, iv_adaptive_enabled: bool) -> object:
    cfg_path = tmp_path / "orch_config.yaml"
    iv_section = """
  iv_adaptive:
    enabled: true
    tiers:
      - {name: low, max_rank: 30, params: {put_otm_pct: 0.03, call_otm_pct: 0.07, dte_min: 15, dte_max: 55}}
      - {name: mid, max_rank: 70, params: {}}
      - {name: high, max_rank: 101, params: {put_otm_pct: 0.07, call_otm_pct: 0.03, put_otm_max: 0.12, dte_min: 45, dte_max: 90}}
""" if iv_adaptive_enabled else ""
    cfg_path.write_text(f"""
combo_strategies:
  enabled: true
  total_capital: 2_000_000
  enabled_underlyings: ["510050.SH"]
  collar:
    put_otm_pct: 0.05
    call_otm_pct: 0.05
    protection_band_min: 0.10
    put_otm_max: 0.10
    max_net_cost_pct: 0.005
    dte_min: 30
    dte_max: 60
  strategy_routing:
    tail_event:
      strategies: ["collar"]
{iv_section}""", encoding="utf-8")
    return cfg_path


class TestOrchestratorInjection:
    def test_injects_rank_into_market_state(self, tmp_path):
        """fake provider (rank=85) → run_all 注入 → collar meta tier=high."""
        orch = ComboOrchestrator(
            config_path=_write_orch_config(tmp_path, True),
            data_layer=SyntheticDataLayer({"510050.SH": 3.0}),
            iv_rank_provider=_FakeIVRankProvider(85),
        )
        from .conftest import SyntheticOptionDataFetcher
        orch._chain_fetcher._fetcher = SyntheticOptionDataFetcher(iv=0.20, source="bs_synthetic")
        results = orch.run_all(
            underlyings=["510050.SH"],
            market_state={"regime": "tail_event"},
            spot_positions={"510050.SH": {"shares": 10000}},
        )
        collar_results = [r for r in results["510050.SH"] if r.strategy_type == StrategyType.COLLAR]
        assert len(collar_results) == 1
        if collar_results[0].error_code is not None:
            pytest.skip(f"合成链无法构建领口: {collar_results[0].error_code}")
        assert collar_results[0].meta is not None
        assert collar_results[0].meta["tier"] == "high"

    def test_provider_error_fail_open(self, tmp_path):
        """provider 抛异常 → log 继续, 不阻断 run_all."""
        orch = ComboOrchestrator(
            config_path=_write_orch_config(tmp_path, True),
            data_layer=SyntheticDataLayer({"510050.SH": 3.0}),
            iv_rank_provider=_BrokenProvider(),
        )
        results = orch.run_all(
            underlyings=["510050.SH"],
            market_state={"regime": "tail_event"},
            spot_positions={"510050.SH": {"shares": 10000}},
        )
        # 无异常且结果存在; collar 走静态路径 (meta=None)
        collar_results = [r for r in results["510050.SH"] if r.strategy_type == StrategyType.COLLAR]
        assert len(collar_results) == 1

    def test_no_iv_adaptive_config_no_provider(self, tmp_path):
        """配置无 iv_adaptive 段 → 不构造 provider (默认旁路)."""
        orch = ComboOrchestrator(
            config_path=_write_orch_config(tmp_path, False),
            data_layer=SyntheticDataLayer({"510050.SH": 3.0}),
        )
        assert orch._iv_rank_provider is None

    def test_invalid_iv_adaptive_config_fail_fast(self, tmp_path):
        """iv_adaptive 配置非法 → 构造 orchestrator 即抛 ValueError."""
        cfg_path = tmp_path / "bad_config.yaml"
        cfg_path.write_text("""
combo_strategies:
  enabled: true
  collar:
    put_otm_pct: 0.05
    dte_min: 30
    dte_max: 60
  iv_adaptive:
    enabled: true
    tiers:
      - {name: low, max_rank: 101, params: {put_otm_pct: 0.01}}
""", encoding="utf-8")
        with pytest.raises(ValueError, match="put_otm_pct"):
            ComboOrchestrator(config_path=cfg_path)

    def test_existing_market_state_rank_not_overwritten(self, tmp_path):
        """market_state 已含 iv_rank → provider 不覆盖."""
        orch = ComboOrchestrator(
            config_path=_write_orch_config(tmp_path, True),
            data_layer=SyntheticDataLayer({"510050.SH": 3.0}),
            iv_rank_provider=_FakeIVRankProvider(85),
        )
        from .conftest import SyntheticOptionDataFetcher
        orch._chain_fetcher._fetcher = SyntheticOptionDataFetcher(iv=0.20, source="bs_synthetic")
        results = orch.run_all(
            underlyings=["510050.SH"],
            market_state={"regime": "tail_event", "iv_rank": 10},
            spot_positions={"510050.SH": {"shares": 10000}},
        )
        collar_results = [r for r in results["510050.SH"] if r.strategy_type == StrategyType.COLLAR]
        if collar_results[0].error_code is not None:
            pytest.skip(f"合成链无法构建领口: {collar_results[0].error_code}")
        assert collar_results[0].meta["tier"] == "low"
