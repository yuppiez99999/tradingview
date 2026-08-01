"""
对冲策略执行器 - ETF现货+衍生品对冲组合管理
核心功能：
1. 根据现货规模和风险偏好，计算最优对冲比例
2. 在期权和期货之间分配对冲预算
3. 动态调整Delta/Gamma/Vega暴露
4. 执行对冲订单并监控成本效益
"""

import json
import numpy as np
from datetime import datetime
from dataclasses import dataclass, field
from typing import List, Dict
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


@dataclass
class Position:
    """持仓信息"""

    code: str
    name: str
    market_value: float
    delta: float = 1.0
    beta: float = 1.0
    sector: str = ""


@dataclass
class HedgeInstrument:
    """对冲工具"""

    code: str
    name: str
    instrument_type: str  # 'option' or 'future'
    delta: float = 0.0
    gamma: float = 0.0
    vega: float = 0.0
    theta: float = 0.0
    notional_value: float = 0.0
    cost: float = 0.0
    strike_price: float = 0.0
    expiry_date: str = ""


@dataclass
class HedgePlan:
    """对冲计划"""

    timestamp: str
    spot_value: float
    target_hedge_ratio: float
    options_allocation: float
    futures_allocation: float
    instruments: List[HedgeInstrument] = field(default_factory=list)
    estimated_cost: float = 0.0
    expected_delta_neutral: bool = False


class HedgeStrategyExecutor:
    """对冲策略执行器"""

    def __init__(self, config_path: str):
        with open(config_path, "r", encoding="utf-8") as f:
            self.config = json.load(f)

        self.portfolio = self.config["portfolio"]
        self.hedge_alloc = self.config["hedge_allocation"]
        self.risk_limits = self.config["risk_limits"]
        self.instruments_config = self.config["instrument_config"]

    def calculate_optimal_hedge_ratio(
        self, portfolio_beta: float = 1.0, implied_vol: float = 0.25, market_regime: str = "normal"
    ) -> float:
        """
        计算最优对冲比例

        考虑因素：
        1. 组合Beta
        2. 隐含波动率水平
        3. 市场状态（正常/高风险/恐慌）
        4. 对冲成本效益比
        """
        base_ratio = 0.5  # 基础对冲比例

        # Beta调整
        beta_adjustment = (portfolio_beta - 1.0) * 0.3

        # 波动率调整 - V高时增加对冲
        vol_adjustment = 0
        if implied_vol > 0.30:
            vol_adjustment = 0.2  # 高波动增加对冲
        elif implied_vol < 0.15:
            vol_adjustment = -0.1  # 低波动减少对冲

        # 市场状态调整
        regime_adjustments = {"normal": 0.0, "high_risk": 0.15, "panic": 0.25, "bull_market": -0.1}

        final_ratio = min(
            1.0, max(0.0, base_ratio + beta_adjustment + vol_adjustment + regime_adjustments.get(market_regime, 0))
        )

        logger.info(f"最优对冲比例: {final_ratio:.2%}")
        return final_ratio

    def allocate_hedge_budget(
        self, total_budget: float, hedge_ratio: float, volatility_regime: str = "normal"
    ) -> Dict[str, float]:
        """
        在期权和期货之间分配对冲预算

        分配逻辑：
        1. 正常市场：期货60% + 期权40%
        2. 高波动：期货40% + 期权60%（用Put Spread降低成本）
        3. 恐慌市场：期货30% + 期权70%（全面保护）
        """
        allocations = {
            "normal": {"futures": 0.6, "options": 0.4},
            "high_vol": {"futures": 0.4, "options": 0.6},
            "panic": {"futures": 0.3, "options": 0.7},
        }

        ratio = allocations.get(volatility_regime, allocations["normal"])

        futures_budget = total_budget * hedge_ratio * ratio["futures"]
        options_budget = total_budget * hedge_ratio * ratio["options"]

        logger.info(f"预算分配 - 期货: {futures_budget:.2f}, 期权: {options_budget:.2f}")

        return {"futures": futures_budget, "options": options_budget}

    def select_option_strategy(
        self, underlying: str, budget: float, delta_target: float, max_cost_ratio: float = 0.15
    ) -> Dict:
        """
        选择期权策略

        策略库：
        1. 裸买Put - 全面保护
        2. Put Spread - 降低成本，保留部分下行风险
        3. Collar - 零成本或低成本保护
        4. Ratio Backspread - 看跌但保留上行空间
        """
        strategies = []

        # 策略1: 裸买Put
        naked_put_cost = budget * 0.8  # 预留20%缓冲
        strategies.append(
            {
                "type": "Naked Put",
                "delta": -0.5,
                "cost": naked_put_cost,
                "protection_level": "full",
                "cost_efficiency": 1.0,
            }
        )

        # 策略2: Put Spread (买1% Put + 卖3% Put)
        put_spread_cost = budget * 0.4  # 成本更低
        put_spread_delta = -0.35  # Delta绝对值更小
        protection_cap = 0.03  # 保护到下跌3%
        strategies.append(
            {
                "type": "Put Spread",
                "delta": put_spread_delta,
                "cost": put_spread_cost,
                "protection_level": f"down to {protection_cap:.0%}",
                "cost_efficiency": 1.5,  # 效率更高
            }
        )

        # 策略3: Collar (买Put + 卖Call)
        collar_cost = budget * 0.15  # 接近零成本
        collar_delta = -0.3
        strategies.append(
            {
                "type": "Collar",
                "delta": collar_delta,
                "cost": collar_cost,
                "upside_cap": "5%",
                "downside_protect": "8%",
                "cost_efficiency": 2.5,  # 最高效率
            }
        )

        # 选择最优策略
        optimal = max(strategies, key=lambda x: x["cost_efficiency"])

        logger.info(f"最优期权策略: {optimal['type']}")
        return optimal

    def calculate_future_contracts(
        self, target_delta: float, future_price: float, multiplier: int = 200, margin_ratio: float = 0.12
    ) -> int:
        """
        计算所需期货合约数量

        公式：合约数 = |target_delta| / (期货价格 × 乘数 / 现货价值)
        简化：合约数 = |目标Delta| × 现货价值 / (期货价格 × 乘数)
        """
        self.portfolio["spot_value"]
        contract_value = future_price * multiplier

        # 关键修正：target_delta已经是金额概念，不需要再乘以现货价值
        num_contracts = abs(target_delta) / contract_value

        # 向上取整
        num_contracts = int(np.ceil(num_contracts))

        # 保证金需求
        margin_required = num_contracts * future_price * multiplier * margin_ratio

        logger.info(f"需要做空 {num_contracts} 手期货合约")
        logger.info(f"保证金需求: {margin_required:,.0f}")

        return num_contracts

    def generate_hedge_plan(self, positions: List[Position], market_data: Dict) -> HedgePlan:
        """
        生成完整对冲计划
        """
        # 1. 计算组合总Delta
        total_delta = sum(p.market_value * p.delta * p.beta for p in positions)
        portfolio_beta = np.mean([p.beta for p in positions])

        # 2. 确定对冲比例
        hedge_ratio = self.calculate_optimal_hedge_ratio(
            portfolio_beta=portfolio_beta,
            implied_vol=market_data.get("implied_vol", 0.25),
            market_regime=market_data.get("regime", "normal"),
        )

        # 3. 分配预算
        budget_allocation = self.allocate_hedge_budget(
            total_budget=self.hedge_alloc["total_hedge_budget"],
            hedge_ratio=hedge_ratio,
            volatility_regime=market_data.get("vol_regime", "normal"),
        )

        # 4. 选择具体工具
        plan = HedgePlan(
            timestamp=datetime.now().isoformat(),
            spot_value=self.portfolio["spot_value"],
            target_hedge_ratio=hedge_ratio,
            options_allocation=budget_allocation["options"],
            futures_allocation=budget_allocation["futures"],
        )

        # 5. 构建对冲工具列表
        # 期货部分 - 用期货对冲50%的Delta
        if plan.futures_allocation > 0:
            # total_delta已经是金额概念（元）
            # 目标期货对冲金额 = hedge_ratio × total_delta × 0.5
            target_hedge_amount = hedge_ratio * abs(total_delta) * 0.5

            future_price = market_data.get("future_price", 4000)
            multiplier = self.instruments_config["futures"]["contract_multiplier"]
            margin_ratio = self.instruments_config["futures"]["margin_ratio"]

            # 每手期货名义价值 = 期货价格 × 乘数
            contract_value = future_price * multiplier

            # 合约数 = 目标对冲金额 / 每手期货名义价值
            num_contracts = target_hedge_amount / contract_value
            num_contracts = int(np.ceil(num_contracts))

            # 保证金需求
            margin_required = num_contracts * future_price * multiplier * margin_ratio

            logger.info(f"需要做空 {num_contracts} 手期货合约")
            logger.info(f"保证金需求: {margin_required:,.0f}")

            future_instrument = HedgeInstrument(
                code=self.instruments_config["futures"]["primary"],
                name="沪深300期货",
                instrument_type="future",
                delta=-num_contracts,  # 每手Delta=1
                notional_value=num_contracts * future_price * multiplier,
                cost=0,  # 期货无权利金
            )
            plan.instruments.append(future_instrument)

        # 期权部分
        if plan.options_allocation > 0:
            option_strategy = self.select_option_strategy(
                underlying=self.portfolio["etf_codes"][0],
                budget=plan.options_allocation,
                delta_target=plan.target_hedge_ratio * total_delta * 0.5,
                max_cost_ratio=self.risk_limits["max_single_position"] / plan.options_allocation,
            )

            option_instrument = HedgeInstrument(
                code="IO2608.CFFEX",
                name="沪深300期权",
                instrument_type="option",
                delta=option_strategy["delta"],
                cost=option_strategy["cost"],
                notional_value=plan.options_allocation * 5,  # 杠杆约5倍
            )
            plan.instruments.append(option_instrument)

        plan.estimated_cost = sum(i.cost for i in plan.instruments)

        logger.info("对冲计划生成完成")
        logger.info(f"总对冲比例: {plan.target_hedge_ratio:.2%}")
        logger.info(f"预计成本: {plan.estimated_cost:,.0f}")

        return plan

    def execute_hedge_orders(self, plan: HedgePlan, live_mode: bool = False):
        """
        执行对冲订单

        live_mode=False: 模拟盘 - 只生成订单不执行
        live_mode=True: 实盘 - 真实下单
        """
        if not live_mode:
            logger.info("=" * 60)
            logger.info("模拟盘 - 对冲订单预览（未实际执行）")
            logger.info("=" * 60)

            for i, instrument in enumerate(plan.instruments, 1):
                logger.info(f"\n工具 {i}: {instrument.name} ({instrument.code})")
                logger.info(f"  类型: {instrument.instrument_type}")
                logger.info(f"  Delta: {instrument.delta:.2f}")
                logger.info(f"  名义价值: {instrument.notional_value:,.0f}")
                if instrument.cost > 0:
                    logger.info(f"  成本: {instrument.cost:,.0f}")

            logger.info("\n总计:")
            logger.info(f"  对冲比例: {plan.target_hedge_ratio:.2%}")
            logger.info(f"  总成本: {plan.estimated_cost:,.0f}")
            logger.info(f"  成本/现货比: {plan.estimated_cost / plan.spot_value:.2%}")

            # 成本效益检查
            cost_ratio = plan.estimated_cost / plan.spot_value
            if cost_ratio > 0.20:
                logger.warning(f"⚠️  对冲成本占比过高 ({cost_ratio:.2%})，建议优化！")
                logger.warning("   建议：使用Put Spread或Collar降低成本")
            else:
                logger.info(f"✓ 对冲成本合理 ({cost_ratio:.2%})")

            return plan

        else:
            # TODO: 实盘下单逻辑
            logger.warning("实盘模式 - 正在执行对冲订单...")
            # 接入券商API
            pass


# 使用示例
if __name__ == "__main__":
    executor = HedgeStrategyExecutor(
        "e:/各种PY程序/28-终极量化交易系统8.4/v8.3_institutional/src/config/hedge_strategy_config.json"
    )

    # 模拟持仓
    positions = [
        Position(code="510300.SH", name="沪深300ETF", market_value=2000000, beta=1.0),
        Position(code="510500.SH", name="中证500ETF", market_value=1000000, beta=1.15),
    ]

    # 模拟市场数据
    market_data = {"implied_vol": 0.22, "regime": "normal", "vol_regime": "normal", "future_price": 3950.0}

    # 生成对冲计划
    plan = executor.generate_hedge_plan(positions, market_data)

    # 执行（模拟盘）
    executor.execute_hedge_orders(plan, live_mode=False)
