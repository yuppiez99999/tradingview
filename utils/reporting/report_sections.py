"""T5.4 报告渲染函数 — 6 个独立纯函数.

从 daily_workflow.phase_report (615 行巨型方法) 抽取的子函数, 每个函数:
  - 接受明确的输入参数 (不依赖 self.state 等实例状态)
  - 返回 List[str] (Markdown 行)
  - 可独立单元测试
  - 行为等价于原 phase_report 对应段落

设计原则:
  1. 纯函数: 无副作用, 不修改输入
  2. 显式依赖: 所有数据通过参数传入
  3. 容错降级: 输入缺失时返回 "[N/A]" 占位, 不抛异常
  4. 可组合: 主类按顺序调用 6 个函数组装完整报告

模块整合 8.4 — ARCHITECTURE §5
"""

from __future__ import annotations

import json
import logging
import time
from collections.abc import Sequence
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

logger = logging.getLogger("daily_report_generator")


# ============================================================
# 常量
# ============================================================

# 报告占位符
NA_PLACEHOLDER = "[N/A]"
EMPTY_SECTION_PLACEHOLDER = "_(暂无数据)_"

# 报告头部标题
REPORT_TITLE = "# v8.7 综合量化策略系统 — 每日工作报告"
REPORT_SEPARATOR = "---"

# 阶段名称映射 (phase_key -> 中文名)
PHASE_NAMES: dict[str, str] = {
    "check": "系统自检",
    "calibrate": "收益校准",
    "market": "市场状态",
    "risk": "风险预算",
    "hedge": "对冲评估",
    "hedge_fund": "对冲基金",
    "v10_risk": "V10 风险",
    "quant_neutral": "量化中性",
    "cash_management": "现金管理",
    "directional_futures": "方向性期货",
    "signal": "交易信号",
    "execute": "智能执行",
    "report": "盘后报告",
    "autolearn": "自主学习",
    "factor_kill_switch": "因子监控",
    "shadow_monitor": "影子账户",
}

# 允许降级的阶段 (失败不阻塞主流程)
ALLOW_DEGRADE_PHASES = frozenset({"calibrate", "autolearn", "factor_kill_switch"})


# ============================================================
# 1. 报告头部构建
# ============================================================


def build_report_header(
    trade_date: str,
    capital: float = 0.0,
    dry_run: bool = False,
    sim_mode: bool = False,
    live_mode: bool = False,
    generated_at: str | None = None,
) -> list[str]:
    """构建报告头部 (段 1+2).

    Args:
        trade_date: 交易日期 (YYYY-MM-DD)
        capital: 资金规模
        dry_run: 干跑模式
        sim_mode: 模拟盘模式
        live_mode: 实盘模式
        generated_at: 生成时间戳 (默认当前时间)

    Returns:
        Markdown 行列表
    """
    if not generated_at:
        generated_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    # 执行模式标注
    if live_mode:
        mode_tag = "🔴 实盘模式"
    elif sim_mode:
        mode_tag = "🟡 模拟盘"
    elif dry_run:
        mode_tag = "🟢 干跑模式"
    else:
        mode_tag = "⚪ 默认模式"

    lines: list[str] = []
    lines.append(REPORT_TITLE)
    lines.append("")
    lines.append(f"- **交易日期**: {trade_date}")
    lines.append(f"- **资金规模**: {capital:,.2f} 元")
    lines.append(f"- **执行模式**: {mode_tag}")
    lines.append(f"- **生成时间**: {generated_at}")
    lines.append(REPORT_SEPARATOR)
    lines.append("")
    return lines


def build_phase_execution_summary(
    phases_state: dict[str, Any],
    executed_phases: Sequence[str] | None = None,
) -> list[str]:
    """构建阶段执行摘要表 (段 2).

    Args:
        phases_state: 各阶段状态字典 {phase_key: {"status": ..., "duration_ms": ...}}
        executed_phases: 实际执行的阶段顺序 (默认按 PHASE_NAMES 顺序)

    Returns:
        Markdown 行列表
    """
    lines: list[str] = []
    lines.append("## 阶段执行摘要")
    lines.append("")
    lines.append("| 阶段 | 状态 | 耗时(ms) | 说明 |")
    lines.append("|------|------|----------|------|")

    if not phases_state:
        lines.append(f"| (无) | {NA_PLACEHOLDER} | - | phases_state 为空 |")
        lines.append("")
        return lines

    phase_order = list(executed_phases) if executed_phases else list(PHASE_NAMES.keys())
    for phase_key in phase_order:
        phase_data = phases_state.get(phase_key)
        if not isinstance(phase_data, dict):
            continue
        phase_name = PHASE_NAMES.get(phase_key, phase_key)
        status = phase_data.get("status", NA_PLACEHOLDER)
        duration_ms = phase_data.get("duration_ms", 0)
        reason = phase_data.get("reason", "")
        # 状态标记
        if status == "ok" or status is True:
            status_str = "✅ OK"
        elif status == "skipped":
            status_str = "⏭️ SKIP"
        elif status in ("error", "failed"):
            status_str = "❌ FAIL"
        elif phase_key in ALLOW_DEGRADE_PHASES:
            status_str = f"⚠️ DEGRADED ({status})"
        else:
            status_str = f"🔵 {status}"
        lines.append(f"| {phase_name} | {status_str} | {duration_ms:.0f} | {reason} |")

    lines.append("")
    return lines


# ============================================================
# 2. 阶段状态渲染
# ============================================================


def render_phase_summary(phases_state: dict[str, Any]) -> list[str]:
    """渲染各阶段状态详情 (段 4+5+6).

    Args:
        phases_state: 各阶段状态字典

    Returns:
        Markdown 行列表
    """
    lines: list[str] = []
    lines.append("## 各阶段详情")
    lines.append("")

    if not phases_state:
        lines.append(EMPTY_SECTION_PLACEHOLDER)
        lines.append("")
        return lines

    # Phase 1: 系统自检
    check_state = phases_state.get("check", {})
    if check_state:
        lines.append("### Phase 1: 系统自检")
        lines.append("")
        ntp_status = check_state.get("ntp_sync", NA_PLACEHOLDER)
        connector_status = check_state.get("connector", NA_PLACEHOLDER)
        risk_status = check_state.get("risk_manager", NA_PLACEHOLDER)
        lines.append(f"- NTP 同步: {ntp_status}")
        lines.append(f"- 连接器状态: {connector_status}")
        lines.append(f"- 风控管理器: {risk_status}")
        lines.append("")

    # Phase 2: 市场状态
    market_state = phases_state.get("market", {})
    if market_state:
        lines.append("### Phase 2: 市场状态")
        lines.append("")
        vix = market_state.get("vix", NA_PLACEHOLDER)
        circuit_level = market_state.get("circuit_level", NA_PLACEHOLDER)
        lines.append(f"- VIX 指数: {vix}")
        lines.append(f"- 熔断级别: {circuit_level}")
        lines.append("")

    # Phase 3: 风险预算
    risk_state = phases_state.get("risk", {})
    if risk_state:
        lines.append("### Phase 3: 风险预算")
        lines.append("")
        total_risk_budget = risk_state.get("total_risk_budget", NA_PLACEHOLDER)
        kelly_fraction = risk_state.get("kelly_fraction", NA_PLACEHOLDER)
        lines.append(f"- 总风险预算: {total_risk_budget}")
        lines.append(f"- Kelly 比例: {kelly_fraction}")
        lines.append("")

    # Phase 4: 对冲评估
    hedge_state = phases_state.get("hedge", {})
    if hedge_state:
        lines.append("### Phase 4: 对冲评估")
        lines.append("")
        beta_hedge = hedge_state.get("beta_hedge", NA_PLACEHOLDER)
        vol_hedge = hedge_state.get("vol_hedge", NA_PLACEHOLDER)
        corr_hedge = hedge_state.get("correlation_hedge", NA_PLACEHOLDER)
        lines.append(f"- Beta 对冲: {beta_hedge}")
        lines.append(f"- Vol 对冲: {vol_hedge}")
        lines.append(f"- 相关性对冲: {corr_hedge}")
        lines.append("")

    # Phase 5: 交易信号
    signal_state = phases_state.get("signal", {})
    if signal_state:
        lines.append("### Phase 5: 交易信号")
        lines.append("")
        signals = signal_state.get("signals", [])
        if isinstance(signals, list):
            lines.append(f"- 信号数量: {len(signals)}")
            for i, sig in enumerate(_safe_iter(signals)[:10]):  # 仅展示前 10 个
                if isinstance(sig, dict):
                    symbol = sig.get("symbol", NA_PLACEHOLDER)
                    direction = sig.get("direction", NA_PLACEHOLDER)
                    weight = sig.get("weight", NA_PLACEHOLDER)
                    lines.append(f"  {i + 1}. {symbol} | {direction} | 权重={weight}")
        else:
            lines.append(f"- 信号: {signals}")
        lines.append("")

    # Phase 6: 执行记录
    execute_state = phases_state.get("execute", {})
    if execute_state:
        lines.append("### Phase 6: 智能执行")
        lines.append("")
        fills = execute_state.get("fills", [])
        if isinstance(fills, list):
            total_notional = sum(
                f.get("notional", 0) for f in fills if isinstance(f, dict)
            )
            lines.append(f"- 成交笔数: {len(fills)}")
            lines.append(f"- 总成交金额: {total_notional:,.2f}")
        else:
            lines.append(f"- 执行记录: {fills}")
        lines.append("")

    return lines


def _safe_iter(items: Any) -> Sequence:
    """安全迭代器 (非列表返回空列表)."""
    if isinstance(items, (list, tuple)):
        return items
    return []


# ============================================================
# 3. P&L 归因渲染
# ============================================================


def render_pnl_attribution(
    pnl_attribution_result: dict[str, Any] | None,
) -> list[str]:
    """渲染 P&L 八维归因分析 (段 8 上半).

    Args:
        pnl_attribution_result: PnLAttributionEngine.attribute() 返回的结果 dict
            预期字段: alpha_pnl / execution_pnl / risk_pnl / total_pnl 等

    Returns:
        Markdown 行列表
    """
    lines: list[str] = []
    lines.append("### P&L 八维归因分析")
    lines.append("")

    if not pnl_attribution_result or not isinstance(pnl_attribution_result, dict):
        lines.append(f"- {NA_PLACEHOLDER} (PnLAttributionEngine 未运行或返回空)")
        lines.append("")
        return lines

    # 提取关键字段
    alpha_pnl = pnl_attribution_result.get("alpha_pnl", 0.0)
    execution_pnl = pnl_attribution_result.get("execution_pnl", 0.0)
    risk_pnl = pnl_attribution_result.get("risk_pnl", 0.0)
    total_pnl = pnl_attribution_result.get("total_pnl", 0.0)

    lines.append(f"- Alpha 贡献: {alpha_pnl:+.2f}")
    lines.append(f"- 执行贡献: {execution_pnl:+.2f}")
    lines.append(f"- 风险贡献: {risk_pnl:+.2f}")
    lines.append(f"- **总 PnL**: {total_pnl:+.2f}")
    lines.append("")

    # 详细分解 (可选字段)
    details = pnl_attribution_result.get("details", {})
    if isinstance(details, dict) and details:
        lines.append("#### 归因明细")
        lines.append("")
        lines.append("| 维度 | PnL | 占比 |")
        lines.append("|------|-----|------|")
        for dim, value in details.items():
            if isinstance(value, (int, float)):
                pct = (value / total_pnl * 100) if total_pnl != 0 else 0.0
                lines.append(f"| {dim} | {value:+.2f} | {pct:.1f}% |")
        lines.append("")

    return lines


# ============================================================
# 4. Barra 风险因子暴露分解
# ============================================================


def render_barra_decomposition(
    barra_result: dict[str, Any] | None,
) -> list[str]:
    """渲染 Barra 风险因子暴露分解 (段 8 下半).

    Args:
        barra_result: BarraRiskDecomposer.decompose_from_positions() 返回的结果 dict
            预期字段: factor_exposures / specific_risk / total_risk 等

    Returns:
        Markdown 行列表
    """
    lines: list[str] = []
    lines.append("### Barra 风险因子暴露分解")
    lines.append("")

    if not barra_result or not isinstance(barra_result, dict):
        lines.append(f"- {NA_PLACEHOLDER} (BarraRiskDecomposer 未运行或返回空)")
        lines.append("")
        return lines

    # 提取关键字段
    factor_exposures = barra_result.get("factor_exposures", {})
    specific_risk = barra_result.get("specific_risk", 0.0)
    total_risk = barra_result.get("total_risk", 0.0)

    lines.append(f"- 总风险: {total_risk:.4f}")
    lines.append(f"- 特异性风险: {specific_risk:.4f}")

    # 因子风险 = sqrt(total_risk^2 - specific_risk^2)
    if total_risk > 0 and specific_risk >= 0:
        factor_risk = (total_risk**2 - specific_risk**2) ** 0.5
        lines.append(f"- 因子风险: {factor_risk:.4f}")
    lines.append("")

    # 因子暴露明细
    if isinstance(factor_exposures, dict) and factor_exposures:
        lines.append("#### 因子暴露")
        lines.append("")
        lines.append("| 因子 | 暴露 |")
        lines.append("|------|------|")
        for factor, exposure in sorted(factor_exposures.items()):
            if isinstance(exposure, (int, float)):
                lines.append(f"| {factor} | {exposure:+.4f} |")
            else:
                lines.append(f"| {factor} | {exposure} |")
        lines.append("")

    return lines


# ============================================================
# 5. EOD 七 Guard 风控链渲染
# ============================================================


def render_eod_guard_chain(
    guard_results: dict[str, Any] | None,
) -> list[str]:
    """渲染 EOD 七 Guard 风控链结果 (段 9 上半).

    Args:
        guard_results: RiskGuardIntegrator.run_eod_chain() 返回的结果 dict
            预期字段: guards (List[Dict]) / overall_status / recommendations

    Returns:
        Markdown 行列表
    """
    lines: list[str] = []
    lines.append("### EOD 七 Guard 风控链")
    lines.append("")

    if not guard_results or not isinstance(guard_results, dict):
        lines.append(f"- {NA_PLACEHOLDER} (RiskGuardIntegrator 未运行或返回空)")
        lines.append("")
        return lines

    overall_status = guard_results.get("overall_status", NA_PLACEHOLDER)
    lines.append(f"- **总体状态**: {overall_status}")
    lines.append("")

    guards = guard_results.get("guards", [])
    if isinstance(guards, list) and guards:
        lines.append("#### Guard 明细")
        lines.append("")
        lines.append("| Guard 名称 | 状态 | 检查结果 | 建议 |")
        lines.append("|-----------|------|---------|------|")
        for guard in guards:
            if not isinstance(guard, dict):
                continue
            name = guard.get("name", NA_PLACEHOLDER)
            status = guard.get("status", NA_PLACEHOLDER)
            result = guard.get("result", NA_PLACEHOLDER)
            recommendation = guard.get("recommendation", "")
            lines.append(f"| {name} | {status} | {result} | {recommendation} |")
        lines.append("")

    recommendations = guard_results.get("recommendations", [])
    if isinstance(recommendations, list) and recommendations:
        lines.append("#### 建议")
        lines.append("")
        for rec in recommendations:
            lines.append(f"- {rec}")
        lines.append("")

    return lines


# ============================================================
# 6. 报告写入 (带重试)
# ============================================================


def write_report_with_retry(
    report_path: Path,
    content_lines: list[str],
    max_retries: int = 3,
    retry_delay_seconds: float = 0.5,
) -> Path:
    """写入报告文件 (带重试机制, 段 9 下半).

    Args:
        report_path: 报告文件路径
        content_lines: Markdown 行列表
        max_retries: 最大重试次数 (默认 3)
        retry_delay_seconds: 重试间隔 (默认 0.5s)

    Returns:
        实际写入的文件路径

    Raises:
        OSError: 所有重试均失败时抛出
    """
    report_path = Path(report_path)
    report_path.parent.mkdir(parents=True, exist_ok=True)

    content = "\n".join(content_lines)
    last_error: Exception | None = None

    for attempt in range(1, max_retries + 1):
        try:
            report_path.write_text(content, encoding="utf-8")
            logger.info(
                f"[ReportWriter] 报告写入成功: {report_path} (attempt={attempt})"
            )
            return report_path
        except PermissionError as e:
            last_error = e
            logger.warning(
                f"[ReportWriter] 写入失败 (attempt={attempt}/{max_retries}): {e}, 等待 {retry_delay_seconds}s 重试"
            )
            if attempt < max_retries:
                time.sleep(retry_delay_seconds)
        except OSError as e:
            last_error = e
            logger.error(f"[ReportWriter] 写入失败 (OSError): {e}")
            if attempt < max_retries:
                time.sleep(retry_delay_seconds)

    # 所有重试失败
    raise OSError(
        f"报告写入失败, 已重试 {max_retries} 次: {report_path}, 最后错误: {last_error}"
    )


def save_state_json(
    state_path: Path,
    phases_state: dict[str, Any],
    extra_fields: dict[str, Any] | None = None,
) -> Path:
    """保存状态 JSON 文件 (段 9 末尾).

    Args:
        state_path: JSON 状态文件路径
        phases_state: 各阶段状态字典
        extra_fields: 额外字段 (如 generated_at / trade_date)

    Returns:
        实际写入的文件路径
    """
    state_path = Path(state_path)
    state_path.parent.mkdir(parents=True, exist_ok=True)

    state_data: dict[str, Any] = {
        "phases": phases_state,
        "generated_at": datetime.now().isoformat(timespec="seconds"),
    }
    if extra_fields:
        state_data.update(extra_fields)

    state_path.write_text(
        json.dumps(state_data, ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )
    logger.info(f"[ReportWriter] 状态 JSON 已保存: {state_path}")
    return state_path


# ============================================================
# 7. 总结构建
# ============================================================


def build_report_summary(
    phases_state: dict[str, Any],
    pnl_attribution_result: dict[str, Any] | None = None,
) -> list[str]:
    """构建报告总结段落 (段 9 中部).

    Args:
        phases_state: 各阶段状态字典
        pnl_attribution_result: P&L 归因结果 (可选)

    Returns:
        Markdown 行列表
    """
    lines: list[str] = []
    lines.append("## 总结")
    lines.append("")

    # 统计阶段成功率
    total_phases = len(phases_state)
    ok_count = sum(
        1
        for p in phases_state.values()
        if isinstance(p, dict) and (p.get("status") in ("ok", True))
    )
    fail_count = sum(
        1
        for p in phases_state.values()
        if isinstance(p, dict) and p.get("status") in ("error", "failed")
    )

    lines.append(f"- 阶段执行: {ok_count}/{total_phases} 成功, {fail_count} 失败")

    if pnl_attribution_result and isinstance(pnl_attribution_result, dict):
        total_pnl = pnl_attribution_result.get("total_pnl", 0.0)
        lines.append(f"- 当日 PnL: {total_pnl:+,.2f}")

    # 下一交易日提示
    today = datetime.now()
    next_trading_day = today + timedelta(days=1)
    while next_trading_day.weekday() >= 5:  # 跳过周末
        next_trading_day += timedelta(days=1)
    next_date_str = next_trading_day.strftime("%Y-%m-%d")
    lines.append(f"- 下一交易日: {next_date_str}")
    lines.append("")

    return lines


# ============================================================
# 模块导出
# ============================================================

__all__ = [
    "ALLOW_DEGRADE_PHASES",
    "EMPTY_SECTION_PLACEHOLDER",
    # 常量
    "NA_PLACEHOLDER",
    "PHASE_NAMES",
    "REPORT_SEPARATOR",
    "REPORT_TITLE",
    "build_phase_execution_summary",
    # 函数
    "build_report_header",
    "build_report_summary",
    "render_barra_decomposition",
    "render_eod_guard_chain",
    "render_phase_summary",
    "render_pnl_attribution",
    "save_state_json",
    "write_report_with_retry",
]
