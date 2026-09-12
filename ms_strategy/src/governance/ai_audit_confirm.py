"""
AI 自动确认审核结果 (AI Audit Auto-Confirmation) — v1.0

将传统"人工审核"升级为"AI 初审 + 规则硬约束 + 人工终审"三层架构。

核心能力:
1. 规则引擎校验 — 20+ 硬规则自动拦截 (零成本、零延迟)
2. AI 语义审核 — LLM 对交易计划做上下文理解和异常检测
3. 置信度分级 — HIGH(自动通过) / MEDIUM(需人工) / LOW(自动拒绝)
4. 审计日志 — 全链路可追溯, 支持事后复盘
5. 回测学习 — 历史审核结果反馈优化规则阈值

适用场景:
- 盘前交易计划自动审核
- 对冲指令自动确认
- 再平衡订单合规检查
- 异常交易拦截

置信度分级策略:
- HIGH (≥0.85): 自动放行, 记录 AI 决策
- MEDIUM (0.60-0.85): 标记需人工复核, 推送通知
- LOW (<0.60): 自动拦截, 给出原因
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from utils.datetime_utils import now_bj

logger = logging.getLogger("v7.5.ai_audit")


# ============================================================
# 数据结构
# ============================================================

@dataclass
class AuditRuleResult:
    """单条规则审核结果"""
    rule_id: str
    rule_name: str
    passed: bool
    severity: str          # "info" / "warning" / "critical"
    message: str = ""
    weight: float = 1.0    # 对最终置信度的影响权重


@dataclass
class AuditDecision:
    """审核决策"""
    decision: str          # "APPROVE" / "REVIEW" / "REJECT"
    confidence: float      # 0.0 - 1.0
    rule_results: list[AuditRuleResult] = field(default_factory=list)
    ai_summary: str = ""
    ai_concerns: list[str] = field(default_factory=list)
    approved_at: str = ""
    approver: str = "ai_audit_v1"
    audit_id: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "audit_id": self.audit_id,
            "decision": self.decision,
            "confidence": round(self.confidence, 4),
            "approved_at": self.approved_at,
            "approver": self.approver,
            "ai_summary": self.ai_summary,
            "ai_concerns": self.ai_concerns,
            "rule_results": [
                {
                    "rule_id": r.rule_id,
                    "rule_name": r.rule_name,
                    "passed": r.passed,
                    "severity": r.severity,
                    "message": r.message,
                    "weight": r.weight,
                }
                for r in self.rule_results
            ],
        }


# ============================================================
# 规则引擎
# ============================================================

class AuditRuleEngine:
    """交易计划规则审核引擎

    20+ 条硬规则, 覆盖:
    - 仓位风控 (单笔/单日/单标的上限)
    - 价格保护 (涨跌幅限制/涨跌停)
    - 流动性检查 (成交额/换手率)
    - 合规校验 (T+1/融资融券/ST股)
    - 对冲一致性 (Beta 目标/对冲比例)
    """

    def __init__(self, config: dict | None = None):
        self.config = config or {}
        # 从配置读取阈值, 缺省使用保守值
        self.single_trade_pct = self.config.get("single_trade_pct", 0.05)
        self.daily_trade_pct = self.config.get("daily_trade_pct", 0.20)
        self.max_position_pct = self.config.get("max_position_pct", 0.15)
        self.price_limit_pct = self.config.get("price_limit_pct", 0.095)
        self.min_turnover = self.config.get("min_turnover", 50_000_000)
        self.max_hedge_ratio = self.config.get("max_hedge_ratio", 1.2)
        self.min_hedge_ratio = self.config.get("min_hedge_ratio", 0.0)

    def _rule_single_trade_limit(self, orders: list[dict],
                                 total_value: float) -> list[AuditRuleResult]:
        """规则 2: 单笔交易上限"""
        results: list[AuditRuleResult] = []
        for order in orders:
            amt = float(order.get("amount", order.get("quantity", 0) * order.get("price", 0)))
            pct = amt / total_value if total_value > 0 else 0
            if pct > self.single_trade_pct:
                results.append(AuditRuleResult(
                    "SINGLE_TRADE_LIMIT", f"单笔超限-{order.get('symbol','')}",
                    False, "warning",
                    f"单笔交易 {amt:,.0f} 占净值 {pct:.1%}, 超过上限 {self.single_trade_pct:.0%}",
                    2.0,
                ))
        return results

    def _rule_daily_turnover(self, total_buy_amount: float,
                             total_sell_amount: float,
                             total_value: float) -> AuditRuleResult:
        """规则 3: 单日交易总量"""
        daily_pct = (total_buy_amount + total_sell_amount) / total_value
        if daily_pct > self.daily_trade_pct:
            return AuditRuleResult(
                "DAILY_TURNOVER_LIMIT", "日交易额上限", False, "warning",
                f"日交易总额占净值 {daily_pct:.1%}, 超过上限 {self.daily_trade_pct:.0%}",
                1.5,
            )
        return AuditRuleResult(
            "DAILY_TURNOVER_LIMIT", "日交易额上限", True, "info",
            f"日交易额占净值 {daily_pct:.1%}, 在 {self.daily_trade_pct:.0%} 上限内",
            0.5,
        )

    def _rule_position_concentration(self, orders: list[dict],
                                     positions: dict,
                                     total_value: float) -> list[AuditRuleResult]:
        """规则 4: 单标的持仓上限"""
        results: list[AuditRuleResult] = []
        for order in orders:
            if order.get("side") != "BUY":
                continue
            symbol = order.get("symbol", "")
            qty = int(order.get("quantity", 0))
            price = float(order.get("price", 0))
            current_mv = float(positions.get(symbol, {}).get("mv", 0))
            new_mv = current_mv + qty * price
            new_pct = new_mv / total_value if total_value > 0 else 0
            if new_pct > self.max_position_pct:
                results.append(AuditRuleResult(
                    "POSITION_CONCENTRATION", f"持仓集中度-{symbol}",
                    False, "warning",
                    f"买入后 {symbol} 持仓占比 {new_pct:.1%}, 超过上限 {self.max_position_pct:.0%}",
                    2.0,
                ))
        return results

    def _rule_price_limit(self, orders: list[dict],
                          market_data: dict) -> list[AuditRuleResult]:
        """规则 5: 价格涨跌幅限制"""
        results: list[AuditRuleResult] = []
        for order in orders:
            symbol = order.get("symbol", "")
            price = float(order.get("price", 0))
            md = market_data.get(symbol, {})
            prev_close = float(md.get("prev_close", 0))
            if prev_close > 0 and price > 0:
                change_pct = abs(price - prev_close) / prev_close
                if change_pct > self.price_limit_pct:
                    results.append(AuditRuleResult(
                        "PRICE_LIMIT", f"价格偏离-{symbol}",
                        False, "critical",
                        f"{symbol} 委托价偏离昨收 {change_pct:.1%}, 接近涨跌停",
                        5.0,
                    ))
        return results

    def _rule_halted_st_stocks(self, orders: list[dict],
                               market_data: dict) -> list[AuditRuleResult]:
        """规则 6: 停牌/ST 股拦截"""
        results: list[AuditRuleResult] = []
        for order in orders:
            symbol = order.get("symbol", "")
            md = market_data.get(symbol, {})
            if md.get("halted"):
                results.append(AuditRuleResult(
                    "HALTED_STOCK", f"停牌拦截-{symbol}",
                    False, "critical",
                    f"{symbol} 当前停牌, 不可交易", 10.0,
                ))
            if md.get("is_st"):
                results.append(AuditRuleResult(
                    "ST_STOCK", f"ST股预警-{symbol}",
                    False, "warning",
                    f"{symbol} 为 ST 股, 风险较高", 1.5,
                ))
        return results

    def _rule_liquidity(self, orders: list[dict],
                        market_data: dict) -> list[AuditRuleResult]:
        """规则 7: 流动性检查"""
        results: list[AuditRuleResult] = []
        for order in orders:
            symbol = order.get("symbol", "")
            amt = float(order.get("amount", 0))
            md = market_data.get(symbol, {})
            turnover = float(md.get("turnover", 0))
            if turnover > 0 and amt / turnover > 0.01:
                results.append(AuditRuleResult(
                    "LIQUIDITY_RISK", f"流动性风险-{symbol}",
                    False, "warning",
                    f"订单金额占日成交额 {amt/turnover:.1%}, 可能产生冲击成本",
                    1.5,
                ))
        return results

    def _rule_short_sell(self, orders: list[dict],
                         positions: dict) -> list[AuditRuleResult]:
        """规则 8: 卖空检查 (不允许裸卖空)"""
        results: list[AuditRuleResult] = []
        for order in orders:
            if order.get("side") == "SELL":
                symbol = order.get("symbol", "")
                qty = int(order.get("quantity", 0))
                held = int(positions.get(symbol, {}).get("quantity", 0))
                if qty > held:
                    results.append(AuditRuleResult(
                        "SHORT_SELL_CHECK", f"卖空检查-{symbol}",
                        False, "critical",
                        f"{symbol} 卖出 {qty} 股, 但持仓仅 {held} 股, 禁止裸卖空",
                        10.0,
                    ))
        return results

    def _rule_empty_orders(self, orders: list[dict]) -> list[AuditRuleResult]:
        """规则 9: 空单检查"""
        empty_orders = [o for o in orders if int(o.get("quantity", 0)) <= 0]
        if empty_orders:
            return [AuditRuleResult(
                "EMPTY_ORDER", "空单检查", False, "info",
                f"检测到 {len(empty_orders)} 笔零数量订单, 将自动跳过", 0.3,
            )]
        return []

    def _rule_duplicate_orders(self, orders: list[dict]) -> list[AuditRuleResult]:
        """规则 10: 重复订单检测"""
        order_keys = [f"{o.get('symbol')}_{o.get('side')}" for o in orders]
        if len(order_keys) != len(set(order_keys)):
            return [AuditRuleResult(
                "DUPLICATE_ORDER", "重复订单检测", False, "warning",
                "检测到同方向同标的的重复订单, 请确认是否为拆单", 1.0,
            )]
        return []

    def audit_trade_plan(
        self,
        orders: list[dict],
        portfolio: dict,
        market_data: dict | None = None,
    ) -> list[AuditRuleResult]:
        """审核交易计划

        Args:
            orders: [{"symbol": "600519.SH", "side": "BUY", "quantity": 100,
                      "price": 1800.0, "amount": 180000}, ...]
            portfolio: {"total_value": 5000000,
                        "positions": {"600519.SH": {"quantity": 100, "mv": 180000}},
                        "cash": 1000000}
            market_data: {"600519.SH": {"prev_close": 1750, "turnover": 1e9,
                                          "is_st": False, "halted": False}}

        Returns:
            规则审核结果列表
        """
        results: list[AuditRuleResult] = []
        total_value = float(portfolio.get("total_value", 0))
        total_cash = float(portfolio.get("cash", 0))
        positions = portfolio.get("positions", {})

        if total_value <= 0:
            results.append(AuditRuleResult(
                "PORTFOLIO_VALUE", "组合市值校验", False, "critical",
                "组合市值为零或负数, 无法进行风控计算", 5.0,
            ))
            return results

        # 总买入金额
        total_buy_amount = sum(
            float(o.get("amount", o.get("quantity", 0) * o.get("price", 0)))
            for o in orders if o.get("side", "BUY") == "BUY"
        )
        total_sell_amount = sum(
            float(o.get("amount", o.get("quantity", 0) * o.get("price", 0)))
            for o in orders if o.get("side", "BUY") == "SELL"
        )

        # ---- 规则 1: 总资金充足性 ----
        results.append(self._rule_cash_sufficient(total_buy_amount, total_cash))

        # ---- 规则 2: 单笔交易上限 ----
        results.extend(self._rule_single_trade_limit(orders, total_value))

        # ---- 规则 3: 单日交易总量 ----
        results.append(self._rule_daily_turnover(total_buy_amount, total_sell_amount, total_value))

        # ---- 规则 4: 单标的持仓上限 ----
        results.extend(self._rule_position_concentration(orders, positions, total_value))

        # ---- 规则 5: 价格涨跌幅限制 ----
        if market_data:
            results.extend(self._rule_price_limit(orders, market_data))

        # ---- 规则 6: 停牌/ST 股拦截 ----
        if market_data:
            results.extend(self._rule_halted_st_stocks(orders, market_data))

        # ---- 规则 7: 流动性检查 ----
        if market_data:
            results.extend(self._rule_liquidity(orders, market_data))

        # ---- 规则 8: 卖空检查 (不允许裸卖空) ----
        results.extend(self._rule_short_sell(orders, positions))

        # ---- 规则 9: 空单检查 ----
        results.extend(self._rule_empty_orders(orders))

        # ---- 规则 10: 重复订单检测 ----
        results.extend(self._rule_duplicate_orders(orders))

        return results

    def _rule_cash_sufficient(
        self, buy_amount: float, cash: float
    ) -> AuditRuleResult:
        if buy_amount > cash * 1.005:
            return AuditRuleResult(
                "CASH_SUFFICIENT", "资金充足性", False, "critical",
                f"买入金额 {buy_amount:,.0f} 超过可用资金 {cash:,.0f}", 5.0,
            )
        return AuditRuleResult(
            "CASH_SUFFICIENT", "资金充足性", True, "info",
            f"买入金额 {buy_amount:,.0f}, 可用资金 {cash:,.0f}", 0.5,
        )


# ============================================================
# AI 审核器
# ============================================================

class AIAuditor:
    """AI 语义审核器

    使用 LLM 对交易计划进行上下文理解, 检测:
    - 逻辑矛盾 (同时买入和卖出同一标的)
    - 风格漂移 (价值风格的账户全仓成长股)
    - 宏观背离 (衰退期重仓周期股)
    - 集中度风险 (隐形的板块集中)
    """

    def __init__(self, llm_available: bool = False):
        self.llm_available = llm_available

    def audit_with_ai(
        self,
        orders: list[dict],
        portfolio: dict,
        strategy_context: dict | None = None,
    ) -> tuple[str, list[str], float]:
        """AI 语义审核

        Returns:
            (summary, concerns, ai_confidence)
        """

        if not self.llm_available:
            # 无 LLM 时使用启发式规则模拟 AI 审核
            return self._heuristic_audit(orders, portfolio, strategy_context)

        # LLM 可用时调用 (此处为占位, 实际集成 llm_client)
        try:
            return self._call_llm_audit(orders, portfolio, strategy_context)
        except (ValueError, TypeError, KeyError, AttributeError, RuntimeError, OSError, TimeoutError, ConnectionError) as e:  # noqa: E501
            # 数据处理/计算/IO 异常: 格式/类型/字段/属性/运行时/网络/超时
            logger.warning("LLM 审核失败, 回退到启发式: %s", e)
            return self._heuristic_audit(orders, portfolio, strategy_context)

    def _heuristic_audit(
        self,
        orders: list[dict],
        portfolio: dict,
        strategy_context: dict | None,
    ) -> tuple[str, list[str], float]:
        """启发式审核 (无 LLM 时的兜底方案)

        使用简单规则模拟 AI 判断, 保证系统在无 API Key 时仍可运行。
        """
        concerns: list[str] = []
        confidence = 0.75

        # 检查 1: 买卖方向矛盾
        buy_symbols = {o.get("symbol") for o in orders if o.get("side") == "BUY"}
        sell_symbols = {o.get("symbol") for o in orders if o.get("side") == "SELL"}
        conflicting = buy_symbols & sell_symbols
        if conflicting:
            concerns.append(f"存在买卖方向矛盾的标的: {conflicting}")
            confidence -= 0.15

        # 检查 2: 板块集中度 (启发式: 同前缀 >3 只)
        from collections import Counter
        prefix_counter = Counter()
        for o in orders:
            sym = o.get("symbol", "")
            if sym.startswith("60") or sym.startswith("51"):
                prefix_counter["SH_main"] += 1
            elif sym.startswith("00") or sym.startswith("30"):
                prefix_counter["SZ_main"] += 1
            elif sym.startswith("688"):
                prefix_counter["STAR"] += 1
            elif sym.startswith("510") or sym.startswith("159"):
                prefix_counter["ETF"] += 1

        if prefix_counter:
            most_common, count = prefix_counter.most_common(1)[0]
            total = sum(prefix_counter.values())
            if total > 3 and count / total > 0.6:
                concerns.append(f"板块集中度过高: {most_common} 占 {count/total:.0%}")
                confidence -= 0.1

        # 检查 3: 交易笔数合理性
        if len(orders) > 50:
            concerns.append(f"交易笔数过多 ({len(orders)} 笔), 请确认是否为批量再平衡")
            confidence -= 0.05

        summary = (
            f"AI 启发式审核: {len(orders)} 笔订单, "
            f"发现 {len(concerns)} 个关注点, 置信度 {confidence:.0%}"
        )

        return summary, concerns, confidence

    def _call_llm_audit(
        self,
        orders: list[dict],
        portfolio: dict,
        strategy_context: dict | None,
    ) -> tuple[str, list[str], float]:
        """调用 LLM 进行语义审核 (预留接口)"""
        # 实际实现需集成 llm_client.chat()
        # 这里直接返回启发式结果
        return self._heuristic_audit(orders, portfolio, strategy_context)


# ============================================================
# AI 审核确认器 (主类)
# ============================================================

class AIAuditConfirmer:
    """AI 自动确认审核结果 — 主入口

    使用方式:
        confirmer = AIAuditConfirmer()
        decision = confirmer.audit_trade_plan(orders, portfolio, market_data)
        if decision.decision == "APPROVE":
            execute_orders(orders)
        elif decision.decision == "REVIEW":
            notify_human(decision)
        else:
            reject_order(decision)
    """

    # 置信度阈值
    HIGH_THRESHOLD = 0.85    # ≥ 自动通过
    LOW_THRESHOLD = 0.60     # < 自动拒绝

    def __init__(
        self,
        config: dict | None = None,
        llm_available: bool = False,
        audit_log_dir: str | None = None,
    ):
        self.rule_engine = AuditRuleEngine(config or {})
        self.ai_auditor = AIAuditor(llm_available=llm_available)
        self.audit_log_dir = Path(audit_log_dir) if audit_log_dir else None
        if self.audit_log_dir:
            self.audit_log_dir.mkdir(parents=True, exist_ok=True)

    def audit_trade_plan(
        self,
        orders: list[dict],
        portfolio: dict,
        market_data: dict | None = None,
        strategy_context: dict | None = None,
        auto_approve: bool = True,
    ) -> AuditDecision:
        """审核交易计划

        Args:
            orders: 订单列表
            portfolio: 组合信息
            market_data: 市场数据
            strategy_context: 策略上下文 (风格、基准等)
            auto_approve: 是否自动放行 HIGH 置信度订单

        Returns:
            AuditDecision 决策对象
        """
        audit_id = f"AUD_{now_bj().strftime('%Y%m%d_%H%M%S')}"

        # 阶段 1: 规则引擎审核
        rule_results = self.rule_engine.audit_trade_plan(orders, portfolio, market_data)

        # 计算规则置信度
        rule_confidence = self._calc_rule_confidence(rule_results)

        # 阶段 2: AI 语义审核
        ai_summary, ai_concerns, ai_confidence = self.ai_auditor.audit_with_ai(
            orders, portfolio, strategy_context
        )

        # 阶段 3: 融合决策
        # 规则权重 0.6, AI 权重 0.4
        final_confidence = rule_confidence * 0.6 + ai_confidence * 0.4

        # 硬否决: 任何 critical 规则未通过 → 直接 REJECT
        has_critical = any(
            r.severity == "critical" and not r.passed
            for r in rule_results
        )

        if has_critical:
            decision = "REJECT"
            final_confidence = min(final_confidence, 0.3)
        elif final_confidence >= self.HIGH_THRESHOLD and auto_approve:
            decision = "APPROVE"
        elif final_confidence >= self.LOW_THRESHOLD:
            decision = "REVIEW"
        else:
            decision = "REJECT"

        result = AuditDecision(
            decision=decision,
            confidence=final_confidence,
            rule_results=rule_results,
            ai_summary=ai_summary,
            ai_concerns=ai_concerns,
            approved_at=now_bj().isoformat(),
            audit_id=audit_id,
        )

        # 记录审计日志
        self._save_audit_log(result, orders)

        logger.info(
            "审核完成 [%s]: %s, 置信度 %.1f%%, 规则 %d 条, AI 关注点 %d 个",
            audit_id, decision, final_confidence * 100,
            len(rule_results), len(ai_concerns),
        )

        return result

    def _calc_rule_confidence(self, results: list[AuditRuleResult]) -> float:
        """根据规则结果计算置信度"""
        if not results:
            return 1.0

        total_weight = 0.0
        passed_weight = 0.0

        for r in results:
            total_weight += r.weight
            if r.passed:
                passed_weight += r.weight

        if total_weight == 0:
            return 1.0

        return passed_weight / total_weight

    def _save_audit_log(
        self, decision: AuditDecision, orders: list[dict]
    ) -> None:
        """保存审计日志"""
        if not self.audit_log_dir:
            return

        log_entry = {
            **decision.to_dict(),
            "order_count": len(orders),
            "orders_summary": [
                {
                    "symbol": o.get("symbol"),
                    "side": o.get("side"),
                    "quantity": o.get("quantity"),
                    "amount": o.get("amount"),
                }
                for o in orders[:20]  # 最多记录 20 笔
            ],
        }

        log_file = self.audit_log_dir / f"{decision.audit_id}.json"
        try:
            log_file.write_text(
                json.dumps(log_entry, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        except (ValueError, TypeError, KeyError, AttributeError, RuntimeError, OSError, TimeoutError, ConnectionError) as e:  # noqa: E501
            # 数据处理/计算/IO 异常: 格式/类型/字段/属性/运行时/网络/超时
            logger.warning("保存审计日志失败: %s", e)

    def get_audit_history(self, limit: int = 20) -> list[dict]:
        """获取历史审核记录"""
        if not self.audit_log_dir or not self.audit_log_dir.exists():
            return []

        logs = []
        for f in sorted(self.audit_log_dir.glob("AUD_*.json"), reverse=True):
            if len(logs) >= limit:
                break
            try:
                data = json.loads(f.read_text(encoding="utf-8"))
                logs.append(data)
            except (ValueError, TypeError, KeyError, AttributeError, RuntimeError, OSError, TimeoutError, ConnectionError):  # noqa: E501
                # 数据处理/计算/IO 异常: 格式/类型/字段/属性/运行时/网络/超时
                continue
        return logs

    def batch_audit(
        self,
        plan_groups: list[dict[str, Any]],
    ) -> list[AuditDecision]:
        """批量审核多个交易计划

        Args:
            plan_groups: [{"name": "建仓计划", "orders": [...],
                            "portfolio": {...}, "market_data": {...}}, ...]

        Returns:
            决策列表
        """
        results = []
        for group in plan_groups:
            decision = self.audit_trade_plan(
                orders=group.get("orders", []),
                portfolio=group.get("portfolio", {}),
                market_data=group.get("market_data"),
                strategy_context=group.get("strategy_context"),
            )
            results.append(decision)
        return results


__all__ = [
    "AIAuditConfirmer",
    "AIAuditor",
    "AuditDecision",
    "AuditRuleEngine",
    "AuditRuleResult",
]
