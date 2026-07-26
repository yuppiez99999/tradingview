# -*- coding: utf-8 -*-
"""V9 基线回归测试套件.

模块整合 8.4 — T1.8
HC-1: 任何 PR 必须通过 V9 基线回归测试

测试分层 (满足 T1.8 验收标准 "运行时间 < 30 分钟可用于 CI"):

    Layer 1 — 基线完整性 (<1 秒):
        - V9_BASELINE_LOCK.txt 存在且字段完整
        - 基线 JSON 文件存在 (回测结果 + DSR 评估)

    Layer 2 — 指标重算一致性 (<5 秒):
        - 基于回测 records 独立重算 annual_return / max_drawdown / win_rate
        - 独立实现 DSR (Bailey & Lopez de Prado 2014 标准公式)
        - 独立实现 Sharpe CV (12 月滚动)
        - 与基线 JSON 中存储的值比对 (容差 1e-6)

    Layer 3 — 阈值通过检查 (<1 秒):
        - DSR max_pass >= 5
        - 年化收益 >= 15%
        - 最大回撤 <= 10%
        - Sharpe CV < 1.0

    Layer 4 — V9 代码可导入性 (<30 秒):
        - institutional_pipeline_runner 模块可导入
        - lgb_enhanced_trainer 模块可导入
        - backtest_runner 模块可导入
        - V9 配置常量 _V9_REGIME_SPECIFIC_ENABLED == True

    Layer 5 — 完整回测 (nightly, >30 分钟, 默认跳过):
        - 调用 _run_v9_backtest.main() 重新跑回测
        - 用 pytest marker @pytest.mark.nightly 标记
        - CI 通过 --pytest '-m "not nightly"' 跳过

设计原则:
    - 不重新训练 V9 模型 (耗时数小时)
    - 不依赖网络/数据库 (基线 JSON 已包含全部所需数据)
    - 独立实现 DSR/CV 公式 (双盲验证 _calc_v9_dsr_v2.py 的正确性)
    - 失败时输出详细差异报告 (T1.8 验收标准 #3)
"""
from __future__ import annotations

import json
import logging
import math
import statistics
from pathlib import Path
from typing import Any, Dict, List, Tuple

import pytest

logger = logging.getLogger("v9_regression")

# 项目根目录
PROJECT_ROOT = Path(__file__).resolve().parents[2]


# ============================================================
# 独立实现 DSR / Sharpe CV 公式 (双盲验证)
# ============================================================

def _compute_sharpe_monthly(returns: List[float]) -> float:
    """计算月度 Sharpe 比率.

    Args:
        returns: 月度收益率序列

    Returns:
        月度 Sharpe = mean / std (无风险利率假设为 0)
    """
    if len(returns) < 2:
        return 0.0
    std = statistics.stdev(returns)
    if std <= 0:
        return 0.0
    return statistics.mean(returns) / std


def _compute_dsr_bailey(
    sharpe_monthly: float,
    n_samples: int,
    skewness: float,
    kurtosis_fisher: float,
    n_trials: int,
) -> float:
    """计算 DSR (Bailey & Lopez de Prado 2014 标准公式).

    与 _calc_v9_dsr_v2.py 中 compute_dsr_correct 独立实现, 用于双盲验证.

    公式:
        σ(SR) = sqrt((1/(T-1)) * (1 - skew*SR + (kurt/4)*SR^2))   [H1 分布]
        DSR   = SR / σ(SR) - sqrt(2 * ln(n_trials))

    Args:
        sharpe_monthly: 月度 Sharpe 比率
        n_samples: 样本数 (月份数)
        skewness: 收益率偏度
        kurtosis_fisher: Fisher 峰度 (正态=0)
        n_trials: 试验数 (用于多重比较校正)

    Returns:
        DSR 值
    """
    if n_samples <= 1 or n_trials <= 1:
        return 0.0
    sr_var = (1.0 / (n_samples - 1)) * (
        1.0
        - skewness * sharpe_monthly
        + (kurtosis_fisher / 4.0) * sharpe_monthly ** 2
    )
    sr_var = max(sr_var, 1e-10)
    sr_std = math.sqrt(sr_var)
    return sharpe_monthly / sr_std - math.sqrt(2.0 * math.log(n_trials))


def _compute_dsr_max_pass(
    sharpe_monthly: float,
    n_samples: int,
    skewness: float,
    kurtosis_fisher: float,
    max_trials: int = 20,
) -> int:
    """计算 DSR max_pass (DSR > 0 的最大试验数).

    Args:
        sharpe_monthly: 月度 Sharpe
        n_samples: 样本数
        skewness: 偏度
        kurtosis_fisher: Fisher 峰度
        max_trials: 最大试验数 (默认 20)

    Returns:
        max_pass 值 (DSR > 0 的最大 n_trials)
    """
    max_pass = 0
    for n_trials in range(1, max_trials + 1):
        dsr = _compute_dsr_bailey(
            sharpe_monthly, n_samples, skewness, kurtosis_fisher, n_trials
        )
        if dsr > 0:
            max_pass = n_trials
    return max_pass


def _compute_sharpe_cv_rolling(
    returns: List[float],
    window: int = 12,
    min_periods: int = 6,
) -> Tuple[float, List[float]]:
    """计算 12 月滚动 Sharpe CV.

    与 _calc_v9_dsr_v2.py 中滚动 CV 计算独立实现, 用于双盲验证.

    Args:
        returns: 月度收益率序列
        window: 滚动窗口 (默认 12 个月)
        min_periods: 最小有效窗口 (默认 6 个月, 避免早期 NaN)

    Returns:
        (sharpe_cv, rolling_sharpes) 元组. 若滚动窗口不足返回 (inf, [])
    """
    rolling_sharpes: List[float] = []
    n = len(returns)
    for i in range(window, n + 1):
        w = returns[i - window:i]
        if len(w) >= min_periods and statistics.stdev(w) > 0:
            w_sharpe = (statistics.mean(w) / statistics.stdev(w)) * math.sqrt(12)
            rolling_sharpes.append(w_sharpe)

    if len(rolling_sharpes) < 2:
        return float("inf"), rolling_sharpes

    mean_s = statistics.mean(rolling_sharpes)
    if abs(mean_s) < 1e-10:
        return float("inf"), rolling_sharpes
    cv = statistics.stdev(rolling_sharpes) / abs(mean_s)
    return cv, rolling_sharpes


def _recompute_metrics_from_records(
    records: List[Dict[str, Any]],
) -> Dict[str, Any]:
    """基于回测 records 独立重算所有指标.

    重算项:
        - monthly_returns: 月度收益率序列
        - n_months: 月份数
        - annual_return: 年化收益 (算术平均 * 12)
        - max_drawdown: 最大回撤 (基于累计净值)
        - win_rate: 胜率 (正收益月占比)
        - sharpe_monthly: 月度 Sharpe
        - sharpe_annual: 年化 Sharpe
        - skewness / kurtosis_fisher: 偏度 / 峰度
        - dsr_max_pass: DSR max_pass (Bailey 标准公式)
        - sharpe_cv: 12 月滚动 Sharpe CV

    Args:
        records: 回测记录列表 (每条含 portfolio_return 字段)

    Returns:
        含全部重算指标的字典
    """
    returns = [
        float(r.get("portfolio_return", 0.0))
        for r in records
        if r.get("portfolio_return") is not None
    ]
    n = len(returns)

    if n == 0:
        return {
            "n_months": 0,
            "annual_return": 0.0,
            "max_drawdown": 0.0,
            "win_rate": 0.0,
            "sharpe_monthly": 0.0,
            "sharpe_annual": 0.0,
            "skewness": 0.0,
            "kurtosis_fisher": 0.0,
            "dsr_max_pass": 0,
            "sharpe_cv": float("inf"),
            "monthly_returns": [],
        }

    # 基本统计量
    mean_ret = statistics.mean(returns)
    std_ret = statistics.stdev(returns) if n >= 2 else 0.0
    sharpe_monthly = mean_ret / std_ret if std_ret > 0 else 0.0
    sharpe_annual = sharpe_monthly * math.sqrt(12)

    # 年化收益 (月度复利, 与 backtest_runner.py 第 703 行一致)
    # 公式: (1 + monthly_mean)^12 - 1
    annual_return = (1.0 + mean_ret) ** 12 - 1.0

    # 最大回撤 (基于累计净值)
    cumulative = [1.0]
    for r in returns:
        cumulative.append(cumulative[-1] * (1.0 + r))
    peak = cumulative[0]
    max_dd = 0.0
    for v in cumulative:
        if v > peak:
            peak = v
        dd = (peak - v) / peak if peak > 0 else 0.0
        if dd > max_dd:
            max_dd = dd

    # 胜率
    win_rate = sum(1 for r in returns if r > 0) / n

    # 偏度 / 峰度 (用 numpy 计算, 与 _calc_v9_dsr_v2.py 一致)
    try:
        import numpy as np
        from scipy import stats as scipy_stats
        skewness = float(scipy_stats.skew(returns))
        kurtosis_fisher = float(scipy_stats.kurtosis(returns, fisher=True))
    except ImportError:
        # 降级: 用 Pearson 估算
        skewness = 0.0
        kurtosis_fisher = 0.0

    # DSR max_pass (Bailey 标准公式)
    dsr_max_pass = _compute_dsr_max_pass(
        sharpe_monthly, n, skewness, kurtosis_fisher, max_trials=20
    )

    # Sharpe CV (12 月滚动)
    sharpe_cv, rolling_sharpes = _compute_sharpe_cv_rolling(returns, window=12)

    return {
        "n_months": n,
        "monthly_returns": returns,
        "annual_return": annual_return,
        "max_drawdown": max_dd,
        "win_rate": win_rate,
        "sharpe_monthly": sharpe_monthly,
        "sharpe_annual": sharpe_annual,
        "skewness": skewness,
        "kurtosis_fisher": kurtosis_fisher,
        "dsr_max_pass": dsr_max_pass,
        "sharpe_cv": sharpe_cv,
        "rolling_sharpes": rolling_sharpes,
    }


# ============================================================
# Layer 1: 基线完整性测试
# ============================================================

class TestBaselineIntegrity:
    """Layer 1 — 基线文件完整性检查."""

    def test_baseline_lock_file_exists(self, v9_baseline_lock: Dict[str, Any]) -> None:
        """V9_BASELINE_LOCK.txt 必须存在."""
        assert v9_baseline_lock["path"].exists(), (
            f"V9_BASELINE_LOCK.txt 不存在: {v9_baseline_lock['path']}\n"
            f"HC-1 硬约束: 必须有基线锁定文件以记录生产 commit hash"
        )

    def test_baseline_lock_has_commit_hash(self, v9_baseline_lock: Dict[str, Any]) -> None:
        """LOCK 文件必须包含 40 位 commit hash."""
        parsed = v9_baseline_lock["parsed"]
        assert "commit_hash" in parsed, (
            "LOCK 文件缺少 '基线 commit hash' 字段\n"
            f"原始文本:\n{v9_baseline_lock['raw_text']}"
        )
        commit_hash = parsed["commit_hash"]
        assert len(commit_hash) == 40, f"commit hash 长度异常: {commit_hash}"
        assert all(c in "0123456789abcdef" for c in commit_hash.lower()), (
            f"commit hash 含非十六进制字符: {commit_hash}"
        )

    def test_baseline_lock_has_lock_date(self, v9_baseline_lock: Dict[str, Any]) -> None:
        """LOCK 文件必须包含锁定日期."""
        parsed = v9_baseline_lock["parsed"]
        assert "lock_date" in parsed, "LOCK 文件缺少 '锁定日期' 字段"

    def test_baseline_lock_has_metrics(self, v9_baseline_lock: Dict[str, Any]) -> None:
        """LOCK 文件必须包含基线评估指标."""
        parsed = v9_baseline_lock["parsed"]
        required_keys = ["dsr_max_pass", "annual_return", "max_drawdown", "sharpe_cv"]
        missing = [k for k in required_keys if k not in parsed]
        assert not missing, f"LOCK 文件缺少指标字段: {missing}"

    def test_backtest_json_exists(self, v9_backtest_json: Dict[str, Any]) -> None:
        """V9 回测原始结果 JSON 必须存在."""
        path = v9_backtest_json["path"]
        assert path.exists(), f"回测 JSON 不存在: {path}"

    def test_backtest_json_has_records(self, v9_backtest_json: Dict[str, Any]) -> None:
        """回测 JSON 必须包含 records 字段且非空."""
        data = v9_backtest_json["data"]
        assert "records" in data, "回测 JSON 缺少 'records' 字段"
        records = data["records"]
        assert isinstance(records, list), f"records 不是 list: {type(records)}"
        assert len(records) > 0, "records 为空"

    def test_dsr_maxpass_json_exists(self, v9_dsr_maxpass_json: Dict[str, Any]) -> None:
        """V9 DSR max_pass 评估 JSON 必须存在."""
        path = v9_dsr_maxpass_json["path"]
        assert path.exists(), f"DSR maxpass JSON 不存在: {path}"


# ============================================================
# Layer 2: 指标重算一致性测试
# ============================================================

class TestMetricRecomputation:
    """Layer 2 — 独立重算指标并与基线 JSON 比对.

    双盲验证 _calc_v9_dsr_v2.py 的正确性.
    """

    def test_records_count_matches(
        self,
        v9_backtest_json: Dict[str, Any],
        v9_baseline_metrics: Dict[str, Any],
    ) -> None:
        """records 数量与基线 n_months 一致."""
        records = v9_backtest_json["data"]["records"]
        expected = v9_baseline_metrics["n_months"]
        assert len(records) == expected, (
            f"records 数 ({len(records)}) 与基线 n_months ({expected}) 不一致"
        )

    def test_annual_return_consistency(
        self,
        v9_backtest_json: Dict[str, Any],
        v9_baseline_metrics: Dict[str, Any],
    ) -> None:
        """重算年化收益与基线一致 (容差 1e-6)."""
        records = v9_backtest_json["data"]["records"]
        recomputed = _recompute_metrics_from_records(records)
        expected = v9_baseline_metrics["annual_return"]
        actual = recomputed["annual_return"]
        assert abs(actual - expected) < 1e-6, (
            f"年化收益重算不一致: 重算={actual:.10f}, 基线={expected:.10f}, "
            f"差异={abs(actual - expected):.2e}"
        )

    def test_max_drawdown_consistency(
        self,
        v9_backtest_json: Dict[str, Any],
        v9_baseline_metrics: Dict[str, Any],
    ) -> None:
        """重算最大回撤与基线一致 (容差 1e-6)."""
        records = v9_backtest_json["data"]["records"]
        recomputed = _recompute_metrics_from_records(records)
        expected = v9_baseline_metrics["max_drawdown"]
        actual = recomputed["max_drawdown"]
        assert abs(actual - expected) < 1e-6, (
            f"最大回撤重算不一致: 重算={actual:.10f}, 基线={expected:.10f}, "
            f"差异={abs(actual - expected):.2e}"
        )

    def test_win_rate_consistency(
        self,
        v9_backtest_json: Dict[str, Any],
        v9_baseline_metrics: Dict[str, Any],
    ) -> None:
        """重算胜率与基线一致 (容差 1e-6)."""
        records = v9_backtest_json["data"]["records"]
        recomputed = _recompute_metrics_from_records(records)
        expected = v9_baseline_metrics["win_rate"]
        actual = recomputed["win_rate"]
        assert abs(actual - expected) < 1e-6, (
            f"胜率重算不一致: 重算={actual:.10f}, 基线={expected:.10f}, "
            f"差异={abs(actual - expected):.2e}"
        )

    def test_sharpe_annual_consistency(
        self,
        v9_backtest_json: Dict[str, Any],
        v9_dsr_maxpass_json: Dict[str, Any],
    ) -> None:
        """重算年化 Sharpe 与 DSR JSON 一致 (容差 1e-4, scipy.stats 数值噪声)."""
        records = v9_backtest_json["data"]["records"]
        recomputed = _recompute_metrics_from_records(records)
        expected = float(v9_dsr_maxpass_json["data"].get("sharpe_annual", 0.0))
        actual = recomputed["sharpe_annual"]
        assert abs(actual - expected) < 1e-4, (
            f"年化 Sharpe 重算不一致: 重算={actual:.6f}, 基线={expected:.6f}, "
            f"差异={abs(actual - expected):.2e}"
        )

    def test_dsr_max_pass_consistency(
        self,
        v9_backtest_json: Dict[str, Any],
        v9_dsr_maxpass_json: Dict[str, Any],
    ) -> None:
        """重算 DSR max_pass 与 DSR JSON 一致.

        容差: 严格相等 (整数比较)
        """
        records = v9_backtest_json["data"]["records"]
        recomputed = _recompute_metrics_from_records(records)
        expected = int(v9_dsr_maxpass_json["data"].get("max_pass", 0))
        actual = recomputed["dsr_max_pass"]
        assert actual == expected, (
            f"DSR max_pass 重算不一致: 重算={actual}, 基线={expected}\n"
            f"  重算细节: sharpe_monthly={recomputed['sharpe_monthly']:.6f}, "
            f"skew={recomputed['skewness']:.4f}, kurt={recomputed['kurtosis_fisher']:.4f}, "
            f"n={recomputed['n_months']}"
        )

    def test_sharpe_cv_rolling_consistency(
        self,
        v9_backtest_json: Dict[str, Any],
        v9_dsr_maxpass_json: Dict[str, Any],
    ) -> None:
        """重算 12 月滚动 Sharpe CV 与 DSR JSON 一致 (容差 1e-4)."""
        records = v9_backtest_json["data"]["records"]
        recomputed = _recompute_metrics_from_records(records)
        expected = float(v9_dsr_maxpass_json["data"].get("sharpe_cv", 999.0))
        actual = recomputed["sharpe_cv"]
        assert abs(actual - expected) < 1e-4, (
            f"Sharpe CV 重算不一致: 重算={actual:.6f}, 基线={expected:.6f}, "
            f"差异={abs(actual - expected):.2e}"
        )


# ============================================================
# Layer 3: 阈值通过检查
# ============================================================

class TestThresholdCompliance:
    """Layer 3 — V9 基线指标必须通过 HC-1 验收阈值."""

    def test_dsr_max_pass_threshold(self, v9_baseline_metrics: Dict[str, Any]) -> None:
        """DSR max_pass >= 5 (Bailey 标准公式)."""
        actual = v9_baseline_metrics["dsr_max_pass"]
        threshold = v9_baseline_metrics["thresholds"]["dsr_max_pass_min"]
        assert actual >= threshold, (
            f"DSR max_pass 未达标: {actual} < {threshold}\n"
            f"  含义: V9 在 n_trials={actual} 时 DSR > 0, 阈值要求 >= {threshold}\n"
            f"  解释: max_pass 越高表示策略在多重比较校正下越显著"
        )

    def test_annual_return_threshold(self, v9_baseline_metrics: Dict[str, Any]) -> None:
        """年化收益 >= 15%."""
        actual = v9_baseline_metrics["annual_return"]
        threshold = v9_baseline_metrics["thresholds"]["annual_return_min"]
        assert actual >= threshold, (
            f"年化收益未达标: {actual*100:.2f}% < {threshold*100:.2f}%"
        )

    def test_max_drawdown_threshold(self, v9_baseline_metrics: Dict[str, Any]) -> None:
        """最大回撤 <= 10%."""
        actual = v9_baseline_metrics["max_drawdown"]
        threshold = v9_baseline_metrics["thresholds"]["max_drawdown_max"]
        assert actual <= threshold, (
            f"最大回撤未达标: {actual*100:.2f}% > {threshold*100:.2f}%"
        )

    def test_sharpe_cv_threshold(self, v9_baseline_metrics: Dict[str, Any]) -> None:
        """Sharpe CV < 1.0 (12 月滚动)."""
        actual = v9_baseline_metrics["sharpe_cv"]
        threshold = v9_baseline_metrics["thresholds"]["sharpe_cv_max"]
        assert actual < threshold, (
            f"Sharpe CV 未达标: {actual:.4f} >= {threshold}\n"
            f"  含义: 12 月滚动 Sharpe 的变异系数, 越低表示策略越稳定"
        )

    def test_win_rate_threshold(self, v9_baseline_metrics: Dict[str, Any]) -> None:
        """胜率 >= 60% (额外指标)."""
        actual = v9_baseline_metrics["win_rate"]
        threshold = v9_baseline_metrics["thresholds"]["win_rate_min"]
        assert actual >= threshold, (
            f"胜率未达标: {actual*100:.2f}% < {threshold*100:.2f}%"
        )

    def test_all_thresholds_pass(self, v9_baseline_metrics: Dict[str, Any]) -> None:
        """所有阈值同时通过 (综合检查)."""
        m = v9_baseline_metrics
        t = v9_baseline_metrics["thresholds"]
        failures: List[str] = []
        if m["dsr_max_pass"] < t["dsr_max_pass_min"]:
            failures.append(f"DSR max_pass={m['dsr_max_pass']} < {t['dsr_max_pass_min']}")
        if m["annual_return"] < t["annual_return_min"]:
            failures.append(
                f"年化={m['annual_return']*100:.2f}% < {t['annual_return_min']*100:.2f}%"
            )
        if m["max_drawdown"] > t["max_drawdown_max"]:
            failures.append(
                f"回撤={m['max_drawdown']*100:.2f}% > {t['max_drawdown_max']*100:.2f}%"
            )
        if m["sharpe_cv"] >= t["sharpe_cv_max"]:
            failures.append(f"Sharpe CV={m['sharpe_cv']:.4f} >= {t['sharpe_cv_max']}")
        if m["win_rate"] < t["win_rate_min"]:
            failures.append(
                f"胜率={m['win_rate']*100:.2f}% < {t['win_rate_min']*100:.2f}%"
            )
        assert not failures, (
            "V9 基线回归未通过 HC-1 验收标准:\n  - "
            + "\n  - ".join(failures)
        )


# ============================================================
# Layer 4: V9 代码可导入性测试
# ============================================================

class TestV9CodeImportability:
    """Layer 4 — V9 训练/推理代码必须可导入.

    防止 re-export/重构破坏 V9 模块路径.
    """

    def test_institutional_pipeline_runner_importable(self) -> None:
        """institutional_pipeline_runner 模块可导入."""
        import sys
        # 项目根目录必须在 sys.path
        if str(PROJECT_ROOT) not in sys.path:
            sys.path.insert(0, str(PROJECT_ROOT))

        import institutional_pipeline_runner as ipr
        assert hasattr(ipr, "_V9_REGIME_SPECIFIC_ENABLED"), (
            "institutional_pipeline_runner 缺少 _V9_REGIME_SPECIFIC_ENABLED 常量"
        )

    def test_v9_regime_specific_enabled(self) -> None:
        """V9 总开关 _V9_REGIME_SPECIFIC_ENABLED == True."""
        import sys
        if str(PROJECT_ROOT) not in sys.path:
            sys.path.insert(0, str(PROJECT_ROOT))

        import institutional_pipeline_runner as ipr
        assert ipr._V9_REGIME_SPECIFIC_ENABLED is True, (
            f"_V9_REGIME_SPECIFIC_ENABLED = {ipr._V9_REGIME_SPECIFIC_ENABLED}, 期望 True\n"
            f"V9 总开关必须开启以激活 Regime-Specific LGB 训练路径"
        )

    def test_v9_config_constants(self) -> None:
        """V9 配置常量值合理."""
        import sys
        if str(PROJECT_ROOT) not in sys.path:
            sys.path.insert(0, str(PROJECT_ROOT))

        import institutional_pipeline_runner as ipr
        assert ipr._V9_MIN_SAMPLES_PER_REGIME >= 50, (
            f"_V9_MIN_SAMPLES_PER_REGIME={ipr._V9_MIN_SAMPLES_PER_REGIME} 过小 "
            f"(应 >=50 以保证 regime 子集统计可靠性)"
        )
        assert ipr._V9_REGIME_PROXY_SYMBOL == "510300", (
            f"_V9_REGIME_PROXY_SYMBOL={ipr._V9_REGIME_PROXY_SYMBOL}, 期望 510300 (沪深300ETF)"
        )
        assert ipr._V9_REGIME_MA_PERIOD >= 20, (
            f"_V9_REGIME_MA_PERIOD={ipr._V9_REGIME_MA_PERIOD} 过小"
        )

    def test_lgb_enhanced_trainer_importable(self) -> None:
        """lgb_enhanced_trainer 模块可导入且 V9 函数可用."""
        import sys
        if str(PROJECT_ROOT) not in sys.path:
            sys.path.insert(0, str(PROJECT_ROOT))

        from lgb_enhanced_trainer import (
            train_symbol_regime_specific,
            compute_regime_series,
        )
        assert callable(train_symbol_regime_specific), (
            "train_symbol_regime_specific 不可调用"
        )
        assert callable(compute_regime_series), "compute_regime_series 不可调用"

    def test_backtest_runner_importable(self) -> None:
        """backtest_runner 模块可导入且 run_backtest 可调用."""
        import sys
        if str(PROJECT_ROOT) not in sys.path:
            sys.path.insert(0, str(PROJECT_ROOT))
        sys.path.insert(0, str(PROJECT_ROOT / "research"))

        from backtest_runner import run_backtest
        assert callable(run_backtest), "run_backtest 不可调用"


# ============================================================
# Layer 5: 完整回测 (nightly, 默认跳过)
# ============================================================

@pytest.mark.nightly
class TestV9FullBacktestNightly:
    """Layer 5 — 完整 V9 回测 (nightly only).

    默认在 CI 中跳过, 通过 pytest -m nightly 单独触发.
    预计耗时 >30 分钟 (23 标的 × 45 月 × 2 模型 ≈ 数小时).
    """

    def test_v9_full_backtest_regression(self) -> None:
        """重新运行 V9 完整回测, 验证指标不退化.

        失败时输出详细差异报告.
        """
        import sys
        if str(PROJECT_ROOT) not in sys.path:
            sys.path.insert(0, str(PROJECT_ROOT))

        # 加载基线指标
        baseline_path = (
            PROJECT_ROOT
            / "output"
            / "validation_reports"
            / "v9_dsr_maxpass_20260725.json"
        )
        with open(baseline_path, "r", encoding="utf-8") as f:
            baseline = json.load(f)

        # 运行 V9 回测 (调用 _run_v9_backtest.main)
        # 由于 main() 会 sys.exit, 直接调用底层 run_backtest
        from backtest_runner import run_backtest

        symbols = [
            "588000", "688041", "002371", "688981", "300308", "000425", "601088",
            "600276", "600900", "515180", "600036", "518880", "300274", "603019",
            "600089", "688017", "600219", "600019", "000680", "000333", "000408",
            "000975", "002422",
        ]
        result = run_backtest(
            symbols=symbols,
            start="2023-07-01",
            end="2025-12-31",
            resume=False,
        )

        # 重算指标
        recomputed = _recompute_metrics_from_records(result.get("records", []))

        # 阈值检查 (不允许退化)
        failures: List[str] = []
        if recomputed["annual_return"] < 0.15:
            failures.append(
                f"年化收益退化: {recomputed['annual_return']*100:.2f}% < 15%"
            )
        if recomputed["max_drawdown"] > 0.10:
            failures.append(
                f"最大回撤退化: {recomputed['max_drawdown']*100:.2f}% > 10%"
            )
        if recomputed["sharpe_cv"] >= 1.0:
            failures.append(
                f"Sharpe CV 退化: {recomputed['sharpe_cv']:.4f} >= 1.0"
            )
        if recomputed["dsr_max_pass"] < 5:
            failures.append(
                f"DSR max_pass 退化: {recomputed['dsr_max_pass']} < 5"
            )

        # 与基线对比 (不允许显著退化, 容差 10%)
        baseline_annual = float(baseline["annual_return"])
        if recomputed["annual_return"] < baseline_annual * 0.9:
            failures.append(
                f"年化收益显著退化: 重算={recomputed['annual_return']*100:.2f}% "
                f"vs 基线={baseline_annual*100:.2f}% (退化 >10%)"
            )

        baseline_dd = float(baseline["max_drawdown"])
        if recomputed["max_drawdown"] > baseline_dd * 1.1:
            failures.append(
                f"最大回撤显著退化: 重算={recomputed['max_drawdown']*100:.2f}% "
                f"vs 基线={baseline_dd*100:.2f}% (扩大 >10%)"
            )

        assert not failures, (
            "V9 完整回测回归失败 — 指标退化:\n  - "
            + "\n  - ".join(failures)
        )
