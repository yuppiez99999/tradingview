"""
参数调整治理器 (Parameter Adjustment Governor)
=============================================

T18: 月度参数调整机制 — 防止 chasing (过度调整).

背景问题:
    量化系统中参数调整最容易陷入 "chasing" 陷阱:
    - 看到本月回测不好 → 立刻调参 → 用新参数追下月行情 → 又不好 → 再调
    - 这种短期反复调整本质是过拟合最近噪声, 会摧毁 OOS 泛化能力

治理原则:
    1. **频率限制**: 同一参数每月最多调整 1 次 (可配置)
    2. **冷却期**: 调整后 30 天内禁止再调 (强制冷却)
    3. **理由验证**: 调整必须有明确理由 (IC 衰减/OOS gap/市场环境变化)
    4. **审计追踪**: 所有调整记录持久化, 便于事后复盘
    5. **回滚机制**: 调整后若 OOS 表现恶化, 支持回滚到上一个版本

使用方式:
    governor = ParameterAdjustmentGovernor(min_interval_days=30)
    req = AdjustmentRequest(param="max_weight", old=0.10, new=0.08,
                             reason="OOS gap>5%", operator="risk_manager")
    result = governor.request(req)
    if result.approved:
        governor.commit(result)
    else:
        logger.info(f"调整被拒: {result.rejection_reason}")
"""

from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta
from enum import StrEnum
from pathlib import Path
from typing import Any

logger = logging.getLogger("param_governor")


class AdjustmentReason(StrEnum):
    """参数调整的合法理由 (白名单)"""

    IC_DECAY = "ic_decay"  # IC 衰减 (T17 OOS gap 触发)
    OOS_GAP_CRITICAL = "oos_gap_critical"  # OOS gap > critical
    MARKET_REGIME_CHANGE = "regime_change"  # 市场环境变化
    RISK_BREACH = "risk_breach"  # 风控触发
    POSTMORTEM = "postmortem"  # 事故复盘
    ANNUAL_REVIEW = "annual_review"  # 年度评审
    MANUAL_OVERRIDE = "manual_override"  # 人工紧急覆盖 (需额外审批)


class RejectionCode(StrEnum):
    """拒绝码"""

    COOLDOWN_ACTIVE = "cooldown_active"  # 冷却期内
    MONTHLY_LIMIT_EXCEEDED = "monthly_limit"  # 月度次数超限
    INVALID_REASON = "invalid_reason"  # 理由不在白名单
    MISSING_EVIDENCE = "missing_evidence"  # 缺少证据
    DUPLICATE_REQUEST = "duplicate"  # 重复请求 (无变化)


@dataclass
class AdjustmentRequest:
    """参数调整请求"""

    param: str  # 参数名 (如 "max_weight")
    old_value: Any  # 旧值
    new_value: Any  # 新值
    reason: str  # 调整理由 (AdjustmentReason 值)
    operator: str  # 操作人
    evidence: dict[str, Any] = field(default_factory=dict)  # 证据 (IC值/gap值等)
    requested_at: datetime | None = None  # 请求时间 (None=now)
    notes: str = ""  # 备注

    def __post_init__(self):
        if self.requested_at is None:
            self.requested_at = datetime.now()

    @property
    def value_changed(self) -> bool:
        """新值与旧值是否真的不同"""
        return self.old_value != self.new_value


@dataclass
class AdjustmentResult:
    """参数调整审批结果"""

    approved: bool
    request: AdjustmentRequest
    rejection_code: RejectionCode | None = None
    rejection_reason: str = ""
    decided_at: datetime = field(default_factory=datetime.now)
    effective_after: datetime | None = None  # 生效时间 (冷却期结束时)


@dataclass
class AdjustmentRecord:
    """已提交的调整记录 (持久化)"""

    param: str
    old_value: Any
    new_value: Any
    reason: str
    operator: str
    evidence: dict[str, Any]
    committed_at: datetime
    record_id: str


class ParameterAdjustmentGovernor:
    """参数调整治理器

    防止 chasing: 限制参数调整频率, 强制冷却期, 要求白名单理由.

    Config:
        min_interval_days: 同一参数最小调整间隔 (默认 30)
        max_per_month: 每月最大调整次数 (默认 1)
        require_evidence: 是否强制要求证据 (默认 True)
    """

    def __init__(
        self,
        min_interval_days: int = 30,
        max_per_month: int = 1,
        require_evidence: bool = True,
        history_file: Path | None = None,
    ) -> None:
        self.min_interval_days = min_interval_days
        self.max_per_month = max_per_month
        self.require_evidence = require_evidence
        self.history_file = history_file

        self._history: list[AdjustmentRecord] = []
        self._load_history()

    # ============================================================
    # 核心审批逻辑
    # ============================================================

    def request(self, req: AdjustmentRequest) -> AdjustmentResult:
        """提交调整请求, 返回审批结果.

        审批流程:
            1. 检查值是否变化 (无变化直接拒绝)
            2. 检查理由是否在白名单
            3. 检查是否需要证据
            4. 检查冷却期 (距上次调整 < min_interval_days)
            5. 检查月度次数 (本月已调整 >= max_per_month)

        Returns:
            AdjustmentResult (approved=True 才可 commit)
        """
        # 1. 值未变化
        if not req.value_changed:
            return AdjustmentResult(
                approved=False,
                request=req,
                rejection_code=RejectionCode.DUPLICATE_REQUEST,
                rejection_reason=f"参数 {req.param} 新旧值相同 ({req.old_value}), 无需调整",
            )

        # 2. 理由白名单
        valid_reasons = {r.value for r in AdjustmentReason}
        if req.reason not in valid_reasons:
            return AdjustmentResult(
                approved=False,
                request=req,
                rejection_code=RejectionCode.INVALID_REASON,
                rejection_reason=(f"理由 '{req.reason}' 不在白名单 {sorted(valid_reasons)}"),
            )

        # 3. 证据要求 (除 MANUAL_OVERRIDE 外都需证据)
        if self.require_evidence and req.reason != AdjustmentReason.MANUAL_OVERRIDE.value:
            if not req.evidence:
                return AdjustmentResult(
                    approved=False,
                    request=req,
                    rejection_code=RejectionCode.MISSING_EVIDENCE,
                    rejection_reason=f"理由 '{req.reason}' 需要证据 (evidence 不能为空)",
                )

        # 4. 冷却期
        last = self._last_adjustment(req.param)
        if last is not None:
            elapsed = (req.requested_at - last.committed_at).days
            if elapsed < self.min_interval_days:
                cooldown_remaining = self.min_interval_days - elapsed
                return AdjustmentResult(
                    approved=False,
                    request=req,
                    rejection_code=RejectionCode.COOLDOWN_ACTIVE,
                    rejection_reason=(
                        f"参数 {req.param} 在冷却期内 (距上次调整 {elapsed} 天, "
                        f"需 {self.min_interval_days} 天, 还需 {cooldown_remaining} 天)"
                    ),
                    effective_after=last.committed_at + timedelta(days=self.min_interval_days),
                )

        # 5. 月度次数
        month_count = self._count_adjustments_this_month(req.param, req.requested_at)
        if month_count >= self.max_per_month:
            return AdjustmentResult(
                approved=False,
                request=req,
                rejection_code=RejectionCode.MONTHLY_LIMIT_EXCEEDED,
                rejection_reason=(f"参数 {req.param} 本月已调整 {month_count} 次, 超过月度上限 {self.max_per_month}"),
            )

        # 全部通过
        return AdjustmentResult(
            approved=True,
            request=req,
            effective_after=req.requested_at,
        )

    def commit(self, result: AdjustmentResult) -> AdjustmentRecord:
        """提交已批准的调整 (写入历史).

        Args:
            result: 已批准的 AdjustmentResult

        Returns:
            AdjustmentRecord 持久化记录

        Raises:
            ValueError: 如果 result 未批准
        """
        if not result.approved:
            raise ValueError(f"不能提交未批准的调整: {result.rejection_code}")

        record = AdjustmentRecord(
            param=result.request.param,
            old_value=result.request.old_value,
            new_value=result.request.new_value,
            reason=result.request.reason,
            operator=result.request.operator,
            evidence=result.request.evidence,
            committed_at=datetime.now(),
            record_id=f"{result.request.param}_{datetime.now().strftime('%Y%m%d%H%M%S')}",
        )
        self._history.append(record)
        self._save_history()
        logger.info(
            f"T18: 参数调整已提交: {record.param} {record.old_value}→{record.new_value}, "
            f"reason={record.reason}, operator={record.operator}"
        )
        return record

    # ============================================================
    # 回滚
    # ============================================================

    def rollback(self, param: str, operator: str, reason: str = "rollback") -> AdjustmentRecord | None:
        """回滚到上一个参数值.

        Args:
            param: 要回滚的参数名
            operator: 操作人
            reason: 回滚理由

        Returns:
            回滚记录, 若无历史可回滚返回 None
        """
        # 找到该参数最近一次调整
        last_idx = None
        for i in range(len(self._history) - 1, -1, -1):
            if self._history[i].param == param:
                last_idx = i
                break

        if last_idx is None:
            logger.warning(f"T18: 回滚失败, 参数 {param} 无历史记录")
            return None

        last = self._history[last_idx]
        # 回滚 = 用 old_value 作为新值
        rollback_req = AdjustmentRequest(
            param=param,
            old_value=last.new_value,
            new_value=last.old_value,
            reason=AdjustmentReason.MANUAL_OVERRIDE.value,
            operator=operator,
            evidence={"rollback_from": last.record_id, "reason": reason},
        )
        # 回滚绕过冷却期 (用 MANUAL_OVERRIDE)
        result = AdjustmentResult(
            approved=True,
            request=rollback_req,
            effective_after=datetime.now(),
        )
        return self.commit(result)

    # ============================================================
    # 查询
    # ============================================================

    def get_history(self, param: str | None = None) -> list[AdjustmentRecord]:
        """获取调整历史 (可按参数过滤)"""
        if param is None:
            return list(self._history)
        return [r for r in self._history if r.param == param]

    def get_cooldown_status(self, param: str, now: datetime | None = None) -> dict[str, Any]:
        """查询参数的冷却状态"""
        now = now or datetime.now()
        last = self._last_adjustment(param)
        if last is None:
            return {
                "in_cooldown": False,
                "last_adjusted": None,
                "days_since_last": None,
                "days_until_available": 0,
            }
        elapsed = (now - last.committed_at).days
        remaining = max(0, self.min_interval_days - elapsed)
        return {
            "in_cooldown": remaining > 0,
            "last_adjusted": last.committed_at.isoformat(),
            "days_since_last": elapsed,
            "days_until_available": remaining,
            "last_value": last.old_value,
            "current_value": last.new_value,
        }

    def _last_adjustment(self, param: str) -> AdjustmentRecord | None:
        """获取某参数最近一次调整记录"""
        for i in range(len(self._history) - 1, -1, -1):
            if self._history[i].param == param:
                return self._history[i]
        return None

    def _count_adjustments_this_month(self, param: str, now: datetime) -> int:
        """统计本月该参数的调整次数"""
        count = 0
        for r in self._history:
            if r.param != param:
                continue
            if r.committed_at.year == now.year and r.committed_at.month == now.month:
                count += 1
        return count

    # ============================================================
    # 持久化
    # ============================================================

    def _load_history(self) -> None:
        if self.history_file is None or not self.history_file.exists():
            return
        try:
            with open(self.history_file, encoding="utf-8") as f:
                data = json.load(f)
            for item in data:
                item["committed_at"] = datetime.fromisoformat(item["committed_at"])
                self._history.append(AdjustmentRecord(**item))
            logger.info(f"T18: 加载 {len(self._history)} 条调整历史")
        except (json.JSONDecodeError, KeyError, TypeError) as e:
            logger.error(f"T18: 加载历史失败: {e}", exc_info=True)

    def _save_history(self) -> None:
        if self.history_file is None:
            return
        try:
            self.history_file.parent.mkdir(parents=True, exist_ok=True)
            data = []
            for r in self._history:
                d = asdict(r)
                d["committed_at"] = r.committed_at.isoformat()
                data.append(d)
            with open(self.history_file, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
        except OSError as e:
            logger.error(f"T18: 保存历史失败: {e}", exc_info=True)
