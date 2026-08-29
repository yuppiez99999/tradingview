"""
方向性期货交易模块 v1.0
========================

实现 v10.0 macro_hedge_account 中 CU(沪铜)/AU(黄金)/T(国债) 三个方向性品种的
信号生成 + 仓位计算 + 风控 + 调仓指令生成。

合约规格:
    - CU 沪铜:    5 吨/手,    保证金 9%,    最小变动 10 元/吨
    - AU 黄金:    1000 克/手, 保证金 6%,    最小变动 0.02 元/克
    - T  10年国债: 面值 100 万/手, 保证金 2%, 最小变动 0.005 元

信号源:
    - 技术指标: MA20/MA60 趋势 + RSI 超买超卖 + MACD 动量
    - 基本面 (可选): 新能源需求/库存周期/实际利率/避险情绪/通胀预期

风控:
    - 单笔最大亏损 20% (保证金视角)
    - 日最大亏损 15% (账户视角)
    - 周连续亏损 25% → 暂停 7 天

用法:
    from utils.directional_futures_trader import DirectionalFuturesTrader
    trader = DirectionalFuturesTrader()
    signals = trader.generate_signals(market_data)
    orders = trader.generate_orders(signals, current_positions, trade_date)
"""

from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass, field
from datetime import date, timedelta
from pathlib import Path
from typing import Any

# W6.3.3 Step 3: 统一合约规格注册表入口 (替代 CONTRACT_SPECS dict + ~10 处 type: ignore)
from utils.contracts.registry import ContractSpec, default_registry

logger = logging.getLogger("directional_futures")

# ============================================================
# v10.0 配置常量 (来自 auto_trade_plan_v10_十五五.json)
# ============================================================
FUTURES_MARGIN_CAPITAL = 500_000  # 期货账户总资金
FUTURES_MARGIN_MAX_PCT = 0.60  # 保证金占用上限 60%

# 单品种资金分配 (3 个方向性品种: CU/AU/T)
PER_SYMBOL_MARGIN_BUDGET = FUTURES_MARGIN_CAPITAL * 0.30  # 每品种最多 15 万保证金

# 风控阈值
MAX_SINGLE_TRADE_LOSS_PCT = 0.20  # 单笔最大亏损 20%
DAILY_MAX_LOSS_PCT = 0.15  # 日最大亏损 15%
WEEKLY_CONSECUTIVE_LOSS_MAX_PCT = 0.25  # 周连续亏损 25%
LOSS_PAUSE_DAYS = 7  # 连续亏损暂停 7 天

# 合约规格
# W6.3.3 Step 3: 真实来源为 utils.contracts.registry.default_registry
# (3 来源整合 13 品种, 见 registry.py)。保留 CONTRACT_SPECS 为兼容层 (生成自注册表),
# 避免外部模块直接 import CONTRACT_SPECS 的引用被破坏。
# 原硬编码 dict: CU/AU/T 9 字段已与 registry 全对齐验证 (test_registry.py)
_CONTRACT_SPECS_SOURCES: list[
    tuple[str, str, str, float, float, float, str, str, str]
] = [
    ("CU", "沪铜期货", "SHFE", 5, 0.09, 10, "元/吨", "new_energy_demand", "long"),
    ("AU", "黄金期货", "SHFE", 1000, 0.06, 0.02, "元/克", "safe_haven", "long"),
    (
        "T",
        "10年国债期货",
        "CFFEX",
        10_000,
        0.02,
        0.005,
        "元",
        "rate_directional",
        "short",
    ),
]

CONTRACT_SPECS: dict[str, dict[str, Any]] = {
    _prod: {
        "name": _name,
        "exchange": _exc,
        "multiplier": _mult,
        "margin_rate": _mrg,
        "tick_size": _tick,
        "price_unit": _pun,
        "purpose": _purp,
        "default_direction": _dir,
    }
    for (
        _prod,
        _name,
        _exc,
        _mult,
        _mrg,
        _tick,
        _pun,
        _purp,
        _dir,
    ) in _CONTRACT_SPECS_SOURCES
}


def _get_spec(symbol: str) -> ContractSpec:
    """按品种代码查询合约规格 (类型安全, 替代 CONTRACT_SPECS dict 索引)。

    保证: symbol ∈ CONTRACT_SPECS.keys() 时永不返回 None, 与旧 dict key 范围一致。
    未注册品种抛出 SymbolParseError 风格错误 (严格门禁)。
    """
    spec = default_registry.lookup(symbol)
    if spec is None:
        raise KeyError(
            f"[directional_futures_trader] 未注册期货品种 {symbol!r}: "
            f"已注册列表 = {default_registry.all_products()}"
        )
    return spec


# ============================================================
# 数据类
# ============================================================
@dataclass
class FuturesSignal:
    """单品种期货信号"""

    symbol: str
    name: str
    direction: str = "flat"  # long / short / flat
    strength: float = 0.0  # 信号强度 [-1, 1]
    ma20: float = 0.0
    ma60: float = 0.0
    rsi: float = 50.0
    macd_hist: float = 0.0
    rationale: str = ""  # 信号依据
    confidence: float = 0.0  # 置信度 [0, 1]


@dataclass
class FuturesOrder:
    """单品种期货交易指令"""

    symbol: str
    name: str
    exchange: str
    action: str = (
        "hold"  # open_long / open_short / close_long / close_short / add / reduce / hold
    )
    direction: str = "flat"  # long / short / flat
    contracts: int = 0
    price: float = 0.0
    notional_value: float = 0.0
    required_margin: float = 0.0
    stop_loss: float = 0.0  # 止损价
    take_profit: float = 0.0  # 止盈价
    trade_date: str = ""
    rationale: str = ""


@dataclass
class DirectionalFuturesResult:
    """方向性期货组合结果"""

    trade_date: str = ""
    action: str = "skip"  # trade / skip / pause (供 daily_workflow 消费)
    signals: list[FuturesSignal] = field(default_factory=list)
    orders: list[FuturesOrder] = field(default_factory=list)
    total_margin_used: float = 0.0
    total_notional: float = 0.0
    margin_usage_ratio: float = 0.0
    risk_status: str = "normal"  # normal / warning / paused
    pause_until: str | None = None
    summary_text: str = ""


# ============================================================
# 主交易器
# ============================================================
class DirectionalFuturesTrader:
    """方向性期货交易器

    管理 CU/AU/T 三个品种的方向性头寸, 基于技术指标 + 基本面生成信号,
    并按风控规则生成交易指令。
    """

    def __init__(
        self,
        capital: float = FUTURES_MARGIN_CAPITAL,
        per_symbol_budget: float = PER_SYMBOL_MARGIN_BUDGET,
        max_margin_pct: float = FUTURES_MARGIN_MAX_PCT,
    ):
        self.capital = capital
        self.per_symbol_budget = per_symbol_budget
        self.max_margin_pct = max_margin_pct
        self.symbols = list(CONTRACT_SPECS.keys())

    # ------------------------------------------------------------
    # 信号生成
    # ------------------------------------------------------------
    def generate_signals(
        self,
        market_data: dict[str, dict[str, Any]],
        fundamental_factors: dict[str, dict] | None = None,
    ) -> list[FuturesSignal]:
        """生成方向性期货信号

        Args:
            market_data: {symbol: {"closes": [价格序列], "volumes": [成交量]}}
            fundamental_factors: {symbol: {因子: 值}} (可选)

        Returns:
            信号列表
        """
        signals = []
        for symbol in self.symbols:
            spec = _get_spec(symbol)
            data = market_data.get(symbol, {})
            closes = data.get("closes", [])

            signal = FuturesSignal(
                symbol=symbol,
                name=spec.name,
            )

            if len(closes) < 60:
                signal.direction = "flat"
                signal.rationale = f"数据不足 ({len(closes)} < 60)"
                signals.append(signal)
                continue

            # 计算技术指标
            ma20 = sum(closes[-20:]) / 20
            ma60 = sum(closes[-60:]) / 60
            rsi = self._calc_rsi(closes, 14)
            macd_hist = self._calc_macd_hist(closes)

            signal.ma20 = ma20
            signal.ma60 = ma60
            signal.rsi = rsi
            signal.macd_hist = macd_hist

            # 信号方向: 趋势 + 动量 + RSI 三重确认
            trend = 1 if ma20 > ma60 else -1
            momentum = 1 if macd_hist > 0 else -1
            rsi_signal = 1 if rsi > 50 else -1

            # 默认方向 (品种属性)
            default_dir = spec.default_direction
            default_factor = 1 if default_dir == "long" else -1

            # 综合方向投票 (3 票技术 + 1 票品种属性)
            votes = trend + momentum + rsi_signal + default_factor * 0.5
            if votes >= 2:
                signal.direction = "long"
            elif votes <= -2:
                signal.direction = "short"
            else:
                signal.direction = "flat"

            # 信号强度
            strength = abs(votes) / 3.5
            signal.strength = round(min(1.0, strength), 3)

            # 置信度
            confirm_count = sum(
                [
                    1
                    for v in [trend, momentum, rsi_signal]
                    if v == (1 if signal.direction == "long" else -1)
                ]
            )
            signal.confidence = round(confirm_count / 3, 3)

            # 基本面因子 (可选)
            fund = (fundamental_factors or {}).get(symbol, {})
            fund_text = ""
            if fund:
                if symbol == "CU":
                    demand = fund.get("new_energy_demand", 0)
                    inventory = fund.get("inventory_cycle", 0)
                    fund_text = f" 新能源需求 {demand:+.2f}, 库存周期 {inventory:+.2f}"
                elif symbol == "AU":
                    real_rate = fund.get("real_rate", 0)
                    safe_haven = fund.get("safe_haven", 0)
                    fund_text = f" 实际利率 {real_rate:+.2f}, 避险 {safe_haven:+.2f}"
                elif symbol == "T":
                    inflation = fund.get("inflation_expectation", 0)
                    monetary = fund.get("monetary_policy", 0)
                    fund_text = f" 通胀预期 {inflation:+.2f}, 货币政策 {monetary:+.2f}"

            signal.rationale = (
                f"MA20 {ma20:.2f} vs MA60 {ma60:.2f} (trend {trend:+d}), "
                f"RSI {rsi:.1f} ({'超买' if rsi > 70 else '超卖' if rsi < 30 else '中性'}), "
                f"MACD hist {macd_hist:+.4f} (momentum {momentum:+d})."
                f"{fund_text}"
            )

            signals.append(signal)

        return signals

    # ------------------------------------------------------------
    # 指标计算
    # ------------------------------------------------------------
    def _calc_rsi(self, closes: list[float], period: int = 14) -> float:
        """计算 RSI"""
        if len(closes) < period + 1:
            return 50.0

        gains, losses = [], []
        for i in range(-period, 0):
            diff = closes[i] - closes[i - 1]
            gains.append(max(0, diff))
            losses.append(max(0, -diff))

        avg_gain = sum(gains) / period
        avg_loss = sum(losses) / period
        if avg_loss == 0:
            return 100.0
        rs = avg_gain / avg_loss
        return round(100 - (100 / (1 + rs)), 2)

    def _calc_macd_hist(self, closes: list[float]) -> float:
        """计算 MACD 柱状图 (12,26,9)"""
        if len(closes) < 35:
            return 0.0

        ema12 = self._calc_ema(closes, 12)
        ema26 = self._calc_ema(closes, 26)
        macd_line = ema12 - ema26
        # 简化信号线 = EMA9 of MACD (用最近 9 期 macd 近似)
        macd_series = []
        for i in range(-9, 0):
            if abs(i) + 26 <= len(closes):
                e12 = self._calc_ema(
                    closes[: len(closes) + i + 1] if i < -1 else closes, 12
                )
                e26 = self._calc_ema(
                    closes[: len(closes) + i + 1] if i < -1 else closes, 26
                )
                macd_series.append(e12 - e26)
        signal_line = sum(macd_series) / len(macd_series) if macd_series else macd_line
        return round(macd_line - signal_line, 6)

    def _calc_ema(self, closes: list[float], period: int) -> float:
        """计算 EMA"""
        if len(closes) < period:
            return sum(closes) / len(closes) if closes else 0
        multiplier = 2 / (period + 1)
        ema = closes[-period]
        for price in closes[-period + 1 :]:
            ema = price * multiplier + ema * (1 - multiplier)
        return ema

    # ------------------------------------------------------------
    # 仓位计算
    # ------------------------------------------------------------
    def calculate_position(
        self,
        symbol: str,
        signal: FuturesSignal,
        current_price: float,
    ) -> tuple[int, float, float]:
        """计算单品种合约数与所需保证金

        Returns:
            (contracts, notional_value, required_margin)
        """
        spec = _get_spec(symbol)
        multiplier = spec.multiplier
        margin_rate = spec.margin_rate

        if signal.direction == "flat" or signal.strength < 0.3:
            return 0, 0.0, 0.0

        # 名义价值 = 价格 × 合约乘数
        # 保证金 = 名义价值 × 保证金率
        # 按信号强度分配资金
        budget = self.per_symbol_budget * signal.strength
        one_contract_margin = current_price * multiplier * margin_rate
        if one_contract_margin <= 0:
            return 0, 0.0, 0.0

        contracts = max(1, int(budget // one_contract_margin))
        notional = contracts * current_price * multiplier
        margin = notional * margin_rate
        return contracts, notional, margin

    # ------------------------------------------------------------
    # 风控检查
    # ------------------------------------------------------------
    def check_risk(
        self,
        current_positions: dict[str, dict],
        daily_pnl_pct: float = 0.0,
        weekly_consecutive_loss_pct: float = 0.0,
        last_loss_pause_date: date | None = None,
    ) -> tuple[str, date | None]:
        """风控检查

        Returns:
            (status, pause_until)
            status: normal / warning / paused
        """
        today = date.today()

        # 暂停期内
        if last_loss_pause_date is not None:
            pause_until = last_loss_pause_date + timedelta(days=LOSS_PAUSE_DAYS)
            if today < pause_until:
                return "paused", pause_until

        # 周连续亏损超限 → 暂停
        if weekly_consecutive_loss_pct >= WEEKLY_CONSECUTIVE_LOSS_MAX_PCT:
            pause_until = today + timedelta(days=LOSS_PAUSE_DAYS)
            return "paused", pause_until

        # 日最大亏损超限 → 警告
        if daily_pnl_pct <= -DAILY_MAX_LOSS_PCT:
            return "warning", None

        return "normal", None

    # ------------------------------------------------------------
    # 指令生成
    # ------------------------------------------------------------
    def generate_orders(
        self,
        signals: list[FuturesSignal],
        current_positions: dict[str, dict],
        prices: dict[str, float],
        trade_date: date,
        risk_status: str = "normal",
    ) -> list[FuturesOrder]:
        """生成交易指令

        Args:
            signals: 信号列表
            current_positions: {symbol: {"direction": "long"/"short", "contracts": int, "entry_price": float}}
            prices: {symbol: 当前价格}
            trade_date: 交易日期
            risk_status: 风控状态

        Returns:
            交易指令列表
        """
        orders = []

        if risk_status == "paused":
            # 暂停状态: 平掉所有持仓
            for symbol in self.symbols:
                pos = current_positions.get(symbol, {})
                if pos.get("contracts", 0) > 0:
                    action = (
                        "close_long"
                        if pos.get("direction") == "long"
                        else "close_short"
                    )
                    orders.append(
                        self._build_close_order(
                            symbol,
                            pos,
                            prices.get(symbol, 0),
                            trade_date,
                            "风控暂停强制平仓",
                        )
                    )
            return orders

        for signal in signals:
            symbol = signal.symbol
            spec = _get_spec(symbol)
            price = prices.get(symbol, 0)
            current = current_positions.get(symbol, {})
            current_dir = current.get("direction", "flat")
            current_contracts = current.get("contracts", 0)
            current.get("entry_price", 0)

            if price <= 0:
                continue

            # 目标仓位
            target_contracts, notional, margin = self.calculate_position(
                symbol, signal, price
            )
            target_dir = signal.direction

            # 止损止盈 (基于信号强度)
            stop_loss, take_profit = self._calc_stops(
                symbol, price, target_dir, signal.strength
            )

            if current_dir == target_dir:
                # 方向一致: 调整仓位
                delta = target_contracts - current_contracts
                if delta > 0:
                    action = "add"
                elif delta < 0:
                    action = "reduce"
                else:
                    action = "hold"
                contracts = abs(delta)
            elif target_dir == "flat":
                # 目标空仓: 平仓
                action = "close_long" if current_dir == "long" else "close_short"
                contracts = current_contracts
            elif current_dir == "flat":
                # 当前空仓, 目标有方向: 开仓
                action = "open_long" if target_dir == "long" else "open_short"
                contracts = target_contracts
            else:
                # 方向反转 (long→short 或 short→long): 先平后开 (合并为一条指令)
                action = "reverse"
                contracts = target_contracts

            order = FuturesOrder(
                symbol=symbol,
                name=spec.name,
                exchange=spec.exchange,
                action=action,
                direction=target_dir,
                contracts=contracts,
                price=price,
                notional_value=notional,
                required_margin=margin,
                stop_loss=stop_loss,
                take_profit=take_profit,
                trade_date=trade_date.isoformat(),
                rationale=signal.rationale,
            )
            orders.append(order)

        return orders

    def _build_close_order(
        self,
        symbol: str,
        position: dict,
        price: float,
        trade_date: date,
        reason: str,
    ) -> FuturesOrder:
        """构建平仓指令"""
        spec = _get_spec(symbol)
        return FuturesOrder(
            symbol=symbol,
            name=spec.name,
            exchange=spec.exchange,
            action=(
                "close_long" if position.get("direction") == "long" else "close_short"
            ),
            direction="flat",
            contracts=position.get("contracts", 0),
            price=price,
            notional_value=position.get("contracts", 0) * price * spec.multiplier,
            required_margin=0,
            trade_date=trade_date.isoformat(),
            rationale=reason,
        )

    def _calc_stops(
        self,
        symbol: str,
        entry_price: float,
        direction: str,
        strength: float,
    ) -> tuple[float, float]:
        """计算止损止盈

        止损: 信号强度越强, 止损越宽 (允许更多波动)
        止盈: 止盈为止损的 2 倍 (1:2 风险回报比)
        """
        if direction == "flat" or entry_price <= 0:
            return 0.0, 0.0

        # 基础止损幅度 3-6% (信号越强, 止损越宽)
        stop_pct = 0.03 + 0.03 * strength

        if direction == "long":
            stop_loss = entry_price * (1 - stop_pct)
            take_profit = entry_price * (1 + stop_pct * 2)
        else:
            stop_loss = entry_price * (1 + stop_pct)
            take_profit = entry_price * (1 - stop_pct * 2)

        return round(stop_loss, 4), round(take_profit, 4)

    # ------------------------------------------------------------
    # 主流程
    # ------------------------------------------------------------
    def run(
        self,
        market_data: dict[str, dict[str, Any]],
        current_positions: dict[str, dict],
        prices: dict[str, float],
        trade_date: date,
        fundamental_factors: dict[str, dict] | None = None,
        daily_pnl_pct: float = 0.0,
        weekly_consecutive_loss_pct: float = 0.0,
        last_loss_pause_date: date | None = None,
    ) -> DirectionalFuturesResult:
        """方向性期货主流程

        Args:
            market_data: {symbol: {"closes": [...], "volumes": [...]}}
            current_positions: {symbol: {"direction": "long"/"short", "contracts": int, "entry_price": float}}
            prices: {symbol: 当前价格}
            trade_date: 交易日期
            fundamental_factors: 基本面因子 (可选)
            daily_pnl_pct: 当日盈亏百分比
            weekly_consecutive_loss_pct: 周连续亏损百分比
            last_loss_pause_date: 上次暂停日期

        Returns:
            DirectionalFuturesResult
        """
        result = DirectionalFuturesResult(trade_date=trade_date.isoformat())

        # 1. 风控检查
        risk_status, pause_until = self.check_risk(
            current_positions=current_positions,
            daily_pnl_pct=daily_pnl_pct,
            weekly_consecutive_loss_pct=weekly_consecutive_loss_pct,
            last_loss_pause_date=last_loss_pause_date,
        )
        result.risk_status = risk_status
        result.pause_until = pause_until.isoformat() if pause_until else None

        # 2. 信号生成 (暂停状态也生成, 但不执行新开仓)
        signals = self.generate_signals(market_data, fundamental_factors)
        result.signals = signals

        # 3. 指令生成
        orders = self.generate_orders(
            signals, current_positions, prices, trade_date, risk_status
        )
        result.orders = orders

        # 4. 统计
        result.total_margin_used = sum(
            o.required_margin
            for o in orders
            if o.action in ("open_long", "open_short", "add", "reverse")
        )
        result.total_notional = sum(
            o.notional_value
            for o in orders
            if o.action in ("open_long", "open_short", "add", "reverse")
        )
        result.margin_usage_ratio = (
            result.total_margin_used / self.capital if self.capital > 0 else 0
        )

        # 4.1 设置 action 字段 (供 daily_workflow 消费)
        if risk_status == "paused":
            result.action = "pause"
        elif any(
            o.action
            in (
                "open_long",
                "open_short",
                "close_long",
                "close_short",
                "add",
                "reduce",
                "reverse",
            )
            for o in orders
        ):
            result.action = "trade"
        else:
            result.action = "skip"

        # 5. 摘要
        result.summary_text = self._build_summary(result)

        # 6. 持久化
        self._save_report(result, trade_date)

        logger.info(
            f"[DirectionalFutures] {trade_date} 风控: {risk_status}, "
            f"信号: {[(s.symbol, s.direction, s.strength) for s in signals]}, "
            f"指令: {len(orders)} 条, 保证金 ¥{result.total_margin_used:,.0f} ({result.margin_usage_ratio:.1%})"
        )

        return result

    def _build_summary(self, result: DirectionalFuturesResult) -> str:
        """生成结果摘要"""
        lines = [
            "=" * 60,
            f"方向性期货组合报告 ({result.trade_date})",
            "=" * 60,
            f"风控状态: {result.risk_status}",
        ]
        if result.pause_until:
            lines.append(f"暂停至: {result.pause_until}")

        lines.extend(
            [
                "",
                "信号:",
            ]
        )
        for s in result.signals:
            lines.append(
                f"  {s.symbol} ({s.name}): {s.direction} (强度 {s.strength:.2f}, 置信 {s.confidence:.2f})"
            )

        lines.extend(
            [
                "",
                "指令:",
            ]
        )
        for o in result.orders:
            lines.append(
                f"  {o.symbol} {o.action} {o.contracts} 张 @ {o.price:.2f}, "
                f"保证金 ¥{o.required_margin:,.0f}, "
                f"止损 {o.stop_loss:.2f}, 止盈 {o.take_profit:.2f}"
            )

        lines.extend(
            [
                "",
                f"总保证金占用: ¥{result.total_margin_used:,.0f} ({result.margin_usage_ratio:.1%})",
                f"总名义价值: ¥{result.total_notional:,.0f}",
                "=" * 60,
            ]
        )
        return "\n".join(lines)

    def summary(self, result: DirectionalFuturesResult) -> str:
        """公开摘要接口 (供 daily_workflow 调用)

        Args:
            result: DirectionalFuturesResult 对象

        Returns:
            摘要字符串
        """
        # 优先返回已构建的 summary_text
        if result.summary_text:
            return result.summary_text
        return self._build_summary(result)

    def _save_report(self, result: DirectionalFuturesResult, trade_date: date) -> None:
        """保存报告"""
        try:
            report_dir = Path(__file__).resolve().parent.parent / "reports"
            report_dir.mkdir(parents=True, exist_ok=True)
            report_path = (
                report_dir / f"directional_futures_{trade_date.isoformat()}.json"
            )

            report_data = {
                "trade_date": result.trade_date,
                "risk_status": result.risk_status,
                "pause_until": result.pause_until,
                "signals": [asdict(s) for s in result.signals],
                "orders": [asdict(o) for o in result.orders],
                "total_margin_used": result.total_margin_used,
                "total_notional": result.total_notional,
                "margin_usage_ratio": result.margin_usage_ratio,
            }
            with open(report_path, "w", encoding="utf-8") as f:
                json.dump(report_data, f, ensure_ascii=False, indent=2, default=str)
            logger.info(f"[DirectionalFutures] 报告已保存: {report_path}")
        except (
            ValueError,
            TypeError,
            KeyError,
            AttributeError,
            RuntimeError,
            OSError,
            TimeoutError,
            ConnectionError,
        ) as e:  # P2 模块 fail-safe, 待后续精确化
            logger.warning(f"[DirectionalFutures] 报告保存失败: {e}")


# ============================================================
# CLI 入口
# ============================================================
if __name__ == "__main__":
    import random

    logging.basicConfig(level=logging.INFO, format="%(message)s")

    trader = DirectionalFuturesTrader()

    # 模拟市场数据
    random.seed(42)
    market_data: dict[str, dict[str, list[float]]] = {}
    for symbol in trader.symbols:
        base_price: float = {"CU": 75000, "AU": 550, "T": 100}[symbol]
        closes: list[float] = [base_price]
        for _ in range(60):
            closes.append(closes[-1] * (1 + random.uniform(-0.02, 0.025)))
            market_data[symbol] = {"closes": closes}

    prices: dict[str, float] = {
        symbol: data["closes"][-1] for symbol, data in market_data.items()
    }

    result = trader.run(
        market_data=market_data,
        current_positions={},
        prices=prices,
        trade_date=date(2026, 7, 14),
    )

    logger.info(result.summary_text)
