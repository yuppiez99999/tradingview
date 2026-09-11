"""
ai_decision.grayscale_state — 灰度发布状态机
=============================================

从 execution_bridge.py 拆分 (v8.6 重构, 接口完全不变).

管理 shadow → paper → auto_10 → auto_50 → auto_100 五阶段灰度发布,
含 14 天硬约束 / 推进条件矩阵 / 回滚触发 / 持久化.
"""

from __future__ import annotations

import json
import logging
import math
import os
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, ClassVar

from ai_decision.config import get_config
from utils.datetime_utils import now_bj

# S-2 (2026-09-11, Issue #13): 阶段初期绝对回撤阈值走单一事实源
from utils.risk_thresholds import (
    get_portfolio_protection_config as _get_portfolio_protection_config,
)

logger = logging.getLogger("ai_decision.grayscale_state")

_GRAYSCALE_STATE_FILE = os.path.join("reports", "ai_decision", "grayscale_state.json")


# ============================================================
# 灰度状态管理
# ============================================================


@dataclass
class GrayscaleState:
    """灰度发布状态机"""

    stage: str = "shadow"  # shadow / paper / auto_10 / auto_50 / auto_100
    started_at: str = ""  # 当前阶段开始时间 ISO
    cumulative_pnl: float = 0.0  # 累计 PnL
    daily_pnl_series: list[float] = field(default_factory=list)  # 最近 30 日 PnL
    consecutive_losses: int = 0  # 连续亏损天数
    rollback_count: int = 0  # 回滚次数
    last_evaluation: str = ""  # 上次评估时间
    # Step 5 新增: 推进条件评估所需指标
    daily_decision_count: list[int] = field(
        default_factory=list
    )  # 每日决策数 (shadow→paper 条件)
    paper_fill_rate: float = 0.0  # paper 阶段模拟成交率 (paper→auto_10 条件)
    escalation_count: int = 0  # 当前阶段 escalation 计数 (paper→auto_10 条件)

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
                with open(_GRAYSCALE_STATE_FILE, encoding="utf-8") as fh:
                    data = json.load(fh)
                    return cls(**{k: data.get(k, v) for k, v in cls().__dict__.items()})
        except (json.JSONDecodeError, OSError, TypeError, ValueError) as e:
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
        gs.started_at = now_bj().isoformat()
        return gs

    def save(self) -> None:
        """将灰度状态持久化到 JSON 文件。

        自动创建缺失目录，以 UTF-8 编码写入所有字段。
        """
        os.makedirs(os.path.dirname(_GRAYSCALE_STATE_FILE), exist_ok=True)
        with open(_GRAYSCALE_STATE_FILE, "w", encoding="utf-8") as fh:
            json.dump(
                {k: getattr(self, k) for k in self.__dict__},
                fh,
                ensure_ascii=False,
                indent=2,
            )

    def should_rollback(self) -> tuple[bool, str]:
        """检查是否应触发回滚 (在 auto 模式下每笔交易前调用)"""
        if self.stage in ("shadow", "paper"):
            return False, ""

        reasons: list[str] = []

        # 1. 连续亏损 (连续 5 笔亏损回滚一档, 连续 8 笔回滚到 paper)
        if self.consecutive_losses >= 8:
            reasons.append(f"连续亏损 {self.consecutive_losses} 天 (>=8), 严重异常")

        # 2. PnL 偏离 > 2σ (基于近 30 日序列)
        # S-2 修复 (2026-09-11, Issue #13): 原实现仅在 len(daily_pnl_series) >= 5 时
        # 启用 2σ 分支, 叠加回滚后 consecutive_losses 归零 → 刚进入 auto_10 的前 5 个
        # 交易日内**没有任何自动回滚保护**。现补一条与样本数无关的绝对阈值分支:
        # 累计 PnL 跌破 initial_drawdown_stop_pct → 直接回滚一档。
        protection = _get_portfolio_protection_config()
        min_samples = int(protection.get("initial_min_samples", 5))
        abs_stop = float(protection.get("initial_drawdown_stop_pct", 0.05))

        if self.cumulative_pnl <= -abs_stop:
            reasons.append(
                f"累计 PnL {self.cumulative_pnl:.4f} 跌破绝对阈值 "
                f"-{abs_stop:.2%} (阶段初期保护, 与原 2σ 分支互补)"
            )

        if len(self.daily_pnl_series) >= min_samples:
            mu = sum(self.daily_pnl_series) / len(self.daily_pnl_series)
            var = sum((x - mu) ** 2 for x in self.daily_pnl_series) / len(
                self.daily_pnl_series
            )
            sigma = math.sqrt(var) if var > 0 else 0.001
            latest = self.daily_pnl_series[-1]
            if latest < mu - 2 * sigma and sigma > 0:
                reasons.append(
                    f"PnL {latest:.4f} 偏离均值 {mu:.4f} 超过 2σ ({sigma:.4f})"
                )

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

        self.last_evaluation = now_bj().isoformat()
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
        self.started_at = now_bj().isoformat()
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
    _ADVANCE_MAP: ClassVar[dict[str, tuple[str, int]]] = {
        # 当前阶段 → (下一阶段, 最小停留天数)
        "shadow": ("paper", 14),  # shadow → paper: 跑满 14 天
        "paper": ("auto_10", 3),  # paper → auto_10: 跑满 3 天
        "auto_10": ("auto_50", 3),  # auto_10 → auto_50: 跑满 3 天
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
            delta = now_bj() - start
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

    def _check_advance_conditions(self) -> tuple[bool, str]:
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
        paper_fill_rate: float | None = None,
        escalation_count: int | None = None,
    ) -> dict[str, Any]:
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

        self.last_evaluation = now_bj().isoformat()

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
                self.stage,
                next_stage,
                self._calc_days_in_stage(),
                self.cumulative_pnl,
            )
            self.stage = next_stage
            self.started_at = now_bj().isoformat()
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
# 灰度管理工具函数
# ============================================================


def get_grayscale_summary() -> dict[str, Any]:
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
    paper_fill_rate: float | None = None,
    escalation_count: int | None = None,
) -> dict[str, Any]:
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
