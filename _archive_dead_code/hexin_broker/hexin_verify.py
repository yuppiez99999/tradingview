"""
快速验证：连接、选中窗口、关键文本命中。
"""

import logging
import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE_DIR / "src"))
sys.path.insert(0, str(BASE_DIR))

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("v75.hexin_verify")


def main():
    from pywinauto import Application, findwindows
    from hexin_broker.stock_trader import HexinStockTrader

    print("STEP1: connect")
    trader = HexinStockTrader()
    ok = trader.connect()
    print("connect=", ok)
    if not ok:
        return

    window = trader._window
    print("selected_title=", window.window_text())

    print("STEP2: text_hits")
    targets = ["资金余额", "可用金额", "总 资 产", "股票市值", "买入", "卖出", "下单", "代码编辑", "价格", "数量", "持仓", "委托"]
    hits = {}
    for name in targets:
        nodes = []
        try:
            nodes = _find_by_text(window, name, max_depth=10)
        except Exception as e:
            print("find_error", name, e)
        hits[name] = len(nodes)
    for k, v in hits.items():
        print(k, v)


def _find_by_text(node, keyword, max_depth=10):
    results = []
    try:
        title = node.window_text() or ""
    except Exception:
        title = ""
    if keyword in title:
        results.append(node)
    if len(results) >= 20:
        return results
    try:
        for child in node.children():
            results.extend(_find_by_text(child, keyword, max_depth))
            if len(results) >= 20:
                return results
    except Exception:
        pass
    return results


if __name__ == "__main__":
    main()
