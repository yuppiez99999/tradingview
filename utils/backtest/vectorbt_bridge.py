"""W6.4.1 vectorbt 向量化回测对照桥接模块。

职责:
    - 桥接 G15 事件驱动引擎 (utils/backtest/event_driven_engine.py)
      与 vectorbt 向量化回测 (https://github.com/polakowo/vectorbt)
    - 在相同输入数据 + 相同策略 (MA 交叉) 下对照两个引擎的输出
    - 验证 final_equity / total_return 偏差 <5% (W6.4.1 验收标准)

语义对齐 (关键):
    G15 (FixedLatency(0) + BAR 撮合):
        - 策略在 on_bar(bar_T) 中提交 MARKET 订单
        - 订单进入延迟队列 (remaining_latency=0)
        - 下一事件 _drain_latency_queue_to_matchable: 0-1=-1 ≤0 → 就绪
        - T+1 bar 的 _match_ready_orders: 以 bar.open 成交
        → "信号 T 收盘 → 成交 T+1 open"

    vectorbt (from_signals + 信号 shift(1)):
        - 信号在 bar T 收盘价上生成
        - signals.shift(1) 延迟 1 bar
        - entry_price="open" + exit_price="open"
        → "信号 T 收盘 → 成交 T+1 open"

    两者语义完全对齐, 偏差来源仅为:
        a) G15 手续费: commission_rate * fill_value (买卖双边)
        b) vectorbt 手续费: fees 参数 (买卖双边)
        c) 数值精度差异 (float64 vs float64)

设计原则 (AGENTS.md):
    - 不可变性 (§5.1): 对比结果为 dataclass, 不修改入参
    - 单一职责: 只做桥接 + 对比, 不替代任一引擎
    - 多小文件 (§5.3): 本模块 < 400 行
"""

from __future__ import annotations

from dataclasses import dataclass, field

import pandas as pd

from utils.backtest.adapters import (
    OrderSubmitter,
    StrategyAdapter,
    _EngineBackedHedgeContext,
)
from utils.backtest.event_driven_engine import EngineSummary, EventDrivenEngine
from utils.backtest.latency_model import FixedLatency
from utils.backtest.matching_engine import MatchingEngine
from utils.datetime_utils import now_bj
from utils.wt_hedge_strategy import HedgeContext, HedgeStrategy
from utils.wt_structs import BarData, OrderData

# ============================================================
# 1. 扩展上下文: 增加普通多空订单提交能力
# ============================================================


class _LongShortContext(_EngineBackedHedgeContext):
    """扩展 _EngineBackedHedgeContext, 增加 buy/sell 方法。

    G15 的 HedgeContext 仅有 open_hedge/close_hedge (SELL OPEN / BUY CLOSE),
    用于对冲场景。本类增加 buy (BUY OPEN) / sell (SELL CLOSE) 方法,
    支持普通多空策略, 以便与 vectorbt 的 entries/exits 对齐。

    订单类型: MARKET (与 vectorbt from_signals 默认行为一致)
    成交价: 由引擎在 T+1 bar 以 bar.open 撮合 (matching_engine BAR 模式)
    """

    def buy(self, code: str, volume: float, price: float | None = None) -> bool:
        """提交 BUY OPEN MARKET 订单 (开多 / 平空后开多)。

        Args:
            code: 标的代码
            volume: 手数 (>0)
            price: 委托价 (None 时用 current_prices[code], MARKET 模式下仅用于占位)

        Returns: True=成功入队
        """
        if volume <= 0:
            return False
        ref_price = price or self.current_prices.get(code, 0.0)
        if ref_price <= 0:
            return False
        order = self._create_market_order(code, "BUY", "OPEN", volume, ref_price)
        return self._order_submitter(order)

    def sell(self, code: str, volume: float, price: float | None = None) -> bool:
        """提交 SELL CLOSE MARKET 订单 (平多)。

        Args:
            code: 标的代码
            volume: 手数 (>0)
            price: 委托价 (None 时用 current_prices[code])

        Returns: True=成功入队
        """
        if volume <= 0:
            return False
        ref_price = price or self.current_prices.get(code, 0.0)
        if ref_price <= 0:
            return False
        order = self._create_market_order(code, "SELL", "CLOSE", volume, ref_price)
        return self._order_submitter(order)

    @staticmethod
    def _create_market_order(
        code: str, direction: str, offset: str, volume: float, price: float
    ) -> OrderData:
        """创建 MARKET 订单 (复用 adapters._create_hedge_order 的结构)。"""
        import uuid

        exchange = code.split(".")[-1] if "." in code else "UNKNOWN"
        return OrderData(
            order_id=f"ls_{uuid.uuid4().hex[:12]}",
            code=code,
            exchange=exchange,
            direction=direction,
            offset=offset,
            order_type="MARKET",
            price=price,
            volume=volume,
            timestamp=now_bj().timestamp(),
            datetime_str=now_bj().isoformat(),
        )


# ============================================================
# 2. MA 交叉策略 — 使用预计算信号
# ============================================================


class _MACrossStrategy(HedgeStrategy):
    """MA 交叉策略 — 用于 vectorbt 对照验证。

    不自行计算 MA, 而是接收预计算的信号列表, 确保与 vectorbt 使用完全相同信号。
    在 on_bar 中按信号提交 buy/sell 订单。

    仓位管理 (固定手数, 与 vectorbt size=N 对齐):
        - BUY 信号: 买入 position_size 股 (若空仓)
        - SELL 信号: 全部平仓 (若持仓)
        - HOLD: 无操作

    使用固定手数而非全仓估算, 避免两引擎间现金跟踪差异,
    确保偏差来源仅为手续费 + 成交价时点。
    """

    def __init__(
        self,
        signals: list[str],
        target_code: str,
        position_size: float = 1000.0,
    ) -> None:
        """
        Args:
            signals: 信号列表 ["BUY"/"SELL"/"HOLD", ...], 长度 = bar 数
            target_code: 目标标的代码
            position_size: 每次买入手数 (固定, 与 vectorbt size 参数对齐)
        """
        super().__init__(name="ma_cross_bridge", config={})
        self._signals = list(signals)
        self._target_code = target_code
        self._position_size = position_size
        self._bar_index = 0
        self._held_volume: float = 0.0

    def on_rebalance(self, ctx: HedgeContext) -> None:
        pass

    def on_bar(self, ctx: HedgeContext, bar: BarData) -> None:
        if bar.code != self._target_code:
            return
        if self._bar_index >= len(self._signals):
            return

        signal = self._signals[self._bar_index]
        self._bar_index += 1

        ls_ctx = ctx  # type: _LongShortContext

        if signal == "BUY" and self._held_volume == 0:
            success = ls_ctx.buy(
                self._target_code, self._position_size, price=bar.close
            )
            if success:
                self._held_volume = self._position_size

        elif signal == "SELL" and self._held_volume > 0:
            success = ls_ctx.sell(self._target_code, self._held_volume, price=bar.close)
            if success:
                self._held_volume = 0.0


class _LongShortAdapter(StrategyAdapter):
    """使用 _LongShortContext 的策略适配器。"""

    def __init__(
        self, strategy: HedgeStrategy, order_submitter: OrderSubmitter
    ) -> None:
        self.strategy = strategy
        self.context = _LongShortContext(strategy, order_submitter)


# ============================================================
# 3. 信号生成工具
# ============================================================


def generate_ma_cross_signals(
    closes: pd.Series,
    fast_window: int = 5,
    slow_window: int = 20,
) -> list[str]:
    """生成 MA 交叉信号列表。

    Args:
        closes: 收盘价序列
        fast_window: 快线窗口
        slow_window: 慢线窗口

    Returns:
        信号列表 ["BUY"/"SELL"/"HOLD", ...], 长度 = len(closes)
        - 快线从下方穿越慢线 → BUY
        - 快线从上方穿越慢线 → SELL
        - 其他 → HOLD
    """
    fast_ma = closes.rolling(window=fast_window, min_periods=1).mean()
    slow_ma = closes.rolling(window=slow_window, min_periods=1).mean()

    # 交叉检测: 快线 - 慢线 的符号变化
    diff = fast_ma - slow_ma
    diff_prev = diff.shift(1)

    cross_up = (diff > 0) & (diff_prev <= 0)  # 金叉
    cross_down = (diff < 0) & (diff_prev >= 0)  # 死叉

    signals: list[str] = []
    for i in range(len(closes)):
        if i < slow_window - 1:
            signals.append("HOLD")  # 慢线未形成, 不交易
        elif cross_up.iloc[i]:
            signals.append("BUY")
        elif cross_down.iloc[i]:
            signals.append("SELL")
        else:
            signals.append("HOLD")

    return signals


# ============================================================
# 4. 对比报告数据类
# ============================================================


@dataclass
class ComparisonReport:
    """G15 vs vectorbt 对照报告。

    Attributes:
        g15_final_equity: G15 引擎最终权益
        vbt_final_equity: vectorbt 最终权益
        equity_deviation_pct: 最终权益偏差百分比 (abs(g15 - vbt) / vbt * 100)
        g15_total_return: G15 总收益率
        vbt_total_return: vectorbt 总收益率
        return_deviation_pct: 总收益率偏差百分比
        passed: 是否通过 <5% 偏差门禁
        n_signals: 信号总数
        n_buy_signals: BUY 信号数
        n_sell_signals: SELL 信号数
        threshold_pct: 偏差门禁阈值 (默认 5.0)
    """

    g15_final_equity: float
    vbt_final_equity: float
    equity_deviation_pct: float
    g15_total_return: float
    vbt_total_return: float
    return_deviation_pct: float
    passed: bool
    n_signals: int
    n_buy_signals: int
    n_sell_signals: int
    threshold_pct: float = 5.0
    notes: list[str] = field(default_factory=list)

    def summary(self) -> str:
        status = "✅ PASS" if self.passed else "❌ FAIL"
        return (
            f"[{status}] G15 vs vectorbt 对照\n"
            f"  G15  final_equity={self.g15_final_equity:,.2f}  return={self.g15_total_return:.4%}\n"
            f"  VBT  final_equity={self.vbt_final_equity:,.2f}  return={self.vbt_total_return:.4%}\n"
            f"  权益偏差={self.equity_deviation_pct:.3f}%  收益偏差={self.return_deviation_pct:.3f}%"
            f"  (门禁 <{self.threshold_pct:.1f}%)\n"
            f"  信号: {self.n_signals} 总 / {self.n_buy_signals} BUY / {self.n_sell_signals} SELL"
        )


# ============================================================
# 5. 主桥接器
# ============================================================


class VectorBtBridge:
    """桥接 G15 事件驱动引擎与 vectorbt 向量化回测。

    使用示例:
        bridge = VectorBtBridge(initial_capital=1_000_000, commission_rate=0.0003)
        report = bridge.run_ma_cross_comparison(
            bars=bars, closes=closes, opens=opens,
            fast_window=5, slow_window=20,
        )
        print(report.summary())
        assert report.passed  # 偏差 <5%
    """

    def __init__(
        self,
        initial_capital: float = 1_000_000.0,
        commission_rate: float = 0.0003,
        threshold_pct: float = 5.0,
        position_size: float = 1000.0,
    ) -> None:
        """
        Args:
            initial_capital: 初始资金
            commission_rate: 手续费率 (买卖双边, 与 EventDrivenEngine 默认对齐)
            threshold_pct: 偏差门禁阈值 (默认 5.0%)
            position_size: 每次买入手数 (固定, 两个引擎共用)
        """
        self._initial_capital = initial_capital
        self._commission_rate = commission_rate
        self._threshold_pct = threshold_pct
        self._position_size = position_size

    # --------------------------------------------------------
    # G15 回测
    # --------------------------------------------------------

    def run_g15(
        self,
        bars: list[BarData],
        signals: list[str],
        target_code: str,
    ) -> EngineSummary:
        """用 G15 事件驱动引擎运行 MA 交叉策略。

        配置:
            - FixedLatency(0): 订单 T 提交 → T+1 撮合 (next-event 语义)
            - BAR 撮合模式: MARKET 订单成交于 bar.open
            - allow_partial_fill=True, max_participation_rate=1.0
              (关闭流动性限制, 与 vectorbt 全量成交对齐)

        Args:
            bars: BarData 列表 (按时间顺序)
            signals: 预计算信号列表 (长度 = len(bars))
            target_code: 目标标的代码

        Returns:
            EngineSummary 汇总结果
        """
        strategy = _MACrossStrategy(
            signals=signals,
            target_code=target_code,
            position_size=self._position_size,
        )

        # 构建引擎 — 使用 _LongShortAdapter 注入 _LongShortContext
        matcher = MatchingEngine(
            mode="BAR",
            allow_partial_fill=True,
            max_participation_rate=1.0,  # 全量成交 (与 vectorbt 对齐)
            enforce_price_limit=False,  # 关闭涨跌停约束 (对照测试不涉及)
        )
        latency = FixedLatency(latency_ticks=0)  # next-event: T 提交 → T+1 撮合

        engine = EventDrivenEngine(
            strategy=strategy,
            matching_engine=matcher,
            latency_model=latency,
            initial_capital=self._initial_capital,
            commission_rate=self._commission_rate,
        )

        # 替换默认 adapter 为 _LongShortAdapter (注入 buy/sell 能力)
        engine._adapter = _LongShortAdapter(
            strategy, order_submitter=engine.submit_order
        )

        return engine.run(bars)

    # --------------------------------------------------------
    # vectorbt 回测
    # --------------------------------------------------------

    def run_vectorbt(
        self,
        closes: pd.Series,
        opens: pd.Series,
        signals: list[str],
    ) -> object:
        """用 vectorbt 运行 MA 交叉策略。

        信号对齐:
            - 信号在 bar T 收盘价上生成
            - signals.shift(1) 延迟 1 bar → 信号 T → 执行 T+1
            - entry_price="open" + exit_price="open" → 以 T+1 open 成交

        Args:
            closes: 收盘价序列
            opens: 开盘价序列 (与 closes 等长)
            signals: 预计算信号列表 (长度 = len(closes))

        Returns:
            vectorbt.Portfolio 对象
        """
        import vectorbt as vbt

        # 信号转 bool 数组
        sig_series = pd.Series(signals, index=closes.index)
        entries = (sig_series == "BUY").astype(bool)
        exits = (sig_series == "SELL").astype(bool)

        # 延迟 1 bar: 信号 T → 执行 T+1 (与 G15 next-event 语义对齐)
        entries_shifted = entries.shift(1).fillna(False).astype(bool)
        exits_shifted = exits.shift(1).fillna(False).astype(bool)

        pf = vbt.Portfolio.from_signals(
            close=closes,
            open=opens,
            entries=entries_shifted,
            exits=exits_shifted,
            price=opens.values,  # 以 open 价成交 (与 G15 BAR 模式 MARKET 订单一致)
            size=self._position_size,  # 固定手数 (与 G15 _MACrossStrategy 对齐)
            init_cash=self._initial_capital,
            fees=self._commission_rate,
            freq="1D",
            cash_sharing=True,
            group_by=True,
        )
        return pf

    # --------------------------------------------------------
    # 一键对照
    # --------------------------------------------------------

    def run_ma_cross_comparison(
        self,
        bars: list[BarData],
        closes: pd.Series,
        opens: pd.Series,
        fast_window: int = 5,
        slow_window: int = 20,
        target_code: str | None = None,
    ) -> ComparisonReport:
        """一键运行 G15 + vectorbt MA 交叉对照。

        Args:
            bars: BarData 列表
            closes: 收盘价序列 (与 bars 等长)
            opens: 开盘价序列
            fast_window: 快线窗口
            slow_window: 慢线窗口
            target_code: 目标代码 (None 时取 bars[0].code)

        Returns:
            ComparisonReport 对照报告
        """
        if target_code is None:
            target_code = bars[0].code if bars else ""

        # 1. 生成信号 (两个引擎共用)
        signals = generate_ma_cross_signals(closes, fast_window, slow_window)

        # 2. G15 回测
        g15_summary = self.run_g15(bars, signals, target_code)

        # 3. vectorbt 回测
        pf = self.run_vectorbt(closes, opens, signals)

        # 4. 提取 vectorbt 指标
        vbt_equity = float(pf.value().iloc[-1])

        # 5. 构建报告
        g15_eq = g15_summary.final_equity
        g15_ret = g15_summary.total_return
        vbt_ret = (vbt_equity - self._initial_capital) / self._initial_capital

        eq_dev = abs(g15_eq - vbt_equity) / max(abs(vbt_equity), 1.0) * 100
        ret_dev = abs(g15_ret - vbt_ret) * 100

        n_buy = sum(1 for s in signals if s == "BUY")
        n_sell = sum(1 for s in signals if s == "SELL")

        notes: list[str] = []
        if g15_summary.n_orders_filled == 0 and (n_buy + n_sell) > 0:
            notes.append("G15 未成交任何订单 — 检查信号与 code 匹配")

        return ComparisonReport(
            g15_final_equity=g15_eq,
            vbt_final_equity=vbt_equity,
            equity_deviation_pct=eq_dev,
            g15_total_return=g15_ret,
            vbt_total_return=vbt_ret,
            return_deviation_pct=ret_dev,
            passed=(eq_dev < self._threshold_pct),
            n_signals=len(signals),
            n_buy_signals=n_buy,
            n_sell_signals=n_sell,
            threshold_pct=self._threshold_pct,
            notes=notes,
        )


__all__ = [
    "ComparisonReport",
    "VectorBtBridge",
    "generate_ma_cross_signals",
]
