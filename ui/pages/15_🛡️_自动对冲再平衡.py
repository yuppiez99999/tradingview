"""自动对冲再平衡监控页 — Streamlit UI。

实时展示目标达成监控指标，提供策略切换审批与熔断解除入口。

页面功能:
    - 滚动年化收益曲线 (252日窗口)
    - 滚动最大回撤曲线
    - 策略等级指示器 (6档状态机)
    - 降级标记列表
    - 熔断状态告警
    - 对冲工具选择决策面板
    - 策略切换审批入口
"""

from __future__ import annotations

import sys
from pathlib import Path

# 添加项目根目录到路径
PROJECT_ROOT = Path(__file__).parent.parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import streamlit as st

from utils.auto_hedge_rebalance.engine import AutoHedgeRebalanceEngine
from utils.auto_hedge_rebalance.models import StrategyLevel


# ============================================================================
# 策略等级颜色编码
# ============================================================================

_LEVEL_COLORS = {
    StrategyLevel.NORMAL: "#28a745",              # 绿
    StrategyLevel.MILD_CORRECTION: "#ffc107",     # 黄
    StrategyLevel.MODERATE_CORRECTION: "#fd7e14", # 橙
    StrategyLevel.SEVERE_CORRECTION: "#dc3545",   # 红
    StrategyLevel.CONSERVATIVE_DEFENSE: "#a71d2d",# 深红
    StrategyLevel.CIRCUIT_BREAKER: "#000000",     # 黑
}

_LEVEL_LABELS = {
    StrategyLevel.NORMAL: "正常",
    StrategyLevel.MILD_CORRECTION: "温和纠偏",
    StrategyLevel.MODERATE_CORRECTION: "中度纠偏",
    StrategyLevel.SEVERE_CORRECTION: "重度纠偏",
    StrategyLevel.CONSERVATIVE_DEFENSE: "保守防御",
    StrategyLevel.CIRCUIT_BREAKER: "熔断",
}


@st.cache_resource
def get_engine() -> AutoHedgeRebalanceEngine:
    """获取引擎实例 (缓存)。"""
    return AutoHedgeRebalanceEngine(
        base_dir=str(PROJECT_ROOT),
        config_path="config/auto_hedge_rebalance.yaml",
    )


def render_strategy_level(state) -> None:
    """渲染策略等级指示器。"""
    level = state.current_level
    color = _LEVEL_COLORS.get(level, "#999999")
    label = _LEVEL_LABELS.get(level, level.value)
    st.markdown(
        f'<div style="background-color:{color};color:white;padding:10px;'
        f'border-radius:5px;text-align:center;font-size:18px;font-weight:bold;">'
        f"策略等级: {label}"
        f"</div>",
        unsafe_allow_html=True,
    )


def render_monitor_metrics(monitor) -> None:
    """渲染监控指标。"""
    col1, col2, col3, col4 = st.columns(4)
    with col1:
        st.metric("滚动年化收益", f"{monitor.rolling_annual_return:.2%}")
    with col2:
        st.metric("滚动最大回撤", f"{monitor.rolling_max_drawdown:.2%}")
    with col3:
        st.metric("收益偏离度", f"{monitor.return_deviation:.2%}")
    with col4:
        st.metric("回撤余量", f"{monitor.drawdown_margin:.2%}")

    if monitor.sample_insufficient:
        st.warning("⚠️ 样本不足，指标仅供参考")


def render_tool_selection(selection) -> None:
    """渲染对冲工具选择决策面板。"""
    st.subheader("对冲工具选择")
    col1, col2, col3 = st.columns(3)
    with col1:
        st.metric("工具类型", selection.tool_type.value)
    with col2:
        st.metric("对冲比例", f"{selection.hedge_ratio:.0%}")
    with col3:
        st.metric("标的数量", len(selection.instruments))

    if selection.instruments:
        st.write("**选定标的:**", ", ".join(selection.instruments))
    if selection.reasoning:
        st.info(f"决策推理: {selection.reasoning}")


def render_degradation_flags(flags: list[str]) -> None:
    """渲染降级标记列表。"""
    if flags:
        st.subheader("⚠️ 降级标记")
        for flag in flags:
            st.warning(flag)


def render_circuit_breaker(breaker_status, engine) -> None:
    """渲染熔断状态告警。"""
    if breaker_status.active:
        st.error("🚨 熔断活跃！拒绝新交易")
        st.write(f"**触发原因:** {breaker_status.trigger_reason}")
        st.write(f"**触发时间:** {breaker_status.trigger_time}")
        st.write(f"**紧急动作:** {breaker_status.emergency_action}")

        with st.form("release_breaker"):
            approver = st.text_input("审批人姓名")
            comment = st.text_input("解除备注")
            if st.form_submit_button("解除熔断"):
                if approver:
                    success = engine.release_circuit_breaker(approver, comment)
                    if success:
                        st.success("熔断已解除")
                        st.rerun()
                    else:
                        st.error("解除失败")
                else:
                    st.error("请填写审批人姓名")


def main() -> None:
    """主页面渲染。"""
    st.set_page_config(page_title="自动对冲再平衡", page_icon="🛡️", layout="wide")
    st.title("🛡️ 自动对冲再平衡监控")

    engine = get_engine()

    # 监控报告
    st.subheader("目标达成监控")
    monitor = engine.get_monitor_report()
    render_monitor_metrics(monitor)

    # 策略等级
    st.subheader("策略等级状态")
    state = engine.get_strategy_state()
    render_strategy_level(state)

    # 熔断状态
    breaker_status = engine.run_eod_decision.__self__.circuit_breaker.get_status()
    render_circuit_breaker(breaker_status, engine)

    # EOD决策
    st.subheader("EOD决策")
    if st.button("执行EOD决策"):
        with st.spinner("执行中..."):
            plan = engine.run_eod_decision()
            render_tool_selection(plan.tool_selection)
            render_degradation_flags(plan.degradation_flags)
            st.json(plan.__dict__, expanded=False)

    # 盘中检查
    st.subheader("盘中紧急检查")
    col1, col2 = st.columns(2)
    with col1:
        current_value = st.number_input("当前组合价值", value=1000.0)
    with col2:
        previous_value = st.number_input("上一时刻价值", value=1000.0)
    if st.button("执行盘中检查"):
        action = engine.run_intraday_check(current_value, previous_value)
        if action.action_type == "none":
            st.success("无需紧急操作")
        else:
            st.warning(f"触发: {action.description}")


if __name__ == "__main__":
    main()