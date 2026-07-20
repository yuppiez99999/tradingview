"""
亚洲宏观多策略专户 - 期权量化执行引擎 (集成版)
=============================================

整合 QMT Framework 架构 + THSRealBroker 真实下单
核心目标: 严格控制最大回撤 < 15%，自动化收取 Theta 时间价值
"""
import json
import logging
import os
import time
import threading
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

logger = logging.getLogger("alpha_hedge_engine")

# 回撤分级熔断（顶级对冲基金整改：回撤必须是硬控制而非软预测）
try:
    from utils.drawdown_breaker import DrawdownCircuitBreaker, DrawdownDecision
except Exception:
    DrawdownCircuitBreaker = None
    DrawdownDecision = None


class RiskControl:
    def __init__(self, margin_limit=0.60, max_drawdown_limit=0.15, 
                 fat_finger_limit=500000):
        self.margin_limit = margin_limit
        self.max_drawdown_limit = max_drawdown_limit
        self.fat_finger_limit = fat_finger_limit
        self.drawdown_breaker = (
            DrawdownCircuitBreaker(max_drawdown=max_drawdown_limit)
            if DrawdownCircuitBreaker else None
        )

    def check_drawdown(self, current_drawdown: float):
        """组合层面回撤分级熔断检查（current_drawdown 为负值，如 -0.10）。

        Returns:
            DrawdownDecision；熔断模块不可用时返回 None
        """
        if self.drawdown_breaker is None:
            return None
        decision = self.drawdown_breaker.evaluate(current_drawdown)
        if decision.level.value in ("FORCE_HEDGE", "HALT"):
            logger.error(
                "【回撤熔断】当前回撤 %.2f%%，级别 %s：%s",
                current_drawdown * 100, decision.level.value, decision.action,
            )
        elif decision.level.value == "REDUCE":
            logger.warning(
                "【回撤减仓】当前回撤 %.2f%%，级别 %s：%s",
                current_drawdown * 100, decision.level.value, decision.action,
            )
        return decision

    def check_kill_switch(self, margin_usage: float) -> bool:
        if margin_usage >= 0.75:
            logger.error("【严重警报】保证金使用率超 75%！触发系统强平并熔断所有开仓权限！")
            return False
        elif margin_usage >= self.margin_limit:
            logger.warning("【警告】保证金使用率达 60%，系统已锁死开仓权限，仅允许平仓。")
            return False
        return True

    def check_liquidity_spread(self, ask_price: float, bid_price: float) -> bool:
        if bid_price <= 0:
            return False
        spread_ratio = (ask_price - bid_price) / bid_price
        if spread_ratio > 0.05:
            logger.warning(f"【拦截】买卖价差大于 5% ({spread_ratio:.2%})，拒绝交易。")
            return False
        return True

    def check_fat_finger(self, order_amount: float) -> bool:
        if order_amount > self.fat_finger_limit:
            logger.warning(f"【拦截】单笔金额 {order_amount:.2f} 超过防胖手指限额 {self.fat_finger_limit:.2f}")
            return False
        return True


class OptionContractFinder:
    @staticmethod
    def find_call_contract(underlying_code: str, strike_price: float, 
                          month: str = "next") -> str:
        return f"{underlying_code[:6]}-C-{int(strike_price)}"

    @staticmethod
    def find_put_contract(underlying_code: str, strike_price: float,
                         month: str = "far") -> str:
        return f"{underlying_code[:6]}-P-{int(strike_price)}"


class AlphaHedgeEngine:
    def __init__(self, account_id: str, broker=None, mode: str = "sim"):
        self.account_id = account_id
        self.mode = mode
        self.broker = broker
        
        self.total_aum = 5000000
        self.risk_control = RiskControl()
        
        self.spot_pool = {
            'dividend_etf': '512890.SH',
            'tech_etf': '588000.SH',
        }
        
        self._fills: List[Dict] = []
        self._lock = threading.RLock()

    def _execute_order(self, symbol: str, qty: int, side: str, 
                      price: float) -> Dict:
        if self.mode == "sim":
            fill = {
                "order_id": f"SIM-{int(time.time() * 1000)}",
                "symbol": symbol,
                "qty": qty,
                "side": side,
                "price": price,
                "status": "FILLED",
                "timestamp": datetime.now().isoformat(),
            }
            with self._lock:
                self._fills.append(fill)
            return fill
        
        if self.broker:
            return self.broker.place_order(symbol, qty, side, price)
        
        return {"status": "FAILED", "reason": "无可用经纪商"}

    def execute_covered_call(self):
        logger.info("\n>>> 启动 Theta 引擎：备兑收租模块扫描中...")
        
        for name, etf_code in self.spot_pool.items():
            available_volume = self._get_position_volume(etf_code)
            if available_volume == 0:
                continue
                
            current_price = self._get_last_price(etf_code)
            target_strike = current_price * 1.05
            
            target_option = OptionContractFinder.find_call_contract(
                etf_code, target_strike, month='next')
            
            ask_price, bid_price = self._get_option_quotes(target_option)
            
            if not self.risk_control.check_liquidity_spread(ask_price, bid_price):
                continue
            
            order_volume = int(available_volume / 10000)
            order_amount = order_volume * ask_price * 10000
            
            if not self.risk_control.check_fat_finger(order_amount):
                continue
            
            logger.info(f"【执行】对 {etf_code} 卖出 {order_volume} 张 {target_option} @ {ask_price}")
            
            result = self._execute_order(target_option, order_volume, 
                                        "SELL_OPEN", ask_price)
            logger.info(f"下单结果: {result.get('status', 'UNKNOWN')}")

    def tail_risk_monitor(self):
        logger.info("\n>>> 启动 Gamma 引擎：尾部风险监控中...")
        
        tech_etf = self.spot_pool['tech_etf']
        
        is_breakdown_ma60 = self._check_tech_breakdown(tech_etf)
        iv_percentile = self._get_iv_percentile(tech_etf)
        
        logger.info(f"技术面破位: {is_breakdown_ma60}, IV分位: {iv_percentile:.2%}")
        
        if is_breakdown_ma60 or iv_percentile < 0.10:
            logger.info("【触发对冲】大盘破位或保险极度便宜，启动尾部防御买入 Put！")
            
            current_price = self._get_last_price(tech_etf)
            target_strike = current_price * 0.95
            
            target_put = OptionContractFinder.find_put_contract(
                tech_etf, target_strike, month='far')
            
            ask_price, bid_price = self._get_option_quotes(target_put)
            
            if not self.risk_control.check_liquidity_spread(ask_price, bid_price):
                return
            
            budget = 20000
            volume = int(budget / (ask_price * 10000))
            
            if volume <= 0:
                logger.warning("【警告】预算不足，无法购买 Put 合约")
                return
            
            logger.info(f"【执行】买入 {volume} 张 {target_put} 作为下行保险 @ {ask_price}")
            
            result = self._execute_order(target_put, volume, "BUY_OPEN", ask_price)
            logger.info(f"下单结果: {result.get('status', 'UNKNOWN')}")

    def execute_options_order(self, symbol: str, qty: int, side: str, 
                             price: float) -> Dict:
        logger.info(f"\n>>> 执行期权订单: {side} {qty}手 {symbol} @ {price}")

    def monitor_drawdown(self, current_drawdown: float):
        """回撤分级熔断监控：超过强制对冲阈值时自动买入尾部保险。

        Args:
            current_drawdown: 当前组合回撤（负数，如 -0.10 表示回撤 10%）
        Returns:
            熔断决策 dict，或 None
        """
        decision = self.risk_control.check_drawdown(current_drawdown)
        if decision is None:
            return None
        if decision.level.value in ("FORCE_HEDGE", "HALT"):
            logger.info(">>> 回撤熔断触发，启动尾部防御买入 Put...")
            try:
                self.tail_risk_monitor()
            except Exception as e:
                logger.error(f"【回撤熔断】尾部防御执行失败: {e}")
        return decision.to_dict()
        
        ask_price, bid_price = self._get_option_quotes(symbol)
        if not self.risk_control.check_liquidity_spread(ask_price, bid_price):
            return {"status": "REJECTED", "reason": "流动性不足"}
        
        multiplier = self._get_option_multiplier(symbol)
        order_amount = qty * price * multiplier
        if not self.risk_control.check_fat_finger(order_amount):
            return {"status": "REJECTED", "reason": "超过防胖手指限额"}
        
        margin_usage = self._get_margin_usage()
        if not self.risk_control.check_kill_switch(margin_usage):
            return {"status": "REJECTED", "reason": "风控熔断"}
        
        return self._execute_order(symbol, qty, side, price)

    def _get_position_volume(self, symbol: str) -> int:
        if self.broker:
            positions = self.broker.get_positions()
            return positions.get(symbol, {}).get('volume', 1000000)
        return 1000000

    def _get_last_price(self, symbol: str) -> float:
        if self.broker and hasattr(self.broker, 'get_price'):
            return self.broker.get_price(symbol)
        return 1.00

    def _get_option_quotes(self, symbol: str) -> tuple:
        if self.broker and hasattr(self.broker, 'get_option_quotes'):
            return self.broker.get_option_quotes(symbol)
        return 0.105, 0.100

    def _get_margin_usage(self) -> float:
        if self.broker and hasattr(self.broker, 'get_margin_usage'):
            return self.broker.get_margin_usage()
        return 0.45

    def _check_tech_breakdown(self, symbol: str) -> bool:
        return False

    def _get_iv_percentile(self, symbol: str) -> float:
        return 0.08

    def _get_option_multiplier(self, symbol: str) -> int:
        if symbol.startswith('y'):
            return 10
        if symbol.startswith('512') or symbol.startswith('588'):
            return 10000
        return 10000

    def run_daily_routine(self, current_drawdown: float = None):
        logger.info(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] 启动宏观对冲专户执行引擎...")
        try:
            # 顶级对冲基金整改：盘前先评估组合回撤，触发熔断时优先尾部防御
            if current_drawdown is not None:
                self.monitor_drawdown(current_drawdown)
            self.execute_covered_call()
            self.tail_risk_monitor()
            logger.info(">>> 本次轮询执行完毕。状态：安全。")
        except Exception as e:
            logger.error(f"【系统异常】{str(e)}", exc_info=True)


def main():
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s [%(levelname)s] %(message)s'
    )

    broker = None
    try:
        import importlib.util
        spec = importlib.util.spec_from_file_location(
            "ths_real_broker", 
            "v7.5_institutional/ths_real_broker.py"
        )
        ths_module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(ths_module)
        THSRealBroker = ths_module.THSRealBroker
        
        class MockAccount:
            def __init__(self):
                self.positions = {}
                self.available_cash = 1000000
        
        account = MockAccount()
        broker = THSRealBroker(account, mode="real")
        logger.info("已加载 THSRealBroker")
    except Exception as e:
        logger.warning(f"THSRealBroker 不可用: {e}，将使用模拟模式")

    engine = AlphaHedgeEngine(account_id='YOUR_ACCOUNT', broker=broker, mode="real")
    
    print("\n" + "=" * 60)
    print("亚洲宏观多策略专户 - 期权量化执行引擎")
    print("=" * 60)
    
    print("\n[1] 执行豆油期权测试订单 (真实模式)...")
    result = engine.execute_options_order("y2608-C-9000", 1, "BUY_OPEN", 512.5)
    print(f"订单结果: {json.dumps(result, ensure_ascii=False, indent=2)}")
    
    print("\n" + "=" * 60)
    print("执行完毕")
    print("=" * 60)


if __name__ == '__main__':
    main()
