# -*- coding: utf-8 -*-
"""
同花顺期货通模拟交易主入口
==========================

功能:
    1. 启动期货模拟交易系统
    2. 支持股指期货/商品期货/期权模拟交易
    3. 实时行情获取（iFinD → AKShare → 兜底）
    4. 交易日志持久化
    5. 持仓查询与盈亏统计
    6. 支持夜盘交易
    7. 与 v7.5/v7.6 系统无缝对接

使用方式:
    python run_ths_sim_futures.py --help
    python run_ths_sim_futures.py --mode=sim --capital=2000000
    python run_ths_sim_futures.py --mode=sim --test-if
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import time
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
sys.path.insert(0, str(Path(__file__).parent.parent))

from sim_broker_integration import SimAccount, TradingSessionCalendar, SessionType
from ths_sim_broker import THSSimFuturesBroker, THSQuoteProvider, SimOptionsBroker

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    handlers=[logging.StreamHandler(), logging.FileHandler("logs/ths_sim_futures.log", encoding="utf-8")]
)
logger = logging.getLogger("ths_sim_futures")


class THSFuturesSimulator:
    """同花顺期货通模拟交易器"""

    def __init__(self, capital: float = 1_060_000, mode: str = "sim"):
        self.capital = capital
        self.mode = mode
        self.account = SimAccount(
            account_id=f"THS-SIM-{datetime.now():%Y%m%d-%H%M%S}",
            total_capital=capital,
            available_cash=capital,
            positions={}
        )
        self.calendar = TradingSessionCalendar()
        self.quote_provider = THSQuoteProvider()
        self.futures_broker = THSSimFuturesBroker(
            account=self.account,
            quote_provider=self.quote_provider
        )
        self.options_broker = SimOptionsBroker(
            account=self.account,
            quote_provider=self.quote_provider
        )

    def get_session_status(self) -> str:
        """获取当前交易时段状态"""
        session_type, session = self.calendar.get_current_session()
        if session_type == SessionType.NIGHT:
            return f"夜盘交易中 ({session.name}: {session.start} - {session.end})"
        elif session_type == SessionType.DAY:
            return f"日盘交易中 ({session.name}: {session.start} - {session.end})"
        return "休市中"

    def get_account_summary(self) -> dict:
        """获取账户汇总"""
        positions = self.futures_broker.get_positions()
        total_market_value = sum(
            abs(pos.get("qty", 0)) * pos.get("avg_price", 0) * pos.get("multiplier", 300)
            for pos in positions.values()
        )
        used_margin = sum(
            pos.get("qty", 0) * pos.get("avg_price", 0) * pos.get("multiplier", 300) * 0.12
            for pos in positions.values() if pos.get("qty", 0) != 0
        )
        return {
            "account_id": self.account.account_id,
            "total_capital": self.account.total_capital,
            "available_cash": self.account.available_cash,
            "used_margin": used_margin,
            "total_market_value": total_market_value,
            "total_assets": self.account.available_cash + total_market_value,
            "position_count": len([p for p in positions.values() if p.get("qty", 0) != 0]),
            "session_status": self.get_session_status(),
        }

    def print_account_summary(self):
        """打印账户汇总"""
        summary = self.get_account_summary()
        print("\n" + "="*60)
        print("同花顺期货通模拟交易账户")
        print("="*60)
        print(f"账户ID:     {summary['account_id']}")
        print(f"总资金:     ¥{summary['total_capital']:,.2f}")
        print(f"可用资金:   ¥{summary['available_cash']:,.2f}")
        print(f"已用保证金: ¥{summary['used_margin']:,.2f}")
        print(f"持仓市值:   ¥{summary['total_market_value']:,.2f}")
        print(f"总资产:     ¥{summary['total_assets']:,.2f}")
        print(f"持仓合约:   {summary['position_count']} 个")
        print(f"当前状态:   {summary['session_status']}")
        print("="*60)

    def print_positions(self):
        """打印持仓明细"""
        positions = self.futures_broker.get_positions()
        active_positions = {k: v for k, v in positions.items() if v.get("qty", 0) != 0}

        if not active_positions:
            print("\n当前无持仓")
            return

        print("\n" + "="*80)
        print("持仓明细")
        print("="*80)
        print(f"{'合约代码':<12} {'方向':<6} {'持仓手数':>8} {'均价':>10} {'现价':>10} {'市值':>12} {'盈亏':>12}")
        print("-"*80)

        total_pnl = 0
        for symbol, pos in active_positions.items():
            qty = pos.get("qty", 0)
            avg_price = pos.get("avg_price", 0)
            multiplier = pos.get("multiplier", 300)
            direction = pos.get("direction", "")
            market_value = pos.get("market_value", 0)

            current_price = self.futures_broker._get_futures_price(symbol, avg_price)
            pnl = (current_price - avg_price) * abs(qty) * multiplier if avg_price > 0 else 0
            total_pnl += pnl

            print(f"{symbol:<12} {direction:<6} {abs(qty):>8} {avg_price:>10.2f} "
                  f"{current_price:>10.2f} {market_value:>12,.2f} {pnl:>+12,.2f}")

        print("-"*80)
        print(f"{'合计':<12} {'':<6} {'':>8} {'':>10} {'':>10} {'':>12} {total_pnl:>+12,.2f}")
        print("="*80)

    def place_futures_order(self, symbol: str, qty: int, side: str, price: float = 0.0):
        """下单"""
        result = self.futures_broker.place_order(
            symbol=symbol,
            qty=qty,
            side=side,
            price=price,
            session="night" if self.calendar.get_current_session()[0] == SessionType.NIGHT else "day"
        )
        print(f"\n下单结果: {result}")
        return result

    def get_futures_price(self, symbol: str) -> float:
        """获取期货价格"""
        return self.futures_broker._get_futures_price(symbol)

    def run_simulation_loop(self, duration_minutes: int = 60):
        """运行模拟交易循环"""
        print(f"\n启动模拟交易循环，持续 {duration_minutes} 分钟...")
        start_time = time.time()

        while time.time() - start_time < duration_minutes * 60:
            session_type, _ = self.calendar.get_current_session()
            if session_type != SessionType.CLOSED:
                print(f"\n[{datetime.now():%H:%M:%S}] 交易中 - 查询行情...")
                test_symbols = ["IF2506", "IC2506", "IM2506", "CU2406", "AU2406"]
                for sym in test_symbols:
                    price = self.get_futures_price(sym)
                    print(f"  {sym}: ¥{price:.2f}")
            else:
                print(f"\n[{datetime.now():%H:%M:%S}] 休市中")

            self.print_account_summary()
            self.print_positions()
            time.sleep(30)

    def run_test_trade(self, symbol: str = "IF2506", qty: int = 1, side: str = "BUY_OPEN"):
        """运行测试交易"""
        print(f"\n--- 测试交易: {symbol} {side} {qty}手 ---")

        price = self.get_futures_price(symbol)
        print(f"当前价格: ¥{price:.2f}")

        result = self.place_futures_order(symbol, qty, side, price)
        if result.get("status") == "FILLED":
            print("交易成功!")
            self.print_account_summary()
            self.print_positions()

            close_side = "SELL_CLOSE" if side == "BUY_OPEN" else "BUY_CLOSE"
            print(f"\n--- 平仓: {symbol} {close_side} {qty}手 ---")
            time.sleep(2)
            close_result = self.place_futures_order(symbol, qty, close_side, price)
            if close_result.get("status") == "FILLED":
                print("平仓成功!")
                self.print_account_summary()
                self.print_positions()


def main():
    parser = argparse.ArgumentParser(description="同花顺期货通模拟交易")
    parser.add_argument("--mode", type=str, default="sim", choices=["sim", "test"],
                        help="运行模式: sim=模拟交易, test=测试交易")
    parser.add_argument("--capital", type=float, default=1_060_000,
                        help="初始资金 (默认: 1,060,000)")
    parser.add_argument("--test-if", action="store_true",
                        help="测试沪深300股指期货交易")
    parser.add_argument("--test-cu", action="store_true",
                        help="测试沪铜期货交易")
    parser.add_argument("--test-au", action="store_true",
                        help="测试沪金期货交易")
    parser.add_argument("--symbol", type=str, default="IF2506",
                        help="测试合约代码")
    parser.add_argument("--qty", type=int, default=1,
                        help="测试手数")
    parser.add_argument("--side", type=str, default="BUY_OPEN",
                        choices=["BUY_OPEN", "SELL_OPEN", "BUY_CLOSE", "SELL_CLOSE"],
                        help="交易方向")
    parser.add_argument("--loop", type=int, default=0,
                        help="持续运行分钟数 (0=单次执行)")
    args = parser.parse_args()

    simulator = THSFuturesSimulator(capital=args.capital, mode=args.mode)

    print("\n" + "="*60)
    print("同花顺期货通模拟交易系统 v1.0")
    print("="*60)

    if args.test_if:
        simulator.run_test_trade("IF2506", args.qty, args.side)
    elif args.test_cu:
        simulator.run_test_trade("CU2406", args.qty, args.side)
    elif args.test_au:
        simulator.run_test_trade("AU2406", args.qty, args.side)
    elif args.mode == "test":
        simulator.run_test_trade(args.symbol, args.qty, args.side)
    elif args.loop > 0:
        simulator.run_simulation_loop(args.loop)
    else:
        simulator.print_account_summary()
        simulator.print_positions()

        test_symbols = ["IF2506", "IC2506", "IM2506", "IH2506", "CU2406", "AL2406", "AU2406", "AG2406"]
        print("\n--- 当前行情 ---")
        for sym in test_symbols:
            price = simulator.get_futures_price(sym)
            print(f"{sym}: ¥{price:.2f}")


if __name__ == "__main__":
    main()