"""
同花顺股票客户端端到端验证脚本（只读）
"""

import logging
import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE_DIR / "src"))
sys.path.insert(0, str(BASE_DIR))

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("v75.hexin_e2e")


def main():
    from hexin_broker.stock_trader import HexinStockTrader
    from hexin_broker.futures_trader import HexinFuturesTrader

    print("=== 股票客户端 ===")
    stock = HexinStockTrader()
    print("connect=", stock.connect())
    if stock.is_connected():
        print("query_account=", stock.query_account())
        print("query_positions=", stock.query_positions())

    print("\n=== 期货客户端 ===")
    futures = HexinFuturesTrader()
    print("connect=", futures.connect())
    if futures.is_connected():
        print("query_account=", futures.query_account())
        print("query_positions=", futures.query_positions())


if __name__ == "__main__":
    main()
