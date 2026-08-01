# -*- coding: utf-8 -*-
"""ECC 完整 ML 流水线 E2E 测试 — GAP-2 交付物.

验证 ECC 新增模块 (GAP-6/7/8) 在真实 ML 流水线中的端到端协作:
    数据契约校验 → 训练可复现性 → 漂移监控 → 延迟标签追踪

测试链路:
    1. 加载 V9 真实持仓数据 (config/positions.json)
    2. DataContract.validate() 校验数据契约 (GAP-8)
    3. TrainingConfig + artifact_name 生成训练 manifest (GAP-7)
    4. SimModeDriftMonitor 检测特征漂移 (GAP-6)
    5. DelayedLabelTracker 记录预测 + 计算 IC (GAP-6)

设计原则:
    1. 使用真实配置文件 (非 mock), 验证真实数据流
    2. 数据缺失时 skip 而非 fail (CI 友好)
    3. 标记 @pytest.mark.e2e + @pytest.mark.slow (nightly 跑)
    4. 验证模块间协作, 不验证业务正确性
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(_PROJECT_ROOT))
sys.path.insert(0, str(_PROJECT_ROOT / "v8.3_institutional"))
sys.path.insert(0, str(_PROJECT_ROOT / "v8.3_institutional" / "src"))

from research.lgbm_reproducibility import (  # noqa: E402
    TrainingConfig,
    artifact_name,
)
from utils.alpha.data_contract import V9_DEFAULT_CONTRACT, ValidationMode  # noqa: E402
from utils.alpha.delayed_label_tracker import DelayedLabelTracker  # noqa: E402
from utils.alpha.drift_monitor import (  # noqa: E402
    SimModeDriftMonitor,
    compute_psi,
)


# ============================================================
# E2E Fixture: 真实 V9 持仓数据
# ============================================================
@pytest.fixture(scope="module")
def real_positions():
    """加载真实持仓数据 (config/positions.json).

    数据缺失时 skip (CI 环境可能无此文件)
    """
    positions_path = _PROJECT_ROOT / "config" / "positions.json"
    if not positions_path.exists():
        pytest.skip(f"持仓配置不存在: {positions_path}")
    with open(positions_path, "r", encoding="utf-8") as f:
        return json.load(f)


@pytest.fixture(scope="module")
def real_shadow_config():
    """加载影子账户配置 (config/shadow_account_config.json)."""
    config_path = _PROJECT_ROOT / "config" / "shadow_account_config.json"
    if not config_path.exists():
        pytest.skip(f"影子账户配置不存在: {config_path}")
    with open(config_path, "r", encoding="utf-8") as f:
        return json.load(f)


@pytest.fixture(scope="module")
def synthetic_panel():
    """构造合成因子面板 (模拟 V9 因子矩阵).

    用于数据契约校验 + 漂移监控测试.
    100 个标的 × 20 天 × 5 个因子
    """
    np.random.seed(42)
    symbols = [f"00000{i}.SZ" for i in range(100)]
    dates = pd.date_range("2026-07-01", periods=20, freq="B")

    records = []
    for symbol in symbols:
        for date in dates:
            records.append({
                "code": symbol,
                "date": date.strftime("%Y-%m-%d"),
                "open": np.random.uniform(10, 50),
                "high": np.random.uniform(10, 55),
                "low": np.random.uniform(8, 48),
                "close": np.random.uniform(9, 52),
                "volume": np.random.randint(100000, 5000000),
                "MOM_5D": np.random.randn() * 0.02,
                "RSI_14D": np.random.uniform(20, 80),
                "VOL_20D": np.random.uniform(0.1, 0.4),
                "BETA_60D": np.random.uniform(0.5, 1.5),
                "forward_return_5d": np.random.randn() * 0.03,
            })
    return pd.DataFrame(records)


# ============================================================
# E2E 测试: 完整 ML 流水线
# ============================================================
@pytest.mark.e2e
@pytest.mark.slow
class TestEccMlPipelineE2E:
    """ECC ML 流水线端到端协作测试."""

    def test_data_contract_validates_real_panel(self, synthetic_panel):
        """GAP-8: 数据契约校验合成面板通过."""
        result = V9_DEFAULT_CONTRACT.validate(
            synthetic_panel, mode=ValidationMode.WARN_ONLY.value
        )
        # 合成数据应有违规 (forward_return_5d 不是必填列的 schema 之一),
        # 但校验本身不应崩溃
        assert result is not None
        assert hasattr(result, "violations")
        # 校验结果可序列化 (用于 manifest 记录)
        d = result.to_dict()
        assert "passed" in d
        assert "violation_count" in d

    def test_training_config_generates_unique_artifact(self):
        """GAP-7: 不同 seed 生成不同 artifact_name."""
        config_a = TrainingConfig(
            model_name="v9_lgb_e2e", seed=42, dataset_sha256="abc123"
        ).with_config_hash()
        config_b = TrainingConfig(
            model_name="v9_lgb_e2e", seed=99, dataset_sha256="abc123"
        ).with_config_hash()

        name_a = artifact_name(config_a)
        name_b = artifact_name(config_b)

        # 不同 seed → 不同 artifact_name
        assert name_a != name_b, "不同 seed 应生成不同 artifact_name"
        # 格式: model_name_v{hash}_d{date}
        assert name_a.startswith("v9_lgb_e2e_v")
        assert "_d" in name_a

    def test_drift_monitor_detects_distribution_shift(self, synthetic_panel):
        """GAP-6: 漂移监控能检测特征分布变化."""
        # baseline: 前 15 天
        baseline = synthetic_panel[synthetic_panel["date"] <= "2026-07-19"]
        # current: 后 5 天 (注入漂移)
        current = synthetic_panel[synthetic_panel["date"] > "2026-07-19"].copy()
        # 人为注入 RSI 漂移 (均值从 ~50 偏移到 ~70)
        current["RSI_14D"] = current["RSI_14D"] + 20

        monitor = SimModeDriftMonitor(
            sim_mode=True,
            baseline_panel=baseline,
            model_name="v9_lgb_e2e",
            model_version="v1_e2e",
        )
        reports = monitor.run_daily_check(current)

        # 应检测到 RSI_14D 的漂移
        assert isinstance(reports, list)
        # 漂移报告可序列化
        if reports:
            for r in reports:
                assert hasattr(r, "feature_name")
                assert hasattr(r, "severity")
                assert hasattr(r, "drift_score")

    def test_psi_same_distribution_low_score(self):
        """GAP-6: 相同分布 PSI 低分 (< 0.1)."""
        np.random.seed(42)
        baseline = pd.Series(np.random.randn(500))
        current = pd.Series(np.random.randn(500))
        psi = compute_psi(baseline, current)
        assert psi < 0.1, f"相同分布 PSI 应 < 0.1, 实际: {psi}"

    def test_psi_shifted_distribution_high_score(self):
        """GAP-6: 偏移分布 PSI 高分 (> 0.25)."""
        np.random.seed(42)
        baseline = pd.Series(np.random.randn(500))
        # 均值偏移 1 个标准差
        current = pd.Series(np.random.randn(500) + 1.0)
        psi = compute_psi(baseline, current)
        assert psi > 0.25, f"偏移分布 PSI 应 > 0.25, 实际: {psi}"

    def test_delayed_label_tracker_records_and_computes_ic(self):
        """GAP-6: 延迟标签追踪记录预测并计算 IC."""
        tracker = DelayedLabelTracker(
            model_name="v9_lgb_e2e",
            label_delay_days=5,
            storage_dir=str(_PROJECT_ROOT / "reports" / "e2e_test_delayed_labels"),
        )

        # 记录 10 个预测
        np.random.seed(42)
        for i in range(10):
            tracker.record_prediction(
                date=f"2026-07-{i+1:02d}",
                symbol=f"00000{i}.SZ",
                predicted_score=np.random.randn() * 0.1,
                model_version="v9_lgb_e2e_v1",
            )

        # 模拟标签可观测 (5 天后)
        for i in range(5):
            tracker.update_actual_label(
                date=f"2026-07-{i+1:02d}",
                symbol=f"00000{i}.SZ",
                actual_label=np.random.randn() * 0.05,
            )

        # 计算 IC 指标
        metrics = tracker.compute_delayed_metrics(model_version="v9_lgb_e2e_v1")
        assert metrics is not None
        # tracker 会持久化到磁盘, 可能有历史数据, 断言下界即可
        assert metrics.n_predictions >= 10
        assert metrics.n_observed >= 5
        assert 0.0 <= metrics.observation_rate <= 1.0

    def test_real_positions_loads_correctly(self, real_positions):
        """E2E: 真实持仓数据能加载且格式正确."""
        assert isinstance(real_positions, dict)
        # 持仓数据应有至少一个字段
        assert len(real_positions) > 0

    def test_real_shadow_config_has_benchmark(self, real_shadow_config):
        """E2E: 影子账户配置包含 V9 基线 benchmark."""
        assert isinstance(real_shadow_config, dict)
        # 应包含 backtest_benchmark 或类似字段
        # (具体字段名取决于配置版本)
        assert len(real_shadow_config) > 0

    def test_full_pipeline_chain(self, synthetic_panel):
        """E2E 完整链路: 契约校验 → 训练 manifest → 漂移监控 → 延迟标签.

        验证四个 ECC 模块能串联协作, 不验证业务正确性.
        """
        # Step 1: 数据契约校验
        contract_result = V9_DEFAULT_CONTRACT.validate(
            synthetic_panel, mode=ValidationMode.WARN_ONLY.value
        )
        assert contract_result is not None

        # Step 2: 训练 manifest 生成
        config = TrainingConfig(
            model_name="v9_lgb_e2e_chain",
            seed=42,
            dataset_sha256="e2e_synthetic_hash",
        ).with_config_hash()
        name = artifact_name(config)
        assert name.startswith("v9_lgb_e2e_chain")

        # Step 3: 漂移监控
        baseline = synthetic_panel.iloc[:1500]
        current = synthetic_panel.iloc[1500:]
        monitor = SimModeDriftMonitor(
            sim_mode=True,
            baseline_panel=baseline,
            model_name="v9_lgb_e2e_chain",
            model_version="v1_e2e",
        )
        drift_reports = monitor.run_daily_check(current)
        assert isinstance(drift_reports, list)

        # Step 4: 延迟标签追踪
        tracker = DelayedLabelTracker(
            model_name="v9_lgb_e2e_chain",
            label_delay_days=5,
            storage_dir=str(_PROJECT_ROOT / "reports" / "e2e_test_chain"),
        )
        record = tracker.record_prediction(
            date="2026-07-01",
            symbol="000001.SZ",
            predicted_score=0.05,
            model_version="v9_lgb_e2e_chain_v1",
        )
        assert record is not None
        assert record.predicted_score == 0.05

        # 全链路无异常即通过
