"""test_factor_discovery_unit.py — 因子挖掘工具单元测试

覆盖要点:
    - UNIVERSE_PRESETS 常量
    - FactorValidationResult / DiscoveryReport dataclass
    - FactorDataFetcher._normalize_columns (列名映射/数值转换)
    - FactorDataFetcher.get_available_cached_symbols (缓存不存在)
    - FactorValidator 常量
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pandas as pd
import pytest

from utils.factor_research.factor_discovery import (
    UNIVERSE_PRESETS,
    DiscoveryReport,
    FactorDataFetcher,
    FactorValidationResult,
    FactorValidator,
)

# ============================================================
# 常量
# ============================================================


class TestConstants:
    @pytest.mark.unit
    def test_universe_presets(self):
        assert "etf_core" in UNIVERSE_PRESETS
        assert "etf_broad" in UNIVERSE_PRESETS
        assert "etf50" in UNIVERSE_PRESETS

    @pytest.mark.unit
    def test_etf_core_subset_of_broad(self):
        assert set(UNIVERSE_PRESETS["etf_core"]).issubset(
            set(UNIVERSE_PRESETS["etf_broad"])
        )

    @pytest.mark.unit
    def test_etf50_largest(self):
        assert len(UNIVERSE_PRESETS["etf50"]) > len(UNIVERSE_PRESETS["etf_broad"])


# ============================================================
# FactorValidationResult
# ============================================================


class TestFactorValidationResult:
    @pytest.mark.unit
    def test_defaults(self):
        r = FactorValidationResult(factor_name="MOM_20D", category="momentum")
        assert r.ic_mean == 0.0
        assert r.effective is False
        assert r.direction == "positive"
        assert r.score == 0.0

    @pytest.mark.unit
    def test_with_values(self):
        r = FactorValidationResult(
            factor_name="MOM_20D",
            category="momentum",
            ic_mean=0.05,
            ic_ir=0.6,
            effective=True,
        )
        assert r.ic_mean == 0.05
        assert r.effective is True


# ============================================================
# DiscoveryReport
# ============================================================


class TestDiscoveryReport:
    @pytest.mark.unit
    def test_defaults(self):
        r = DiscoveryReport(
            universe=["A", "B"],
            start_date="2024-01-01",
            end_date="2024-06-01",
            n_symbols=2,
            n_dates=100,
            n_factors_tested=10,
        )
        assert r.effective_factors == []
        assert r.strong_factors == []
        assert r.generation_time == ""


# ============================================================
# FactorDataFetcher._normalize_columns
# ============================================================


class TestNormalizeColumns:
    @pytest.mark.unit
    def test_chinese_column_mapping(self):
        fetcher = FactorDataFetcher.__new__(FactorDataFetcher)
        df = pd.DataFrame(
            {
                "日期": ["2024-01-01", "2024-01-02"],
                "开盘": [10.0, 11.0],
                "收盘": [10.5, 11.5],
                "最高": [11.0, 12.0],
                "最低": [9.5, 10.5],
                "成交量": [1000, 2000],
            }
        )
        result = fetcher._normalize_columns(df)
        assert "open" in result.columns
        assert "close" in result.columns
        assert result.index.name == "date"  # date 被设为 index

    @pytest.mark.unit
    def test_numeric_conversion(self):
        fetcher = FactorDataFetcher.__new__(FactorDataFetcher)
        df = pd.DataFrame(
            {
                "日期": ["2024-01-01"],
                "收盘": ["10.5"],
                "成交量": ["1000"],
            }
        )
        result = fetcher._normalize_columns(df)
        assert pd.api.types.is_numeric_dtype(result["close"])
        assert pd.api.types.is_numeric_dtype(result["volume"])


# ============================================================
# FactorDataFetcher.get_available_cached_symbols
# ============================================================


class TestGetAvailableCachedSymbols:
    @pytest.mark.unit
    def test_cache_dir_not_exists(self):
        fetcher = FactorDataFetcher.__new__(FactorDataFetcher)
        with patch.object(
            FactorDataFetcher, "CACHE_DIR", MagicMock(exists=lambda: False)
        ):
            symbols = fetcher.get_available_cached_symbols()
        assert symbols == []


# ============================================================
# FactorValidator 常量
# ============================================================


class TestFactorValidator:
    @pytest.mark.unit
    def test_constants(self):
        """P1-3 (Issue #13): 判定口径改由 config/risk_thresholds.yaml 唯一事实源提供。"""
        from utils.risk_thresholds import get_factor_validation_config

        cfg = get_factor_validation_config()
        assert FactorValidator.MIN_SAMPLES == cfg["min_samples"] == 60
        assert (
            FactorValidator.IC_EFFECTIVE_THRESHOLD == cfg["ic_effective_threshold"] == 0.03
        )
        assert FactorValidator.IC_STRONG_THRESHOLD == cfg["ic_strong_threshold"]
        assert FactorValidator.IR_STRONG_THRESHOLD == cfg["ir_strong_threshold"] == 0.5
        assert (
            FactorValidator.IR_EFFECTIVE_THRESHOLD
            == cfg["ir_effective_threshold"]
            == 0.5
        )
        assert FactorValidator.SCORE_MODE == "signed"
        assert FactorValidator.SHADOW_LEGACY is True

    @pytest.mark.unit
    def test_legacy_constants_preserved_for_shadow(self):
        """旧口径常量保留 (仅供影子对照, 不参与判定)。"""
        assert FactorValidator.LEGACY_MIN_SAMPLES == 5
        assert FactorValidator.LEGACY_IC_EFFECTIVE_THRESHOLD == 0.02
        assert FactorValidator.LEGACY_IR_EFFECTIVE_THRESHOLD == 0.2

    @pytest.mark.unit
    def test_signed_score_penalizes_negative_factor(self):
        """P1-3 核心回归: 稳定反向因子必须得负分, 不再与正向同分。

        旧口径 (三项 abs) 下正/反向因子得分几乎相同 (实测 49.71 vs 49.59),
        导致"稳定反向"被当成"稳定有效"沉淀。
        """
        import numpy as np
        import pandas as pd

        rng = np.random.default_rng(7)
        dates = pd.date_range("2026-01-01", periods=80, freq="D")
        cols = [f"S{i:02d}" for i in range(30)]
        fvals = pd.DataFrame(rng.normal(size=(80, 30)), index=dates, columns=cols)
        rets_pos = fvals * 0.6 + rng.normal(scale=0.5, size=(80, 30))
        rets_neg = -rets_pos

        validator = FactorValidator()
        pos = validator._validate_single("POS", fvals, {1: rets_pos, 5: rets_pos})
        neg = validator._validate_single("NEG", fvals, {1: rets_neg, 5: rets_neg})

        assert pos.ic_mean > 0 and neg.ic_mean < 0
        assert pos.score > 0 > neg.score, (
            f"带符号评分应能区分方向: pos={pos.score:.2f}, neg={neg.score:.2f}"
        )
        # 影子口径仍复现旧行为: 反向因子同样得正分 (旧公式 abs 饱和),
        # 差异仅来自 ic_positive_ratio 一项, 远小于新口径的方向差
        assert neg.legacy_score > 0
        assert abs(pos.legacy_score - neg.legacy_score) < abs(pos.score - neg.score)

    @pytest.mark.unit
    def test_insufficient_samples_returns_none(self):
        """P1-3: 样本 < MIN_SAMPLES(60) 时不再用 5 个样本判定有效性。"""
        import numpy as np
        import pandas as pd

        rng = np.random.default_rng(11)
        dates = pd.date_range("2026-01-01", periods=30, freq="D")
        cols = [f"S{i:02d}" for i in range(20)]
        fvals = pd.DataFrame(rng.normal(size=(30, 20)), index=dates, columns=cols)
        rets = fvals * 0.6

        validator = FactorValidator()
        assert validator._validate_single("SHORT", fvals, {1: rets, 5: rets}) is None

    @pytest.mark.unit
    def test_default_forward_days(self):
        validator = FactorValidator()
        assert validator.forward_days == [1, 5, 10, 20]

    @pytest.mark.unit
    def test_custom_forward_days(self):
        validator = FactorValidator(forward_days=[5, 20])
        assert validator.forward_days == [5, 20]


# ============================================================
# P1-3 影子报告完备性 (Issue #13 · 口径切换淘汰名单)
# ============================================================
#
# 背景: 影子对照的初版只输出"旧有效 & 新无效"一张表, 依据是
# ``report.all_factors_sorted``。但 ``_validate_single`` 在样本不足时返回 ``None``,
# 该因子**不进入** ``all_factors_sorted``, 于是报告的任何章节都没有它。
# 若其样本数落在 [LEGACY_MIN_SAMPLES, MIN_SAMPLES), 旧口径本可给出判定,
# 仅看该表会遗漏它 => "可直接出淘汰名单"的说法不成立。
#
# 下表锁定: 名单必须同时覆盖 A 类 (两口径可判) 与 B 类 (新口径不可判)。


class TestShadowEliminationList:
    """影子报告淘汰候选名单的两维完备性。"""

    @staticmethod
    def _panel(n_days: int, n_cols: int = 30, seed: int = 11):
        import numpy as np

        rng = np.random.default_rng(seed)
        dates = pd.date_range("2025-01-01", periods=n_days, freq="B")
        cols = [f"S{i:02d}" for i in range(n_cols)]
        return pd.DataFrame(rng.normal(size=(n_days, n_cols)), index=dates, columns=cols)

    @pytest.mark.unit
    def test_insufficient_factor_is_recorded_not_silently_dropped(self):
        """样本不足的因子必须被显式记录, 不能静默消失。"""

        fvals = self._panel(20)
        rets = fvals * 0.3
        validator = FactorValidator()
        sink: list = []
        assert validator._validate_single("SHORT_20D", fvals, {1: rets, 5: rets}, sink) is None

        assert len(sink) == 1
        rec = sink[0]
        assert rec.factor_name == "SHORT_20D"
        assert rec.n_samples == 20
        # 20 >= LEGACY_MIN_SAMPLES(5) => 旧口径下"会"被判定, 属口径切换冲击面
        assert rec.legacy_decidable is True

    @pytest.mark.unit
    def test_very_short_factor_not_flagged_as_criteria_impact(self):
        """样本 < 旧口径下限时两口径都不可判, 不应计入口径切换冲击面。"""
        fvals = self._panel(3)
        rets = fvals * 0.3
        validator = FactorValidator()
        sink: list = []
        assert validator._validate_single("TINY_3D", fvals, {1: rets, 5: rets}, sink) is None
        assert sink[0].legacy_decidable is False

    @pytest.mark.unit
    def test_validate_all_exposes_insufficient_samples(self):
        """validate_all 必须把样本不足清单暴露给调用方 (报告完备性的前提)。"""
        import numpy as np

        dates = pd.date_range("2025-01-01", periods=120, freq="B")
        cols = [f"S{i:02d}" for i in range(30)]
        # 用带噪声的行情, 保证各日 IC 可算出 (常量序列会让 spearman 退化为 NaN)
        rng = np.random.default_rng(23)
        daily = {
            c: pd.DataFrame(
                {
                    "close": 10
                    * np.cumprod(1 + rng.normal(0.0005, 0.01, len(dates)))
                },
                index=dates,
            )
            for c in cols
        }
        validator = FactorValidator()
        validator.validate_all({"SHORT_20D": self._panel(20)}, daily)

        names = [r.factor_name for r in validator.last_insufficient_samples]
        assert names == ["SHORT_20D"]
        # 20 日面板在 1 日/5 日远期收益下应能算出 IC, 样本数接近 20 (非 0)
        assert validator.last_insufficient_samples[0].n_samples > 0

    @pytest.mark.unit
    def test_report_lists_new_undecidable_but_legacy_decidable(self):
        """B 类清单: 只看"旧有效/新无效"表会漏掉的那批因子必须单独列出。"""
        import tempfile
        from pathlib import Path

        from utils.factor_research.factor_discovery import (
            InsufficientSamplesRecord,
            ReportGenerator,
        )

        report = DiscoveryReport(
            universe=["X"],
            start_date="2025-01-01",
            end_date="2026-01-01",
            n_symbols=30,
            n_dates=120,
            n_factors_tested=1,
            insufficient_samples=[
                InsufficientSamplesRecord("SHORT_20D", "SHORT", 20, True),
                InsufficientSamplesRecord("TINY_3D", "TINY", 3, False),
            ],
            generation_time="test",
        )
        content = ReportGenerator.generate_markdown(report, Path(tempfile.mkdtemp()))

        assert "口径影子对照" in content
        assert "SHORT_20D" in content, "B 类因子必须出现在淘汰候选名单中"
        assert "需补 40 日" in content, "应给出距新口径的样本缺口"
        assert "TINY_3D" not in content.split("## 备注")[0], (
            "两口径均不可判的因子不应计入口径切换冲击面"
        )

    @pytest.mark.unit
    def test_report_impact_count_covers_both_classes(self):
        """冲击面总数 = A 类 + B 类, 不能只报 A 类。"""
        import tempfile
        from pathlib import Path

        from utils.factor_research.factor_discovery import (
            InsufficientSamplesRecord,
            ReportGenerator,
        )

        report = DiscoveryReport(
            universe=["X"],
            start_date="2025-01-01",
            end_date="2026-01-01",
            n_symbols=30,
            n_dates=120,
            n_factors_tested=0,
            insufficient_samples=[
                InsufficientSamplesRecord("SHORT_20D", "SHORT", 20, True),
            ],
            generation_time="test",
        )
        content = ReportGenerator.generate_markdown(report, Path(tempfile.mkdtemp()))
        assert "口径切换冲击面: 共 1 个因子" in content
        assert "B 类 1 个" in content
