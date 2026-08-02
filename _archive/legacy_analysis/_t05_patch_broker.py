"""T05: 给 SimulatedBroker 接入 Almgren-Chriss 滑点模型."""
from __future__ import annotations

from pathlib import Path

FILE = Path(r"E:\各种PY程序\28-终极量化交易系统8.4\ms_strategy\src\execution\broker_api.py")

OLD_BLOCK = '''class SimulatedBroker(BrokerAPI):
    """回测模式：模拟盘口与撮合"""

    def __init__(self, initial_capital: float = 5_000_000,
                 commission_stock: float = 0.00025,
                 commission_futures: float = 0.000023,
                 slippage_bps: float = 2.0):
        super().__init__()
        self.capital = initial_capital
        self.available = initial_capital
        self.commission_stock = commission_stock
        self.commission_futures = commission_futures
        self.slippage_bps = slippage_bps  # 模拟滑点 bps
        self._prices: Dict[str, float] = {}  # 当前价格
        self._volumes: Dict[str, int] = {}
        self._depth_cache: Dict[str, List[float]] = {}

    def set_price(self, symbol: str, price: float, volume: int = 100000):
        self._prices[symbol] = price
        self._volumes[symbol] = volume'''

NEW_BLOCK = '''class SimulatedBroker(BrokerAPI):
    """回测模式：模拟盘口与撮合

    v8.4 T05 (2026-07-28): 接入 Almgren-Chriss 平方根滑点模型
        - 滑点 = σ × η × √(qty / ADV) × vol_scaling
        - 当 set_market_context(symbol, price, adv, volatility) 被调用时启用
        - 未调用时回退到固定 slippage_bps (向后兼容)
    """

    def __init__(self, initial_capital: float = 5_000_000,
                 commission_stock: float = 0.00025,
                 commission_futures: float = 0.000023,
                 slippage_bps: float = 2.0,
                 cost_model: Optional[Any] = None,
                 slippage_coef: float = 0.142):
        super().__init__()
        self.capital = initial_capital
        self.available = initial_capital
        self.commission_stock = commission_stock
        self.commission_futures = commission_futures
        self.slippage_bps = slippage_bps  # 固定滑点 (fallback, 无 ADV 时使用)
        self.slippage_coef = slippage_coef  # Almgren-Chriss 平方根系数 η
        self._prices: Dict[str, float] = {}  # 当前价格
        self._volumes: Dict[str, int] = {}
        self._volatilities: Dict[str, float] = {}  # 日波动率 (T05 新增)
        self._depth_cache: Dict[str, List[float]] = {}
        # 外部注入的 CostModel 实例 (可选)
        self._cost_model = cost_model

    def set_price(self, symbol: str, price: float, volume: int = 100000,
                  volatility: Optional[float] = None):
        """设置标的当前价格、成交量和波动率.

        Args:
            symbol: 标的代码
            price: 当前价格
            volume: 日成交量 (ADV), 用于 Almgren-Chriss 滑点计算
            volatility: 日波动率 (0-1), None 时默认 0.02 (2%)
        """
        self._prices[symbol] = price
        self._volumes[symbol] = volume
        self._volatilities[symbol] = volatility if volatility is not None else 0.02

    def set_market_context(self, symbol: str, price: float,
                           adv: int, volatility: float):
        """设置市场上下文 (T05 新增, Almgren-Chriss 滑点必需).

        Args:
            symbol: 标的代码
            price: 当前价格
            adv: 日均成交量 (Average Daily Volume)
            volatility: 日波动率 (如 0.02 = 2%)
        """
        self.set_price(symbol, price, volume=adv, volatility=volatility)

    def _compute_slippage_bps(self, symbol: str, qty: int) -> float:
        """计算滑点 (bps).

        优先使用 Almgren-Chriss 平方根模型 (需要 ADV + 波动率),
        缺失数据时回退到固定 slippage_bps.

        Almgren-Chriss 公式:
            slip_bps = η × σ × √(qty / ADV) × (σ / 0.02) × 10000

        Args:
            symbol: 标的代码
            qty: 委托数量

        Returns:
            滑点 bps (例如 2.0 = 0.02%)
        """
        adv = self._volumes.get(symbol, 0)
        vol = self._volatilities.get(symbol, 0.0)

        # 缺失 ADV 或波动率, 回退到固定滑点
        if adv <= 0 or vol <= 0:
            return self.slippage_bps

        # 参与率
        participation = qty / adv
        if participation <= 0:
            return self.slippage_bps

        # Almgren-Chriss 平方根模型
        # slip_bps = η × σ × √(participation) × 10000
        # (volatility_scaling: 以 2% vol 为基准, 高波动放大滑点)
        vol_scaling = vol / 0.02
        slip_bps = self.slippage_coef * vol * (participation ** 0.5) * vol_scaling * 10000

        # 上限保护: 不超过 100 bps (1%)
        slip_bps = min(slip_bps, 100.0)

        # 下限保护: 不低于固定 slippage_bps 的 10%
        slip_bps = max(slip_bps, self.slippage_bps * 0.1)

        return slip_bps'''


def main() -> None:
    text = FILE.read_text(encoding="utf-8")
    if OLD_BLOCK not in text:
        print("ERROR: 未找到原始代码块")
        return
    new_text = text.replace(OLD_BLOCK, NEW_BLOCK, 1)

    # 同时修改 wait_fill 中的滑点计算
    OLD_WAIT = """        # 模拟滑点
        slip = self.slippage_bps / 10000
        fill_price = price * (1 + slip) if order.side == 'BUY' else price * (1 - slip)"""

    NEW_WAIT = """        # 模拟滑点 (T05: Almgren-Chriss 平方根模型, 随参与率+波动率缩放)
        slip_bps = self._compute_slippage_bps(order.symbol, order.qty)
        slip = slip_bps / 10000
        fill_price = price * (1 + slip) if order.side == 'BUY' else price * (1 - slip)"""

    if OLD_WAIT in new_text:
        new_text = new_text.replace(OLD_WAIT, NEW_WAIT, 1)
        print("wait_fill 滑点逻辑已更新")
    else:
        print("WARN: 未找到 wait_fill 中的滑点代码块")

    FILE.write_text(new_text, encoding="utf-8")
    print(f"已修改: {FILE.name}")


if __name__ == "__main__":
    main()
