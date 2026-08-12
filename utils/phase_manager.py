"""
十五五规划年度阶段管理器 v1.0
================================

基于《十五五规划五年投资计划》第八章 8.1-8.7 节, 实现 5 个年度阶段的
自动切换、季度评估触发、2030 清仓流程控制。

5 年度阶段:
    2026 建仓期   — 目标 8%,  最大回撤 8%,  Q1-Q2 建仓 70%, Q3-Q4 满配
    2027 主线兑现 — 目标 12%, 最大回撤 10%, 第一梯队业绩兑现 + 第二梯队布局
    2028 分化期   — 目标 10%, 最大回撤 10%, alpha 选股 + 板块轮动
    2029 �杠杆期 — 目标 8%,  最大回撤 8%,  期货减半 + 量化中性降至 50%
    2030 退出期   — 目标 5%,  最大回撤 3%,  Q1 保留核心 → Q4 全现金

季度评估触发:
    3/6/9/12 月最后一个交易日 → 触发压力测试 + 策略有效性检验

2030 清仓流程 (Q1-Q4 分步):
    Q1: 保留核心持仓 50%, 期货仅保留对冲空头, 方向性清零
    Q2: 分 4 周卖出股票 (每周 25%), ETF 二级卖出, 量化中性平仓
    Q3: 全部持仓清零, 转入逆回购+货基+短期国债
    Q4: 100% 现金/类现金, 12-31 清算完成

用法:
    from utils.phase_manager import PhaseManager
    pm = PhaseManager()
    phase = pm.get_current_phase()
    if pm.is_quarter_end():
        pm.trigger_quarterly_review()
    if pm.is_liquidation_phase():
        actions = pm.get_liquidation_actions()
"""

from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

logger = logging.getLogger("phase_manager")

BASE_DIR = Path(__file__).resolve().parent.parent
REPORT_DIR = BASE_DIR / "reports"


# ============================================================
# 5 年度阶段定义 (来自十五五规划投资计划 8.1 节)
# ============================================================
ANNUAL_PHASES: dict[str, dict[str, Any]] = {
    "2026": {
        "name": "建仓期",
        "period": "2026-07-14 to 2026-12-31",
        "target_return": 0.08,
        "max_drawdown": 0.08,
        "leverage_target": 1.28,
        "actions": {
            "Q1_Q2": "分批建仓70%资金,优先第一梯队核心标的+第三梯队底仓",
            "Q3_Q4": "全面建仓至100%,各策略满配,期货小仓位试水",
            "hedge": "建立常态化尾部期权保护",
        },
        "capital_deployment": {
            "target_by_q2_end": 0.70,
            "target_by_q4_end": 1.00,
        },
        "risk_focus": "建仓期遇回调是加仓机会,预留30万应急保证金",
    },
    "2027": {
        "name": "主线兑现期",
        "period": "2027-01-01 to 2027-12-31",
        "target_return": 0.12,
        "max_drawdown": 0.10,
        "leverage_target": 1.28,
        "actions": {
            "H1": "维持满仓,第一梯队业绩催化,期货偏多头寸",
            "H2": "布局第二梯队标的(量子/氢能/合成生物),量化中性满配",
            "hedge": "备兑看涨覆盖40%,随估值上升增加对冲比例",
        },
        "valuation_alert": {
            "trigger": "沪深300动态PE > 18",
            "action": "increase_hedge_ratio_to_50pct",
        },
        "risk_focus": "估值快速膨胀风险,PE>18倍时增加对冲",
    },
    "2028": {
        "name": "分化期",
        "period": "2028-01-01 to 2028-12-31",
        "target_return": 0.10,
        "max_drawdown": 0.10,
        "leverage_target": 1.20,
        "actions": {
            "Q1": "全面调仓,卖出因子打分下降标的,加仓验证逻辑的第二梯队",
            "Q2_Q3": "alpha选股为主,减少beta暴露,量化中性维持满配",
            "Q4": "评估宏观,若利率上行增加国债空头对冲,开始降杠杆",
        },
        "rotation_focus": "sector_rotation_alpha_driven",
        "risk_focus": "分化期易踩雷,严格执行单一持仓≤5%和单一行业≤15%",
    },
    "2029": {
        "name": "去杠杆期",
        "period": "2029-01-01 to 2029-12-31",
        "target_return": 0.08,
        "max_drawdown": 0.08,
        "leverage_target": 0.96,
        "actions": {
            "Q1_Q2": "期货方向性仓位减半(保证金降至30万),量化中性降至50%",
            "Q3_Q4": "股票调仓转向高股息标的,红利ETF增至40%,现金提至35%",
            "hedge": "备兑覆盖提至50%,尾部保护力度增加(权利金0.4%/季)",
        },
        "risk_focus": "去杠杆节奏不宜过快,避免在仍有行情时过早退出",
    },
    "2030": {
        "name": "退出期",
        "period": "2030-01-01 to 2030-12-31",
        "target_return": 0.05,
        "max_drawdown": 0.03,
        "leverage_target": 0.40,
        "actions": {
            "Q1": "保留核心持仓50%,其余获利了结,期货仅保留对冲空头",
            "Q2": "分4周卖出股票(每周25%),ETF二级市场卖出,量化中性平仓",
            "Q3": "全部持仓清零,转入逆回购+货基+短期国债",
            "Q4": "100%现金/类现金,12月31日清算完成",
        },
        "liquidation_order": [
            "1_illiquid_small_cap",
            "2_quant_neutral_shorts_then_longs",
            "3_futures_directional",
            "4_large_cap_stocks_and_ETFs",
            "5_options_expire_or_exercise",
            "6_futures_hedge_close",
        ],
        "extended_exit_clause": {
            "condition": "2030年底明确牛市初期且组合运行良好",
            "max_retained_pct": 0.10,
            "deadline": "2031-03-31",
            "requirement": "100pct_tail_hedged",
        },
        "risk_focus": "退出期优先流动性,防止冲击成本",
    },
}


# ============================================================
# 2030 清仓 Q1-Q4 详细动作
# ============================================================
LIQUIDATION_QUARTERLY_ACTIONS: dict[str, dict[str, Any]] = {
    "Q1": {
        "name": "保留核心+方向性清零",
        "period": "2030-01-01 to 2030-03-31",
        "stock_target_pct": 0.50,  # 保留 50% 核心持仓
        "etf_target_pct": 0.50,  # 保留 50% ETF
        "futures_directional": "clear",  # 方向性仓位清零
        "futures_hedge": "keep_short",  # 仅保留对冲空头
        "quant_neutral": "reduce_50pct",
        "options": "keep_tail_put",  # 保留尾部保护
        "weekly_sell_pct": None,
        "actions": [
            "保留 50% 核心持仓 (第一梯队龙头)",
            "其余 50% 股票逐步获利了结",
            "期货方向性仓位全部平仓",
            "期货仅保留 IF/IC 对冲空头",
            "量化中性策略减仓 50%",
            "备兑看涨期权全部平仓回收权利金",
            "保留尾部 put 作为最后保险",
        ],
    },
    "Q2": {
        "name": "分4周系统性清仓",
        "period": "2030-04-01 to 2030-06-30",
        "stock_target_pct": 0.0,
        "etf_target_pct": 0.0,
        "futures_directional": "clear",
        "futures_hedge": "reduce_50pct",
        "quant_neutral": "close_all",  # 多头卖出+空头平仓
        "options": "exercise_or_close",
        "weekly_sell_pct": 0.25,  # 每周卖出 25%
        "actions": [
            "股票分 4 周卖出, 每周 25%, 避免冲击成本",
            "优先卖出流动性差的小盘股",
            "大盘股和 ETF 最后卖出",
            "ETF 直接二级市场卖出",
            "量化中性策略平仓: 先平空头期货, 再卖多头股票",
            "期货对冲空头减仓 50%",
            "期权在到期前 2 周评估行权",
        ],
    },
    "Q3": {
        "name": "全部清零+转入安全资产",
        "period": "2030-07-01 to 2030-09-30",
        "stock_target_pct": 0.0,
        "etf_target_pct": 0.0,
        "futures_directional": "clear",
        "futures_hedge": "close_all",
        "quant_neutral": "closed",
        "options": "expired",
        "cash_allocation": {
            "reverse_repo": 0.50,  # 50% 逆回购
            "money_market_fund": 0.30,  # 30% 货基
            "short_term_bond": 0.20,  # 20% 短期国债
        },
        "actions": [
            "全部持仓清零",
            "期货对冲头寸全部平仓",
            "资金转入逆回购 RCO001 (50%)",
            "资金转入货基 511990.SH (30%)",
            "资金转入短期国债 019696.SH (20%)",
            "12月31日正式清算完成的提前准备",
        ],
    },
    "Q4": {
        "name": "纯现金+清算完成",
        "period": "2030-10-01 to 2030-12-31",
        "stock_target_pct": 0.0,
        "etf_target_pct": 0.0,
        "futures_directional": "clear",
        "futures_hedge": "closed",
        "quant_neutral": "closed",
        "options": "keep_minimal_tail",
        "cash_allocation": {
            "reverse_repo": 0.60,
            "money_market_fund": 0.30,
            "short_term_bond": 0.10,
        },
        "final_liquidation_date": "2030-12-31",
        "actions": [
            "100% 现金/类现金",
            "仅保留极少量尾部期权 (以防黑天鹅)",
            "12月31日正式清算完成",
            "500万+累计收益转入下一周期",
        ],
    },
}


# ============================================================
# 数据类
# ============================================================
@dataclass
class PhaseInfo:
    """年度阶段信息"""

    year: str
    phase_name: str
    period: str
    target_return: float
    max_drawdown: float
    leverage_target: float
    actions: dict[str, str] = field(default_factory=dict)
    risk_focus: str = ""
    is_liquidation_year: bool = False
    current_quarter: str = ""  # Q1/Q2/Q3/Q4
    liquidation_actions: dict | None = None


@dataclass
class QuarterlyReviewResult:
    """季度评估结果"""

    review_date: str = ""
    quarter: str = ""  # Q1/Q2/Q3/Q4
    is_quarter_end: bool = False
    stress_test_triggered: bool = False
    stress_test_result: dict | None = None
    strategy_effectiveness: dict[str, float] = field(default_factory=dict)
    rebalance_needed: bool = False
    actions: list[str] = field(default_factory=list)


class PhaseManager:
    """十五五规划年度阶段管理器

    职责:
        1. 判断当前年度阶段 (2026建仓 / 2027主线 / 2028分化 / 2029去杠杆 / 2030退出)
        2. 季度末自动触发压力测试 + 策略有效性检验
        3. 2030 年触发 Q1-Q4 分步清仓流程
        4. 输出年度配置 (目标收益率/最大回撤/杠杆目标)

    集成路径:
        daily_workflow.py → PhaseManager.get_current_phase()
        季度末: PhaseManager.trigger_quarterly_review()
        2030年: PhaseManager.get_liquidation_actions()
    """

    # 计划起止日期
    PLAN_START_DATE = date(2026, 7, 14)
    PLAN_END_DATE = date(2030, 12, 31)

    # 季度末日期 (用于触发季度评估)
    QUARTER_END_MONTHS = {3, 6, 9, 12}

    def __init__(self):
        self.phases = ANNUAL_PHASES.copy()
        logger.info(f"[PhaseManager] 初始化: 计划周期 {self.PLAN_START_DATE} ~ {self.PLAN_END_DATE}, 5 个年度阶段")

    # --------------------------------------------------------
    # 当前阶段判断
    # --------------------------------------------------------
    def get_current_phase(self, today: date | None = None) -> PhaseInfo:
        """获取当前年度阶段信息

        Args:
            today: 当前日期 (默认今天)

        Returns:
            PhaseInfo 对象
        """
        today = today or date.today()
        year_str = str(today.year)

        # 计划尚未开始
        if today < self.PLAN_START_DATE:
            return PhaseInfo(
                year="pre_plan",
                phase_name="计划未启动",
                period=f"{today} ~ {self.PLAN_START_DATE}",
                target_return=0.0,
                max_drawdown=0.0,
                leverage_target=0.0,
                risk_focus="尚未进入十五五投资计划周期",
            )

        # 计划已结束
        if today > self.PLAN_END_DATE:
            return PhaseInfo(
                year="post_plan",
                phase_name="计划已完成",
                period=f"{self.PLAN_END_DATE} ~ {today}",
                target_return=0.0,
                max_drawdown=0.0,
                leverage_target=0.0,
                risk_focus="十五五投资计划已清算完成,进入下一周期",
            )

        # 获取对应年度配置
        phase_cfg = self.phases.get(year_str, {})
        if not phase_cfg:
            # 不在5年计划范围内的年份
            return PhaseInfo(
                year=year_str,
                phase_name="未定义阶段",
                period=year_str,
                target_return=0.0,
                max_drawdown=0.0,
                leverage_target=0.0,
                risk_focus=f"{year_str} 年不在十五五计划范围内",
            )

        # 计算当前季度
        current_quarter = f"Q{(today.month - 1) // 3 + 1}"

        # 判断是否为清仓年 (2030)
        is_liquidation_year = year_str == "2030"
        liquidation_actions = None
        if is_liquidation_year:
            liquidation_actions = LIQUIDATION_QUARTERLY_ACTIONS.get(current_quarter, {})

        return PhaseInfo(
            year=year_str,
            phase_name=phase_cfg.get("name", ""),
            period=phase_cfg.get("period", ""),
            target_return=phase_cfg.get("target_return", 0.0),
            max_drawdown=phase_cfg.get("max_drawdown", 0.0),
            leverage_target=phase_cfg.get("leverage_target", 0.0),
            actions=phase_cfg.get("actions", {}),
            risk_focus=phase_cfg.get("risk_focus", ""),
            is_liquidation_year=is_liquidation_year,
            current_quarter=current_quarter,
            liquidation_actions=liquidation_actions,
        )

    # --------------------------------------------------------
    # 季度末判断
    # --------------------------------------------------------
    def is_quarter_end(self, today: date | None = None) -> bool:
        """判断是否为季度末

        季度末定义: 3/6/9/12 月的最后一个交易日
        简化判断: 3/6/9/12 月的最后一天 (或倒数第2天)

        Args:
            today: 当前日期

        Returns:
            True 表示今天是季度末
        """
        today = today or date.today()
        if today.month not in self.QUARTER_END_MONTHS:
            return False
        # 当月最后一天 (简化版, 不考虑交易日)
        next_month = today.replace(day=28) + timedelta(days=4)
        last_day = next_month - timedelta(days=next_month.day)
        # 当前日期是当月最后3天内 (考虑交易日提前)
        return today.day >= last_day.day - 2

    def get_current_quarter(self, today: date | None = None) -> str:
        """获取当前季度

        Returns:
            Q1 / Q2 / Q3 / Q4
        """
        today = today or date.today()
        return f"Q{(today.month - 1) // 3 + 1}"

    # --------------------------------------------------------
    # 季度评估触发
    # --------------------------------------------------------
    def trigger_quarterly_review(
        self,
        positions: list[dict] | None = None,
        portfolio_value: float = 5_000_000,
        today: date | None = None,
    ) -> QuarterlyReviewResult:
        """触发季度评估

        季度评估内容 (来自计划 9.1 节):
            1. 压力测试 (4 场景)
            2. 策略有效性检验
            3. 偏离目标 ±5% → 调仓
            4. 策略间再平衡 (跑输/跑赢 2 季度)

        Args:
            positions: 当前持仓列表
            portfolio_value: 组合总市值
            today: 当前日期

        Returns:
            QuarterlyReviewResult 评估结果
        """
        today = today or date.today()
        quarter = self.get_current_quarter(today)

        result = QuarterlyReviewResult(
            review_date=today.isoformat(),
            quarter=quarter,
            is_quarter_end=self.is_quarter_end(today),
        )

        actions: list[str] = []

        # 1. 触发压力测试
        if result.is_quarter_end:
            result.stress_test_triggered = True
            actions.append("触发季度压力测试 (4 场景: 2015股灾/2018慢熊/2020冲击/流动性危机)")
            try:
                from utils.stress_test_runner import StressTestRunner

                runner = StressTestRunner()
                stress_result = runner.run_all_scenarios(positions or [], portfolio_value)
                scenarios = stress_result.get("scenarios", {})
                result.stress_test_result = {
                    "scenarios_run": len(scenarios),
                    "all_passed": all(s.get("pass", True) for s in scenarios.values()),
                    "worst_drawdown": min(
                        (s.get("actual_portfolio_dd", 0) for s in scenarios.values()),
                        default=0,
                    ),
                    "worst_scenario": stress_result.get("worst_scenario", ""),
                }
                actions.append(f"压力测试完成: {result.stress_test_result['scenarios_run']} 场景")
            except Exception as e:  # P2 模块 fail-safe, 待后续精确化  # noqa: BLE001
                logger.warning(f"[PhaseManager] 压力测试失败 (降级): {e}")
                actions.append(f"压力测试降级: {e}")

        # 2. 策略有效性检验
        phase = self.get_current_phase(today)
        result.strategy_effectiveness = {
            "phase": phase.phase_name,
            "target_return": phase.target_return,
            "max_drawdown_limit": phase.max_drawdown,
            "leverage_target": phase.leverage_target,
        }

        # 3. 偏离检查 (需要实际持仓数据)
        if positions:
            # 简化: 检查单一行业集中度
            sector_weights: dict[str, float] = {}
            for p in positions:
                sector = p.get("sector", "unknown")
                weight = float(p.get("weight", 0))
                sector_weights[sector] = sector_weights.get(sector, 0) + weight

            for sector, weight in sector_weights.items():
                if weight > 0.15:  # 单一行业 ≤ 15%
                    result.rebalance_needed = True
                    actions.append(f"行业集中度超限: {sector} = {weight:.1%} > 15%")
        else:
            actions.append("无持仓数据, 跳过集中度检查")

        # 4. 2030 清仓年: 返回清仓动作
        if phase.is_liquidation_year and phase.liquidation_actions:
            actions.append(f"2030 清仓 {phase.current_quarter}: {phase.liquidation_actions.get('name', '')}")
            for action in phase.liquidation_actions.get("actions", []):
                actions.append(f"  - {action}")

        result.actions = actions

        # 5. 保存评估报告
        self._save_quarterly_review(result, today)

        logger.info(
            f"[PhaseManager] 季度评估 {today.isoformat()} ({quarter}): "
            f"压测={result.stress_test_triggered}, 调仓={result.rebalance_needed}, "
            f"动作数={len(actions)}"
        )

        return result

    # --------------------------------------------------------
    # 2030 清仓流程
    # --------------------------------------------------------
    def is_liquidation_phase(self, today: date | None = None) -> bool:
        """判断是否处于清仓阶段 (2030 年)"""
        today = today or date.today()
        return today.year == 2030 and today <= self.PLAN_END_DATE

    def get_liquidation_actions(self, today: date | None = None) -> dict | None:
        """获取当前清仓动作 (Q1-Q4 分步)

        Returns:
            当前季度的清仓动作配置, 不在清仓期返回 None
        """
        today = today or date.today()
        if not self.is_liquidation_phase(today):
            return None

        quarter = self.get_current_quarter(today)
        return LIQUIDATION_QUARTERLY_ACTIONS.get(quarter)

    def get_liquidation_order(self) -> list[str]:
        """获取清仓顺序 (来自计划 10.1 节)

        清仓优先级:
            1. 流动性差的标的 (小盘股/低流动性 ETF/远月期权)
            2. 量化中性策略 (先平空头, 再卖多头)
            3. 期货方向性仓位 (平多头, 保留对冲空头)
            4. 大市值股票和主流 ETF (流动性好, 最后卖)
            5. 期权行权或到期
            6. 期货对冲头寸平仓
        """
        return [
            "1_illiquid_small_cap",
            "2_quant_neutral_shorts_then_longs",
            "3_futures_directional",
            "4_large_cap_stocks_and_ETFs",
            "5_options_expire_or_exercise",
            "6_futures_hedge_close",
        ]

    # --------------------------------------------------------
    # 提前退出评估
    # --------------------------------------------------------
    def check_early_exit_trigger(
        self,
        current_drawdown: float,
        today: date | None = None,
    ) -> dict | None:
        """检查提前退出触发条件 (来自计划 10.2 节)

        触发条件:
            - 组合累计回撤达 15%
            - 政策系统性风险
            - 个人流动性需求

        Args:
            current_drawdown: 当前组合回撤 (正数, 0.15 = 15%)
            today: 当前日期

        Returns:
            触发信息 (None 表示未触发)
        """
        today = today or date.today()

        # 回撤达 15% → 立即转入防御模式
        if current_drawdown >= 0.15:
            return {
                "trigger": "drawdown_15pct",
                "action": "defensive_mode",
                "target_allocation": {
                    "cash": 0.60,
                    "short_term_bond": 0.40,
                },
                "timeline": "1个月内完成清仓至现金80%",
                "message": f"组合回撤 {current_drawdown:.1%} ≥ 15% 红线, 立即转入防御模式",
            }

        # 回撤 12% → 大幅防御
        if current_drawdown >= 0.12:
            return {
                "trigger": "drawdown_12pct",
                "action": "reduce_60pct_and_hedge_80pct",
                "message": f"组合回撤 {current_drawdown:.1%} ≥ 12%, 减仓60%+对冲80%+仅留现金+对冲",
            }

        # 回撤 8% → 全面减仓+对冲
        if current_drawdown >= 0.08:
            return {
                "trigger": "drawdown_8pct",
                "action": "reduce_20pct_and_hedge_60pct",
                "message": f"组合回撤 {current_drawdown:.1%} ≥ 8%, 减仓20%+加期货空头对冲至40%",
            }

        # 回撤 5% → 预警审查
        if current_drawdown >= 0.05:
            return {
                "trigger": "drawdown_5pct",
                "action": "alert_review",
                "message": f"组合回撤 {current_drawdown:.1%} ≥ 5%, 启动预警审查",
            }

        return None

    # --------------------------------------------------------
    # 持久化
    # --------------------------------------------------------
    def _save_quarterly_review(self, result: QuarterlyReviewResult, today: date) -> None:
        """保存季度评估报告"""
        try:
            REPORT_DIR.mkdir(parents=True, exist_ok=True)
            file_path = REPORT_DIR / f"quarterly_review_{today.isoformat()}.json"

            data = asdict(result)
            with open(file_path, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2, default=str)

            logger.info(f"[PhaseManager] 季度评估报告已保存: {file_path}")
        except Exception as e:  # P2 模块 fail-safe, 待后续精确化  # noqa: BLE001
            logger.error(f"[PhaseManager] 保存季度评估报告失败: {e}")

    # --------------------------------------------------------
    # 摘要
    # --------------------------------------------------------
    def summary(self, today: date | None = None) -> str:
        """生成当前阶段摘要"""
        today = today or date.today()
        phase = self.get_current_phase(today)
        quarter = self.get_current_quarter(today)

        lines = [
            "=" * 60,
            f"十五五规划年度阶段 ({today.isoformat()})",
            "=" * 60,
            f"年度: {phase.year}",
            f"阶段: {phase.phase_name}",
            f"周期: {phase.period}",
            f"当前季度: {quarter}",
            "",
            f"目标年化收益: {phase.target_return:.1%}",
            f"最大回撤限制: {phase.max_drawdown:.1%}",
            f"杠杆目标: {phase.leverage_target:.2f}x",
            "",
            f"风险关注: {phase.risk_focus}",
        ]

        if phase.is_liquidation_year:
            lines.append("")
            lines.append(f"⚠️ 2030 清仓年 - {quarter} 阶段")
            if phase.liquidation_actions:
                lines.append(f"动作: {phase.liquidation_actions.get('name', '')}")
                for action in phase.liquidation_actions.get("actions", []):
                    lines.append(f"  - {action}")

        if self.is_quarter_end(today):
            lines.append("")
            lines.append("📅 季度末 - 触发季度评估")

        lines.append("=" * 60)
        return "\n".join(lines)


# ============================================================
# CLI 入口
# ============================================================
if __name__ == "__main__":
    import argparse
    from datetime import timedelta

    logging.basicConfig(level=logging.INFO, format="%(message)s")

    parser = argparse.ArgumentParser(description="十五五规划年度阶段管理器")
    parser.add_argument("--current", action="store_true", help="查看当前阶段")
    parser.add_argument("--quarterly", action="store_true", help="触发季度评估")
    parser.add_argument("--liquidation", action="store_true", help="查看清仓动作")
    parser.add_argument("--date", type=str, help="模拟日期 (YYYY-MM-DD)")
    args = parser.parse_args()

    pm = PhaseManager()
    sim_date = None
    if args.date:
        sim_date = datetime.strptime(args.date, "%Y-%m-%d").date()

    if args.current or not (args.quarterly or args.liquidation):
        logger.info(pm.summary(sim_date))

    if args.quarterly:
        result = pm.trigger_quarterly_review(today=sim_date)
        logger.info("\n季度评估结果:")
        logger.info(f"  季度: {result.quarter}")
        logger.info(f"  季度末: {result.is_quarter_end}")
        logger.debug(f"  压测触发: {result.stress_test_triggered}")
        logger.info(f"  调仓需要: {result.rebalance_needed}")
        logger.info(f"  动作数: {len(result.actions)}")
        for action in result.actions:
            logger.info(f"    - {action}")

    if args.liquidation:
        if pm.is_liquidation_phase(sim_date):
            actions = pm.get_liquidation_actions(sim_date)
            if actions is not None:
                logger.info(f"\n2030 清仓动作 ({actions.get('period', '')}):")
                logger.info(f"  名称: {actions.get('name', '')}")
                logger.info("  动作:")
                for action in actions.get("actions", []):
                    logger.info(f"    - {action}")
            logger.info("\n清仓顺序:")
            for i, step in enumerate(pm.get_liquidation_order(), 1):
                logger.info(f"  {i}. {step}")
        else:
            logger.info("\n当前不在清仓阶段 (2030 年)")
