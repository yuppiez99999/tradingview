"""Portfolio Builder MVSK P5-1 shadow 接入单元测试.

覆盖场景:
    1. MVSK 分支: use_mvsk=True + mvsk_mode=shadow 时触发 MVSK 优化
    2. shadow 差异记录: mvsk_p5_daily_diff.jsonl 追加权重差异记录
    3. 冷启动数据不足: 历史数据 < 378 天时 fail-closed + 告警
    4. kill_switch 降级: kill_switch_triggered=True 时降级至 BL+MV

对齐 tasks T1.5.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import patch

import numpy as np
import pandas as pd

_PROJECT_ROOT = Path(__file__).resolve().parents[3]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from utils.universe.portfolio_builder import (
    MVSK_EXCLUDED_STYLES,
    MVSK_GAMMA_K,
    MVSK_GAMMA_S,
    MVSK_WARMUP_DAYS_REQUIRED,
    Holding,
    LayeredPortfolio,
    MVSKShadowResult,
    _compute_baseline_weights,
    _is_mvsk_optimizable,
    _load_historical_returns,
    _save_shadow_diff,
    apply_mvsk_shadow_to_mid_layer,
)


def _build_test_portfolio() -> LayeredPortfolio:
    """构造测试用分层组合 (含中线层)."""
    portfolio = LayeredPortfolio(trade_date="2026-08-18")
    mid_symbols = ["600519", "000858", "601318", "600036", "000333"]
    for i, sym in enumerate(mid_symbols):
        portfolio.holdings.append(
            Holding(
                symbol=sym,
                layer="mid",
                weight=0.06,
                score_composite=0.8 - i * 0.05,
            )
        )
    short_symbols = ["600000", "000001"]
    for sym in short_symbols:
        portfolio.holdings.append(
            Holding(
                symbol=sym,
                layer="short",
                weight=0.10,
            )
        )
    return portfolio


# ============================================================
# 场景 1: MVSK 分支
# ============================================================


class TestMVSKBranch:
    """MVSK 分支: use_mvsk=True + mvsk_mode=shadow 时触发 MVSK 优化."""

    def test_mvsk_shadow_returns_result(self):
        portfolio = _build_test_portfolio()
        _, result = apply_mvsk_shadow_to_mid_layer(
            portfolio,
            trade_date="2026-08-18",
            use_mvsk=True,
            mvsk_mode="shadow",
        )
        assert isinstance(result, MVSKShadowResult)

    def test_mvsk_shadow_success(self):
        portfolio = _build_test_portfolio()
        _, result = apply_mvsk_shadow_to_mid_layer(
            portfolio,
            trade_date="2026-08-18",
            use_mvsk=True,
            mvsk_mode="shadow",
        )
        assert result.success is True
        assert len(result.mvsk_weights) > 0

    def test_mvsk_disabled_returns_skip(self):
        portfolio = _build_test_portfolio()
        _, result = apply_mvsk_shadow_to_mid_layer(
            portfolio,
            use_mvsk=False,
            mvsk_mode="shadow",
        )
        assert result.success is False
        assert "跳过" in result.error_message

    def test_mvsk_shadow_mode_flag(self):
        portfolio = _build_test_portfolio()
        _, result = apply_mvsk_shadow_to_mid_layer(
            portfolio,
            use_mvsk=True,
            mvsk_mode="shadow",
        )
        assert result.shadow_mode is True

    def test_mvsk_active_mode_modifies_weights(self):
        portfolio = _build_test_portfolio()
        modified_portfolio, result = apply_mvsk_shadow_to_mid_layer(
            portfolio,
            use_mvsk=True,
            mvsk_mode="active",
        )
        assert result.success is True
        assert result.shadow_mode is False

    def test_no_mid_holdings_returns_error(self):
        portfolio = LayeredPortfolio(trade_date="2026-08-18")
        portfolio.holdings.append(Holding(symbol="600000", layer="short", weight=0.1))
        _, result = apply_mvsk_shadow_to_mid_layer(
            portfolio,
            use_mvsk=True,
            mvsk_mode="shadow",
        )
        assert result.success is False
        assert "无持仓" in result.error_message


# ============================================================
# 场景 2: shadow 差异记录
# ============================================================


class TestShadowDiffRecording:
    """shadow 差异记录: mvsk_p5_daily_diff.jsonl 追加权重差异记录."""

    def test_shadow_diff_l2_computed(self):
        portfolio = _build_test_portfolio()
        _, result = apply_mvsk_shadow_to_mid_layer(
            portfolio,
            trade_date="2026-08-18",
            use_mvsk=True,
            mvsk_mode="shadow",
        )
        if result.success:
            assert result.weight_diff_l2 >= 0.0

    def test_shadow_weights_present(self):
        portfolio = _build_test_portfolio()
        _, result = apply_mvsk_shadow_to_mid_layer(
            portfolio,
            trade_date="2026-08-18",
            use_mvsk=True,
            mvsk_mode="shadow",
        )
        if result.success:
            assert len(result.mvsk_weights) > 0
            assert len(result.baseline_weights) > 0

    def test_save_shadow_diff_writes_jsonl(self, tmp_path, monkeypatch):
        report_path = tmp_path / "mvsk_diff.jsonl"
        monkeypatch.setattr(
            "utils.universe.portfolio_builder.MVSK_SHADOW_REPORT_PATH",
            report_path,
        )
        _save_shadow_diff(
            date="2026-08-18",
            mvsk_weights={"A": 0.5, "B": 0.5},
            baseline_weights={"A": 0.4, "B": 0.6},
            weight_diff_l2=0.1414,
        )
        assert report_path.exists()
        lines = report_path.read_text(encoding="utf-8").strip().split("\n")
        assert len(lines) == 1
        record = json.loads(lines[0])
        assert record["date"] == "2026-08-18"
        assert record["weight_diff_l2"] == 0.1414
        assert record["gamma_s"] == MVSK_GAMMA_S
        assert record["gamma_k"] == MVSK_GAMMA_K

    def test_save_shadow_diff_appends(self, tmp_path, monkeypatch):
        report_path = tmp_path / "mvsk_diff.jsonl"
        monkeypatch.setattr(
            "utils.universe.portfolio_builder.MVSK_SHADOW_REPORT_PATH",
            report_path,
        )
        for i in range(3):
            _save_shadow_diff(
                date=f"2026-08-{18+i}",
                mvsk_weights={"A": 0.5},
                baseline_weights={"A": 0.5},
                weight_diff_l2=0.0,
            )
        lines = report_path.read_text(encoding="utf-8").strip().split("\n")
        assert len(lines) == 3

    def test_save_shadow_diff_empty_date_fail_closed(self, tmp_path, monkeypatch):
        """空 date fail-closed: 不落盘, 不产生 {"date": ""} 脏记录.

        2026-09-05 治理: apply_mvsk_shadow_to_mid_layer(trade_date="") 默认参
        曾把 {"date": ""} 记录写进生产 jsonl (09-04 17:30:46 测试批实锤).
        """
        report_path = tmp_path / "mvsk_diff.jsonl"
        monkeypatch.setattr(
            "utils.universe.portfolio_builder.MVSK_SHADOW_REPORT_PATH",
            report_path,
        )
        ret = _save_shadow_diff(
            date="",
            mvsk_weights={"A": 0.5},
            baseline_weights={"A": 0.5},
            weight_diff_l2=0.0,
        )
        assert ret == ""
        assert not report_path.exists()


# ============================================================
# 场景 3: 冷启动数据不足
# ============================================================


class TestColdStartInsufficientData:
    """冷启动数据不足: 历史数据 < 378 天时 fail-closed + 告警."""

    def test_load_historical_returns_default_sufficient(self):
        returns = _load_historical_returns(["A", "B", "C"])
        assert returns is not None
        assert returns.shape[0] == MVSK_WARMUP_DAYS_REQUIRED
        assert returns.shape[1] == 3

    def test_load_historical_returns_insufficient_from_file(self, tmp_path):
        short_path = tmp_path / "short_returns.csv"
        pd.DataFrame(np.random.randn(100, 3)).to_csv(short_path, index=False)
        with patch("utils.universe.portfolio_builder.pd") as mock_pd:
            mock_pd.read_parquet.side_effect = ImportError("no pyarrow")
            returns = _load_historical_returns(
                ["A", "B", "C"],
                feature_store_path=short_path,
            )
        assert returns is None

    def test_mvsk_shadow_insufficient_data_fail_closed(self, tmp_path):
        short_path = tmp_path / "short_returns.csv"
        pd.DataFrame(np.random.randn(100, 5)).to_csv(short_path, index=False)
        portfolio = _build_test_portfolio()
        with patch(
            "utils.universe.portfolio_builder._load_historical_returns",
            return_value=None,
        ):
            _, result = apply_mvsk_shadow_to_mid_layer(
                portfolio,
                use_mvsk=True,
                mvsk_mode="shadow",
                feature_store_path=short_path,
            )
        assert result.success is False
        assert result.data_sufficient is False
        assert "不足" in result.error_message

    def test_warmup_days_constant(self):
        assert MVSK_WARMUP_DAYS_REQUIRED == 378

    def test_load_historical_returns_sufficient_from_parquet(self, tmp_path):
        """feature_store_path 指向充足 parquet 时返回真实数据 (非 fallback)."""
        n_days = 400
        n_symbols = 4
        real_data = np.random.default_rng(99).normal(0.001, 0.02, (n_days, n_symbols))
        parquet_path = tmp_path / "mid_returns.parquet"
        pd.DataFrame(real_data, columns=["A", "B", "C", "D"]).to_parquet(
            parquet_path, index=False
        )
        returns = _load_historical_returns(
            ["A", "B", "C", "D"],
            feature_store_path=parquet_path,
        )
        assert returns is not None
        assert returns.shape == (MVSK_WARMUP_DAYS_REQUIRED, n_symbols)
        assert np.allclose(returns, real_data[-MVSK_WARMUP_DAYS_REQUIRED:])

    def test_mvsk_shadow_sufficient_data_from_parquet(self, tmp_path):
        """apply_mvsk_shadow_to_mid_layer 传入真实 parquet → data_sufficient=True."""
        n_days = 400
        n_symbols = 4
        real_data = np.random.default_rng(77).normal(0.001, 0.02, (n_days, n_symbols))
        parquet_path = tmp_path / "mid_returns.parquet"
        pd.DataFrame(real_data, columns=["510300", "510500", "513100", "512890"]).to_parquet(
            parquet_path, index=False
        )
        portfolio = LayeredPortfolio(
            trade_date="2026-09-01",
            holdings=[
                Holding(symbol=s, name=s, layer="mid", weight=0.25)
                for s in ["510300", "510500", "513100", "512890"]
            ],
        )
        _, result = apply_mvsk_shadow_to_mid_layer(
            portfolio,
            trade_date="2026-09-01",
            use_mvsk=True,
            mvsk_mode="shadow",
            feature_store_path=parquet_path,
        )
        assert result.data_sufficient is True

    def test_load_historical_returns_aligns_cache_cols_to_symbols(self, tmp_path):
        """治理⑤回归: 缓存含防御仓等多余列时按 symbols 对齐列序.

        真实场景 (2026-09-07): mvsk_mid_layer_returns_378d.parquet 为全 mid
        标的 26 列 (含 511010 国债), 剔防御后决策域 symbols 25 → 修复前
        df.values 返 26 列, cov_matrix 26x26 vs n=25 → MVSK 优化失败.
        """
        n_days = 400
        real_data = np.random.default_rng(55).normal(0.001, 0.02, (n_days, 5))
        parquet_path = tmp_path / "mid_returns_extra_col.parquet"
        pd.DataFrame(
            real_data, columns=["510300", "510500", "513100", "512890", "511010"]
        ).to_parquet(parquet_path, index=False)
        symbols = ["510300", "510500", "513100", "512890"]  # 511010 已剔
        returns = _load_historical_returns(symbols, feature_store_path=parquet_path)
        assert returns is not None
        assert returns.shape == (MVSK_WARMUP_DAYS_REQUIRED, 4)
        # 值须等于缓存中对应 4 列 (非 511010 所在列), 且列序与 symbols 对齐
        assert np.allclose(
            returns,
            real_data[-MVSK_WARMUP_DAYS_REQUIRED:, :4],
        )

    def test_mvsk_shadow_cache_with_defensive_col_optimizes(self, tmp_path):
        """治理⑤回归: parquet 缓存含防御仓列时 apply 仍成功且 keys 无防御仓.

        真实复现 (2026-09-07 22:09 前): 26 列缓存 + 25 决策域 symbols →
        _compute_mvsk_weights 内 cov_matrix 26x26 != n=25 → success=False
        (error="MVSK 优化失败", L2 兜底 0.0 是假回落). 修复列对齐后须成功.
        """
        n_days = 400
        n_mid = 5
        real_data = np.random.default_rng(66).normal(0.001, 0.02, (n_days, n_mid))
        syms = ["510300", "510500", "513100", "512890", "511010"]
        parquet_path = tmp_path / "mid_returns_5col.parquet"
        pd.DataFrame(real_data, columns=syms).to_parquet(parquet_path, index=False)
        portfolio = LayeredPortfolio(
            trade_date="2026-09-07",
            holdings=[
                Holding(symbol="510300", name="a", layer="mid", style="宽基", weight=0.20),
                Holding(symbol="510500", name="b", layer="mid", style="宽基", weight=0.18),
                Holding(symbol="513100", name="c", layer="mid", style="科技", weight=0.15),
                Holding(symbol="512890", name="d", layer="mid", style="宽基", weight=0.10),
                # 防御仓: 与缓存中的第 5 列 511010 对应
                Holding(symbol="511010", name="国债ETF", layer="mid", style="国债", weight=0.37),
            ],
        )
        _, result = apply_mvsk_shadow_to_mid_layer(
            portfolio,
            trade_date="2026-09-07",
            use_mvsk=True,
            mvsk_mode="shadow",
            feature_store_path=parquet_path,
        )
        assert result.success is True, result.error_message
        assert "511010" not in result.mvsk_weights
        assert "511010" not in result.baseline_weights
        assert len(result.mvsk_weights) == 4
        assert result.weight_diff_l2 > 0


# ============================================================
# 场景 4: kill_switch 降级
# ============================================================


class TestKillSwitchDegradation:
    """kill_switch 降级: kill_switch_triggered=True 时降级至 BL+MV."""

    def test_kill_switch_returns_degradation(self):
        portfolio = _build_test_portfolio()
        _, result = apply_mvsk_shadow_to_mid_layer(
            portfolio,
            use_mvsk=True,
            mvsk_mode="shadow",
            kill_switch_triggered=True,
        )
        assert result.success is False
        assert "kill_switch" in result.error_message
        assert "降级" in result.error_message

    def test_kill_switch_preserves_portfolio(self):
        portfolio = _build_test_portfolio()
        original_weights = {h.symbol: h.weight for h in portfolio.holdings}
        returned_portfolio, _ = apply_mvsk_shadow_to_mid_layer(
            portfolio,
            use_mvsk=True,
            mvsk_mode="shadow",
            kill_switch_triggered=True,
        )
        for h in returned_portfolio.holdings:
            assert h.weight == original_weights[h.symbol]

    def test_kill_switch_takes_precedence_over_use_mvsk(self):
        portfolio = _build_test_portfolio()
        _, result = apply_mvsk_shadow_to_mid_layer(
            portfolio,
            use_mvsk=False,
            mvsk_mode="shadow",
            kill_switch_triggered=True,
        )
        assert "kill_switch" in result.error_message


# ============================================================
# 辅助测试: 基线权重计算
# ============================================================


class TestBaselineWeights:
    """BL+MV(252) 基线权重计算."""

    def test_compute_baseline_weights_mid_layer(self):
        portfolio = _build_test_portfolio()
        weights = _compute_baseline_weights(portfolio.holdings)
        assert len(weights) == 5
        assert abs(sum(weights.values()) - 1.0) < 1e-6

    def test_compute_baseline_weights_excludes_short(self):
        portfolio = _build_test_portfolio()
        weights = _compute_baseline_weights(portfolio.holdings)
        assert "600000" not in weights
        assert "000001" not in weights

    def test_compute_baseline_weights_empty(self):
        weights = _compute_baseline_weights([])
        assert weights == {}

    def test_compute_baseline_weights_zero_total(self):
        holdings = [
            Holding(symbol="A", layer="mid", weight=0.0),
            Holding(symbol="B", layer="mid", weight=0.0),
        ]
        weights = _compute_baseline_weights(holdings)
        assert len(weights) == 2
        assert abs(sum(weights.values()) - 1.0) < 1e-6

    def test_compute_baseline_weights_excludes_defensive_style(self):
        """治理⑤回归 (2026-09-07): style=国债 防御仓不进基线.

        positions.json 中 511010.SH (国债, 组合 ~51.5%) 是静态防御仓, 若计入
        baseline 再被 MVSK max_weight=0.10 压制 → 单点 0.515 vs 0.100 制造
        0.4154 差异 → 每日 weight_diff_l2 恒 ~0.45 触发 fail-fast 误报.
        """
        portfolio = _build_test_portfolio()
        # 追加一个国债防御仓 (真实占比 ~51.5%)
        portfolio.holdings.append(
            Holding(symbol="511010", name="上证5年期国债ETF", layer="mid", style="国债", weight=0.5154)
        )
        weights = _compute_baseline_weights(portfolio.holdings)
        assert "511010" not in weights
        # 其余 5 个 mid 风险仓仍全量保留且归一
        assert len(weights) == 5
        assert abs(sum(weights.values()) - 1.0) < 1e-6

    def test_mvsk_apply_excludes_defensive_from_all_keys(self):
        """治理⑤回归: apply_mvsk_shadow_to_mid_layer 的 mvsk/baseline/all_keys
        不含防御仓, 国债不污染 weight_diff_l2."""
        portfolio = _build_test_portfolio()
        portfolio.holdings.append(
            Holding(symbol="511010", name="上证5年期国债ETF", layer="mid", style="国债", weight=0.5154)
        )
        # 关闭收益率缓存依赖 (默认合成矩阵路径), 仅验证 keys 域
        _, result = apply_mvsk_shadow_to_mid_layer(
            portfolio,
            trade_date="2026-09-07",
            use_mvsk=True,
            mvsk_mode="shadow",
        )
        if result.success:
            assert "511010" not in result.mvsk_weights
            assert "511010" not in result.baseline_weights
            assert len(result.mvsk_weights) == 5


# ============================================================
# 场景 5: active 模式权重守恒 (P2-2 回归, 2026-09-08)
# ============================================================


class TestMVSKActiveModeWeightRescaling:
    """P2-2 回归 (2026-09-08 审查): active 模式权重归一化守恒.

    mvsk_weights 是可优化子集内归一化 (和=1.0), 防御/现金仓不在其中。
    修复前: 直接赋给全组合 mid 层 → 全组合权重和 = 1.0 + 防御仓权重
    (511010 国债 0.37 → 1.37), shadow 切 active 即爆。
    修复 (portfolio_builder.py active 分支): 按子集原始权重和 subset_total
    缩放回原占比, 防御/现金仓权重保持不动。

    不变量:
        I1 全组合权重和守恒 (apply 前后差 < 1e-9)
        I2 防御/现金仓权重逐仓不变
        I3 可优化子集权重和守恒, 且每仓 = mvsk_weights[symbol] * subset_total
    """

    @staticmethod
    def _build_portfolio_with_defensive() -> LayeredPortfolio:
        """4 个可优化 mid 仓 (子集和 0.63) + 1 个国债防御仓 0.37."""
        return LayeredPortfolio(
            trade_date="2026-09-08",
            holdings=[
                Holding(symbol="510300", name="a", layer="mid", style="宽基", weight=0.20),
                Holding(symbol="510500", name="b", layer="mid", style="宽基", weight=0.18),
                Holding(symbol="513100", name="c", layer="mid", style="科技", weight=0.15),
                Holding(symbol="512890", name="d", layer="mid", style="宽基", weight=0.10),
                Holding(symbol="511010", name="国债ETF", layer="mid", style="国债", weight=0.37),
            ],
        )

    @staticmethod
    def _write_returns_cache(tmp_path: Path, seed: int) -> Path:
        """写入含防御仓列的 5 列收益率 parquet 缓存 (MVSK 真实优化路径)."""
        n_days = 400
        data = np.random.default_rng(seed).normal(0.001, 0.02, (n_days, 5))
        syms = ["510300", "510500", "513100", "512890", "511010"]
        parquet_path = tmp_path / f"mid_returns_active_{seed}.parquet"
        pd.DataFrame(data, columns=syms).to_parquet(parquet_path, index=False)
        return parquet_path

    def test_active_mode_conserves_total_weight(self, tmp_path):
        """I1: 全组合权重和守恒 — 修复前 0.63 子集直接赋值会使总权重和 = 1.37."""
        parquet_path = self._write_returns_cache(tmp_path, 77)
        portfolio = self._build_portfolio_with_defensive()
        before_total = sum(h.weight for h in portfolio.holdings)
        _, result = apply_mvsk_shadow_to_mid_layer(
            portfolio,
            trade_date="2026-09-08",
            use_mvsk=True,
            mvsk_mode="active",
            feature_store_path=parquet_path,
        )
        assert result.success is True, result.error_message
        after_total = sum(h.weight for h in portfolio.holdings)
        assert abs(after_total - before_total) < 1e-9

    def test_active_mode_preserves_defensive_weights(self, tmp_path):
        """I2: 防御/现金仓 (style=国债) 权重逐仓不变."""
        parquet_path = self._write_returns_cache(tmp_path, 78)
        portfolio = self._build_portfolio_with_defensive()
        before = {
            h.symbol: h.weight
            for h in portfolio.holdings
            if h.style in MVSK_EXCLUDED_STYLES
        }
        assert before == {"511010": 0.37}
        _, result = apply_mvsk_shadow_to_mid_layer(
            portfolio,
            trade_date="2026-09-08",
            use_mvsk=True,
            mvsk_mode="active",
            feature_store_path=parquet_path,
        )
        assert result.success is True, result.error_message
        for h in portfolio.holdings:
            if h.style in MVSK_EXCLUDED_STYLES:
                assert h.weight == before[h.symbol]

    def test_active_mode_subset_rescaled_to_original_share(self, tmp_path):
        """I3: 可优化子集权重和守恒, 且每仓 = mvsk_weights[symbol] * subset_total."""
        parquet_path = self._write_returns_cache(tmp_path, 79)
        portfolio = self._build_portfolio_with_defensive()
        subset_before = sum(
            h.weight for h in portfolio.holdings if _is_mvsk_optimizable(h)
        )
        assert abs(subset_before - 0.63) < 1e-12
        _, result = apply_mvsk_shadow_to_mid_layer(
            portfolio,
            trade_date="2026-09-08",
            use_mvsk=True,
            mvsk_mode="active",
            feature_store_path=parquet_path,
        )
        assert result.success is True, result.error_message
        subset_after = sum(
            h.weight for h in portfolio.holdings if _is_mvsk_optimizable(h)
        )
        assert abs(subset_after - subset_before) < 1e-9
        for h in portfolio.holdings:
            if _is_mvsk_optimizable(h):
                expected = result.mvsk_weights[h.symbol] * subset_before
                assert abs(h.weight - expected) < 1e-12

    def test_active_mode_without_defensive_also_conserves(self):
        """纯可优化组合 (无防御仓) active 后全组合权重和与 mid 层权重和守恒."""
        portfolio = _build_test_portfolio()  # 5 mid (各 0.06) + 2 short (各 0.10), 总 0.50
        before_total = sum(h.weight for h in portfolio.holdings)
        _, result = apply_mvsk_shadow_to_mid_layer(
            portfolio,
            trade_date="2026-09-08",
            use_mvsk=True,
            mvsk_mode="active",
        )
        assert result.success is True, result.error_message
        after_total = sum(h.weight for h in portfolio.holdings)
        assert abs(after_total - before_total) < 1e-9
        mid_after = sum(
            h.weight for h in portfolio.holdings if _is_mvsk_optimizable(h)
        )
        assert abs(mid_after - 0.30) < 1e-9
