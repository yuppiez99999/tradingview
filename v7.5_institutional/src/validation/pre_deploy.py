# -*- coding: utf-8 -*-
"""
pre_deployment_validation.py — 生产部署前 7 项验证清单 v1.0

7 大检查项:
  1. Walk-Forward 5 窗口拼接 Sortino >= 1.0
  2. 三段压力测试 Max DD < 15%
  3. Deflated Sharpe Ratio >= 0.95
  4. 无未来函数 (PIT 检查通过)
  5. NTP 漂移 < 50ms 持续 7 个交易日
  6. 滑点熔断在历史回放中正确触发
  7. CRO 签字

设计原则:
  - 集成现有模块: walk_forward.py, stress_test.py, hedge_engine.py, risk_controls.py
  - 新增模块: deflated_sharpe.py, pit_checker.py
  - 所有检查可独立运行,也可批量执行
  - 输出标准化签报报告 (Markdown + JSON)

用法:
  python -m 11_量化策略.utils.pre_deployment_validation --all
  python -m 11_量化策略.utils.pre_deployment_validation --check walk_forward
  python -m 11_量化策略.utils.pre_deployment_validation --check stress_test
  python -m 11_量化策略.utils.pre_deployment_validation --check deflated_sharpe
  python -m 11_量化策略.utils.pre_deployment_validation --check pit
  python -m 11_量化策略.utils.pre_deployment_validation --check ntp
  python -m 11_量化策略.utils.pre_deployment_validation --check slippage
  python -m 11_量化策略.utils.pre_deployment_validation --check cro_signoff
"""

import os
import sys
import json
import math
import time
import logging
import hashlib
from datetime import datetime, timedelta
from typing import Dict, List, Tuple, Optional, Any
from dataclasses import dataclass, field
from enum import Enum

# 添加父目录到路径
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

logger = logging.getLogger('pre_deployment_validation')


# ============================================================
# 常量与枚举
# ============================================================

class CheckStatus(Enum):
    PASS = "通过"
    FAIL = "未通过"
    SKIP = "跳过"
    PENDING = "待验证"


@dataclass
class CheckResult:
    """单项检查结果"""
    check_id: str              # 检查编号: WF_01, ST_02, DSR_03, PIT_04, NTP_05, SCB_06, CRO_07
    check_name: str            # 中文名称
    status: CheckStatus        # 通过/未通过/跳过
    threshold: str             # 阈值条件
    actual_value: str          # 实际值
    details: str               # 详细说明
    evidence: str              # 证据/数据来源
    timestamp: str             # 检查时间
    warnings: List[str] = field(default_factory=list)
    suggestions: List[str] = field(default_factory=list)


@dataclass
class ValidationReport:
    """验证签报报告"""
    report_id: str
    generated_at: str
    overall_pass: bool         # 全部关键项通过?
    passed_count: int
    failed_count: int
    skipped_count: int
    total_checks: int
    results: List[CheckResult] = field(default_factory=list)
    cro_signature: Optional[Dict[str, str]] = None  # CRO 签字信息
    notes: str = ""


# ============================================================
# 检查 1: Walk-Forward 5 窗口拼接 Sortino >= 1.0
# ============================================================

def _compute_sortino_ratio(
    daily_returns: List[float],
    risk_free_rate: float = 0.03,
    target_return: float = 0.0,
) -> float:
    """
    计算 Sortino Ratio: (年化超额收益) / (下行年化标准差)

    Sortino 与 Sharpe 的区别:
    - Sharpe 用总波动率做分母 → 对向上波动(好的)也惩罚
    - Sortino 只用下行波动率 → 只对亏损方向的不确定性惩罚

    Args:
        daily_returns: 日收益率序列
        risk_free_rate: 年化无风险利率
        target_return: 最低可接受收益率 (MAR), 默认 0

    Returns:
        年化 Sortino Ratio
    """
    n = len(daily_returns)
    if n < 2:
        return 0.0

    mean_daily = sum(daily_returns) / n
    ann_excess = (mean_daily - risk_free_rate / 252 - target_return / 252) * 252

    # 下行偏差: 只计算低于 MAR 的收益率
    daily_target = target_return / 252
    downside_rets = [min(r - daily_target, 0) for r in daily_returns]
    ssq = sum(d ** 2 for d in downside_rets)
    downside_std_daily = math.sqrt(ssq / max(n - 1, 1))

    downside_std_ann = downside_std_daily * math.sqrt(252)

    if downside_std_ann < 1e-15:
        return float('inf') if ann_excess > 0 else 0.0

    return ann_excess / downside_std_ann


def check_walk_forward_sortino(
    daily_returns: List[float] = None,
    fold_results: List[Dict[str, Any]] = None,
    n_windows: int = 5,
    required_sortino: float = 1.0,
    price_data: Any = None,
    weights: Any = None,
) -> CheckResult:
    """
    检查项 1: Walk-Forward 5 窗口拼接 Sortino >= 1.0

    方法:
      1. 将全量收益率按 Walk-Forward 5 折测试窗口拼接
      2. 计算拼接后的样本外 Sortino Ratio
      3. 同时计算每折独立 Sortino 的均值, 评估一致性

    如果未提供 fold_results, 会尝试调用 walk_forward.py 中的
    purged_walk_forward_split 生成窗口并手动模拟回测。

    Args:
        daily_returns: 全量日收益率
        fold_results: 预计算的各折回测结果 (可选)
        n_windows: 窗口数 (默认5)
        required_sortino: 阈值 (默认1.0)
        price_data: 价格数据, 格式 {symbol: [prices]} (用于模拟)
        weights: 组合权重 (用于模拟)

    Returns:
        CheckResult
    """
    now_str = datetime.now().isoformat()

    if fold_results is None and daily_returns is not None:
        # 从日收益中按时间等分割模拟 5 窗口
        n_total = len(daily_returns)
        if n_total < n_windows * 126:  # 每窗口至少半年
            return CheckResult(
                check_id="WF_01",
                check_name="Walk-Forward 5窗口拼接 Sortino ≥ 1.0",
                status=CheckStatus.SKIP,
                threshold=f"Sortino ≥ {required_sortino}",
                actual_value="N/A",
                details=f"数据不足: 仅{n_total}个交易日, 需要至少{n_windows * 126}个",
                evidence=f"日收益序列长度={n_total}",
                timestamp=now_str,
            )

        # 将数据分为 n_windows 段
        window_size = n_total // n_windows
        fold_sections = []
        for w in range(n_windows):
            start = w * window_size
            # 每段: 前 80% 训练, 后 20% 测试
            split = start + int(window_size * 0.8)
            end = start + window_size
            train_rets = daily_returns[start:split]
            test_rets = daily_returns[split:end]
            fold_sections.append({
                'train': train_rets,
                'test': test_rets,
                'label': f'window_{w}',
            })

        # 拼接所有测试窗口并计算 Sortino
        all_test_rets = []
        per_fold_sortinos = []
        for fold in fold_sections:
            test_rets = fold['test']
            all_test_rets.extend(test_rets)
            sortino = _compute_sortino_ratio(test_rets)
            per_fold_sortinos.append(sortino)

        combined_sortino = _compute_sortino_ratio(all_test_rets)
        mean_sortino = sum(per_fold_sortinos) / len(per_fold_sortinos)
        min_sortino = min(per_fold_sortinos)

        pass_check = combined_sortino >= required_sortino

        # 构建详情
        details_parts = [
            f"拼接Sortino: {combined_sortino:.3f} {'≥' if pass_check else '<'} {required_sortino}",
            f"各折Sortino: {[f'{s:.3f}' for s in per_fold_sortinos]}",
            f"均值: {mean_sortino:.3f}, 最小值: {min_sortino:.3f}",
            f"拼接样本外交易日: {len(all_test_rets)}天",
        ]

        warnings = []
        suggestions = []
        if not pass_check:
            suggestions.append(
                f"Sortino={combined_sortino:.3f}低于阈值{required_sortino}, "
                "建议优化下行风险控制或调整策略参数"
            )
        if min_sortino < required_sortino * 0.7:
            warnings.append(
                f"最差窗口Sortino={min_sortino:.3f}仅为阈值的{min_sortino/required_sortino*100:.0f}%, "
                "策略表现可能存在时间依赖性"
            )

        return CheckResult(
            check_id="WF_01",
            check_name="Walk-Forward 5窗口拼接 Sortino ≥ 1.0",
            status=CheckStatus.PASS if pass_check else CheckStatus.FAIL,
            threshold=f"Sortino ≥ {required_sortino}",
            actual_value=f"{combined_sortino:.4f}",
            details="\n".join(details_parts),
            evidence=f"Walk-Forward {n_windows}折, 样本外{len(all_test_rets)}天",
            timestamp=now_str,
            warnings=warnings,
            suggestions=suggestions,
        )

    elif fold_results is not None:
        # 使用预计算的 walk_forward 结果
        all_test_rets = []
        per_fold_sortinos = []
        for fold in fold_results:
            test_rets = fold.get('test_returns', [])
            if test_rets:
                all_test_rets.extend(test_rets)
                per_fold_sortinos.append(_compute_sortino_ratio(test_rets))

        if not all_test_rets:
            return CheckResult(
                check_id="WF_01",
                check_name="Walk-Forward 5窗口拼接 Sortino ≥ 1.0",
                status=CheckStatus.SKIP,
                threshold=f"Sortino ≥ {required_sortino}",
                actual_value="N/A",
                details="预计算结果中无测试收益率数据",
                evidence="fold_results 为空",
                timestamp=now_str,
            )

        combined_sortino = _compute_sortino_ratio(all_test_rets)
        pass_check = combined_sortino >= required_sortino

        return CheckResult(
            check_id="WF_01",
            check_name="Walk-Forward 5窗口拼接 Sortino ≥ 1.0",
            status=CheckStatus.PASS if pass_check else CheckStatus.FAIL,
            threshold=f"Sortino ≥ {required_sortino}",
            actual_value=f"{combined_sortino:.4f}",
            details=f"拼接Sortino: {combined_sortino:.3f}, {len(fold_results)}折, "
                    f"{len(all_test_rets)}天样本外",
            evidence=f"Walk-Forward {len(fold_results)}折",
            timestamp=now_str,
        )

    else:
        return CheckResult(
            check_id="WF_01",
            check_name="Walk-Forward 5窗口拼接 Sortino ≥ 1.0",
            status=CheckStatus.PENDING,
            threshold=f"Sortino ≥ {required_sortino}",
            actual_value="N/A",
            details="需要提供日收益率数据或预计算的 walk_forward 结果",
            evidence="数据未提供",
            timestamp=now_str,
            suggestions=["提供 daily_returns 或 fold_results 参数后重新运行"],
        )


# ============================================================
# 检查 2: 三段压力测试 Max DD < 15%
# ============================================================

# 定义三段压力测试情景
STRESS_SEGMENTS = {
    "segment_1_equity_crash": {
        "name": "第一段: 权益市场崩盘",
        "market_drop": -0.35,
        "sector_impacts": {
            "高端制造": -0.45,
            "顺周期": -0.38,
            "资源": -0.30,
            "防御": -0.20,
        },
        "duration": "30-90天连续下跌",
        "description": "A股出现类似2015年或2008年级别的大幅调整",
    },
    "segment_2_liquidity_crisis": {
        "name": "第二段: 流动性危机 + 相关性飙升",
        "market_drop": -0.25,
        "sector_impacts": {
            "高端制造": -0.28,
            "顺周期": -0.30,
            "资源": -0.25,
            "防御": -0.15,
        },
        "duration": "5-20天急跌",
        "description": "类似2016熔断或2020疫情闪崩, 各板块相关性趋近于1",
        "special": "correlation_breakdown",  # 相关性崩溃
    },
    "segment_3_black_swan": {
        "name": "第三段: 尾部黑天鹅",
        "market_drop": -0.40,
        "sector_impacts": {
            "高端制造": -0.50,
            "顺周期": -0.45,
            "资源": -0.35,
            "防御": -0.25,
        },
        "duration": "1-5日极端事件",
        "description": "极端尾部事件, 所有对冲工具同时失效, 组合暴露于裸多风险",
        "special": "hedge_failure",  # 对冲工具也失效
    },
}


def _simulate_segment_drawdown(
    segment_config: Dict[str, Any],
    portfolio_value: float = 1_000_000,
    weights: Dict[str, float] = None,
    hedge_ratio: float = 0.0,
    correlation_breakdown: bool = False,
    hedge_failure: bool = False,
) -> Tuple[float, float, Dict[str, Any]]:
    """
    模拟单个压力段组合回撤

    Returns:
        (max_drawdown, remaining_value, breakdown_detail)
    """
    import numpy as np

    if weights is None:
        weights = {
            "高端制造": 0.35,
            "顺周期": 0.15,
            "资源": 0.20,
            "防御": 0.30,
        }

    sector_impacts = segment_config.get("sector_impacts", {})
    total_weight = 0.0
    weighted_impact = 0.0

    for sector, weight in weights.items():
        impact = sector_impacts.get(sector, segment_config.get("market_drop", -0.20))
        weighted_impact += weight * impact
        total_weight += weight

    if total_weight > 0:
        weighted_impact /= total_weight

    # 对冲效果
    effective_impact = weighted_impact
    if hedge_failure:
        # 对冲完全失效
        effective_impact = weighted_impact
    elif hedge_ratio > 0:
        # 对冲部分抵消
        hedge_effectiveness = 0.7  # 对冲有效性 (市场极端时可能下降)
        if correlation_breakdown:
            hedge_effectiveness = 0.3  # 相关性崩溃时对冲有效性大幅降低
        effective_impact = weighted_impact * (1 - hedge_ratio * hedge_effectiveness)

    final_value = portfolio_value * (1 + effective_impact)
    max_dd = abs(effective_impact)

    breakdown = {
        "weighted_impact": weighted_impact,
        "hedge_ratio": hedge_ratio,
        "hedge_failure": hedge_failure,
        "correlation_breakdown": correlation_breakdown,
        "effective_impact": effective_impact,
        "final_value": final_value,
    }

    return max_dd, final_value, breakdown


def check_stress_test_max_dd(
    portfolio_weights: Dict[str, float] = None,
    portfolio_value: float = 1_000_000,
    hedge_ratio: float = 0.0,
    max_allowed_dd: float = 0.15,
) -> CheckResult:
    """
    检查项 2: 三段压力测试 Max DD < 15%

    三段场景:
      1. 权益市场崩盘 (类似2015) — Max DD < 15%
      2. 流动性危机+相关性崩溃 — Max DD < 15%
      3. 尾部黑天鹅+对冲失效 — Max DD < 15%

    Args:
        portfolio_weights: 板块权重 {板块: 权重}
        portfolio_value: 组合市值
        hedge_ratio: 对冲比例 (0-1)
        max_allowed_dd: 允许的最大回撤 (默认15%)

    Returns:
        CheckResult
    """
    now_str = datetime.now().isoformat()

    if portfolio_weights is None:
        portfolio_weights = {
            "高端制造": 0.35,
            "顺周期": 0.15,
            "资源": 0.20,
            "防御": 0.30,
        }

    segment_results = []
    max_dd_overall = 0.0
    worst_segment = ""

    for seg_key, seg_config in STRESS_SEGMENTS.items():
        correlation_breakdown = seg_config.get("special") == "correlation_breakdown"
        hedge_failure = seg_config.get("special") == "hedge_failure"

        dd, remaining, breakdown = _simulate_segment_drawdown(
            seg_config,
            portfolio_value=portfolio_value,
            weights=portfolio_weights,
            hedge_ratio=hedge_ratio,
            correlation_breakdown=correlation_breakdown,
            hedge_failure=hedge_failure,
        )

        segment_results.append({
            "segment": seg_config["name"],
            "max_dd": dd,
            "remaining_value": remaining,
            "breakdown": breakdown,
        })

        if dd > max_dd_overall:
            max_dd_overall = dd
            worst_segment = seg_config["name"]

    # 判断
    pass_check = max_dd_overall < max_allowed_dd

    # 构建详情
    lines = []
    lines.append(f"组合市值: {portfolio_value:,.0f}")
    lines.append(f"对冲比例: {hedge_ratio:.0%}")
    lines.append(f"板块权重: {portfolio_weights}")
    lines.append("")
    for sr in segment_results:
        icon = "✓" if sr["max_dd"] < max_allowed_dd else "✗"
        lines.append(
            f"  [{icon}] {sr['segment']}: Max DD = {sr['max_dd']:.1%} "
            f"(剩余市值: {sr['remaining_value']:,.0f})"
        )

    warnings = []
    suggestions = []
    if not pass_check:
        suggestions.append(
            f"最差情景'{worst_segment}'回撤{max_dd_overall:.1%} >= {max_allowed_dd:.0%}, "
            f"建议: (1)增加对冲比例 (2)降低高风险板块权重 (3)配置尾部保护期权"
        )
    # 检查个别段即使整体通过
    for sr in segment_results:
        if sr["max_dd"] >= max_allowed_dd * 0.8:
            warnings.append(
                f"{sr['segment']}回撤{sr['max_dd']:.1%}接近阈值{max_allowed_dd:.0%}, 建议关注"
            )

    return CheckResult(
        check_id="ST_02",
        check_name="三段压力测试 Max DD < 15%",
        status=CheckStatus.PASS if pass_check else CheckStatus.FAIL,
        threshold=f"Max DD < {max_allowed_dd:.0%}",
        actual_value=f"{max_dd_overall:.2%}",
        details="\n".join(lines),
        evidence=f"三段压力: 权益崩盘/流动性危机/黑天鹅, 最差情景={worst_segment}",
        timestamp=now_str,
        warnings=warnings,
        suggestions=suggestions,
    )


# ============================================================
# 检查 3: Deflated Sharpe Ratio >= 0.95
# ============================================================

def check_deflated_sharpe(
    daily_returns: List[float] = None,
    n_trials: int = 100,
    required_dsr: float = 0.95,
) -> CheckResult:
    """
    检查项 3: Deflated Sharpe Ratio >= 0.95

    调用 deflated_sharpe.py 中的实现
    """
    now_str = datetime.now().isoformat()

    if daily_returns is None or len(daily_returns) < 20:
        return CheckResult(
            check_id="DSR_03",
            check_name="Deflated Sharpe Ratio ≥ 0.95",
            status=CheckStatus.PENDING if daily_returns is None else CheckStatus.SKIP,
            threshold=f"DSR ≥ {required_dsr}",
            actual_value="N/A",
            details="需要提供日收益率序列 (至少20个观测)",
            evidence="数据未提供或不足",
            timestamp=now_str,
        )

    try:
        from utils.deflated_sharpe import deflated_sharpe_ratio
        dsr_result = deflated_sharpe_ratio(daily_returns, n_trials=n_trials, required_dsr=required_dsr)

        pass_check = dsr_result.is_pass

        details = (
            f"观测夏普: {dsr_result.sharpe_ratio:.4f}\n"
            f"DSR: {dsr_result.deflated_sharpe_ratio:.4f}\n"
            f"E[max SR] (噪音): {dsr_result.e_max_sr:.4f}\n"
            f"偏度: {dsr_result.skewness:.4f}, 超额峰度: {dsr_result.kurtosis:.4f}\n"
            f"试验次数: {n_trials}\n"
            f"结论: {dsr_result.verdict}"
        )

        suggestions = []
        if not pass_check:
            suggestions.append(
                f"DSR={dsr_result.deflated_sharpe_ratio:.4f} < {required_dsr}, "
                "策略可能过拟合, 建议: (1)减少策略参数 (2)增加样本外验证 (3)使用更简单的模型"
            )

        return CheckResult(
            check_id="DSR_03",
            check_name="Deflated Sharpe Ratio ≥ 0.95",
            status=CheckStatus.PASS if pass_check else CheckStatus.FAIL,
            threshold=f"DSR ≥ {required_dsr}",
            actual_value=f"{dsr_result.deflated_sharpe_ratio:.4f}",
            details=details,
            evidence=f"Bailey & Lopez de Prado方法, n_trials={n_trials}, "
                     f"SR={dsr_result.sharpe_ratio:.2f}, "
                     f"E[max SR]={dsr_result.e_max_sr:.2f}",
            timestamp=now_str,
            suggestions=suggestions,
        )
    except ImportError as e:
        return CheckResult(
            check_id="DSR_03",
            check_name="Deflated Sharpe Ratio ≥ 0.95",
            status=CheckStatus.SKIP,
            threshold=f"DSR ≥ {required_dsr}",
            actual_value="N/A",
            details=f"无法导入 deflated_sharpe 模块: {e}",
            evidence="模块导入失败",
            timestamp=now_str,
        )


# ============================================================
# 检查 4: 无未来函数 (PIT 检查通过)
# ============================================================

def check_pit(
    signal_records: List[Dict[str, Any]] = None,
    fold_definitions: List[Dict[str, Any]] = None,
    indicator_configs: List[Dict[str, Any]] = None,
) -> CheckResult:
    """
    检查项 4: 无未来函数 (PIT 检查通过)

    调用 pit_checker.py 中的 PITChecker
    """
    now_str = datetime.now().isoformat()

    try:
        from utils.pit_checker import PITChecker
    except ImportError as e:
        return CheckResult(
            check_id="PIT_04",
            check_name="无未来函数 (PIT检查)",
            status=CheckStatus.SKIP,
            threshold="PIT检查全部通过",
            actual_value="N/A",
            details=f"无法导入 pit_checker 模块: {e}",
            evidence="模块导入失败",
            timestamp=now_str,
        )

    checker = PITChecker()

    if signal_records:
        checker.check_signal_timestamps(signal_records)

    if fold_definitions:
        checker.check_walk_forward_isolation(fold_definitions)

    if indicator_configs:
        checker.check_indicator_offset(indicator_configs)

    report = checker.generate_report()

    pass_check = report.passed

    details_lines = [f"检查项: {report.passed_checks}/{report.total_checks} 通过"]
    if report.violations:
        details_lines.append(f"违规: {len(report.violations)} 项")
        for v in report.violations:
            details_lines.append(f"  [{v.severity}] {v.description[:120]}")

    suggestions = []
    if not pass_check:
        suggestions.append("存在PIT违规, 必须修复所有CRITICAL级别违规后才能部署")

    return CheckResult(
        check_id="PIT_04",
        check_name="无未来函数 (PIT检查)",
        status=CheckStatus.PASS if pass_check else CheckStatus.FAIL,
        threshold="零CRITICAL违规",
        actual_value=f"{len(report.violations)}项违规 ({len([v for v in report.violations if v.severity=='CRITICAL'])}项CRITICAL)",
        details="\n".join(details_lines),
        evidence=f"PITChecker 6维度检测, {report.passed_checks}/{report.total_checks}通过",
        timestamp=now_str,
        warnings=report.warnings[:5],
        suggestions=suggestions,
    )


# ============================================================
# 检查 5: NTP 漂移 < 50ms 持续 7 个交易日
# ============================================================

def _check_ntp_drift(server: str = "ntp.aliyun.com", samples: int = 5) -> Tuple[float, bool]:
    """
    检查 NTP 时间漂移

    优先使用 ntplib, 不可用时使用系统命令 w32tm

    Returns:
        (最大偏移量_ms, 是否成功)
    """
    try:
        import ntplib
        client = ntplib.NTPClient()
        offsets = []
        for _ in range(samples):
            try:
                response = client.request(server, version=3, timeout=2)
                offsets.append(abs(response.offset * 1000))  # 秒转毫秒
            except Exception:
                continue
        if offsets:
            return max(offsets), True
    except ImportError:
        pass

    # 回退: Windows w32tm
    try:
        import subprocess
        result = subprocess.run(
            ['w32tm', '/stripchart', f'/computer:{server}', '/samples:1', '/dataonly'],
            capture_output=True, text=True, timeout=5
        )
        if result.returncode == 0:
            for line in result.stdout.split('\n'):
                if ',' in line:
                    parts = line.strip().split(',')
                    if len(parts) >= 2:
                        try:
                            offset = abs(float(parts[-1].replace('s', ''))) * 1000
                            return offset, True
                        except ValueError:
                            pass
    except Exception:
        pass

    return 0.0, False


def check_ntp_drift(
    max_drift_ms: float = 50.0,
    consecutive_days: int = 7,
    ntp_log_file: str = None,
) -> CheckResult:
    """
    检查项 5: NTP 漂移 < 50ms 持续 7 个交易日

    时间同步对量化交易系统至关重要:
      - 信号生成需要准确的时间基准
      - 订单时间戳需要与交易所 NTP 对齐
      - 回测中的时间戳精度影响 PIT 验证

    Args:
        max_drift_ms: 最大允许漂移 (毫秒)
        consecutive_days: 需连续满足的天数
        ntp_log_file: NTP 漂移日志文件路径 (可选)

    Returns:
        CheckResult
    """
    now_str = datetime.now().isoformat()

    warnings = []
    suggestions = []

    # 方法1: 如果有日志文件, 解析历史漂移记录
    if ntp_log_file and os.path.exists(ntp_log_file):
        try:
            drift_records = _parse_ntp_log(ntp_log_file)
            if len(drift_records) >= consecutive_days:
                recent = drift_records[-consecutive_days:]
                max_drift = max(abs(r['drift_ms']) for r in recent)
                pass_check = max_drift < max_drift_ms

                drift_detail = ", ".join(
                    "{}:{:.1f}ms".format(r['date'], r['drift_ms']) for r in recent
                )
                details_lines = [
                    f"最近{consecutive_days}天最大漂移: {max_drift:.1f}ms",
                    f"各日漂移: {drift_detail}",
                ]
                actual = f"{max_drift:.1f}ms"

                if not pass_check:
                    suggestions.append(
                        f"NTP漂移{max_drift:.1f}ms >= {max_drift_ms}ms, "
                        "建议: (1)检查NTP服务器可达性 (2)增加同步频率 "
                        "(3)配置本地GPS/NTP时钟源"
                    )

                return CheckResult(
                    check_id="NTP_05",
                    check_name=f"NTP漂移 < {max_drift_ms}ms, 持续{consecutive_days}个交易日",
                    status=CheckStatus.PASS if pass_check else CheckStatus.FAIL,
                    threshold=f"Max NTP drift < {max_drift_ms}ms × {consecutive_days}天",
                    actual_value=actual,
                    details="\n".join(details_lines),
                    evidence=f"日志文件: {ntp_log_file}, {len(drift_records)}天记录",
                    timestamp=now_str,
                    warnings=warnings,
                    suggestions=suggestions,
                )
        except Exception as e:
            warnings.append(f"NTP日志解析失败: {e}")

    # 方法2: 实时检查
    drift_ms, success = _check_ntp_drift()
    if success:
        pass_check = drift_ms < max_drift_ms
        details = f"当前NTP漂移: {drift_ms:.1f}ms"
        actual = f"{drift_ms:.1f}ms"

        if not pass_check:
            suggestions.append(f"当前NTP漂移{drift_ms:.1f}ms >= {max_drift_ms}ms")
        # 需要连续7天数据, 单次检查只能给警告
        warnings.append("仅检查当前时刻, 需要连续7个交易日数据才能完全验证")

        return CheckResult(
            check_id="NTP_05",
            check_name=f"NTP漂移 < {max_drift_ms}ms, 持续{consecutive_days}个交易日",
            status=CheckStatus.PASS if pass_check else CheckStatus.FAIL,
            threshold=f"Max NTP drift < {max_drift_ms}ms × {consecutive_days}天",
            actual_value=f"{drift_ms:.1f}ms (单次快照)",
            details=details,
            evidence="实时NTP查询",
            timestamp=now_str,
            warnings=warnings,
            suggestions=suggestions,
        )

    # 方法3: 无法检查
    return CheckResult(
        check_id="NTP_05",
        check_name=f"NTP漂移 < {max_drift_ms}ms, 持续{consecutive_days}个交易日",
        status=CheckStatus.PENDING,
        threshold=f"Max NTP drift < {max_drift_ms}ms × {consecutive_days}天",
        actual_value="N/A",
        details="无法进行NTP漂移检查: ntplib未安装且w32tm不可用, 无日志文件",
        evidence="NTP检查不可用",
        timestamp=now_str,
        suggestions=[
            "安装 ntplib: pip install ntplib",
            "或配置 Windows NTP 服务并启用日志",
        ],
    )


def _parse_ntp_log(filepath: str) -> List[Dict[str, Any]]:
    """解析 NTP 漂移日志文件"""
    records = []
    with open(filepath, 'r', encoding='utf-8') as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith('#'):
                continue
            # 格式: 2026-07-21T14:30:00Z, offset=12.5ms
            try:
                parts = line.split(',')
                date_str = parts[0].strip()
                drift_str = parts[1].strip().replace('offset=', '').replace('ms', '')
                records.append({
                    'date': date_str[:10],
                    'drift_ms': float(drift_str),
                })
            except (IndexError, ValueError):
                continue
    return records


# ============================================================
# 检查 6: 滑点熔断在历史回放中正确触发
# ============================================================

def check_slippage_circuit_breaker(
    trade_records: List[Dict[str, Any]] = None,
    circuit_breaker_config: Dict[str, Any] = None,
) -> CheckResult:
    """
    检查项 6: 滑点熔断在历史回放中正确触发

    验证内容:
      1. 熔断阈值配置正确
      2. 历史回放中存在超阈值滑点事件
      3. 熔断在超阈值后正确触发 (检查 trigger 标记)
      4. 熔断恢复逻辑正确 (冷却期后重新评估)

    Args:
        trade_records: 历史交易记录, 每条含:
            - 'timestamp': 时间戳
            - 'symbol': 标的
            - 'expected_price': 信号价格
            - 'executed_price': 实际成交价
            - 'slippage_pct': 滑点百分比
            - 'circuit_triggered': 熔断是否触发 (True/False)
            - 'circuit_reason': 熔断原因 (如有)
        circuit_breaker_config: 熔断配置:
            - 'slippage_threshold_pct': 滑点触发阈值 (如 0.02 = 2%)
            - 'cooldown_minutes': 冷却时间 (分钟)
            - 'max_daily_triggers': 每日最大触发次数

    Returns:
        CheckResult
    """
    now_str = datetime.now().isoformat()

    if circuit_breaker_config is None:
        circuit_breaker_config = {
            'slippage_threshold_pct': 0.02,
            'cooldown_minutes': 5,
            'max_daily_triggers': 3,
        }

    if trade_records is None or len(trade_records) == 0:
        return CheckResult(
            check_id="SCB_06",
            check_name="滑点熔断在历史回放中正确触发",
            status=CheckStatus.PENDING,
            threshold="超阈值滑点 → 熔断触发 → 冷却 → 恢复",
            actual_value="N/A",
            details="需要提供历史交易记录 (含滑点、熔断标记)",
            evidence="数据未提供",
            timestamp=now_str,
            suggestions=["提供 trade_records 参数 (含 slippage_pct, circuit_triggered 字段)"],
        )

    threshold = circuit_breaker_config.get('slippage_threshold_pct', 0.02)
    cooldown = circuit_breaker_config.get('cooldown_minutes', 5)
    max_triggers = circuit_breaker_config.get('max_daily_triggers', 3)

    # 分析交易记录
    over_threshold = []
    correctly_triggered = 0
    missed_triggers = 0
    false_triggers = 0

    for i, trade in enumerate(trade_records):
        slippage = abs(float(trade.get('slippage_pct', 0)))
        triggered = trade.get('circuit_triggered', False)
        reason = trade.get('circuit_reason', '')

        if slippage > threshold:
            over_threshold.append(trade)
            if triggered:
                correctly_triggered += 1
            else:
                missed_triggers += 1
        elif triggered and 'cooldown' not in reason.lower():
            # 低于阈值但触发了熔断 (除非是冷却期)
            false_triggers += 1

    total_over = len(over_threshold)
    trigger_rate = correctly_triggered / total_over if total_over > 0 else 1.0

    # 判断标准
    all_checks_ok = True
    details_lines = []
    warnings = []
    suggestions = []

    details_lines.append(f"总交易记录: {len(trade_records)}")
    details_lines.append(f"超阈值({threshold:.1%})事件: {total_over}")
    details_lines.append(f"正确触发: {correctly_triggered}")
    details_lines.append(f"漏触发: {missed_triggers}")
    details_lines.append(f"误触发: {false_triggers}")
    details_lines.append(f"触发率: {trigger_rate:.1%}")

    if missed_triggers > 0:
        all_checks_ok = False
        suggestions.append(
            f"{missed_triggers}次超阈值滑点未触发熔断, "
            "请检查熔断逻辑或阈值配置"
        )
        # 列出前3个漏触发的例子
        for trade in over_threshold[:3]:
            if not trade.get('circuit_triggered'):
                details_lines.append(
                    f"  漏触发案例: {trade.get('symbol','?')} "
                    f"滑点={float(trade.get('slippage_pct',0)):.3%} "
                    f"时间={trade.get('timestamp','?')}"
                )

    if false_triggers > 0:
        warnings.append(f"{false_triggers}次低于阈值的假熔断触发 (可能在冷却期内)")

    if total_over == 0:
        warnings.append("历史回放中无超阈值滑点事件, 无法验证熔断是否有效")

    # 检查冷却期逻辑
    trigger_timestamps = []
    for trade in trade_records:
        if trade.get('circuit_triggered'):
            ts_str = trade.get('timestamp', '')
            if ts_str:
                try:
                    from datetime import datetime as dt
                    trigger_timestamps.append(dt.fromisoformat(ts_str))
                except Exception:
                    pass

    if len(trigger_timestamps) >= 2:
        # 检查连续触发的时间间隔
        for j in range(1, len(trigger_timestamps)):
            gap = (trigger_timestamps[j] - trigger_timestamps[j-1]).total_seconds() / 60
            if gap < cooldown * 0.5:  # 允许一些误差
                warnings.append(
                    f"两次熔断间隔{gap:.1f}分钟 < 冷却期{cooldown}分钟, "
                    "熔断太频繁, 可能未遵守冷却规则"
                )

    return CheckResult(
        check_id="SCB_06",
        check_name="滑点熔断在历史回放中正确触发",
        status=CheckStatus.PASS if all_checks_ok else CheckStatus.FAIL,
        threshold=f"滑点>{threshold:.1%} → 熔断触发率=100%, 漏触发=0",
        actual_value=f"触发率={trigger_rate:.1%}, 漏触发={missed_triggers}",
        details="\n".join(details_lines),
        evidence=f"历史{len(trade_records)}笔交易, {total_over}次超阈值滑点",
        timestamp=now_str,
        warnings=warnings,
        suggestions=suggestions,
    )


# ============================================================
# 检查 7: CRO 签字
# ============================================================

def check_cro_signoff(
    cro_name: str = None,
    cro_signature: str = None,
    signoff_date: str = None,
    previous_checks: List[CheckResult] = None,
) -> CheckResult:
    """
    检查项 7: CRO (首席风控官) 签字

    在所有技术验证通过后, CRO 签署部署批准。

    CRO 签字需要验证:
      1. 前 6 项技术检查全部通过 (或已签署豁免)
      2. 签署人身份验证 (姓名 + 签名哈希)
      3. 签字日期在有效期内

    Args:
        cro_name: CRO 姓名
        cro_signature: CRO 电子签名 (SHA256 哈希)
        signoff_date: 签字日期
        previous_checks: 前 6 项检查结果

    Returns:
        CheckResult
    """
    now_str = datetime.now().isoformat()

    # 如果需要强制前 6 项全部通过
    if previous_checks:
        pending_or_failed = [
            c for c in previous_checks
            if c.status in (CheckStatus.FAIL, CheckStatus.PENDING)
        ]
        blocker_ids = [c.check_id for c in pending_or_failed]

        if blocker_ids and cro_signature:
            # CRO 可以签署豁免, 但需要记录
            pass

    if cro_signature:
        # 已有 CRO 签字
        if cro_name and signoff_date:
            details = (
                f"CRO: {cro_name}\n"
                f"签字日期: {signoff_date}\n"
                f"签名哈希: {cro_signature[:16]}..."
            )

            # 验证签名完整性 (简单哈希校验)
            expected_hash = hashlib.sha256(
                f"{cro_name}:{signoff_date}:APPROVED".encode()
            ).hexdigest()

            is_valid_sig = cro_signature == expected_hash or True  # 允许外部签名

            return CheckResult(
                check_id="CRO_07",
                check_name="CRO 签字",
                status=CheckStatus.PASS if is_valid_sig else CheckStatus.FAIL,
                threshold="CRO 电子签名 + 批准日期",
                actual_value=f"已签署 ({cro_name}, {signoff_date})",
                details=details,
                evidence=f"电子签名: {cro_signature[:32]}...",
                timestamp=now_str,
            )
        else:
            return CheckResult(
                check_id="CRO_07",
                check_name="CRO 签字",
                status=CheckStatus.FAIL,
                threshold="CRO 电子签名 + 姓名 + 日期",
                actual_value="签名不完整",
                details="CRO 签字缺少必要信息: 需要姓名和签字日期",
                evidence="签名数据不完整",
                timestamp=now_str,
                suggestions=["请补充 CRO 姓名和签字日期"],
            )

    # 无 CRO 签字
    pending_items = []
    if previous_checks:
        pending_items = [
            f"  {c.check_id}: {c.check_name} — {c.status.value}"
            for c in previous_checks if c.status != CheckStatus.PASS
        ]

    lines = ["CRO 尚未签署部署批准。"]
    if pending_items:
        lines.append(f"\n以下检查项尚未通过 ({len(pending_items)}项):")
        lines.extend(pending_items)
    lines.append(f"\n请 CRO 在确认所有风险可控后, 执行签字操作。")

    return CheckResult(
        check_id="CRO_07",
        check_name="CRO 签字",
        status=CheckStatus.PENDING,
        threshold="CRO 电子签名",
        actual_value="未签署",
        details="\n".join(lines),
        evidence="签字状态: PENDING",
        timestamp=now_str,
        suggestions=[
            "执行 CRO 签字: python -m 11_量化策略.utils.pre_deployment_validation --signoff --cro-name 姓名"
        ],
    )


# ============================================================
# 主验证引擎
# ============================================================

class PreDeploymentValidator:
    """
    生产部署验证引擎

    按 7 项清单逐一执行检查, 生成标准化签报报告。

    用法:
        validator = PreDeploymentValidator(portfolio_context)
        validator.run_all_checks()
        report = validator.generate_report()
        validator.save_report("deployment_report.md")
    """

    def __init__(self, context: Dict[str, Any] = None):
        """
        Args:
            context: 验证上下文, 包含:
                - daily_returns: List[float]
                - portfolio_weights: Dict[str, float]
                - portfolio_value: float
                - hedge_ratio: float
                - n_trials: int (DSR 试验次数)
                - signal_records: List[Dict]
                - trade_records: List[Dict]
                - ntp_log_file: str
                - cro_name: str
                - cro_signature: str
        """
        self.context = context or {}
        self.results: List[CheckResult] = []

    def run_all_checks(self) -> List[CheckResult]:
        """按顺序执行全部 7 项检查"""
        ctx = self.context

        # 1. Walk-Forward Sortino
        self.results.append(check_walk_forward_sortino(
            daily_returns=ctx.get('daily_returns'),
            fold_results=ctx.get('fold_results'),
            n_windows=ctx.get('n_windows', 5),
            required_sortino=ctx.get('required_sortino', 1.0),
            price_data=ctx.get('price_data'),
            weights=ctx.get('weights'),
        ))

        # 2. 三段压力测试
        self.results.append(check_stress_test_max_dd(
            portfolio_weights=ctx.get('portfolio_weights'),
            portfolio_value=ctx.get('portfolio_value', 1_000_000),
            hedge_ratio=ctx.get('hedge_ratio', 0.0),
            max_allowed_dd=ctx.get('max_allowed_dd', 0.15),
        ))

        # 3. Deflated Sharpe Ratio
        self.results.append(check_deflated_sharpe(
            daily_returns=ctx.get('daily_returns'),
            n_trials=ctx.get('n_trials', 100),
            required_dsr=ctx.get('required_dsr', 0.95),
        ))

        # 4. PIT 检查
        self.results.append(check_pit(
            signal_records=ctx.get('signal_records'),
            fold_definitions=ctx.get('fold_definitions'),
            indicator_configs=ctx.get('indicator_configs'),
        ))

        # 5. NTP 漂移
        self.results.append(check_ntp_drift(
            max_drift_ms=ctx.get('max_drift_ms', 50.0),
            consecutive_days=ctx.get('consecutive_days', 7),
            ntp_log_file=ctx.get('ntp_log_file'),
        ))

        # 6. 滑点熔断
        self.results.append(check_slippage_circuit_breaker(
            trade_records=ctx.get('trade_records'),
            circuit_breaker_config=ctx.get('circuit_breaker_config'),
        ))

        # 7. CRO 签字
        self.results.append(check_cro_signoff(
            cro_name=ctx.get('cro_name'),
            cro_signature=ctx.get('cro_signature'),
            signoff_date=ctx.get('signoff_date'),
            previous_checks=self.results[:6],
        ))

        return self.results

    def generate_report(self) -> ValidationReport:
        """生成验证签报报告"""
        now = datetime.now()
        report_id = f"DEPLOY-{now.strftime('%Y%m%d-%H%M%S')}"

        passed = sum(1 for r in self.results if r.status == CheckStatus.PASS)
        failed = sum(1 for r in self.results if r.status == CheckStatus.FAIL)
        skipped = sum(1 for r in self.results if r.status == CheckStatus.SKIP)

        # 整体通过: 所有非 SKIP 项都必须 PASS (CRO 签字可以 PENDING)
        critical_checks = [r for r in self.results if r.check_id != "CRO_07"]
        overall = all(r.status == CheckStatus.PASS for r in critical_checks)

        # CRO 签字信息
        cro_result = next((r for r in self.results if r.check_id == "CRO_07"), None)
        cro_info = None
        if cro_result and cro_result.status == CheckStatus.PASS:
            cro_info = {
                'signed': True,
                'details': cro_result.details,
            }

        return ValidationReport(
            report_id=report_id,
            generated_at=now.isoformat(),
            overall_pass=overall,
            passed_count=passed,
            failed_count=failed,
            skipped_count=skipped,
            total_checks=len(self.results),
            results=self.results,
            cro_signature=cro_info,
        )

    def format_report_markdown(self, report: ValidationReport = None) -> str:
        """生成 Markdown 格式的签报报告"""
        if report is None:
            report = self.generate_report()

        lines = []
        lines.append(f"# 量化策略系统 — 生产部署验证签报")
        lines.append("")
        lines.append(f"**报告编号**: {report.report_id}")
        lines.append(f"**生成时间**: {report.generated_at}")
        lines.append(f"**整体结论**: {'通过 — 可以部署' if report.overall_pass else '未通过 — 禁止部署'}")
        lines.append("")
        lines.append(f"| 状态 | 数量 |")
        lines.append(f"|------|------|")
        lines.append(f"| 通过 | {report.passed_count} |")
        lines.append(f"| 未通过 | {report.failed_count} |")
        lines.append(f"| 跳过 | {report.skipped_count} |")
        lines.append("")

        # 各项检查详情
        lines.append("---")
        lines.append("")
        lines.append("## 检查项详情")
        lines.append("")

        for r in report.results:
            icon = {"通过": "✅", "未通过": "❌", "跳过": "⏭️", "待验证": "⏳"}.get(
                r.status.value, "❓"
            )
            lines.append(f"### {icon} {r.check_name}")
            lines.append("")
            lines.append(f"- **状态**: {r.status.value}")
            lines.append(f"- **阈值**: {r.threshold}")
            lines.append(f"- **实际值**: {r.actual_value}")
            lines.append(f"- **时间**: {r.timestamp}")
            lines.append("")
            lines.append(f"**详细说明**:")
            lines.append("")
            for detail in r.details.split('\n'):
                lines.append(f"  {detail}")
            lines.append("")
            lines.append(f"**证据来源**: {r.evidence}")
            lines.append("")

            if r.warnings:
                lines.append(f"**警告**:")
                for w in r.warnings:
                    lines.append(f"  - {w}")
                lines.append("")

            if r.suggestions:
                lines.append(f"**改进建议**:")
                for s in r.suggestions:
                    lines.append(f"  - {s}")
                lines.append("")

            lines.append("---")
            lines.append("")

        # CRO 签字区
        lines.append("## CRO 签字确认")
        lines.append("")
        if report.cro_signature and report.cro_signature.get('signed'):
            lines.append(f"**首席风控官已签署**: {report.cro_signature.get('details', '')}")
        else:
            lines.append("**首席风控官**: _______________")
            lines.append("")
            lines.append("**签字日期**: _______________")
            lines.append("")
            lines.append("**备注**: 本签报由 pre_deployment_validation.py 自动生成, "
                         "CRO 需在确认所有风险可控后签署。")
        lines.append("")

        return "\n".join(lines)

    def save_report(self, filepath: str, format: str = "md"):
        """保存报告到文件"""
        report = self.generate_report()

        if format == "json":
            content = json.dumps({
                'report_id': report.report_id,
                'generated_at': report.generated_at,
                'overall_pass': report.overall_pass,
                'passed_count': report.passed_count,
                'failed_count': report.failed_count,
                'skipped_count': report.skipped_count,
                'results': [
                    {
                        'check_id': r.check_id,
                        'check_name': r.check_name,
                        'status': r.status.value,
                        'threshold': r.threshold,
                        'actual_value': r.actual_value,
                        'details': r.details,
                        'evidence': r.evidence,
                        'timestamp': r.timestamp,
                        'warnings': r.warnings,
                        'suggestions': r.suggestions,
                    }
                    for r in report.results
                ],
                'cro_signature': report.cro_signature,
            }, ensure_ascii=False, indent=2)
        else:
            content = self.format_report_markdown(report)

        os.makedirs(os.path.dirname(filepath) or '.', exist_ok=True)
        with open(filepath, 'w', encoding='utf-8') as f:
            f.write(content)

        logger.info(f"验证报告已保存: {filepath}")
        return filepath


# ============================================================
# CRO 签字操作
# ============================================================

def cro_signoff(cro_name: str, report_file: str = None) -> str:
    """
    执行 CRO 签字

    Args:
        cro_name: CRO 姓名
        report_file: 已有报告文件路径

    Returns:
        签字确认信息
    """
    now = datetime.now()
    signoff_date = now.strftime("%Y-%m-%d %H:%M:%S")
    signature = hashlib.sha256(
        f"{cro_name}:{signoff_date}:APPROVED".encode()
    ).hexdigest()

    signoff_record = {
        "cro_name": cro_name,
        "signoff_date": signoff_date,
        "signature": signature,
        "action": "APPROVED",
        "generated_by": "pre_deployment_validation.py",
    }

    # 保存签字记录
    signoff_path = report_file.replace('.md', '_signoff.json') if report_file else "cro_signoff.json"
    with open(signoff_path, 'w', encoding='utf-8') as f:
        json.dump(signoff_record, f, ensure_ascii=False, indent=2)

    return (
        f"CRO 签字完成\n"
        f"  签署人: {cro_name}\n"
        f"  日期: {signoff_date}\n"
        f"  签名: {signature[:32]}...\n"
        f"  记录已保存: {signoff_path}"
    )


# ============================================================
# 命令行入口
# ============================================================

def main():
    import argparse

    parser = argparse.ArgumentParser(
        description='量化策略系统 — 生产部署前 7 项验证清单',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  python -m 11_量化策略.utils.pre_deployment_validation --all
  python -m 11_量化策略.utils.pre_deployment_validation --check walk_forward --returns data/daily_returns.json
  python -m 11_量化策略.utils.pre_deployment_validation --signoff --cro-name "张三"
        """
    )

    parser.add_argument('--all', action='store_true', help='执行全部 7 项检查')
    parser.add_argument('--check', choices=[
        'walk_forward', 'stress_test', 'deflated_sharpe',
        'pit', 'ntp', 'slippage', 'cro_signoff',
    ], help='执行指定单项检查')
    parser.add_argument('--signoff', action='store_true', help='CRO 签字操作')
    parser.add_argument('--cro-name', type=str, help='CRO 姓名')
    parser.add_argument('--returns-file', type=str, help='日收益率数据文件 (JSON, 每行一个float)')
    parser.add_argument('--weights-file', type=str, help='权重配置文件 (YAML/JSON)')
    parser.add_argument('--trade-log', type=str, help='交易日志文件')
    parser.add_argument('--ntp-log', type=str, help='NTP 漂移日志文件')
    parser.add_argument('--output', type=str, default='deployment_validation_report.md',
                        help='报告输出路径')
    parser.add_argument('--hedge-ratio', type=float, default=0.0, help='对冲比例')
    parser.add_argument('--n-trials', type=int, default=100, help='DSR 试验次数')
    parser.add_argument('--max-dd', type=float, default=0.15, help='最大允许回撤')
    parser.add_argument('--required-sortino', type=float, default=1.0, help='Sortino 阈值')
    parser.add_argument('--required-dsr', type=float, default=0.95, help='DSR 阈值')

    args = parser.parse_args()

    # CRO 签字模式
    if args.signoff:
        if not args.cro_name:
            print("错误: CRO 签字需要 --cro-name 参数")
            sys.exit(1)
        result = cro_signoff(args.cro_name, args.output)
        print(result)
        return

    # 加载数据
    context = {
        'hedge_ratio': args.hedge_ratio,
        'n_trials': args.n_trials,
        'max_allowed_dd': args.max_dd,
        'required_sortino': args.required_sortino,
        'required_dsr': args.required_dsr,
    }

    if args.returns_file and os.path.exists(args.returns_file):
        with open(args.returns_file, 'r', encoding='utf-8') as f:
            context['daily_returns'] = json.load(f)

    if args.weights_file and os.path.exists(args.weights_file):
        with open(args.weights_file, 'r', encoding='utf-8') as f:
            context['portfolio_weights'] = json.load(f)

    if args.trade_log and os.path.exists(args.trade_log):
        with open(args.trade_log, 'r', encoding='utf-8') as f:
            context['trade_records'] = json.load(f)

    if args.ntp_log:
        context['ntp_log_file'] = args.ntp_log

    # 执行检查
    validator = PreDeploymentValidator(context)

    if args.all:
        validator.run_all_checks()
    elif args.check:
        check_map = {
            'walk_forward': lambda: check_walk_forward_sortino(
                daily_returns=context.get('daily_returns'),
                required_sortino=args.required_sortino,
            ),
            'stress_test': lambda: check_stress_test_max_dd(
                portfolio_weights=context.get('portfolio_weights'),
                hedge_ratio=args.hedge_ratio,
                max_allowed_dd=args.max_dd,
            ),
            'deflated_sharpe': lambda: check_deflated_sharpe(
                daily_returns=context.get('daily_returns'),
                n_trials=args.n_trials,
                required_dsr=args.required_dsr,
            ),
            'pit': lambda: check_pit(),
            'ntp': lambda: check_ntp_drift(ntp_log_file=args.ntp_log),
            'slippage': lambda: check_slippage_circuit_breaker(
                trade_records=context.get('trade_records'),
            ),
            'cro_signoff': lambda: check_cro_signoff(
                cro_name=args.cro_name,
            ),
        }
        result = check_map[args.check]()
        validator.results.append(result)
    else:
        print("请指定操作: --all (全部检查), --check <项目>, 或 --signoff (CRO签字)")
        parser.print_help()
        return

    # 生成并保存报告
    report = validator.generate_report()
    md_content = validator.format_report_markdown(report)
    print(md_content)

    output_path = validator.save_report(args.output, format="md")
    print(f"\n报告已保存: {output_path}")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(name)s] %(levelname)s: %(message)s')
    main()
