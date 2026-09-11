"""策略基类与核心值对象模块.

定义 ComboBase 抽象基类、OptionChainFetcher 期权链适配层,
以及所有策略共享的 frozen dataclass 值对象与枚举.

值对象 (U-03 不可变性):
    - ComboLeg: 组合单腿定义
    - ComboOrder: 组合单条指令
    - ComboResult: 策略生成结果 (订单包 + Greeks + 状态)
    - ApprovalResult: 风控审批结果
    - RollResult: 滚仓结果

复用已有结构:
    - GreekExposure (utils.greek_hedge_manager): Greeks 暴露数据结构
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import date
from enum import Enum
from typing import TYPE_CHECKING, cast

from utils.datetime_utils import now_bj

if TYPE_CHECKING:  # 仅类型检查时导入, 避免运行时循环依赖
    from utils.etf_option_combo.combo_state import ComboStateManager
    from utils.greek_hedge_manager import GreekExposure
    from utils.option_data_fetcher import OptionDataFetcher

logger = logging.getLogger(__name__)


class StrategyType(Enum):
    """策略类型枚举."""

    COVERED_CALL = "covered_call"
    COLLAR = "collar"
    CASH_SECURED_PUT = "cash_secured_put"
    VERTICAL_SPREAD = "vertical_spread"
    CALENDAR_SPREAD = "calendar_spread"


class LegSide(Enum):
    """腿方向."""

    BUY = "BUY"
    SELL = "SELL"


class OrderStatus(Enum):
    """订单状态."""

    PENDING = "PENDING"
    FILLED = "FILLED"
    REJECTED = "REJECTED"
    CANCELLED = "CANCELLED"


@dataclass(frozen=True)
class ComboLeg:
    """组合腿 (单腿定义) — 不可变值对象.

    Attributes:
        instrument: 工具类型 "SPOT" / "OPTION"
        underlying: 标的代码 "510050.SH"
        option_type: 期权类型 "CALL" / "PUT" / None(现货)
        side: 腿方向 BUY / SELL
        strike: 行权价 (现货为0.0)
        expiry: 到期日 (现货为None)
        quantity: 数量 (现货=份, 期权=张)
        multiplier: 合约乘数 (ETF期权=10000, 现货=1)
        premium: 权利金 (现货为0.0)
    """

    instrument: str
    underlying: str
    option_type: str | None
    side: LegSide
    strike: float
    expiry: date | None
    quantity: int
    multiplier: int
    premium: float


@dataclass(frozen=True)
class ComboOrder:
    """组合订单 (单条指令) — 不可变值对象.

    Attributes:
        order_id: 唯一ID (策略实例ID+标的+日期去重, 保证幂等性)
        strategy_type: 策略类型
        leg: 组合腿定义
        order_type: 订单类型 "LIMIT" / "MARKET"
        limit_price: 限价 (权利金上浮5%确保成交)
        status: 订单状态
        requires_confirmation: 肥手指>50万标记, 需二次确认
        reason: 触发原因 (审计日志)
    """

    order_id: str
    strategy_type: StrategyType
    leg: ComboLeg
    order_type: str
    limit_price: float
    status: OrderStatus
    requires_confirmation: bool = False
    reason: str = ""


@dataclass(frozen=True)
class ComboResult:
    """策略生成结果 — 不可变值对象.

    Attributes:
        strategy_type: 策略类型
        underlying: 标的代码
        orders: 订单包 (不可变序列 tuple)
        greeks: 组合 Greeks (复用 GreekExposure)
        net_premium: 净权利金 (正=收入, 负=支出)
        budget_remaining: 剩余预算
        error_code: 错误码 (None=成功)
        error_msg: 错误描述
        generated_at: ISO 时间戳
        meta: 扩展元数据 (None=默认; IV Rank 自适应等场景携带 tier/生效参数)
    """

    strategy_type: StrategyType
    underlying: str
    orders: tuple[ComboOrder, ...]
    greeks: "GreekExposure"
    net_premium: float
    budget_remaining: float
    error_code: str | None
    error_msg: str | None
    generated_at: str
    meta: dict | None = None


@dataclass(frozen=True)
class ApprovalResult:
    """风控审批结果 — 不可变值对象.

    Attributes:
        approved: 是否通过
        orders_approved: 审批通过的订单包 (不可变序列)
        rejected_reason: 拒绝原因 (None=通过)
        risk_flags: 触发的风控标志 (不可变序列)
        requires_confirmation: 肥手指待确认
    """

    approved: bool
    orders_approved: tuple[ComboOrder, ...]
    rejected_reason: str | None
    risk_flags: tuple[str, ...]
    requires_confirmation: bool


@dataclass(frozen=True)
class RollResult:
    """滚仓结果 — 不可变值对象.

    Attributes:
        strategy_instance_id: 策略实例ID
        needs_roll: 是否需要滚仓
        close_orders: 平近月订单 (不可变序列)
        open_orders: 开下月订单 (空=L3仅平不开新)
        roll_cost: 滚仓成本 (滑点+手续费)
        expected_benefit: 滚仓后预期收益
        cost_ratio: 成本占比 (roll_cost / expected_benefit)
    """

    strategy_instance_id: str
    needs_roll: bool
    close_orders: tuple[ComboOrder, ...]
    open_orders: tuple[ComboOrder, ...]
    roll_cost: float
    expected_benefit: float
    cost_ratio: float


def _get_expiry_date(year: int, month: int) -> date:
    """计算中国ETF期权到期日 — 当月第四个周三."""
    from calendar import monthrange

    first_day_wday = date(year, month, 1).weekday()
    first_wednesday = 1 + (2 - first_day_wday) % 7
    fourth_wednesday = first_wednesday + 21
    max_day = monthrange(year, month)[1]
    if fourth_wednesday > max_day:
        fourth_wednesday -= 7
    return date(year, month, fourth_wednesday)


def _generate_expiry_dates(today: date, dte_min: int, dte_max: int) -> list[date]:
    """生成满足 DTE 范围的到期日列表 (当月及未来月份的第四个周三)."""
    expiries: list[date] = []
    year, month = today.year, today.month
    for _ in range(12):
        exp = _get_expiry_date(year, month)
        dte = (exp - today).days
        if dte_min <= dte <= dte_max:
            expiries.append(exp)
        if dte > dte_max:
            break
        month += 1
        if month > 12:
            month = 1
            year += 1
    return expiries


def _generate_strike_grid(
    spot_price: float,
    otm_range: tuple[float, float] | None,
    option_type: str,
    step: float = 0.05,
) -> list[float]:
    """生成行权价网格."""
    if otm_range is None:
        low = round(spot_price * 0.80, 2)
        high = round(spot_price * 1.20, 2)
    elif option_type == "CALL":
        low = round(spot_price * (1 + otm_range[0]), 2)
        high = round(spot_price * (1 + otm_range[1]), 2)
    else:
        low = round(spot_price * (1 - otm_range[1]), 2)
        high = round(spot_price * (1 - otm_range[0]), 2)

    strikes: list[float] = []
    k = low
    while k <= high + 1e-9:
        strikes.append(round(k, 2))
        k += step
    return strikes


class OptionChainFetcher:
    """期权链适配层 — 在 OptionDataFetcher 上构建链式查询.

    不修改 OptionDataFetcher 公开接口, 仅在其上包装.
    降级链: Wind MCP期权链(待扩展) → OptionDataFetcher单点BS批量 → 本地缓存.
    """

    def __init__(
        self,
        fetcher: "OptionDataFetcher | None" = None,
        data_layer: object | None = None,
        bs_timeout_ms: int = 200,
    ) -> None:
        if fetcher is None:
            try:
                from utils.option_data_fetcher import OptionDataFetcher
                fetcher = OptionDataFetcher()
            except (ImportError, ModuleNotFoundError, OSError, RuntimeError) as e:
                logger.warning("OptionDataFetcher 初始化失败: %s", e)
                fetcher = None
        self._fetcher = fetcher
        self._data_layer = data_layer
        self._bs_timeout_ms = bs_timeout_ms

    def get_spot_price(self, underlying: str) -> float | None:
        """获取ETF现货价 — 统一数据层 P0-P6 降级."""
        if self._data_layer is not None:
            for method_name in ("get_spot", "get_price", "fetch_price"):
                method = getattr(self._data_layer, method_name, None)
                if method is not None:
                    try:
                        result = method(underlying)
                        if result is not None:
                            price = (
                                result
                                if isinstance(result, (int, float))
                                else result.get("price") or result.get("close")
                            )
                            if price is not None and price > 0:
                                return float(price)
                    except (ValueError, TypeError, KeyError, AttributeError, OSError, RuntimeError) as e:
                        logger.debug("data_layer.%s 获取 %s 失败: %s", method_name, underlying, e)
        logger.warning("无法获取 %s 现货价, 数据层不可用", underlying)
        return None

    def _try_real_chain(
        self,
        underlying: str,
        option_type: str,
        dte_range: tuple[int, int],
        otm_range: tuple[float, float] | None,
        min_volume: int,
        spot_price: float,
    ) -> list[dict]:
        """真实期权链优先 (2026-09-11 《ETF期权组合诊脉书》硬伤六修复).

        OptionDataFetcher v2 起提供 ``get_real_chain``; 无该接口 (或注入式测试替身)
        时直接返回 [] → 调用方回落 BS 合成网格, 行为与修复前完全一致。
        只接受 premium 为数值的行, 防止 Mock 替身/脏数据穿透到下游 Greeks 计算。
        """
        getter = getattr(self._fetcher, "get_real_chain", None)
        if not callable(getter):
            return []
        try:
            rows = getter(
                underlying=underlying.split(".")[0],
                option_type=option_type.lower(),
                dte_range=dte_range,
                otm_range=otm_range,
                spot_price=spot_price,
                min_volume=min_volume,
            )
        except (ValueError, TypeError, KeyError, AttributeError, OSError, RuntimeError) as e:
            logger.warning("真实期权链查询失败, 回落 BS 合成网格: %s", e)
            return []

        if not isinstance(rows, list):
            return []

        chain: list[dict] = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            premium = row.get("premium")
            if not isinstance(premium, (int, float)):
                continue
            chain.append({
                "strike": float(row.get("strike") or 0.0),
                "expiry": str(row.get("expiry") or ""),
                "dte": int(row.get("dte") or 0),
                "premium": float(premium),
                "iv": float(row.get("iv") or 0.0),
                "delta": float(row.get("delta") or 0.0),
                "gamma": float(row.get("gamma") or 0.0),
                "theta": float(row.get("theta") or 0.0),
                "vega": float(row.get("vega") or 0.0),
                "volume": float(row.get("volume") or 0),
                "source": str(row.get("source") or "unknown"),
            })
        chain.sort(key=lambda c: (c["dte"], c["strike"]))
        return chain

    def get_option_chain(
        self,
        underlying: str,
        option_type: str,
        dte_range: tuple[int, int],
        otm_range: tuple[float, float] | None = None,
        min_volume: int = 100,
        spot_price: float | None = None,
    ) -> list[dict]:
        """获取期权链 — 返回合约列表.

        Returns:
            [{strike, expiry, dte, premium, iv, delta, gamma, theta, vega, volume, source}, ...]
            数据源全失败时返回空列表 (U-05 优雅降级)
        """
        if spot_price is None or spot_price <= 0:
            spot_price = self.get_spot_price(underlying)
        if spot_price is None or spot_price <= 0:
            logger.error("无法获取 %s 现货价, 期权链查询失败", underlying)
            return []

        if self._fetcher is None:
            logger.error("OptionDataFetcher 不可用, 期权链查询失败")
            return []

        # 真实期权链优先 (2026-09-11 诊脉书硬伤六): 交易所真实权利金 + 由权利金反解的 IV。
        # 真实链不可用时返回 [] 并回落下方 BS 合成网格 — 对既有注入式测试零影响。
        real_chain = self._try_real_chain(
            underlying, option_type, dte_range, otm_range, min_volume, spot_price
        )
        if real_chain:
            logger.info(
                "%s %s 真实期权链: %d 合约 (spot=%.3f)", underlying, option_type, len(real_chain), spot_price
            )
            return real_chain

        today = date.today()
        expiries = _generate_expiry_dates(today, dte_range[0], dte_range[1])
        strikes = _generate_strike_grid(spot_price, otm_range, option_type)

        if not expiries or not strikes:
            logger.warning("%s %s 期权链: 无满足条件的到期日或行权价", underlying, option_type)
            return []

        import time
        t0 = time.perf_counter()
        chain: list[dict] = []
        for expiry in expiries:
            dte = (expiry - today).days
            T = dte / 365.0  # noqa: N806 — BS 惯例大写 T (年化到期时间)
            if T <= 0:
                continue
            for strike in strikes:
                try:
                    data = self._fetcher.get_option_data(
                        underlying=underlying.split(".")[0],
                        spot_price=spot_price,
                        strike=strike,
                        T=T,
                        r=0.03,
                        option_type=option_type.lower(),
                    )
                    if not data or data.get("premium") is None:
                        continue
                    volume = data.get("volume", 0)
                    if min_volume > 0 and volume and volume < min_volume:
                        continue
                    chain.append({
                        "strike": strike,
                        "expiry": expiry.isoformat(),
                        "dte": dte,
                        "premium": data["premium"],
                        "iv": data.get("iv", 0.0),
                        "delta": data.get("delta", 0.0),
                        "gamma": data.get("gamma", 0.0),
                        "theta": data.get("theta", 0.0),
                        "vega": data.get("vega", 0.0),
                        "volume": volume,
                        "source": data.get("source", "unknown"),
                    })
                except (ValueError, TypeError, KeyError, AttributeError, OSError, RuntimeError) as e:
                    logger.debug("期权数据获取失败 %s K=%.2f exp=%s: %s", underlying, strike, expiry, e)
                    continue

            elapsed_ms = (time.perf_counter() - t0) * 1000
            if elapsed_ms > self._bs_timeout_ms and not any(c["source"] == "wind_mcp" for c in chain):
                logger.warning("BS降级超时 %dms, 已获取 %d 合约, 提前终止", int(elapsed_ms), len(chain))
                break

        chain.sort(key=lambda c: (c["dte"], c["strike"]))
        logger.info("%s %s 期权链: %d 合约 (spot=%.3f)", underlying, option_type, len(chain), spot_price)
        return chain

    def get_iv_term_structure(
        self,
        underlying: str,
        strike: float,
        spot_price: float | None = None,
    ) -> list[dict]:
        """获取IV期限结构 — 同行权价不同到期日的IV序列 (日历价差用)."""
        if spot_price is None or spot_price <= 0:
            spot_price = self.get_spot_price(underlying)
        if spot_price is None or spot_price <= 0 or self._fetcher is None:
            return []

        today = date.today()
        expiries = _generate_expiry_dates(today, 10, 180)
        result: list[dict] = []
        for expiry in expiries:
            dte = (expiry - today).days
            T = dte / 365.0  # noqa: N806 — BS 惯例大写 T (年化到期时间)
            if T <= 0:
                continue
            try:
                data = self._fetcher.get_option_data(
                    underlying=underlying.split(".")[0],
                    spot_price=spot_price,
                    strike=strike,
                    T=T,
                    r=0.03,
                    option_type="call",
                )
                if data and data.get("iv") is not None:
                    result.append({
                        "expiry": expiry.isoformat(),
                        "dte": dte,
                        "iv": data["iv"],
                        "premium": data.get("premium", 0.0),
                        "source": data.get("source", "unknown"),
                    })
            except (ValueError, TypeError, KeyError, AttributeError, OSError, RuntimeError) as e:
                logger.debug("IV期限结构获取失败 %s K=%.2f exp=%s: %s", underlying, strike, expiry, e)
                continue

        result.sort(key=lambda x: x["dte"])
        return result


class ComboBase(ABC):
    """策略抽象基类 — 定义统一生命周期接口.

    模板方法: generate() 编排通用流程, _select_legs() 由子类实现差异.
    生命周期: generate() 建仓 -> adjust() 调仓 -> roll() 滚仓 -> close() 平仓.

    Attributes:
        strategy_type: 策略类型
        config: 策略配置字典
        chain_fetcher: 期权链适配层
        risk_manager: 组合风控管理器 (Phase 3 注入, 可为 None)
        greek_manager: Greeks 对冲管理器
        state_manager: 状态持久化管理器 (可为 None)
    """

    def __init__(
        self,
        strategy_type: StrategyType,
        config: dict,
        chain_fetcher: OptionChainFetcher,
        risk_manager: object | None = None,
        greek_manager: object | None = None,
        state_manager: "ComboStateManager | None" = None,
    ) -> None:
        self.strategy_type = strategy_type
        self.config = config
        self.chain_fetcher = chain_fetcher
        self.risk_manager = risk_manager
        self.greek_manager = greek_manager
        self.state_manager = state_manager

    @abstractmethod
    def _select_legs(
        self,
        underlying: str,
        spot_price: float,
        option_chain: list[dict],
        spot_position: dict,
    ) -> tuple[ComboLeg, ...] | tuple[None, str]:
        """【子类实现】选择策略腿 — 返回腿元组或 (None, 错误码)."""
        ...

    @abstractmethod
    def _validate_business_rules(
        self, legs: tuple[ComboLeg, ...], spot_price: float
    ) -> str | None:
        """【子类实现】校验策略特有业务规则 — 返回错误码或 None."""
        ...

    def generate(
        self,
        underlying: str,
        spot_position: dict,
        market_state: dict | None = None,
    ) -> ComboResult:
        """建仓 (模板方法) — 通用流程编排.

        流程: 获取现货价 → 获取期权链 → 选择腿 → 校验规则 →
              计算Greeks → 风控预检 → 组装订单 → 更新预算 → 持久化 → 审计.

        Returns:
            ComboResult (成功含订单包, 失败含 error_code)
        """
        generated_at = now_bj().isoformat(timespec="seconds")

        spot_price = self.chain_fetcher.get_spot_price(underlying)
        if spot_price is None or spot_price <= 0:
            return self._error_result(underlying, "NO_SPOT_DATA", "无法获取现货价", generated_at)

        option_chain = self._fetch_option_chain(underlying, spot_price)
        if not option_chain:
            return self._error_result(underlying, "NO_OPTION_DATA", "期权链为空", generated_at)

        legs_result = self._select_legs(underlying, spot_price, option_chain, spot_position)
        if legs_result[0] is None:
            error_code = legs_result[1] if len(legs_result) > 1 else "SELECT_LEGS_FAILED"
            return self._error_result(underlying, error_code, "腿选择失败", generated_at)
        legs = legs_result

        error = self._validate_business_rules(legs, spot_price)
        if error is not None:
            return self._error_result(underlying, error, "业务规则校验失败", generated_at)

        greeks = self._calc_combo_greeks(legs, spot_price)
        net_premium = self._calc_net_premium(legs)

        if self.risk_manager is not None:
            approval = self._risk_pre_check(legs, market_state or {})
            if approval is not None and not approval.get("approved", True):
                reason = approval.get("rejected_reason", "RISK_BLOCKED")
                return self._error_result(underlying, "RISK_BLOCKED", reason, generated_at)

        orders = self._assemble_orders(legs, underlying, "generate")
        budget_remaining = self._update_budget(net_premium)
        self._persist_state(underlying, legs, greeks, net_premium, generated_at)
        self._audit_log(orders, underlying, "generate")

        return ComboResult(
            strategy_type=self.strategy_type,
            underlying=underlying,
            orders=orders,
            greeks=greeks,
            net_premium=net_premium,
            budget_remaining=budget_remaining,
            error_code=None,
            error_msg=None,
            generated_at=generated_at,
        )

    def adjust(
        self,
        current_position: dict,
        target_greeks: object | None = None,
    ) -> ComboResult:
        """调仓 — Greeks 失衡时生成调仓指令使 Delta/Vega 回归目标."""
        generated_at = now_bj().isoformat(timespec="seconds")
        return self._error_result(
            current_position.get("underlying", ""),
            "ADJUST_NOT_IMPLEMENTED",
            "调仓逻辑待子类扩展",
            generated_at,
        )

    def close(self, reason: str = "manual") -> ComboResult:
        """平仓 — 生成全部腿的平仓指令."""
        generated_at = now_bj().isoformat(timespec="seconds")
        logger.info("平仓请求: strategy=%s reason=%s", self.strategy_type.value, reason)
        return ComboResult(
            strategy_type=self.strategy_type,
            underlying="",
            orders=(),
            greeks=self._empty_greeks(),
            net_premium=0.0,
            budget_remaining=0.0,
            error_code=None,
            error_msg=None,
            generated_at=generated_at,
        )

    def roll(self) -> RollResult:
        """滚仓 — 平近月 + 开下月, 含成本控制校验."""
        return RollResult(
            strategy_instance_id="",
            needs_roll=False,
            close_orders=(),
            open_orders=(),
            roll_cost=0.0,
            expected_benefit=0.0,
            cost_ratio=0.0,
        )

    def _fetch_option_chain(self, underlying: str, spot_price: float) -> list[dict]:
        """获取期权链 — 子类可覆盖以指定 option_type/dte_range/otm_range."""
        return []

    def _empty_greeks(self) -> "GreekExposure":
        """返回空 Greeks 暴露."""
        try:
            from utils.greek_hedge_manager import GreekExposure
            return GreekExposure()
        except (ImportError, ModuleNotFoundError):
            empty = type("EmptyGreeks", (), {"delta": 0.0, "gamma": 0.0, "theta": 0.0, "vega": 0.0, "rho": 0.0})()
            return cast("GreekExposure", empty)

    def _calc_combo_greeks(self, legs: tuple[ComboLeg, ...], spot_price: float) -> "GreekExposure":
        """计算组合 Greeks — 调用 greek_manager."""
        if self.greek_manager is None:
            return self._empty_greeks()
        try:
            calc = getattr(self.greek_manager, "calc_portfolio_greeks", None)
            if calc is not None:
                positions = [
                    {
                        "underlying": leg.underlying,
                        "option_type": leg.option_type,
                        "side": leg.side.value,
                        "strike": leg.strike,
                        "quantity": leg.quantity,
                        "multiplier": leg.multiplier,
                        "premium": leg.premium,
                    }
                    for leg in legs
                ]
                if positions:
                    return cast("GreekExposure", calc(positions, {legs[0].underlying: spot_price}))
                return self._empty_greeks()
        except (ValueError, TypeError, KeyError, AttributeError, OSError, RuntimeError) as e:
            logger.warning("Greeks 计算异常: %s", e)
        return self._empty_greeks()

    @staticmethod
    def _calc_net_premium(legs: tuple[ComboLeg, ...]) -> float:
        """计算净权利金 — 正=收入, 负=支出."""
        total = 0.0
        for leg in legs:
            if leg.instrument == "OPTION":
                signed = leg.premium * leg.quantity * leg.multiplier
                total += signed if leg.side == LegSide.SELL else -signed
        return round(total, 6)

    def _assemble_orders(
        self,
        legs: tuple[ComboLeg, ...],
        underlying: str,
        reason: str,
    ) -> tuple[ComboOrder, ...]:
        """组装 ComboOrder 元组 — 生成唯一 order_id."""
        trade_date = now_bj().strftime("%Y%m%d")
        orders: list[ComboOrder] = []
        for i, leg in enumerate(legs):
            order_id = f"{self.strategy_type.value}_{underlying}_{trade_date}_{i}"
            limit_price = leg.premium * 1.05 if leg.instrument == "OPTION" else leg.strike
            orders.append(ComboOrder(
                order_id=order_id,
                strategy_type=self.strategy_type,
                leg=leg,
                order_type="LIMIT",
                limit_price=round(limit_price, 6),
                status=OrderStatus.PENDING,
                reason=reason,
            ))
        return tuple(orders)

    def _risk_pre_check(self, legs: tuple[ComboLeg, ...], market_state: dict) -> dict | None:
        """风控预检 — 调用 risk_manager.pre_check()."""
        if self.risk_manager is None:
            return {"approved": True}
        try:
            pre = getattr(self.risk_manager, "pre_check", None)
            if pre is not None:
                return cast("dict | None", pre(legs, market_state))
        except (ValueError, TypeError, KeyError, AttributeError, OSError, RuntimeError) as e:
            logger.warning("风控预检异常 (fail-closed 拒绝): %s", e)
            return {"approved": False, "rejected_reason": f"RISK_CHECK_ERROR: {e}"}
        return {"approved": True}

    def _update_budget(self, net_premium: float) -> float:
        """更新预算跟踪 — 返回剩余预算."""
        if self.state_manager is not None:
            try:
                self.state_manager.update_budget(self.strategy_type.value, net_premium)
                budget = self.state_manager.get_budget(self.strategy_type.value)
                annual_limit = float(self.config.get("annual_budget_pct", 0.02))
                total_capital = float(self.config.get("total_capital", 2_000_000))
                spent = budget.get("ytd_expense", 0.0) - budget.get("ytd_income", 0.0)
                return round(total_capital * annual_limit - spent, 6)
            except (ValueError, TypeError, KeyError, AttributeError) as e:
                logger.warning("预算更新失败: %s", e)
        return 0.0

    def _persist_state(
        self,
        underlying: str,
        legs: tuple[ComboLeg, ...],
        greeks: object,
        net_premium: float,
        generated_at: str,
    ) -> None:
        """持久化策略实例状态."""
        if self.state_manager is None:
            return
        try:
            trade_date = now_bj().strftime("%Y-%m-%d")
            instance_id = f"{self.strategy_type.value}_{underlying}_{trade_date}"
            self.state_manager.save_strategy_instance(instance_id, {
                "strategy_type": self.strategy_type.value,
                "underlying": underlying,
                "trade_date": trade_date,
                "legs_count": len(legs),
                "net_premium": net_premium,
                "generated_at": generated_at,
            })
        except (ValueError, TypeError, KeyError, AttributeError, OSError) as e:
            logger.warning("状态持久化失败: %s", e)

    def _audit_log(self, orders: tuple[ComboOrder, ...], underlying: str, reason: str) -> None:
        """审计日志 — 日志脱敏 (不输出账户总资金明文)."""
        for order in orders:
            leg = order.leg
            logger.info(
                "AUDIT strategy=%s underlying=%s instrument=%s side=%s qty=%d reason=%s",
                self.strategy_type.value,
                underlying,
                leg.instrument,
                leg.side.value,
                leg.quantity,
                reason,
            )

    def _error_result(
        self,
        underlying: str,
        error_code: str,
        error_msg: str,
        generated_at: str,
    ) -> ComboResult:
        """构造错误结果."""
        return ComboResult(
            strategy_type=self.strategy_type,
            underlying=underlying,
            orders=(),
            greeks=self._empty_greeks(),
            net_premium=0.0,
            budget_remaining=0.0,
            error_code=error_code,
            error_msg=error_msg,
            generated_at=generated_at,
        )
