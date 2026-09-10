"""T09 预交易风控门 — 指令单在发送/落盘前的统一拦截入口.

属于「不崩风控六件套」之首, 核心目的: **下出去的单一定是合法的、合理的、不触发券商异常的**.

拦截项 (6 项, 默认全启用):
    1. LOT_SIZE      : 股数必须是 100 的整数倍 (科创板 200 股起, 可配置)
    2. PRICE_BAND    : 价格在 [昨收×0.9, 昨收×1.1] 涨跌停带内 (±10%, 科创板/创业板 ±20% 可覆盖)
    3. NOTIONAL_CAP  : 单笔名义金额 ≤ 单笔上限 (默认 50 万, 避免误操作 100 万级错单)
    4. ST_FILTER     : ST/*ST 股过滤 (默认拦截买入, 放行卖出)
    5. SYMBOL_WHITELIST: 白名单模式 (默认关闭; 开启后非白名单标的全拦截)
    6. SUSPEND_FILTER: 停牌标的过滤 (默认开启, 依赖 suspend_list 传入)

两种模式:
    - MODE_BLOCK (默认, fail-close): 任一规则触发则 reject, 返回 GuardResult.rejected=True
    - MODE_WARN            (审计) : 触发规则只记 warning + 记录到审计, 仍放行 (用于灰度)

使用 (与主链路集成点: daily_workflow.py phase_execute 在生成 ExecutionPlan 后):
    from utils.risk.pretrade_guard import PreTradeGuard, GuardOrderRequest
    guard = PreTradeGuard()
    req = GuardOrderRequest(symbol="sh600519", side="buy", shares=100, price=1700.0,
                            prev_close=1650.0, notional=1_700_000)
    result = guard.check(req)
    if result.rejected:
        logger.error(f"[PreTradeGuard] 拦截: {result.reasons}")
        return  # 阻断

零行为变更保证: 本模块只做 check, 不改传入对象, 不做任何 I/O (除审计日志外)
"""

from __future__ import annotations

import logging
import re
from collections.abc import Iterable
from dataclasses import dataclass, field

from utils.datetime_utils import now_bj

logger = logging.getLogger("pretrade_guard")

# ============================================================
# 常量与数据结构
# ============================================================

DEFAULT_LOT_SIZE = 100
DEFAULT_PRICE_LIMIT_PCT = 0.10  # ±10%
DEFAULT_NOTIONAL_CAP = 500_000  # 单笔 50 万
ST_PREFIX_PATTERN = re.compile(r"^(ST|\*ST|SST|S\*ST)", re.IGNORECASE)
SYMBOL_CODE_PATTERN = re.compile(r"^(sh|sz|bj)?\d{6}$", re.IGNORECASE)


@dataclass
class GuardOrderRequest:
    """预交易风控检查请求 (与 ExecutionPlan/拆单 slice 的最小交集字段)."""

    symbol: str
    side: str  # "buy" | "sell"
    shares: int
    price: float
    prev_close: float | None = None  # 用于涨跌停计算
    notional: float | None = None  # 未传时 = shares × price
    symbol_name: str = ""  # 用于 ST 过滤 (含 ST/*ST 前缀)
    is_suspended: bool = False  # 是否停牌 (调用方负责查停牌列表)

    def compute_notional(self) -> float:
        return self.notional if self.notional is not None else self.shares * self.price


@dataclass
class GuardResult:
    """风控检查结果."""

    request_symbol: str
    rejected: bool = False
    reasons: list[str] = field(default_factory=list)
    checked_rules: list[str] = field(default_factory=list)
    mode: str = "BLOCK"  # BLOCK | WARN
    timestamp: str = field(
        default_factory=lambda: now_bj().isoformat(timespec="seconds")
    )

    def add_reason(self, rule: str, reason: str) -> None:
        self.checked_rules.append(rule)
        if reason:
            self.reasons.append(f"[{rule}] {reason}")

    @property
    def is_pass(self) -> bool:
        return not self.rejected


class PreTradeGuard:
    """T09 预交易风控门 — 6 项规则 + BLOCK/WARN 双模式."""

    def __init__(
        self,
        mode: str = "BLOCK",
        lot_size: int = DEFAULT_LOT_SIZE,
        price_limit_pct: float = DEFAULT_PRICE_LIMIT_PCT,
        notional_cap: float = DEFAULT_NOTIONAL_CAP,
        enable_st_filter: bool = True,
        enable_suspend_filter: bool = True,
        whitelist: Iterable[str] | None = None,
    ) -> None:
        if mode not in ("BLOCK", "WARN"):
            raise ValueError(f"mode 必须是 BLOCK 或 WARN, 实际 {mode}")
        if not (0 < price_limit_pct <= 0.5):
            raise ValueError(f"price_limit_pct 应在 (0, 0.5], 实际 {price_limit_pct}")
        if lot_size < 1:
            raise ValueError(f"lot_size 应 ≥1, 实际 {lot_size}")
        if notional_cap <= 0:
            raise ValueError(f"notional_cap 应 >0, 实际 {notional_cap}")

        self.mode = mode
        self.lot_size = lot_size
        self.price_limit_pct = price_limit_pct
        self.notional_cap = notional_cap
        self.enable_st_filter = enable_st_filter
        self.enable_suspend_filter = enable_suspend_filter
        self.whitelist: set[str] = (
            {s.upper() for s in whitelist} if whitelist else set()
        )

    # ------------------------------------------------------------
    # 主入口
    # ------------------------------------------------------------

    def check(self, req: GuardOrderRequest) -> GuardResult:
        """对一笔订单执行全部启用的规则检查."""
        result = GuardResult(request_symbol=req.symbol, mode=self.mode)

        # Rule 1: LOT_SIZE
        self._rule_lot_size(req, result)
        # Rule 2: PRICE_BAND (需 prev_close)
        self._rule_price_band(req, result)
        # Rule 3: NOTIONAL_CAP
        self._rule_notional_cap(req, result)
        # Rule 4: ST_FILTER (买入拦截)
        self._rule_st_filter(req, result)
        # Rule 5: SYMBOL_WHITELIST
        self._rule_whitelist(req, result)
        # Rule 6: SUSPEND_FILTER
        self._rule_suspend(req, result)

        # 最终判定: WARN 模式不拦截, 只记原因
        if result.reasons and self.mode == "BLOCK":
            result.rejected = True
            logger.warning(
                f"[PreTradeGuard] 拦截 {req.symbol} {req.side} {req.shares}@{req.price:.2f} "
                f"原因={result.reasons}"
            )
        elif result.reasons and self.mode == "WARN":
            logger.warning(
                f"[PreTradeGuard] WARN (放行) {req.symbol} {req.side} {req.shares}@{req.price:.2f} "
                f"原因={result.reasons}"
            )
        return result

    def check_batch(self, reqs: Iterable[GuardOrderRequest]) -> list[GuardResult]:
        return [self.check(r) for r in reqs]

    # ------------------------------------------------------------
    # 6 条规则实现
    # ------------------------------------------------------------

    def _rule_lot_size(self, req: GuardOrderRequest, res: GuardResult) -> None:
        rule = "LOT_SIZE"
        if req.shares <= 0:
            res.add_reason(rule, f"shares={req.shares} ≤ 0")
            return
        if req.shares % self.lot_size != 0:
            res.add_reason(rule, f"shares={req.shares} 非 {self.lot_size} 整数倍")

    def _rule_price_band(self, req: GuardOrderRequest, res: GuardResult) -> None:
        rule = "PRICE_BAND"
        if req.price <= 0:
            res.add_reason(rule, f"price={req.price} ≤ 0")
            return
        if req.prev_close is None or req.prev_close <= 0:
            # 无昨收: 跳过 (无法判断)
            res.checked_rules.append(rule + "_SKIPPED_NO_PREVCLOSE")
            return
        lo = req.prev_close * (1 - self.price_limit_pct)
        hi = req.prev_close * (1 + self.price_limit_pct)
        if not (lo - 1e-6 <= req.price <= hi + 1e-6):
            res.add_reason(
                rule,
                f"price={req.price:.2f} 超出涨跌停带 [{lo:.2f}, {hi:.2f}] "
                f"(prev_close={req.prev_close:.2f}, limit={self.price_limit_pct:.0%})",
            )

    def _rule_notional_cap(self, req: GuardOrderRequest, res: GuardResult) -> None:
        rule = "NOTIONAL_CAP"
        notional = req.compute_notional()
        if notional > self.notional_cap:
            res.add_reason(
                rule,
                f"名义金额 ¥{notional:,.0f} 超过单笔上限 ¥{self.notional_cap:,.0f}",
            )

    def _rule_st_filter(self, req: GuardOrderRequest, res: GuardResult) -> None:
        rule = "ST_FILTER"
        if not self.enable_st_filter:
            res.checked_rules.append(rule + "_DISABLED")
            return
        if req.side.lower() != "buy":
            # 卖出 ST 不拦截 (允许止损)
            res.checked_rules.append(rule + "_SELL_SKIP")
            return
        name_hit = bool(
            req.symbol_name and ST_PREFIX_PATTERN.match(req.symbol_name.strip())
        )
        if name_hit:
            res.add_reason(
                rule, f"标的名称含 ST/*ST 前缀 (name={req.symbol_name!r}), 禁止买入"
            )

    def _rule_whitelist(self, req: GuardOrderRequest, res: GuardResult) -> None:
        rule = "SYMBOL_WHITELIST"
        if not self.whitelist:
            res.checked_rules.append(rule + "_DISABLED")
            return
        key = req.symbol.upper()
        if key not in self.whitelist:
            res.add_reason(
                rule, f"{req.symbol} 不在白名单 (白名单 {len(self.whitelist)} 个标的)"
            )

    def _rule_suspend(self, req: GuardOrderRequest, res: GuardResult) -> None:
        rule = "SUSPEND_FILTER"
        if not self.enable_suspend_filter:
            res.checked_rules.append(rule + "_DISABLED")
            return
        if req.is_suspended:
            res.add_reason(rule, f"{req.symbol} 处于停牌状态, 禁止下单")
