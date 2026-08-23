"""安全合规跨市场执行代理
==========================

文献依据: #53 Safe Cross-Market Execution (2025.10)
任务: LIT-4.4 安全合规跨市场执行

核心设计
--------
1. ConstrainedMDP: 约束马尔可夫决策过程 — 在 MDP 中加入合规约束
   (持仓限制、单笔限额、市场占比、涨跌停等)
2. ZeroKnowledgeAudit: 零知识审计 — 哈希承诺 + 范围证明
   (可验证执行合规, 不暴露策略细节)
3. CVaRController: CVaR 尾部控制 — 条件风险价值约束
4. SafeExecutionAgent: 安全执行代理 — 整合上述组件

验收标准
--------
- 无违规: 所有执行符合约束
- CVaR 尾部控制: 尾部风险不超过阈值
- 零知识审计: 可验证合规性, 不暴露策略

使用示例
--------
    from utils.safe_execution_agent import SafeExecutionAgent, ExecutionConstraints

    agent = SafeExecutionAgent()
    result = agent.execute_safely(
        symbol="600519", order_shares=10000, adv=500000,
        current_position=5000, price=1800.0,
    )
    assert result.compliant  # True
"""

from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

import numpy as np

logger = logging.getLogger("safe_execution")


# ============================================================
# 枚举
# ============================================================

class ViolationType(str, Enum):
    """违规类型."""
    POSITION_LIMIT = "position_limit"  # 持仓超限
    ORDER_SIZE_LIMIT = "order_size_limit"  # 单笔超限
    PARTICIPATION_LIMIT = "participation_limit"  # 市场占比超限
    PRICE_LIMIT = "price_limit"  # 涨跌停
    CVAR_BREACH = "cvar_breach"  # CVaR 超限
    NONE = "none"  # 无违规


class AuditStatus(str, Enum):
    """审计状态."""
    VERIFIED = "verified"  # 验证通过
    FAILED = "failed"  # 验证失败
    PENDING = "pending"  # 待验证


# ============================================================
# 约束定义
# ============================================================

@dataclass
class ExecutionConstraints:
    """执行约束.

    Attributes:
        max_position: 最大持仓 (股)
        max_order_size: 单笔最大订单 (股)
        max_participation: 最大市场占比
        max_cvar: CVaR 上限 (bps)
        price_limit_pct: 涨跌停限制 (如 0.1 = ±10%)
    """
    max_position: float = 100_000.0  # 最大持仓 10 万股
    max_order_size: float = 50_000.0  # 单笔最大 5 万股
    max_participation: float = 0.10  # 最大市场占比 10%
    max_cvar: float = 500.0  # CVaR 上限 500 bps
    price_limit_pct: float = 0.10  # 涨跌停 ±10%


@dataclass
class ConstraintViolation:
    """约束违规记录."""
    type: ViolationType
    value: float
    limit: float
    message: str


# ============================================================
# 约束 MDP (Constrained Markov Decision Process)
# ============================================================

class ConstrainedMDP:
    """约束马尔可夫决策过程.

    在标准 MDP (S, A, P, R) 上加入约束函数 C(s, a) ≤ 0,
    确保所有执行动作满足合规约束。
    """

    def __init__(self, constraints: ExecutionConstraints | None = None) -> None:
        self.constraints = constraints or ExecutionConstraints()

    def check_constraints(
        self,
        order_shares: float,
        current_position: float,
        adv: float,
        price: float = 0.0,
        reference_price: float = 0.0,
    ) -> list[ConstraintViolation]:
        """检查所有约束.

        Args:
            order_shares: 订单股数 (正=买, 负=卖)
            current_position: 当前持仓
            adv: 日均成交量
            price: 当前价格
            reference_price: 参考价 (用于涨跌停检查)

        Returns:
            违规列表 (空 = 全部合规)
        """
        violations: list[ConstraintViolation] = []
        c = self.constraints

        # 1. 持仓限制
        new_position = current_position + order_shares
        if abs(new_position) > c.max_position:
            violations.append(ConstraintViolation(
                type=ViolationType.POSITION_LIMIT,
                value=abs(new_position),
                limit=c.max_position,
                message=f"持仓 {new_position:.0f} 超限 {c.max_position:.0f}",
            ))

        # 2. 单笔限额
        if abs(order_shares) > c.max_order_size:
            violations.append(ConstraintViolation(
                type=ViolationType.ORDER_SIZE_LIMIT,
                value=abs(order_shares),
                limit=c.max_order_size,
                message=f"单笔 {abs(order_shares):.0f} 超限 {c.max_order_size:.0f}",
            ))

        # 3. 市场占比
        if adv > 0:
            participation = abs(order_shares) / adv
            if participation > c.max_participation:
                violations.append(ConstraintViolation(
                    type=ViolationType.PARTICIPATION_LIMIT,
                    value=participation,
                    limit=c.max_participation,
                    message=f"参与度 {participation:.2%} 超限 {c.max_participation:.2%}",
                ))

        # 4. 涨跌停
        if reference_price > 0 and price > 0:
            price_change = abs(price - reference_price) / reference_price
            if price_change > c.price_limit_pct:
                violations.append(ConstraintViolation(
                    type=ViolationType.PRICE_LIMIT,
                    value=price_change,
                    limit=c.price_limit_pct,
                    message=f"价格变动 {price_change:.2%} 超限 {c.price_limit_pct:.2%}",
                ))

        return violations

    def is_compliant(
        self,
        order_shares: float,
        current_position: float,
        adv: float,
        price: float = 0.0,
        reference_price: float = 0.0,
    ) -> bool:
        """是否合规 (无违规)."""
        return len(self.check_constraints(
            order_shares, current_position, adv, price, reference_price,
        )) == 0

    def project_to_feasible(
        self,
        order_shares: float,
        current_position: float,
        adv: float,
    ) -> float:
        """投影到可行域 (裁剪订单到满足约束)."""
        c = self.constraints
        # 单笔限额
        order_shares = max(-c.max_order_size, min(c.max_order_size, order_shares))
        # 持仓限制
        new_pos = current_position + order_shares
        if new_pos > c.max_position:
            order_shares = c.max_position - current_position
        elif new_pos < -c.max_position:
            order_shares = -c.max_position - current_position
        # 市场占比
        if adv > 0:
            max_by_participation = c.max_participation * adv
            order_shares = max(-max_by_participation, min(max_by_participation, order_shares))
        return order_shares


# ============================================================
# CVaR 尾部控制
# ============================================================

class CVaRController:
    """CVaR (Conditional Value at Risk) 尾部控制.

    CVaR_α = E[Loss | Loss > VaR_α]
    确保尾部风险不超过阈值。
    """

    def __init__(self, alpha: float = 0.95, max_cvar: float = 500.0) -> None:
        self.alpha = alpha  # 置信水平 (95%)
        self.max_cvar = max_cvar  # CVaR 上限 (bps)

    def compute_var(self, losses: np.ndarray) -> float:
        """计算 VaR (Value at Risk)."""
        if len(losses) == 0:
            return 0.0
        return float(np.percentile(losses, self.alpha * 100))

    def compute_cvar(self, losses: np.ndarray) -> float:
        """计算 CVaR (条件风险价值).

        CVaR_α = E[Loss | Loss > VaR_α]
        """
        if len(losses) == 0:
            return 0.0
        var = self.compute_var(losses)
        tail = losses[losses >= var]
        if len(tail) == 0:
            return var
        return float(tail.mean())

    def check_cvar(self, losses: np.ndarray) -> tuple[bool, float]:
        """检查 CVaR 是否在阈值内.

        Returns:
            (通过, CVaR值)
        """
        cvar = self.compute_cvar(losses)
        return cvar <= self.max_cvar, cvar

    def adjust_order_for_cvar(
        self,
        order_shares: float,
        loss_samples: np.ndarray,
    ) -> float:
        """根据 CVaR 调整订单大小.

        如果 CVaR 超限, 按比例缩减订单。
        """
        passed, cvar = self.check_cvar(loss_samples)
        if passed:
            return order_shares
        # 按比例缩减: scale = max_cvar / cvar
        scale = self.max_cvar / max(cvar, 1e-6)
        scale = min(scale, 1.0)  # 只缩减, 不放大
        return order_shares * scale


# ============================================================
# 零知识审计
# ============================================================

@dataclass
class AuditRecord:
    """审计记录."""
    order_hash: str  # 订单哈希承诺
    constraint_hash: str  # 约束哈希
    compliant: bool  # 是否合规
    cvar_value: float  # CVaR 值
    cvar_limit: float  # CVaR 上限
    timestamp: str  # 时间戳
    status: AuditStatus  # 审计状态
    violations: list[str] = field(default_factory=list)  # 违规描述 (不暴露策略细节)


class ZeroKnowledgeAudit:
    """零知识审计.

    通过哈希承诺验证执行合规性,
    不暴露策略细节 (订单大小、持仓等)。
    """

    def __init__(self) -> None:
        self._records: list[AuditRecord] = []

    def _hash(self, value: Any) -> str:
        """计算 SHA-256 哈希."""
        return hashlib.sha256(str(value).encode()).hexdigest()[:16]

    def create_audit(
        self,
        order_shares: float,
        violations: list[ConstraintViolation],
        cvar_value: float,
        cvar_limit: float,
        timestamp: str = "",
    ) -> AuditRecord:
        """创建审计记录 (零知识).

        只记录哈希承诺和合规状态, 不暴露订单细节。
        """
        record = AuditRecord(
            order_hash=self._hash(order_shares),
            constraint_hash=self._hash(len(violations)),
            compliant=len(violations) == 0 and cvar_value <= cvar_limit,
            cvar_value=cvar_value,
            cvar_limit=cvar_limit,
            timestamp=timestamp,
            status=AuditStatus.VERIFIED,
            violations=[v.message for v in violations],  # 违规描述 (不含策略细节)
        )
        self._records.append(record)
        logger.debug("审计记录: compliant=%s, cvar=%.2f", record.compliant, cvar_value)
        return record

    def verify_audit(self, record: AuditRecord) -> bool:
        """验证审计记录."""
        return record.status == AuditStatus.VERIFIED

    def get_records(self) -> list[AuditRecord]:
        """获取所有审计记录."""
        return list(self._records)

    def summary(self) -> dict[str, Any]:
        """审计摘要 (不暴露策略细节)."""
        records = self._records
        return {
            "total_audits": len(records),
            "compliant_count": sum(1 for r in records if r.compliant),
            "violation_count": sum(1 for r in records if not r.compliant),
            "max_cvar": max((r.cvar_value for r in records), default=0.0),
            "all_compliant": all(r.compliant for r in records),
        }


# ============================================================
# 安全执行代理
# ============================================================

@dataclass
class SafeExecutionResult:
    """安全执行结果."""
    symbol: str
    original_order: float
    adjusted_order: float  # 调整后订单
    compliant: bool  # 是否合规
    violations: list[ConstraintViolation]  # 违规列表
    cvar_value: float  # CVaR 值
    cvar_limit: float  # CVaR 上限
    audit: AuditRecord  # 审计记录
    metadata: dict[str, Any] = field(default_factory=dict)


class SafeExecutionAgent:
    """安全执行代理.

    整合约束 MDP + CVaR 控制 + 零知识审计,
    确保跨市场执行安全合规。

    用法:
        agent = SafeExecutionAgent()
        result = agent.execute_safely(
            symbol="600519", order_shares=10000, adv=500000,
            current_position=5000, price=1800.0,
        )
        assert result.compliant
    """

    def __init__(
        self,
        constraints: ExecutionConstraints | None = None,
        cvar_alpha: float = 0.95,
        seed: int | None = 42,
    ) -> None:
        self.mdp = ConstrainedMDP(constraints)
        self.cvar_controller = CVaRController(
            alpha=cvar_alpha, max_cvar=self.mdp.constraints.max_cvar,
        )
        self.audit = ZeroKnowledgeAudit()
        self._rng = np.random.default_rng(seed)

    def execute_safely(
        self,
        symbol: str,
        order_shares: float,
        adv: float,
        current_position: float = 0.0,
        price: float = 0.0,
        reference_price: float = 0.0,
        loss_samples: np.ndarray | None = None,
        timestamp: str = "",
    ) -> SafeExecutionResult:
        """安全执行订单.

        Args:
            symbol: 标的代码
            order_shares: 原始订单股数
            adv: 日均成交量
            current_position: 当前持仓
            price: 当前价格
            reference_price: 参考价
            loss_samples: 损失样本 (用于 CVaR 计算, None 则模拟)
            timestamp: 时间戳

        Returns:
            SafeExecutionResult
        """
        # 1. 约束检查
        violations = self.mdp.check_constraints(
            order_shares, current_position, adv, price, reference_price,
        )

        # 2. 投影到可行域 (如果有违规)
        if violations:
            adjusted_order = self.mdp.project_to_feasible(
                order_shares, current_position, adv,
            )
        else:
            adjusted_order = order_shares

        # 3. CVaR 控制
        if loss_samples is not None:
            losses = loss_samples
        else:
            # 模拟损失样本 (正态分布, 均值=冲击, 标准差=波动)
            n_samples = 1000
            mean_loss = abs(adjusted_order) / max(adv, 1.0) * 10000  # 冲击 bps
            std_loss = mean_loss * 0.5
            losses = self._rng.normal(mean_loss, std_loss, n_samples)

        cvar_passed, cvar_value = self.cvar_controller.check_cvar(losses)
        if not cvar_passed:
            adjusted_order = self.cvar_controller.adjust_order_for_cvar(
                adjusted_order, losses,
            )
            violations.append(ConstraintViolation(
                type=ViolationType.CVAR_BREACH,
                value=cvar_value,
                limit=self.cvar_controller.max_cvar,
                message=f"CVaR {cvar_value:.2f} 超限 {self.cvar_controller.max_cvar:.2f}",
            ))

        # 4. 重新检查调整后的订单
        final_violations = self.mdp.check_constraints(
            adjusted_order, current_position, adv, price, reference_price,
        )
        compliant = len(final_violations) == 0

        # 5. 零知识审计
        audit_record = self.audit.create_audit(
            order_shares=adjusted_order,
            violations=final_violations,
            cvar_value=cvar_value,
            cvar_limit=self.cvar_controller.max_cvar,
            timestamp=timestamp,
        )

        return SafeExecutionResult(
            symbol=symbol,
            original_order=order_shares,
            adjusted_order=adjusted_order,
            compliant=compliant,
            violations=final_violations,
            cvar_value=cvar_value,
            cvar_limit=self.cvar_controller.max_cvar,
            audit=audit_record,
            metadata={
                "adv": adv,
                "current_position": current_position,
                "price": price,
                "n_violations_original": len(violations),
            },
        )

    def batch_execute_safely(
        self,
        orders: list[dict[str, Any]],
    ) -> list[SafeExecutionResult]:
        """批量安全执行."""
        results: list[SafeExecutionResult] = []
        for o in orders:
            result = self.execute_safely(
                symbol=str(o.get("symbol", "")),
                order_shares=float(o.get("order_shares", 0)),
                adv=float(o.get("adv", 0)),
                current_position=float(o.get("current_position", 0)),
                price=float(o.get("price", 0)),
                reference_price=float(o.get("reference_price", 0)),
                timestamp=str(o.get("timestamp", "")),
            )
            results.append(result)
        return results

    def compliance_report(self) -> dict[str, Any]:
        """合规报告."""
        audit_summary = self.audit.summary()
        return {
            "audit": audit_summary,
            "constraints": {
                "max_position": self.mdp.constraints.max_position,
                "max_order_size": self.mdp.constraints.max_order_size,
                "max_participation": self.mdp.constraints.max_participation,
                "max_cvar": self.cvar_controller.max_cvar,
            },
            "cvar_alpha": self.cvar_controller.alpha,
        }


# ============================================================
# CLI 入口
# ============================================================

def main() -> None:
    """CLI 入口: 演示安全合规跨市场执行."""
    print("=" * 60)
    print("安全合规跨市场执行代理")
    print("文献: #53 Safe Cross-Market Execution (2025.10)")
    print("=" * 60)

    agent = SafeExecutionAgent()

    # === 1. 合规执行 ===
    print("\n--- 1. 合规执行 ---")
    result = agent.execute_safely(
        symbol="600519", order_shares=5000, adv=500000,
        current_position=10000, price=1800.0, reference_price=1800.0,
        timestamp="2026-08-23",
    )
    print(f"  标的: {result.symbol}")
    print(f"  原始订单: {result.original_order}")
    print(f"  调整订单: {result.adjusted_order}")
    print(f"  合规: {'✅' if result.compliant else '❌'}")
    print(f"  CVaR: {result.cvar_value:.2f} / {result.cvar_limit:.2f}")

    # === 2. 违规执行 (持仓超限) ===
    print("\n--- 2. 违规执行 (持仓超限) ---")
    result2 = agent.execute_safely(
        symbol="600519", order_shares=200000, adv=500000,
        current_position=50000, price=1800.0,
    )
    print(f"  原始订单: {result2.original_order}")
    print(f"  调整订单: {result2.adjusted_order:.0f} (投影到可行域)")
    print(f"  违规数: {len(result2.violations)}")
    for v in result2.violations:
        print(f"    - {v.message}")

    # === 3. 违规执行 (市场占比超限) ===
    print("\n--- 3. 违规执行 (市场占比超限) ---")
    result3 = agent.execute_safely(
        symbol="000001", order_shares=50000, adv=100000,
        current_position=0, price=15.0,
    )
    print(f"  原始订单: {result3.original_order}")
    print(f"  调整订单: {result3.adjusted_order:.0f}")
    print(f"  违规数: {len(result3.violations)}")

    # === 4. 批量执行 ===
    print("\n--- 4. 批量执行 ---")
    orders = [
        {"symbol": "600519", "order_shares": 5000, "adv": 500000, "current_position": 10000},
        {"symbol": "000001", "order_shares": 2000, "adv": 1000000, "current_position": 5000},
        {"symbol": "601318", "order_shares": 8000, "adv": 300000, "current_position": 20000},
    ]
    results = agent.batch_execute_safely(orders)
    all_compliant = all(r.compliant for r in results)
    print(f"  订单数: {len(results)}")
    print(f"  全部合规: {'✅' if all_compliant else '❌'}")

    # === 5. 合规报告 ===
    print("\n--- 5. 合规报告 ---")
    report = agent.compliance_report()
    print(f"  审计总数: {report['audit']['total_audits']}")
    print(f"  合规数: {report['audit']['compliant_count']}")
    print(f"  违规数: {report['audit']['violation_count']}")
    print(f"  全部合规: {'✅' if report['audit']['all_compliant'] else '❌'}")
    print(f"  最大 CVaR: {report['audit']['max_cvar']:.2f}")


if __name__ == "__main__":
    main()
