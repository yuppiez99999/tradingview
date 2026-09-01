"""
WonderTrader风格执行算法模块

实现核心执行算法：
- MinImpact: 最小冲击拆单算法（模拟WtMinImpactExeUnit）
- TWAP: 时间加权平均执行算法（模拟WtTWapExeUnit）
- VWAP: 成交量加权平均执行算法

适用于300万ETF建仓计划的大单拆分执行，降低市场冲击成本。
"""

import logging
import math
import time
from datetime import datetime

logger = logging.getLogger(__name__)


def _get_adaptive_execution_params(
    target_amount: float, ref_price: float, avg_daily_volume: float, volatility: float
) -> dict:
    """根据实时市场数据计算自适应执行参数

    Args:
        target_amount: 目标金额
        ref_price: 参考价格
        avg_daily_volume: 日均成交量（股）
        volatility: 波动率

    Returns:
        自适应参数字典
    """
    depth_ratio = 0.0
    if avg_daily_volume > 0 and ref_price > 0:
        daily_turnover = avg_daily_volume * ref_price
        if daily_turnover > 0:
            depth_ratio = target_amount / daily_turnover

    # 高波动率阈值
    high_volatility = volatility > 0.05
    # 深度不足：订单占日均成交额比例过高
    shallow_market = depth_ratio > 0.1 or avg_daily_volume <= 0

    # 自适应拆单参与度
    if shallow_market or high_volatility:
        max_participation_pct = 0.08
    elif depth_ratio > 0.05:
        max_participation_pct = 0.10
    else:
        max_participation_pct = 0.15

    # 自适应执行窗口 (分钟)
    if shallow_market and high_volatility:
        execution_window_minutes = 60
        interval_minutes = 10
    elif shallow_market or high_volatility:
        execution_window_minutes = 45
        interval_minutes = 7
    else:
        execution_window_minutes = 30
        interval_minutes = 5

    # 自适应延迟系数
    if shallow_market and high_volatility:
        delay_factor = 1.8
    elif shallow_market or high_volatility:
        delay_factor = 1.4
    else:
        delay_factor = 1.0

    return {
        "max_participation_pct": max_participation_pct,
        "execution_window_minutes": execution_window_minutes,
        "interval_minutes": interval_minutes,
        "delay_factor": delay_factor,
        "depth_ratio": depth_ratio,
        "shallow_market": shallow_market,
        "high_volatility": high_volatility,
    }


class MinImpactExecutor:
    """最小冲击拆单执行器

    将大单拆分为多个小单，根据市场流动性和冲击模型决定每笔订单的大小和时间间隔。
    核心思想：订单越大，市场冲击越大，因此需要拆分并分散执行。
    """

    def __init__(self, max_participation_pct: float = 0.15, min_order_size: int = 100):
        self.max_participation_pct = max_participation_pct
        self.min_order_size = min_order_size

    def calculate_optimal_splits(
        self,
        target_amount: float,
        ref_price: float,
        avg_daily_volume: float = 0,
        volatility: float = 0.02,
    ) -> list[dict]:
        """计算最优拆单方案

        Args:
            target_amount: 目标金额（元）
            ref_price: 参考价格
            avg_daily_volume: 日均成交量（股），用于估算市场深度
            volatility: 波动率（默认2%）

        Returns:
            拆单列表，每个元素包含：order_idx, qty, amount, delay_minutes
        """
        if target_amount <= 0 or ref_price <= 0:
            return []

        target_qty = int(target_amount / ref_price)
        if target_qty < self.min_order_size:
            return [
                {
                    "order_idx": 1,
                    "qty": target_qty,
                    "amount": round(target_qty * ref_price, 2),
                    "delay_minutes": 0,
                    "type": "single",
                }
            ]

        adaptive = _get_adaptive_execution_params(
            target_amount, ref_price, avg_daily_volume, volatility
        )
        max_participation_pct = adaptive.get(
            "max_participation_pct", self.max_participation_pct
        )
        delay_factor = adaptive.get("delay_factor", 1.0)

        if avg_daily_volume > 0:
            max_qty_per_order = int(avg_daily_volume * max_participation_pct / 240)
        else:
            max_qty_per_order = int(target_qty * 0.2)

        max_qty_per_order = max(max_qty_per_order, self.min_order_size)

        num_orders = math.ceil(target_qty / max_qty_per_order)
        base_qty = target_qty // num_orders
        remainder = target_qty % num_orders

        orders = []
        total_qty = 0

        for i in range(num_orders):
            qty = base_qty + (1 if i < remainder else 0)
            total_qty += qty

            if i == 0:
                delay = 0
            elif i == num_orders - 1:
                delay = int((15 + i * 2) * delay_factor)
            else:
                delay = int((5 + i * 3) * delay_factor)

            orders.append(
                {
                    "order_idx": i + 1,
                    "qty": qty,
                    "amount": round(qty * ref_price, 2),
                    "delay_minutes": delay,
                    "type": "min_impact",
                    "cumulative_qty": total_qty,
                    "cumulative_amount": round(total_qty * ref_price, 2),
                    "adaptive_params": adaptive,
                }
            )

            if total_qty >= target_qty:
                break

        return orders

    def simulate_execution(
        self, orders: list[dict], market_impact_factor: float = 0.001
    ) -> dict:
        """模拟执行结果

        Args:
            orders: 拆单列表
            market_impact_factor: 市场冲击系数（每100万订单价格滑点比例）

        Returns:
            执行结果摘要
        """
        total_executed = 0
        total_amount = 0
        avg_execution_price = 0.0
        slippage_total = 0
        start_time = datetime.now()

        for order in orders:
            time.sleep(order["delay_minutes"] / 60)

            base_amount = order["amount"]
            impact = base_amount * 1e-6 * market_impact_factor
            execution_price = order["amount"] / order["qty"] * (1 + impact)
            executed_amount = order["qty"] * execution_price

            total_executed += order["qty"]
            total_amount += executed_amount
            slippage_total += impact * order["amount"]

        if total_executed > 0:
            avg_execution_price = float(total_amount) / float(total_executed)
        total_amount_sum = sum(o["amount"] for o in orders)
        return {
            "total_qty": total_executed,
            "total_amount": round(total_amount, 2),
            "avg_execution_price": round(avg_execution_price, 4),
            "slippage_total": round(slippage_total, 2),
            "slippage_pct": (
                round(slippage_total / total_amount_sum * 100, 4)
                if total_amount_sum > 0
                else 0.0
            ),
            "num_orders": len(orders),
            "execution_time_minutes": sum(o["delay_minutes"] for o in orders),
            "start_time": start_time.isoformat(),
            "end_time": datetime.now().isoformat(),
        }


class TWAPExecutor:
    """时间加权平均执行器

    在指定时间段内均匀拆分订单，以时间加权平均价格执行。
    """

    def __init__(self, execution_window_minutes: int = 30, interval_minutes: int = 5):
        self.execution_window_minutes = execution_window_minutes
        self.interval_minutes = interval_minutes

    def calculate_splits(self, target_amount: float, ref_price: float) -> list[dict]:
        """计算TWAP拆单方案"""
        return self.calculate_optimal_splits(target_amount, ref_price)

    def calculate_optimal_splits(
        self,
        target_amount: float,
        ref_price: float,
        avg_daily_volume: float = 0,
        volatility: float = 0.02,
    ) -> list[dict]:
        """计算TWAP拆单方案

        Args:
            target_amount: 目标金额
            ref_price: 参考价格
            avg_daily_volume: 日均成交量（股）
            volatility: 波动率

        Returns:
            拆单列表
        """
        if target_amount <= 0 or ref_price <= 0:
            return []

        adaptive = _get_adaptive_execution_params(
            target_amount, ref_price, avg_daily_volume, volatility
        )
        execution_window_minutes = adaptive.get(
            "execution_window_minutes", self.execution_window_minutes
        )
        interval_minutes = adaptive.get("interval_minutes", self.interval_minutes)

        target_qty = int(target_amount / ref_price)
        num_intervals = max(1, execution_window_minutes // interval_minutes)
        num_orders = min(num_intervals, max(2, int(target_qty / 100)))

        base_qty = target_qty // num_orders
        remainder = target_qty % num_orders

        orders = []
        total_qty = 0

        for i in range(num_orders):
            qty = base_qty + (1 if i < remainder else 0)
            total_qty += qty

            orders.append(
                {
                    "order_idx": i + 1,
                    "qty": qty,
                    "amount": round(qty * ref_price, 2),
                    "delay_minutes": i * interval_minutes,
                    "type": "twap",
                    "cumulative_qty": total_qty,
                    "cumulative_amount": round(total_qty * ref_price, 2),
                    "adaptive_params": adaptive,
                }
            )

            if total_qty >= target_qty:
                break

        return orders


class VWAPExecutor:
    """成交量加权平均执行器

    根据历史成交量分布来分配订单执行时间，在成交量高峰期执行更多订单。
    """

    def __init__(self):
        pass

    def get_volume_profile(self, session_type: str = "day") -> list[tuple[int, float]]:
        """获取成交量分布曲线

        Args:
            session_type: 交易时段类型（day/night）

        Returns:
            [(分钟偏移, 权重), ...]
        """
        if session_type == "night":
            return [
                (0, 0.05),
                (5, 0.08),
                (10, 0.10),
                (15, 0.12),
                (20, 0.10),
                (25, 0.08),
                (30, 0.08),
                (35, 0.07),
                (40, 0.07),
                (45, 0.07),
                (50, 0.06),
                (55, 0.06),
                (60, 0.05),
            ]
        return [
            (0, 0.08),
            (5, 0.10),
            (10, 0.08),
            (15, 0.06),
            (20, 0.05),
            (25, 0.04),
            (30, 0.04),
            (35, 0.04),
            (40, 0.04),
            (45, 0.04),
            (50, 0.04),
            (55, 0.04),
            (60, 0.04),
            (65, 0.04),
            (70, 0.04),
            (75, 0.04),
            (80, 0.04),
            (85, 0.04),
            (90, 0.04),
            (95, 0.04),
            (100, 0.04),
            (105, 0.04),
            (110, 0.04),
            (115, 0.05),
            (120, 0.06),
        ]

    def calculate_splits(
        self, target_amount: float, ref_price: float, session_type: str = "day"
    ) -> list[dict]:
        """计算VWAP拆单方案"""
        return self.calculate_optimal_splits(
            target_amount, ref_price, session_type=session_type
        )

    def calculate_optimal_splits(
        self,
        target_amount: float,
        ref_price: float,
        avg_daily_volume: float = 0,
        volatility: float = 0.02,
        session_type: str = "day",
    ) -> list[dict]:
        """计算VWAP拆单方案

        Args:
            target_amount: 目标金额
            ref_price: 参考价格
            avg_daily_volume: 日均成交量（股）
            volatility: 波动率
            session_type: 交易时段类型

        Returns:
            拆单列表
        """
        if target_amount <= 0 or ref_price <= 0:
            return []

        adaptive = _get_adaptive_execution_params(
            target_amount, ref_price, avg_daily_volume, volatility
        )
        volume_profile = self.get_volume_profile(session_type)

        # 根据深度和波动率动态调整权重分布
        adaptive.get("depth_ratio", 0.0)
        high_volatility = adaptive.get("high_volatility", False)
        shallow_market = adaptive.get("shallow_market", False)

        adjusted_profile = []
        if shallow_market or high_volatility:
            # 深度不足或高波动时，将更多权重分配到后半段
            for _idx, (delay, weight) in enumerate(volume_profile):
                if delay >= 60:
                    adjusted_weight = weight * 1.3
                elif delay <= 15:
                    adjusted_weight = weight * 0.8
                else:
                    adjusted_weight = weight
                adjusted_profile.append((delay, adjusted_weight))
        else:
            adjusted_profile = list(volume_profile)

        total_weight = sum(w for _, w in adjusted_profile)
        if total_weight <= 0:
            adjusted_profile = list(volume_profile)
            total_weight = sum(w for _, w in adjusted_profile)

        target_qty = int(target_amount / ref_price)
        orders = []
        total_qty = 0

        for idx, (delay, weight) in enumerate(adjusted_profile):
            qty = int(target_qty * weight / total_weight)
            if qty < 100:
                continue

            total_qty += qty

            orders.append(
                {
                    "order_idx": idx + 1,
                    "qty": qty,
                    "amount": round(qty * ref_price, 2),
                    "delay_minutes": delay,
                    "type": "vwap",
                    "volume_weight": round(weight / total_weight, 4),
                    "cumulative_qty": total_qty,
                    "cumulative_amount": round(total_qty * ref_price, 2),
                    "adaptive_params": adaptive,
                }
            )

            if total_qty >= target_qty:
                break

        if total_qty < target_qty:
            remaining = target_qty - total_qty
            if orders:
                orders[-1]["qty"] = float(orders[-1]["qty"]) + float(remaining)  # type: ignore[arg-type]
                orders[-1]["amount"] = float(round(float(orders[-1]["qty"]) * ref_price, 2))  # type: ignore[arg-type]
                orders[-1]["cumulative_qty"] = target_qty
                orders[-1]["cumulative_amount"] = round(target_qty * ref_price, 2)

        return orders


class OrderExecutor:
    """统一订单执行器

    封装多种执行算法，提供统一接口。
    """

    ALGORITHMS = ["min_impact", "twap", "vwap", "immediate"]

    def __init__(self, algorithm: str = "min_impact", **kwargs):
        self.algorithm = algorithm
        self._executor = self._create_executor(algorithm, kwargs)
        try:
            from utils.transaction_cost_model import TransactionCostModel

            self.cost_model = TransactionCostModel()
        except (
            ValueError,
            TypeError,
            KeyError,
            AttributeError,
            RuntimeError,
            OSError,
            TimeoutError,
            ConnectionError,
        ):  # P2 模块 fail-safe, 待后续精确化
            self.cost_model = None

    def _create_executor(self, algorithm: str, kwargs: dict):
        if algorithm == "min_impact":
            return MinImpactExecutor(
                max_participation_pct=kwargs.get("max_participation_pct", 0.15),
                min_order_size=kwargs.get("min_order_size", 100),
            )
        if algorithm == "twap":
            return TWAPExecutor(
                execution_window_minutes=kwargs.get("execution_window_minutes", 30),
                interval_minutes=kwargs.get("interval_minutes", 5),
            )
        if algorithm == "vwap":
            return VWAPExecutor()
        return MinImpactExecutor()

    def split_order(
        self,
        target_amount: float,
        ref_price: float,
        avg_daily_volume: float = 0,
        volatility: float = 0.02,
    ) -> list[dict]:
        """拆分订单

        Args:
            target_amount: 目标金额
            ref_price: 参考价格
            avg_daily_volume: 日均成交量
            volatility: 波动率

        Returns:
            拆单列表
        """
        if self.algorithm == "immediate":
            qty = int(target_amount / ref_price)
            return [
                {
                    "order_idx": 1,
                    "qty": qty,
                    "amount": round(qty * ref_price, 2),
                    "delay_minutes": 0,
                    "type": "immediate",
                    "cumulative_qty": qty,
                    "cumulative_amount": round(qty * ref_price, 2),
                }
            ]

        if isinstance(self._executor, MinImpactExecutor):
            return self._executor.calculate_optimal_splits(
                target_amount, ref_price, avg_daily_volume, volatility
            )
        if isinstance(self._executor, TWAPExecutor) or isinstance(
            self._executor, VWAPExecutor
        ):
            return self._executor.calculate_splits(target_amount, ref_price)
        return []

    def simulate(self, orders: list[dict]) -> dict:
        """模拟执行"""
        if isinstance(self._executor, MinImpactExecutor):
            return self._executor.simulate_execution(orders)
        total_qty = sum(o["qty"] for o in orders)
        total_amount = sum(o["amount"] for o in orders)
        return {
            "total_qty": total_qty,
            "total_amount": round(total_amount, 2),
            "avg_execution_price": (
                round(total_amount / total_qty, 4) if total_qty > 0 else 0
            ),
            "slippage_total": 0,
            "slippage_pct": 0,
            "num_orders": len(orders),
            "execution_time_minutes": sum(o["delay_minutes"] for o in orders),
            "start_time": datetime.now().isoformat(),
            "end_time": datetime.now().isoformat(),
        }

    @staticmethod
    def compare_algorithms(
        target_amount: float, ref_price: float, avg_daily_volume: float = 0
    ) -> dict:
        """比较不同执行算法的效果

        Args:
            target_amount: 目标金额
            ref_price: 参考价格
            avg_daily_volume: 日均成交量

        Returns:
            各算法对比结果
        """
        results = {}

        for algo in ["min_impact", "twap", "vwap", "immediate"]:
            executor = OrderExecutor(algorithm=algo)
            orders = executor.split_order(target_amount, ref_price, avg_daily_volume)
            simulation = executor.simulate(orders)

            cost_info = {}
            if executor.cost_model is not None:
                try:
                    cost_info = executor.cost_model.estimate_total_cost(
                        simulation["total_amount"], avg_daily_volume
                    )
                except (
                    ValueError,
                    TypeError,
                    KeyError,
                    AttributeError,
                    RuntimeError,
                    OSError,
                    TimeoutError,
                    ConnectionError,
                ):  # P2 模块 fail-safe, 待后续精确化
                    cost_info = {}

            results[algo] = {
                "algorithm": algo,
                "num_orders": len(orders),
                "total_qty": simulation["total_qty"],
                "total_amount": simulation["total_amount"],
                "avg_execution_price": simulation["avg_execution_price"],
                "slippage_pct": simulation["slippage_pct"],
                "execution_time_minutes": simulation["execution_time_minutes"],
                "cost_bps": cost_info.get("cost_bps", 0.0),
                "estimated_cost": cost_info.get("total", 0.0),
                "orders": orders,
            }

        return results


def split_order(
    target_amount: float, ref_price: float, algorithm: str = "min_impact", **kwargs
) -> list[dict]:
    """便捷函数：拆分订单"""
    executor = OrderExecutor(algorithm=algorithm, **kwargs)
    return executor.split_order(
        target_amount,
        ref_price,
        kwargs.get("avg_daily_volume", 0),
        kwargs.get("volatility", 0.02),
    )


def compare_execution(
    target_amount: float, ref_price: float, avg_daily_volume: float = 0
) -> dict:
    """便捷函数：比较执行算法"""
    return OrderExecutor.compare_algorithms(target_amount, ref_price, avg_daily_volume)


def execute_order_with_algorithm(
    target_amount: float, ref_price: float, algorithm: str = "min_impact", **kwargs
) -> dict:
    """便捷函数：使用指定算法执行订单"""
    executor = OrderExecutor(algorithm=algorithm, **kwargs)
    orders = executor.split_order(
        target_amount,
        ref_price,
        kwargs.get("avg_daily_volume", 0),
        kwargs.get("volatility", 0.02),
    )
    simulation = executor.simulate(orders)
    return {
        "algorithm": algorithm,
        "orders": orders,
        "simulation": simulation,
    }


if __name__ == "__main__":
    target_amount = 200000
    ref_price = 2.26
    avg_daily_volume = 1325044742

    logger.info(f"===== 订单拆分模拟 (¥{target_amount:,.0f}, 参考价 {ref_price}) =====")

    results = compare_execution(target_amount, ref_price, avg_daily_volume)

    for algo, result in results.items():
        logger.info(f"【{algo.upper()}】")
        logger.info(f"  拆单数: {result['num_orders']} 笔")
        logger.info(f"  总数量: {result['total_qty']:,} 股")
        logger.info(f"  预计金额: ¥{result['total_amount']:,.0f}")
        logger.info(f"  预计均价: {result['avg_execution_price']:.4f}")
        logger.info(f"  预计滑点: {result['slippage_pct']:.4f}%")
        logger.info(f"  执行时间: {result['execution_time_minutes']} 分钟")
