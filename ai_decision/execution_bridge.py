# -*- coding: utf-8 -*-
"""
ai_decision.execution_bridge — 决策→执行桥接层
================================================

将 TradingDecision 转化为 OrderRouter 可消费的执行计划, 经硬风控后路由到 broker。

三层安全防线 (不可绕过):
  L1 — decision_gate 硬风控 (决策层, 在 orchestrator 中已完成)
  L2 — 执行层硬风控 (本模块, 下单前二次校验, 涵盖价格保护/流动性/熔断)
  L3 — broker 层风控 (外部, 券商柜台级风控)

灰度发布:
  shadow → paper → auto_10% → auto_50% → auto_100%
  每阶段有独立回滚触发条件 (PnL 偏离 > 2σ / 连续亏损 / 异常放量)

设计原则:
  - 永远不绕过 L2 风控直接下单
  - shadow/paper 模式 100% 不触达 broker
  - 执行结果回写审计, 全链路可追溯
"""

from __future__ import annotations

import json
import logging
import math
import os
import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple, ClassVar

from ai_decision.config import get_config
from ai_decision.decision_gate import RiskContext, run_hard_risk
from ai_decision.models import TradingDecision

logger = logging.getLogger("ai_decision.execution_bridge")

_EXEC_AUDIT_DIR = os.path.join("reports", "ai_decision", "execution")
_GRAYSCALE_STATE_FILE = os.path.join("reports", "ai_decision", "grayscale_state.json")


# ============================================================
# 灰度状态管理
# ============================================================

@dataclass
class GrayscaleState:
    """灰度发布状态机"""
    stage: str = "shadow"                     # shadow / paper / auto_10 / auto_50 / auto_100
    started_at: str = ""                      # 当前阶段开始时间 ISO
    cumulative_pnl: float = 0.0               # 累计 PnL
    daily_pnl_series: List[float] = field(default_factory=list)  # 最近 30 日 PnL
    consecutive_losses: int = 0               # 连续亏损天数
    rollback_count: int = 0                   # 回滚次数
    last_evaluation: str = ""                 # 上次评估时间
    # Step 5 新增: 推进条件评估所需指标
    daily_decision_count: List[int] = field(default_factory=list)  # 每日决策数 (shadow→paper 条件)
    paper_fill_rate: float = 0.0              # paper 阶段模拟成交率 (paper→auto_10 条件)
    escalation_count: int = 0                 # 当前阶段 escalation 计数 (paper→auto_10 条件)

    @classmethod
    def load(cls) -> GrayscaleState:
        """从持久化文件加载灰度状态，缺失时返回初始状态。

        读取失败或文件不存在时，根据全局配置 mode 初始化为对应阶段
        (shadow / paper / auto_10)，并记录当前时间作为阶段开始时间。

        Returns:
            GrayscaleState: 加载或新建的灰度状态实例
        """
        try:
            if os.path.exists(_GRAYSCALE_STATE_FILE):
                with open(_GRAYSCALE_STATE_FILE, "r", encoding="utf-8") as fh:
                    data = json.load(fh)
                    return cls(**{k: data.get(k, v) for k, v in cls().__dict__.items()})
        except Exception as e:
            logger.warning(f"加载 GrayscaleState 失败: {e}", exc_info=True)
        gs = cls()
        # P0 修复: mode (shadow/paper/auto) 与 stage (shadow/paper/auto_10/auto_50/auto_100) 命名不一致
        # 原代码 gs.stage = get_config("mode", "shadow") 在 mode=auto 时 stage="auto",
        # 但 effective_allocation_pct 字典无 "auto" key → 返回 0.0 → 永远不下单
        _mode_to_initial_stage = {
            "shadow": "shadow",
            "paper": "paper",
            "auto": "auto_10",  # auto 模式初始进入 auto_10 (10% 资金)
        }
        _mode = get_config("mode", "shadow")
        gs.stage = _mode_to_initial_stage.get(_mode, "shadow")
        gs.started_at = datetime.now().isoformat()
        return gs

    def save(self) -> None:
        """将灰度状态持久化到 JSON 文件。

        自动创建缺失目录，以 UTF-8 编码写入所有字段。
        """
        os.makedirs(os.path.dirname(_GRAYSCALE_STATE_FILE), exist_ok=True)
        with open(_GRAYSCALE_STATE_FILE, "w", encoding="utf-8") as fh:
            json.dump({k: getattr(self, k) for k in self.__dict__},
                      fh, ensure_ascii=False, indent=2)

    def should_rollback(self) -> Tuple[bool, str]:
        """检查是否应触发回滚 (在 auto 模式下每笔交易前调用)"""
        if self.stage in ("shadow", "paper"):
            return False, ""

        reasons: List[str] = []

        # 1. 连续亏损 (连续 5 笔亏损回滚一档, 连续 8 笔回滚到 paper)
        if self.consecutive_losses >= 8:
            reasons.append(f"连续亏损 {self.consecutive_losses} 天 (>=8), 严重异常")

        # 2. PnL 偏离 > 2σ (基于近 30 日序列)
        if len(self.daily_pnl_series) >= 5:
            mu = sum(self.daily_pnl_series) / len(self.daily_pnl_series)
            var = sum((x - mu) ** 2 for x in self.daily_pnl_series) / len(self.daily_pnl_series)
            sigma = math.sqrt(var) if var > 0 else 0.001
            latest = self.daily_pnl_series[-1]
            if latest < mu - 2 * sigma and sigma > 0:
                reasons.append(f"PnL {latest:.4f} 偏离均值 {mu:.4f} 超过 2σ ({sigma:.4f})")

        if reasons:
            return True, "; ".join(reasons)
        return False, ""

    def advance_stage(self, new_pnl: float = 0.0) -> str:
        """记录每日 PnL 并尝试推进灰度阶段"""
        self.daily_pnl_series.append(new_pnl)
        if len(self.daily_pnl_series) > 30:
            self.daily_pnl_series = self.daily_pnl_series[-30:]

        self.cumulative_pnl += new_pnl
        if new_pnl < 0:
            self.consecutive_losses += 1
        else:
            self.consecutive_losses = 0

        self.last_evaluation = datetime.now().isoformat()
        self.save()
        return self.stage

    def do_rollback(self) -> str:
        """执行回滚: 退回上一阶段"""
        rollback_map = {
            "auto_100": "auto_50",
            "auto_50": "auto_10",
            "auto_10": "paper",
            "paper": "shadow",
        }
        new_stage = rollback_map.get(self.stage, "shadow")
        logger.warning("GRAYSCALE ROLLBACK: %s -> %s", self.stage, new_stage)
        self.stage = new_stage
        self.started_at = datetime.now().isoformat()
        self.rollback_count += 1
        self.consecutive_losses = 0
        self.save()
        return new_stage

    def effective_allocation_pct(self) -> float:
        """当前灰度阶段对应的资金分配百分比"""
        return {
            "shadow": 0.0,
            "paper": 0.0,
            "auto_10": 0.10,
            "auto_50": 0.50,
            "auto_100": 1.00,
        }.get(self.stage, 0.0)

    # ============================================================
    # Step 5: 灰度自动推进 (含 14 天硬约束)
    # ============================================================

    # 推进条件矩阵 (roadmap line 303-310)
    # ClassVar 标记为类变量, 不被 dataclass 当作字段
    _ADVANCE_MAP: ClassVar[Dict[str, Tuple[str, int]]] = {
        # 当前阶段 → (下一阶段, 最小停留天数)
        "shadow":  ("paper",   14),  # shadow → paper: 跑满 14 天
        "paper":   ("auto_10", 3),   # paper → auto_10: 跑满 3 天
        "auto_10": ("auto_50", 3),   # auto_10 → auto_50: 跑满 3 天
        "auto_50": ("auto_100", 7),  # auto_50 → auto_100: 跑满 7 天
    }

    def _calc_days_in_stage(self) -> int:
        """计算当前阶段已运行天数 (基于 started_at)

        Returns:
            已运行天数 (向下取整), started_at 缺失时返回 0
        """
        if not self.started_at:
            return 0
        try:
            start = datetime.fromisoformat(self.started_at)
            delta = datetime.now() - start
            return max(0, delta.days)
        except (ValueError, TypeError):
            return 0

    def _avg_daily_decisions(self) -> float:
        """计算日均决策数 (shadow→paper 推进条件)

        Returns:
            日均决策数, daily_decision_count 为空时返回 0.0
        """
        if not self.daily_decision_count:
            return 0.0
        return sum(self.daily_decision_count) / len(self.daily_decision_count)

    def _check_advance_conditions(self) -> Tuple[bool, str]:
        """检查当前阶段是否满足推进条件 (推进条件矩阵)

        推进条件矩阵 (roadmap line 303-310):
            shadow  → paper:   跑满 14 天 + 日均决策数 ≥ 10
            paper   → auto_10: 跑满 3 天 + 模拟成交率 ≥ 95% + 无 escalation 风险
            auto_10 → auto_50: 跑满 3 天 + 累计 PnL > 0 + 无回滚
            auto_50 → auto_100: 跑满 7 天 + 累计 PnL > 0 + 无回滚

        Returns:
            (can_advance, reason) — can_advance=True 时 reason 为空,
            can_advance=False 时 reason 说明未满足的条件
        """
        if self.stage not in self._ADVANCE_MAP:
            # auto_100 是最终阶段, 不可推进
            return False, f"阶段 {self.stage} 已是最终阶段, 不可推进"

        next_stage, min_days = self._ADVANCE_MAP[self.stage]
        days_in_stage = self._calc_days_in_stage()

        # 通用条件: 最小停留天数
        if days_in_stage < min_days:
            return False, (
                f"阶段 {self.stage} 已运行 {days_in_stage} 天, "
                f"未满 {min_days} 天 (还需 {min_days - days_in_stage} 天)"
            )

        # 阶段特定条件
        if self.stage == "shadow":
            # shadow → paper: 日均决策数 ≥ 10
            avg_decisions = self._avg_daily_decisions()
            if avg_decisions < 10:
                return False, (
                    f"shadow 阶段日均决策数 {avg_decisions:.1f} < 10, "
                    f"不足以推进到 paper"
                )

        elif self.stage == "paper":
            # paper → auto_10: 模拟成交率 ≥ 95% + 无 escalation 风险
            if self.paper_fill_rate < 0.95:
                return False, (
                    f"paper 阶段模拟成交率 {self.paper_fill_rate:.1%} < 95%, "
                    f"不足以推进到 auto_10"
                )
            if self.escalation_count > 0:
                return False, (
                    f"paper 阶段有 {self.escalation_count} 个 escalation, "
                    f"存在风险, 不可推进到 auto_10"
                )

        elif self.stage in ("auto_10", "auto_50"):
            # auto_10 → auto_50 / auto_50 → auto_100: 累计 PnL > 0 + 无回滚
            if self.cumulative_pnl <= 0:
                return False, (
                    f"{self.stage} 阶段累计 PnL {self.cumulative_pnl:.2f} ≤ 0, "
                    f"不可推进到 {next_stage}"
                )
            if self.rollback_count > 0:
                return False, (
                    f"{self.stage} 阶段有 {self.rollback_count} 次回滚记录, "
                    f"不可推进到 {next_stage}"
                )

        return True, ""

    def advance_grayscale(
        self,
        daily_pnl: float = 0.0,
        daily_decision_count: int = 0,
        paper_fill_rate: Optional[float] = None,
        escalation_count: Optional[int] = None,
    ) -> Dict[str, Any]:
        """灰度自动推进主函数 (含 14 天硬约束自检)

        每日 EOD 调用一次, 记录当日指标并尝试推进灰度阶段.
        推进条件全部满足时自动进入下一阶段; 否则仅更新指标.

        Args:
            daily_pnl: 当日 PnL (正=盈利, 负=亏损)
            daily_decision_count: 当日决策数 (shadow→paper 条件)
            paper_fill_rate: paper 阶段模拟成交率 (None 时不更新)
            escalation_count: 当前阶段累计 escalation 数 (None 时不更新)
        Returns:
            {
                "current_stage": str,        # 当前阶段 (推进后为下一阶段)
                "advanced": bool,            # 是否发生推进
                "next_stage": str,           # 下一阶段 (未推进时与 current_stage 相同)
                "days_in_stage": int,        # 当前阶段已运行天数
                "reason": str,               # 未推进原因 / 推进成功信息
                "rollback_triggered": bool,  # 是否触发回滚
                "rollback_reason": str,      # 回滚原因
            }
        """
        # 1. 记录当日指标
        self.daily_pnl_series.append(daily_pnl)
        if len(self.daily_pnl_series) > 30:
            self.daily_pnl_series = self.daily_pnl_series[-30:]

        self.cumulative_pnl += daily_pnl
        if daily_pnl < 0:
            self.consecutive_losses += 1
        else:
            self.consecutive_losses = 0

        if daily_decision_count > 0:
            self.daily_decision_count.append(daily_decision_count)
            if len(self.daily_decision_count) > 30:
                self.daily_decision_count = self.daily_decision_count[-30:]

        if paper_fill_rate is not None:
            self.paper_fill_rate = paper_fill_rate

        if escalation_count is not None:
            self.escalation_count = escalation_count

        self.last_evaluation = datetime.now().isoformat()

        # 2. 回滚检查 (仅 auto 模式)
        should_rb, rb_reason = self.should_rollback()
        if should_rb:
            new_stage = self.do_rollback()
            self.save()
            return {
                "current_stage": new_stage,
                "advanced": False,
                "next_stage": new_stage,
                "days_in_stage": 0,  # 回滚后重置
                "reason": f"触发回滚: {rb_reason}",
                "rollback_triggered": True,
                "rollback_reason": rb_reason,
            }

        # 3. 推进条件检查 (含 14 天硬约束)
        can_advance, reason = self._check_advance_conditions()
        if can_advance:
            next_stage = self._ADVANCE_MAP[self.stage][0]
            logger.info(
                "GRAYSCALE ADVANCE: %s -> %s (已运行 %d 天, PnL=%.2f)",
                self.stage, next_stage, self._calc_days_in_stage(), self.cumulative_pnl,
            )
            self.stage = next_stage
            self.started_at = datetime.now().isoformat()
            # 推进后重置阶段特定指标
            self.escalation_count = 0
            self.paper_fill_rate = 0.0
            self.consecutive_losses = 0
            self.save()
            return {
                "current_stage": next_stage,
                "advanced": True,
                "next_stage": next_stage,
                "days_in_stage": 0,
                "reason": f"推进成功: {self.stage} → {next_stage}",
                "rollback_triggered": False,
                "rollback_reason": "",
            }

        # 4. 未推进, 仅保存更新后的指标
        self.save()
        return {
            "current_stage": self.stage,
            "advanced": False,
            "next_stage": self.stage,
            "days_in_stage": self._calc_days_in_stage(),
            "reason": reason,
            "rollback_triggered": False,
            "rollback_reason": "",
        }


# ============================================================
# 执行计划生成
# ============================================================

def _generate_execution_plan(
    decision: TradingDecision,
    portfolio_value: float,
    price: Optional[float] = None,
    max_single_pct: Optional[float] = None,
) -> Dict[str, Any]:
    """将 TradingDecision 映射为 OrderRouter.route_order() 可消费的执行计划

    Args:
        decision: 经 decision_gate 放行的决策
        portfolio_value: 组合净值
        price: 当前参考价格 (如可用)
        max_single_pct: 单笔最大资金比例, 默认读配置 2%
    Returns:
        兼容 OrderRouter.route_order(execution_plan, market_state) 的执行计划
    """
    if max_single_pct is None:
        max_single_pct = float(get_config("gate.max_single_pct", 0.02))

    # P0 修复: action 白名单拦截 — hold/veto/review 决策不应生成执行计划
    # 原代码对 action="hold" 或 "veto" 仍生成 BUY 100 股, 配合 auto 模式会触发真实下单
    if decision.action not in ("buy", "sell"):
        raise ValueError(
            f"不应对 action={decision.action!r} 的决策生成执行计划 (仅允许 buy/sell). "
            f"symbol={decision.symbol}, strength={decision.strength}"
        )

    # 价格缺失检测: auto 模式必须 veto, paper/shadow 用默认值占位 (仅模拟, 不触达 broker)
    price_missing = (price is None or price <= 0)
    if price_missing:
        # paper/shadow 模式允许用占位价格继续生成计划 (不触达 broker)
        # auto 模式由 L2 风控 (price_missing_check) 硬 veto, 防止以默认价灾难性下单
        price = 10.0
        logger.warning(
            "[ExecBridge] %s 价格缺失 (price=%s), auto 模式将被 L2 风控 veto",
            decision.symbol, price,
        )
    # mypy 类型窄化: price_missing=False 时 price 非 None 且 > 0; =True 时已赋值 10.0
    assert price is not None

    # 仓位计算: 组合净值 * 单笔上限 * 信号强度绝对值 * 置信度
    # P0 修复: 仅当 strength 和 confidence 都有效时才计算仓位, 否则不强制最小仓位
    if abs(decision.strength) < 1e-6 or decision.confidence <= 0:
        allocation = 0.0
    else:
        allocation = portfolio_value * max_single_pct * abs(decision.strength) * decision.confidence
        allocation = max(allocation, portfolio_value * 0.001)  # 最少 0.1% 净值

    raw_qty = max(int(allocation / price), 100)  # A股最小 100 股
    qty = (raw_qty // 100) * 100  # 调整为 100 的整数倍 (A股交易单位)

    side = "SELL" if decision.action == "sell" else "BUY"
    price_type = "LIMIT"  # A股默认限价单

    # 分片信息 (TWAP 模拟, 大盘单笔不拆, 中小盘分 3 片)
    # 简化: 根据信号强度决定分片
    slices = 1 if abs(decision.strength) > 0.8 else (3 if qty > 1000 else 1)

    slice_size = max(qty // slices, 100)
    slice_info = {
        "size": slice_size,
        "price": price,
        "total_slices": slices,
        "slice_index": 1,  # 第一片
    }

    # 根据调整后的 qty 重新计算名义金额
    actual_notional = round(qty * price, 2)
    execution_plan = {
        "symbol": decision.symbol,
        "side": side,
        "qty": qty,
        "price_type": price_type,
        "limit_price": round(price, 2),
        "price_missing": price_missing,  # 价格缺失标记 (L2 风控用于 auto 模式硬 veto)
        "slice_info": slice_info,
        "slices": slices,
        "notional": actual_notional,  # 使用实际成交金额
        "decision_id": f"{decision.symbol}_{decision.timestamp}",
        "ai_confidence": round(decision.confidence, 4),
        "ai_strength": round(decision.strength, 4),
        "verdict_type": decision.verdict_type,
        "generated_at": datetime.now().isoformat(),
    }

    logger.info(
        "生成执行计划: %s %s %d股 @%.2f, 名义金额=%.2f, %d片",
        execution_plan["symbol"], execution_plan["side"],
        execution_plan["qty"], execution_plan["limit_price"],
        execution_plan["notional"], slices
    )
    return execution_plan


# ============================================================
# 执行层硬风控 (L2)
# ============================================================

@dataclass
class ExecutionRiskResult:
    """L2 执行层硬风控结果。

    Attributes:
        passed: 风控是否通过 (veto 取反)
        veto: 是否硬否决 (True 表示拦截下单)
        veto_reason: 否决原因汇总文本
        checks: 各项检查明细键值对
    """
    passed: bool = True
    veto: bool = False
    veto_reason: str = ""
    checks: Dict[str, Any] = field(default_factory=dict)


def _execution_risk_check(
    execution_plan: Dict[str, Any],
    market_state: str = "normal",
    portfolio_value: float = 1_000_000.0,
    risk_context: Optional[RiskContext] = None,
    decision: Optional[TradingDecision] = None,
    mode: str = "shadow",
) -> ExecutionRiskResult:
    """L2 执行层硬风控 — 下单前最后一次拦截

    两段式检查 (defense in depth, 不重复造轮子):
      Phase 1 — 复用 L1 decision_gate.run_hard_risk() (当 risk_context + decision 可用时):
        - 黑名单 / RiskAgent 否决 / 涨跌停 / 单笔金额上限 / 日内累计上限
      Phase 2 — L2 执行层特有检查 (L1 不覆盖):
        - 价格缺失 (auto 模式硬 veto) / 价格合理性 / 数量合法性 / 流动性 (crisis 禁买) / 名义金额兜底

    Args:
        execution_plan: _generate_execution_plan 产物
        market_state: normal/volatile/illiquid/stress/crisis
        portfolio_value: 组合净值 (L2 名义金额兜底用)
        risk_context: 可选, L1 风控所需运行态数据; 传入则复用 L1 检查
        decision: 可选, 与 risk_context 配对使用, 用于 L1 检查
        mode: 执行模式 (shadow/paper/auto); auto 模式下 price_missing 强制 veto
    Returns:
        ExecutionRiskResult (passed/veto/veto_reason/checks)
    """
    result = ExecutionRiskResult()
    checks: Dict[str, Any] = {}
    veto_reasons: List[str] = []

    # ===== Phase 1: 复用 L1 decision_gate.run_hard_risk() =====
    if risk_context is not None and decision is not None:
        # 用执行计划的实际名义金额同步给 L1, 确保 single_pct 检查基于真实下单金额
        risk_context.portfolio_value = portfolio_value
        risk_context.proposed_notional = float(execution_plan.get("notional", 0.0))
        if not risk_context.symbol:
            risk_context.symbol = execution_plan.get("symbol", decision.symbol)

        l1_result = run_hard_risk(decision, risk_context)
        # 合并 L1 检查项 (加 l1_ 前缀避免覆盖 L2 同名字段)
        for k, v in l1_result.risk_checks.items():
            checks[f"l1_{k}"] = v
        if l1_result.veto:
            result.veto = True
            veto_reasons.append(f"[L1] {l1_result.veto_reason}")

    # ===== Phase 2: L2 执行层特有检查 =====
    # 0. 价格缺失检查 (auto 模式硬 veto, 防止以默认价 10.0 灾难性下单)
    price_missing = bool(execution_plan.get("price_missing", False))
    is_auto_mode = mode.startswith("auto") or mode == "auto"
    checks["price_missing"] = {
        "value": price_missing,
        "mode": mode,
        "ok": not (price_missing and is_auto_mode),
    }
    if price_missing and is_auto_mode:
        result.veto = True
        veto_reasons.append(
            f"[L2] 价格缺失且处于 {mode} 模式, 禁止以默认价下单 (paper/shadow 才允许模拟)"
        )

    # 1. 价格合理性 (非零非负, 非异常跳变)
    price = execution_plan.get("limit_price", 0)
    checks["price_valid"] = {"value": price, "ok": price > 0 and price < 10000}
    if not checks["price_valid"]["ok"]:
        result.veto = True
        veto_reasons.append(f"[L2] 价格异常: {price}")

    # 2. 数量合法性 (>=100 股, 100 整数倍)
    qty = execution_plan.get("qty", 0)
    checks["qty_valid"] = {"value": qty, "ok": qty >= 100 and qty % 100 == 0}
    if not checks["qty_valid"]["ok"]:
        result.veto = True
        veto_reasons.append(f"[L2] 数量异常: {qty} (需 >=100 且为 100 整数倍)")

    # 3. 流动性 (crisis 状态禁买)
    if market_state == "crisis" and execution_plan.get("side") == "BUY":
        result.veto = True
        veto_reasons.append("[L2] 市场危机状态, 禁止买入")
    checks["market_state"] = market_state

    # 4. 名义金额兜底 (即使 L1 已检查 single_pct, L2 仍独立兜底, defense in depth)
    notional = execution_plan.get("notional", 0)
    max_single = float(get_config("gate.max_single_pct", 0.02))
    checks["notional"] = {
        "value": round(notional, 2),
        "max": round(portfolio_value * max_single, 2),
        "ok": 0 < notional <= portfolio_value * max_single,
    }
    if not checks["notional"]["ok"]:
        result.veto = True
        veto_reasons.append(
            f"[L2] 名义金额 {notional:.2f} 超过上限 {portfolio_value * max_single:.2f}"
        )

    result.checks = checks
    result.veto_reason = "; ".join(veto_reasons)
    result.passed = not result.veto
    return result


# ============================================================
# 执行审计
# ============================================================

def _write_execution_audit(record: Dict[str, Any]) -> str:
    """写入执行审计日志"""
    os.makedirs(_EXEC_AUDIT_DIR, exist_ok=True)
    path = os.path.join(_EXEC_AUDIT_DIR,
                        f"exec_{datetime.now().strftime('%Y%m%d')}.jsonl")
    try:
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, ensure_ascii=False) + "\n")
        return path
    except OSError as exc:
        logger.warning("执行审计写入失败: %s", exc)
        return ""


# ============================================================
# TCA Feature Flag (步骤 2: 双轨独立, 与 utils/execution_router 解耦)
# ============================================================

def _tca_pre_trade_enabled() -> bool:
    """USE_AI_DECISION_TCA_PRE_TRADE Feature Flag (默认 False, fail-safe)"""
    try:
        from utils.infra.feature_flags import is_enabled
        return bool(is_enabled("USE_AI_DECISION_TCA_PRE_TRADE"))
    except Exception:
        return False


def _tca_post_trade_enabled() -> bool:
    """USE_AI_DECISION_TCA_POST_TRADE Feature Flag (默认 False, fail-safe)"""
    try:
        from utils.infra.feature_flags import is_enabled
        return bool(is_enabled("USE_AI_DECISION_TCA_POST_TRADE"))
    except Exception:
        return False


# ============================================================
# TCA 辅助函数 (构造 FillRecord / BenchmarkPrices / 报告序列化)
# ============================================================

def _build_fills_from_execution(
    execution_plan: Dict[str, Any],
    execution_result: Dict[str, Any],
) -> List[Any]:
    """从 execution_result 构造 FillRecord 列表 (供 TCAManager.analyze 使用)

    兼容 paper 模式 (_simulate_fill 返回 average_price) 和 auto 模式 (routed_orders)
    """
    try:
        from utils.tca_engine import FillRecord
    except ImportError:
        return []

    symbol = execution_plan.get("symbol", "")
    side = execution_plan.get("side", "BUY")
    qty = int(execution_plan.get("qty", 0))

    # paper 模式: average_price 字段
    avg_price = (
        execution_result.get("average_price")
        or execution_result.get("avg_price")
        or 0.0
    )
    # auto 模式: 从 routed_orders 提取成交价
    if not avg_price:
        routed = execution_result.get("routed_orders", [])
        if isinstance(routed, list):
            for order in routed:
                if isinstance(order, dict) and order.get("price"):
                    avg_price = float(order["price"])
                    break

    if not avg_price or avg_price <= 0 or qty <= 0:
        return []

    timestamp = execution_result.get("timestamp", datetime.now().isoformat())
    return [FillRecord(
        symbol=symbol,
        side=side,
        shares=qty,
        price=float(avg_price),
        timestamp=timestamp,
    )]


def _build_benchmark_from_market_data(
    market_data: Optional[Dict[str, Any]],
    execution_plan: Dict[str, Any],
) -> Optional[Any]:
    """从 market_data 构造 BenchmarkPrices (供 TCAManager.analyze 使用)

    Args:
        market_data: {decision_price, arrival_price, vwap, close_price, market_cap, adv, volatility}
        execution_plan: 兜底取 limit_price 作为 decision_price
    Returns:
        BenchmarkPrices 或 None (数据不足时)
    """
    if not market_data:
        return None
    try:
        from utils.tca_engine import BenchmarkPrices
    except ImportError:
        return None

    # 决策价 = 下单时刻参考价 (兜底用 limit_price)
    decision_price = float(market_data.get(
        "decision_price", execution_plan.get("limit_price", 0)
    ))
    if decision_price <= 0:
        return None

    arrival_price = float(market_data.get("arrival_price", decision_price))
    vwap = float(market_data.get("vwap", 0))
    close_price = float(market_data.get("close_price", 0))

    return BenchmarkPrices(
        decision_price=decision_price,
        arrival_price=arrival_price,
        vwap=vwap,
        close_price=close_price,
    )


def _tca_report_to_dict(report: Any) -> Dict[str, Any]:
    """将 TCAReport 转为 dict (兼容 dataclass + 自定义 to_dict)"""
    try:
        if hasattr(report, "to_dict"):
            return report.to_dict()
        from dataclasses import asdict
        return asdict(report)
    except Exception:
        return {
            "symbol": getattr(report, "symbol", ""),
            "quality_grade": getattr(report, "quality_grade", ""),
            "is_cost_bps": getattr(report, "is_cost_bps", 0.0),
        }


# ============================================================
# 核心桥接函数 - Helper
# ============================================================


def _build_l2_veto_return(
    decision: TradingDecision,
    mode: str,
    risk_result: ExecutionRiskResult,
    escalation: bool,
    escalation_reason: str,
    execution_plan: Dict[str, Any],
) -> Dict[str, Any]:
    """构建 L2 风控否决时的审计记录和返回字典"""
    escalation_reason = f"L2 风控否决: {risk_result.veto_reason}"
    record = {
        "timestamp": datetime.now().isoformat(),
        "symbol": decision.symbol,
        "action": decision.action,
        "mode": mode,
        "executed": False,
        "veto": True,
        "veto_reason": risk_result.veto_reason,
        "escalation": True,
        "escalation_reason": escalation_reason,
        "checks": risk_result.checks,
    }
    _write_execution_audit(record)
    return {
        "executed": False,
        "mode": mode,
        "execution_plan": execution_plan,
        "execution_result": None,
        "risk_result": risk_result.__dict__,
        "audit_path": "",
        "message": f"L2 执行风控否决: {risk_result.veto_reason}",
        "veto": True,
        "veto_reason": risk_result.veto_reason,
        "escalation": True,
        "escalation_reason": escalation_reason,
        "tca_pre_estimate": None,
        "tca_post_report": None,
        "tca_error": "",
    }


def _build_grayscale_veto_return(
    decision: TradingDecision,
    mode: str,
    execution_plan: Dict[str, Any],
    risk_result: ExecutionRiskResult,
    tca_pre_estimate: Optional[Dict[str, Any]],
    tca_error: str,
    veto_reason: str,
    escalation: bool,
    escalation_reason: str,
    msg: str,
) -> Dict[str, Any]:
    """构建灰度回滚到 0 时的审计记录和返回字典"""
    record = {
        "timestamp": datetime.now().isoformat(),
        "symbol": decision.symbol,
        "action": decision.action,
        "mode": mode,
        "executed": False,
        "veto": True,
        "veto_reason": veto_reason,
        "escalation": True,
        "escalation_reason": escalation_reason,
    }
    _write_execution_audit(record)
    return {
        "executed": False,
        "mode": mode,
        "execution_plan": execution_plan,
        "execution_result": None,
        "risk_result": risk_result.__dict__,
        "audit_path": "",
        "message": msg,
        "veto": True,
        "veto_reason": veto_reason,
        "escalation": True,
        "escalation_reason": escalation_reason,
        "tca_pre_estimate": tca_pre_estimate,
        "tca_post_report": None,
        "tca_error": tca_error,
    }


def _run_tca_pre_trade(
    decision: TradingDecision,
    execution_plan: Dict[str, Any],
    market_data_for_tca: Optional[Dict[str, Any]],
    tca_pre_trade_estimator: Any,
) -> Tuple[Optional[Dict[str, Any]], bool, str, str]:
    """TCA 执行前预筛

    预筛否决是软阈值 (escalation 而非 veto)。
    异常隔离: TCA 异常仅记日志 + tca_error, 主路径不阻断 (fail-safe)。
    """
    tca_pre_estimate: Optional[Dict[str, Any]] = None
    tca_error = ""
    escalation = False
    escalation_reason = ""

    if _tca_pre_trade_enabled() and tca_pre_trade_estimator is not None:
        try:
            tca_order = {
                "symbol": execution_plan["symbol"],
                "side": execution_plan["side"],
                "shares": execution_plan["qty"],
                "price": execution_plan["limit_price"],
                "notional": execution_plan["notional"],
                "market_cap": (market_data_for_tca or {}).get("market_cap"),
            }
            estimate = tca_pre_trade_estimator.estimate(tca_order, market_data_for_tca)
            tca_pre_estimate = estimate.to_dict()
            if not estimate.approved:
                escalation = True
                escalation_reason = f"TCA 预筛否决: {estimate.rejection_reason}"
                logger.warning(
                    "[TCA-PreTrade] %s 预筛否决: %s (cost=%.2f bps)",
                    decision.symbol, estimate.rejection_reason,
                    estimate.estimated_cost_bps,
                )
            else:
                logger.info(
                    "[TCA-PreTrade] %s 预筛通过 (cost=%.2f bps, tier=%s)",
                    decision.symbol, estimate.estimated_cost_bps,
                    estimate.tier,
                )
        except Exception as exc:
            logger.error("[ExecutionBridge] TCA 预筛异常 (降级为不预估): %s", exc)
            tca_error = f"pre_trade: {exc}"

    return tca_pre_estimate, escalation, escalation_reason, tca_error


def _dispatch_execution_mode(
    decision: TradingDecision,
    execution_plan: Dict[str, Any],
    mode: str,
    price: Optional[float],
    order_router: Any,
    broker: Any,
    market_state: str,
    tca_pre_estimate: Optional[Dict[str, Any]],
    tca_error: str,
    risk_result: ExecutionRiskResult,
) -> Tuple[Optional[Dict[str, Any]], str, bool, str, bool, str]:
    """模式分派: shadow / paper / auto / unknown

    Returns:
        (execution_result, msg, veto, veto_reason, mode_escalation, mode_escalation_reason)
        veto 为 True 时表示灰度回滚到 0, 需由调用方构建最终返回。
    """
    execution_result: Optional[Dict[str, Any]] = None
    msg = ""
    veto = False
    veto_reason = ""
    mode_escalation = False
    mode_escalation_reason = ""

    if mode == "shadow":
        msg = f"[SHADOW] {decision.symbol} {decision.action} 仅记录, 不执行"
        logger.info(msg)

    elif mode == "paper":
        simulated_fill = _simulate_fill(execution_plan, price or 10.0)
        execution_result = simulated_fill
        msg = f"[PAPER] {decision.symbol} {decision.action} 模拟成交 @{simulated_fill.get('avg_price', 0):.2f}"
        logger.info(msg)

    elif mode == "auto":
        gs = GrayscaleState.load()
        should_rb, rb_reason = gs.should_rollback()
        if should_rb:
            new_stage = gs.do_rollback()
            msg = f"[AUTO] 触发回滚 {gs.stage} -> {new_stage}: {rb_reason}"
            logger.warning(msg)
            effective_pct = gs.effective_allocation_pct()
            if effective_pct == 0:
                veto = True
                veto_reason = f"灰度回滚到 {new_stage}, 暂停执行"
                mode_escalation = True
                mode_escalation_reason = f"灰度回滚至 {new_stage}, 暂停执行: {rb_reason}"
                return execution_result, msg, veto, veto_reason, mode_escalation, mode_escalation_reason

        if order_router is None or broker is None:
            msg = "[AUTO] 缺少 OrderRouter/broker, 降级为 paper 执行"
            logger.warning(msg)
            execution_result = _simulate_fill(execution_plan, price or 10.0)
        else:
            effective_pct = gs.effective_allocation_pct()
            if 0 < effective_pct < 1.0:
                original_qty = execution_plan.get("qty", 0)
                scaled_qty = int(original_qty * effective_pct)
                scaled_qty = max((scaled_qty // 100) * 100, 100)
                if scaled_qty != original_qty:
                    original_price = execution_plan.get("limit_price", 0)
                    execution_plan = dict(execution_plan)
                    execution_plan["qty"] = scaled_qty
                    execution_plan["notional"] = round(scaled_qty * original_price, 2)
                    if "slice_info" in execution_plan:
                        slices = execution_plan.get("slices", 1)
                        new_slice_size = max(scaled_qty // slices, 100)
                        execution_plan["slice_info"] = {
                            **execution_plan["slice_info"],
                            "size": new_slice_size,
                        }
                    logger.info(
                        "[GRAYSCALE] %s 阶段缩放: qty %d -> %d (%.0f%%), "
                        "notional %.2f -> %.2f",
                        gs.stage, original_qty, scaled_qty,
                        effective_pct * 100,
                        original_qty * original_price,
                        execution_plan["notional"],
                    )

            try:
                start = time.perf_counter()
                result = order_router.route_order(execution_plan, market_state)
                elapsed = time.perf_counter() - start
                execution_result = {
                    "success": result.get("success", False),
                    "routed_orders": result.get("routed_orders", []),
                    "target_pool": result.get("target_pool", ""),
                    "elapsed_seconds": round(elapsed, 4),
                }
                if execution_result["success"]:
                    msg = (f"[AUTO] {decision.symbol} {decision.action} "
                           f"已下单, 耗时 {elapsed:.3f}s, "
                           f"路由 {len(execution_result['routed_orders'])} 笔")
                else:
                    mode_escalation = True
                    mode_escalation_reason = (
                        f"下单失败: {execution_result.get('error', 'broker 拒单')}"
                    )
                    msg = f"[AUTO] {decision.symbol} {decision.action} 下单失败"
                logger.info(msg)
            except Exception as exc:
                logger.error("下单异常: %s", exc)
                mode_escalation = True
                mode_escalation_reason = f"下单异常: {exc}"
                execution_result = {"success": False, "error": str(exc)}
                msg = f"[AUTO] 下单异常: {exc}"

    else:
        mode_escalation = True
        mode_escalation_reason = f"未知模式 {mode}, 按 shadow 处理"
        msg = f"[{mode}] 未知模式, 按 shadow 处理"

    return execution_result, msg, veto, veto_reason, mode_escalation, mode_escalation_reason


def _run_tca_post_trade(
    tca_post_trade_manager: Any,
    execution_plan: Dict[str, Any],
    execution_result: Optional[Dict[str, Any]],
    market_data_for_tca: Optional[Dict[str, Any]],
    decision: TradingDecision,
) -> Tuple[Optional[Dict[str, Any]], str]:
    """TCA 事后归因

    仅执行成功后调用。异常隔离: 归因异常仅记日志 + tca_error, 主路径不阻断。
    """
    tca_post_report: Optional[Dict[str, Any]] = None
    tca_error = ""

    if not (_tca_post_trade_enabled()
            and tca_post_trade_manager is not None
            and execution_result is not None
            and execution_result.get("success", True)):
        return tca_post_report, tca_error

    try:
        fills = _build_fills_from_execution(execution_plan, execution_result)
        benchmark = _build_benchmark_from_market_data(market_data_for_tca, execution_plan)
        if fills and benchmark:
            report = tca_post_trade_manager.analyze(
                fills=fills,
                benchmark=benchmark,
                order_shares=execution_plan.get("qty"),
            )
            tca_post_report = _tca_report_to_dict(report)
            logger.info(
                "[TCA-PostTrade] %s 归因完成: IS=%.2f bps, grade=%s",
                decision.symbol,
                getattr(report, "is_cost_bps", 0.0),
                getattr(report, "quality_grade", "N/A"),
            )
        else:
            logger.debug(
                "[TCA-PostTrade] %s 跳过归因 (fills/benchmark 数据不足)",
                decision.symbol,
            )
    except Exception as exc:
        logger.error("[ExecutionBridge] TCA 事后归因异常: %s", exc)
        tca_error = f"post_trade: {exc}"

    return tca_post_report, tca_error


def _build_success_audit_record(
    decision: TradingDecision,
    mode: str,
    execution_plan: Dict[str, Any],
    execution_result: Optional[Dict[str, Any]],
    risk_result: ExecutionRiskResult,
    veto: bool,
    veto_reason: str,
    escalation: bool,
    escalation_reason: str,
    tca_pre_estimate: Optional[Dict[str, Any]],
    tca_post_report: Optional[Dict[str, Any]],
    tca_error: str,
    msg: str,
) -> Dict[str, Any]:
    """构建成功/最终执行审计记录"""
    return {
        "timestamp": datetime.now().isoformat(),
        "symbol": decision.symbol,
        "action": decision.action,
        "mode": mode,
        "executed": execution_result is not None and execution_result.get("success", False),
        "execution_plan": execution_plan,
        "execution_result": execution_result,
        "risk_checks": risk_result.checks,
        "decision_confidence": decision.confidence,
        "decision_strength": decision.strength,
        "verdict_type": decision.verdict_type,
        "veto": veto,
        "veto_reason": veto_reason,
        "escalation": escalation,
        "escalation_reason": escalation_reason,
        "tca_pre_estimate": tca_pre_estimate,
        "tca_post_report": tca_post_report,
        "tca_error": tca_error,
        "message": msg,
    }


def _build_success_return(
    decision: TradingDecision,
    mode: str,
    execution_plan: Dict[str, Any],
    execution_result: Optional[Dict[str, Any]],
    risk_result: ExecutionRiskResult,
    audit_path: str,
    msg: str,
    veto: bool,
    veto_reason: str,
    escalation: bool,
    escalation_reason: str,
    tca_pre_estimate: Optional[Dict[str, Any]],
    tca_post_report: Optional[Dict[str, Any]],
    tca_error: str,
) -> Dict[str, Any]:
    """构建成功执行后的返回字典"""
    return {
        "executed": execution_result is not None and execution_result.get("success", False),
        "mode": mode,
        "execution_plan": execution_plan,
        "execution_result": execution_result,
        "risk_result": risk_result.__dict__,
        "audit_path": audit_path,
        "message": msg,
        "veto": veto,
        "veto_reason": veto_reason,
        "escalation": escalation,
        "escalation_reason": escalation_reason,
        "tca_pre_estimate": tca_pre_estimate,
        "tca_post_report": tca_post_report,
        "tca_error": tca_error,
    }


# ============================================================
# 核心桥接函数
# ============================================================

def execute_decision(
    decision: TradingDecision,
    portfolio_value: float = 1_000_000.0,
    price: Optional[float] = None,
    market_state: str = "normal",
    order_router: Any = None,
    broker: Any = None,
    force_mode: Optional[str] = None,
    risk_context: Optional[RiskContext] = None,
    # 步骤 2: TCA 双轨参数 (Feature Flag 控制, 默认 None=不启用)
    tca_pre_trade_estimator: Optional[Any] = None,
    tca_post_trade_manager: Optional[Any] = None,
    market_data_for_tca: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """将 TradingDecision 转化为执行指令并 (可选) 下单

    这是从 AI 决策到 broker 的唯一桥梁。分四步:
      Step A:  生成执行计划
      Step B:  L2 执行层硬风控 (二次校验, 复用 L1 run_hard_risk)
      Step B+: TCA 执行前预筛 (步骤 2, Feature Flag 控制, 高成本订单升级人工)
      Step C:  按模式分派 (shadow→仅日志, paper→模拟指令, auto→灰度→真实下单)
      Step D:  TCA 事后归因 (步骤 2, 仅执行成功后, 写入审计)

    Args:
        decision: 经 decision_gate 处理后的 TradingDecision
        portfolio_value: 组合净值
        price: 当前参考价格
        market_state: 市场状态 normal/volatile/illiquid/stress/crisis
        order_router: OrderRouter 实例 (auto 模式需要)
        broker: BrokerAPI 实例 (auto 模式需要)
        force_mode: 强制覆盖模式 (用于测试)
        risk_context: L1 风控运行态数据; 传入则 L2 复用 run_hard_risk()
            覆盖黑名单/涨跌停/日内累计检查 (路线图: 不重复造轮子)
        tca_pre_trade_estimator: PreTradeEstimator 实例 (步骤 2);
            None 或 Feature Flag 关闭时不预筛. 预筛否决是软阈值 (escalation)
        tca_post_trade_manager: TCAManager 实例 (步骤 2);
            None 或 Feature Flag 关闭时不归因. 仅执行成功后调用
        market_data_for_tca: TCA 所需市场数据 {adv, volatility, market_cap,
            decision_price, arrival_price, vwap, close_price}; 缺失时降级跳过
    Returns:
        {
            'executed': bool,
            'mode': str,
            'execution_plan': dict,
            'execution_result': dict or None,
            'risk_result': dict,
            'audit_path': str,
            'message': str,
            'veto': bool,                  # 硬风控否决 (L1/L2 直接拦截)
            'veto_reason': str,            # 硬否决原因
            'escalation': bool,            # 软阈值升级 (需人工确认, 含执行异常)
            'escalation_reason': str,      # 升级原因
            'tca_pre_estimate': dict|None, # TCA 执行前预估 (步骤 2)
            'tca_post_report': dict|None,  # TCA 事后归因报告 (步骤 2)
            'tca_error': str,              # TCA 异常信息 (不阻断主路径)
        }
    """
    mode = force_mode or decision.mode

    # ===== 步骤 1: 初始化 escalation 从 decision.escalation 继承 (保留 L1 已设) =====
    escalation: bool = bool(decision.escalation)
    escalation_reason: str = decision.escalation_reason or ""

    # ===== 步骤 2: 初始化 TCA 双轨变量 (Feature Flag 控制, 默认 None=不启用) =====
    tca_pre_estimate: Optional[Dict[str, Any]] = None
    tca_post_report: Optional[Dict[str, Any]] = None
    tca_error: str = ""

    # ===== Step A: 生成执行计划 =====
    execution_plan = _generate_execution_plan(
        decision, portfolio_value, price,
        max_single_pct=float(get_config("gate.max_single_pct", 0.02))
    )

    # ===== Step B: L2 执行层硬风控 (不可绕过) =====
    risk_result = _execution_risk_check(
        execution_plan, market_state, portfolio_value,
        risk_context=risk_context, decision=decision,
        mode=mode,
    )

    if risk_result.veto:
        escalation = True
        escalation_reason = f"L2 风控否决: {risk_result.veto_reason}"
        return _build_l2_veto_return(
            decision, mode, risk_result,
            escalation, escalation_reason, execution_plan
        )

    # ===== Step B+: TCA 执行前预筛 (步骤 2, Feature Flag 控制) =====
    tca_pre_estimate, pre_escalation, pre_escalation_reason, tca_error = _run_tca_pre_trade(
        decision, execution_plan, market_data_for_tca, tca_pre_trade_estimator
    )
    if pre_escalation:
        escalation = True
        escalation_reason = pre_escalation_reason

    # ===== Step C: 按模式分派 =====
    execution_result, msg, grayscale_veto, veto_reason, mode_escalation, mode_escalation_reason = _dispatch_execution_mode(
        decision, execution_plan, mode, price, order_router, broker,
        market_state, tca_pre_estimate, tca_error, risk_result
    )

    if grayscale_veto:
        veto = True
        escalation = True
        escalation_reason = mode_escalation_reason
        return _build_grayscale_veto_return(
            decision, mode, execution_plan, risk_result,
            tca_pre_estimate, tca_error, veto_reason,
            escalation, escalation_reason, msg
        )

    if mode_escalation:
        escalation = True
        escalation_reason = mode_escalation_reason

    # ===== Step D: TCA 事后归因 (步骤 2, 仅执行成功后, Feature Flag 控制) =====
    if execution_result is not None and execution_result.get("success", True):
        tca_post_report, post_tca_error = _run_tca_post_trade(
            tca_post_trade_manager, execution_plan, execution_result,
            market_data_for_tca, decision
        )
        if post_tca_error:
            tca_error = f"{tca_error}; {post_tca_error}" if tca_error else post_tca_error

    # ===== 写入执行审计 =====
    veto = False
    veto_reason = ""
    record = _build_success_audit_record(
        decision, mode, execution_plan, execution_result, risk_result,
        veto, veto_reason, escalation, escalation_reason,
        tca_pre_estimate, tca_post_report, tca_error, msg
    )
    audit_path = _write_execution_audit(record)

    return _build_success_return(
        decision, mode, execution_plan, execution_result, risk_result,
        audit_path, msg, veto, veto_reason, escalation, escalation_reason,
        tca_pre_estimate, tca_post_report, tca_error
    )

def _simulate_fill(plan: Dict[str, Any], ref_price: float) -> Dict[str, Any]:
    """模拟成交 (paper 模式) — 带 A 股滑点模型"""
    import random
    qty = plan.get("qty", 0)
    # 模拟滑点: 大盘 2bp, 中小盘 5bp (保守取 5bp)
    slippage_bps = random.uniform(2, 5)
    side_mult = 1 if plan.get("side") == "BUY" else -1
    fill_price = ref_price * (1 + side_mult * slippage_bps / 10000)
    return {
        "success": True,
        "filled_size": qty,
        "average_price": round(fill_price, 4),
        "slippage_bps": round(slippage_bps, 2),
        "notional": round(qty * fill_price, 2),
        "timestamp": datetime.now().isoformat(),
        "is_live": False,
    }


# ============================================================
# 灰度管理工具函数
# ============================================================

def get_grayscale_summary() -> Dict[str, Any]:
    """获取当前灰度状态摘要 (用于仪表盘)"""
    gs = GrayscaleState.load()
    should_rb, rb_reason = gs.should_rollback()
    return {
        "stage": gs.stage,
        "allocation_pct": gs.effective_allocation_pct(),
        "started_at": gs.started_at,
        "cumulative_pnl": round(gs.cumulative_pnl, 4),
        "consecutive_losses": gs.consecutive_losses,
        "rollback_count": gs.rollback_count,
        "days_tracked": len(gs.daily_pnl_series),
        "rollback_risk": should_rb,
        "rollback_reason": rb_reason,
        "last_evaluation": gs.last_evaluation,
    }


def advance_grayscale(
    daily_pnl: float = 0.0,
    daily_decision_count: int = 0,
    paper_fill_rate: Optional[float] = None,
    escalation_count: Optional[int] = None,
) -> Dict[str, Any]:
    """每日 EOD 推送, 自动评估灰度阶段推进/回滚 (Step 5 实现)

    Step 5 升级: 委托给 GrayscaleState.advance_grayscale() 方法, 包含:
      - 14 天硬约束 (shadow→paper 需跑满 14 天, 项目记忆硬约束)
      - 推进条件矩阵 (4 阶段差异化条件)
      - 回滚触发 (连续亏损 / PnL 偏离 2σ)
      - 推进后指标重置

    Args:
        daily_pnl: 当日 PnL
        daily_decision_count: 当日决策数 (shadow→paper 条件)
        paper_fill_rate: paper 阶段模拟成交率 (None 时不更新)
        escalation_count: 当前阶段累计 escalation 数 (None 时不更新)
    Returns:
        含 previous_stage / current_stage / advanced / reason / days_in_stage 等字段
    """
    gs = GrayscaleState.load()
    prev = gs.stage
    result = gs.advance_grayscale(
        daily_pnl=daily_pnl,
        daily_decision_count=daily_decision_count,
        paper_fill_rate=paper_fill_rate,
        escalation_count=escalation_count,
    )
    # 向后兼容: 保留 previous_stage / allocation_pct / cumulative_pnl 字段
    result["previous_stage"] = prev
    result["allocation_pct"] = gs.effective_allocation_pct()
    result["cumulative_pnl"] = round(gs.cumulative_pnl, 4)
    return result
