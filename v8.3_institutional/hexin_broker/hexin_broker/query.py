"""
v7.5 同花顺客户端自动化 — 查询模块

统一封装:
    - 查询股票/期货持仓
    - 查询账户资金
    - 查询委托状态

注意:
    实际控件名需根据同花顺客户端版本调整。
    此处提供统一接口，内部委托给 stock_trader / futures_trader。
"""

import logging
from typing import Dict

logger = logging.getLogger(__name__)


class HexinQuery:
    """同花顺查询统一入口"""

    def __init__(self, stock_trader, futures_trader):
        self.stock_trader = stock_trader
        self.futures_trader = futures_trader

    def query_all_positions(self) -> Dict[str, Dict]:
        """查询全部持仓（股票+期货）"""
        result = {}
        try:
            if self.stock_trader.is_connected():
                result["stock"] = self.stock_trader.query_positions()
        except Exception as e:
            logger.error("查询股票持仓失败: %s", e)

        try:
            if self.futures_trader.is_connected():
                result["futures"] = self.futures_trader.query_positions()
        except Exception as e:
            logger.error("查询期货持仓失败: %s", e)

        return result

    def query_all_accounts(self) -> Dict[str, Dict]:
        """查询全部账户资金"""
        result = {}
        try:
            if self.stock_trader.is_connected():
                result["stock"] = self.stock_trader.query_account()
        except Exception as e:
            logger.error("查询股票账户失败: %s", e)

        try:
            if self.futures_trader.is_connected():
                result["futures"] = self.futures_trader.query_account()
        except Exception as e:
            logger.error("查询期货账户失败: %s", e)

        return result
