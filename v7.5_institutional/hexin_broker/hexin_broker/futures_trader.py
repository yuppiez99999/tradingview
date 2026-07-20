"""
v7.5 同花顺客户端自动化 — 期货交易

职责:
    - 连接同花顺期货客户端（或模拟交易窗口）
    - 执行期货/期权开仓/平仓
    - 查询期货/期权持仓
"""
import logging
import time
from typing import Optional, Dict, List
from dataclasses import dataclass

from .hexin_config import (
    HEXIN_FUTURES_WINDOW_TITLES,
    HEXIN_SIM_FUTURES_TITLES,
    FUTURES_ORDER_CONTROLS,
    QUERY_CONTROLS,
    QUERY_REFRESH_TIMEOUT,
    ORDER_SUBMIT_TIMEOUT,
    MAX_RETRY,
    HEXIN_FUTURES_EXE,
)
from .utils import find_window, retry, safe_call

logger = logging.getLogger(__name__)


@dataclass
class FuturesOrderRequest:
    """期货/期权下单请求"""
    symbol: str          # 如 CU2406, IF2506, y2608-C-9000
    qty: int             # 手数/张数
    side: str            # BUY_OPEN / SELL_OPEN / BUY_CLOSE / SELL_CLOSE
    price: float = 0.0   # 限价，0表示市价
    order_type: str = "LIMIT"
    option_type: Optional[str] = None  # call/put，期权时使用


class HexinFuturesTrader:
    """同花顺期货客户端自动化（期货+期权）"""

    def __init__(self, app=None):
        self.app = app
        self._connected = False

    def connect(self) -> bool:
        """连接到同花顺期货客户端"""
        self._window = None
        try:
            from pywinauto import Application, findwindows
            candidates = findwindows.find_elements(
                title_re=".*同花顺.*", visible_only=False, backend="uia"
            )
            if not candidates:
                raise RuntimeError("未发现同花顺相关窗口")
            # Python 侧过滤，避免正则元字符问题
            matched = []
            for el in candidates:
                title = getattr(el, "window_text", lambda: "")() or getattr(el, "name", "")
                if any(k in title for k in HEXIN_FUTURES_WINDOW_TITLES):
                    matched.append((el, title))
            if matched:
                el, title = matched[0]
                self.app = Application(backend="uia").connect(process=el.process_id)
                self._window = find_window(self.app, HEXIN_FUTURES_WINDOW_TITLES + HEXIN_SIM_FUTURES_TITLES)
                self._connected = True
                logger.info("已连接到同花顺期货客户端: %s", title)
                return True
            # fallback
            self.app = Application(backend="uia").connect(process=candidates[0].process_id)
            self._window = find_window(self.app, HEXIN_FUTURES_WINDOW_TITLES + HEXIN_SIM_FUTURES_TITLES)
            self._connected = True
            logger.info("已连接到同花顺期货客户端(fallback)")
            return True
        except Exception as e:
            logger.error("连接同花顺期货客户端失败: %s", e)
            self._connected = False
            return False

    def is_connected(self) -> bool:
        return self._connected

    @retry(max_retries=MAX_RETRY)
    def place_order(self, req: FuturesOrderRequest) -> Dict:
        """执行期货/期权下单

        流程:
            1. 激活下单窗口
            2. 输入合约代码
            3. 输入价格/手数
            4. 选择开平方向
            5. 期权时额外选择认购/认沽
            6. 提交订单
        """
        if not self._connected:
            raise RuntimeError("未连接到同花顺期货客户端")

        window = self._window
        window.set_focus()

        # 输入合约代码
        code = req.symbol
        code_edit = window.child_window(**FUTURES_ORDER_CONTROLS["code_edit"])
        code_edit.set_focus()
        code_edit.set_edit_text(code)
        time.sleep(0.5)

        # 输入价格
        price_edit = window.child_window(**FUTURES_ORDER_CONTROLS["price_edit"])
        price_edit.set_focus()
        if req.price > 0:
            price_edit.set_edit_text(str(req.price))
        else:
            price_edit.set_edit_text("0")
        time.sleep(0.3)

        # 输入手数/张数
        qty_edit = window.child_window(**FUTURES_ORDER_CONTROLS["qty_edit"])
        qty_edit.set_focus()
        qty_edit.set_edit_text(str(req.qty))
        time.sleep(0.3)

        # 选择开平方向
        side_map = {
            "BUY_OPEN": FUTURES_ORDER_CONTROLS["buy_open_button"],
            "SELL_OPEN": FUTURES_ORDER_CONTROLS["sell_open_button"],
            "BUY_CLOSE": FUTURES_ORDER_CONTROLS["buy_close_button"],
            "SELL_CLOSE": FUTURES_ORDER_CONTROLS["sell_close_button"],
        }
        button_key = side_map.get(req.side)
        if button_key:
            window.child_window(**button_key).click()
        time.sleep(0.3)

        # 期权类型选择（如有）
        if req.option_type:
            try:
                option_type_control = FUTURES_ORDER_CONTROLS.get("option_type_button")
                if option_type_control:
                    btn = window.child_window(**option_type_control)
                    btn.click()
                    time.sleep(0.3)
            except Exception as e:
                logger.debug("期权类型选择失败（可能是普通期货）: %s", e)

        # 提交订单
        submit_btn = window.child_window(**FUTURES_ORDER_CONTROLS["submit_button"])
        submit_btn.click()
        time.sleep(ORDER_SUBMIT_TIMEOUT)

        return {
            "symbol": req.symbol,
            "qty": req.qty,
            "side": req.side,
            "price": req.price,
            "status": "SUBMITTED",
        }

    def cancel_order(self, order_id: str) -> bool:
        """撤单"""
        try:
            window = self._window
            window.child_window(**QUERY_CONTROLS["order_tab"]).click()
            time.sleep(0.5)
            # 实际实现需根据控件结构调整
            return True
        except Exception as e:
            logger.error("期货撤单失败: %s", e)
            return False

    def query_positions(self) -> Dict[str, Dict]:
        """查询期货/期权持仓"""
        try:
            if not self._connected or not hasattr(self, '_window'):
                return {}
            window = self._window
            window.child_window(**QUERY_CONTROLS["position_tab"]).click()
            time.sleep(0.5)
            window.child_window(**QUERY_CONTROLS["refresh_button"]).click()
            time.sleep(QUERY_REFRESH_TIMEOUT)

            positions = {}
            try:
                table = window.child_window(control_type="Table")
                for row in table.children():
                    cells = [c.window_text() for c in row.children()]
                    if len(cells) < 5:
                        continue
                    symbol = cells[0]
                    qty = int(cells[1]) if cells[1].isdigit() else 0
                    avail = int(cells[2]) if cells[2].isdigit() else 0
                    cost = float(cells[3]) if cells[3] else 0.0
                    margin = float(cells[4]) if cells[4] else 0.0
                    positions[symbol] = {
                        "qty": qty,
                        "available": avail,
                        "cost": cost,
                        "margin": margin,
                    }
            except Exception as table_err:
                logger.warning("解析期货持仓表格失败: %s", table_err)
            return positions
        except Exception as e:
            logger.error("查询期货持仓失败: %s", e)
            return {}

    def query_orders(self, symbol: str = "", side: str = "") -> List[Dict[str, str]]:
        """查询期货/期权委托/成交"""
        try:
            if not self._connected or not hasattr(self, '_window'):
                return []
            window = self._window
            window.child_window(**QUERY_CONTROLS["order_tab"]).click()
            time.sleep(0.5)
            window.child_window(**QUERY_CONTROLS["refresh_button"]).click()
            time.sleep(QUERY_REFRESH_TIMEOUT)

            orders: List[Dict[str, str]] = []
            try:
                table = window.child_window(control_type="Table")
                for row in table.children():
                    cells = [c.window_text() for c in row.children()]
                    if not cells:
                        continue
                    order = {
                        "code": (cells[0] if len(cells) > 0 else "").strip(),
                        "name": (cells[1] if len(cells) > 1 else "").strip(),
                        "side": (cells[2] if len(cells) > 2 else "").strip(),
                        "qty": (cells[3] if len(cells) > 3 else "").strip(),
                        "price": (cells[4] if len(cells) > 4 else "").strip(),
                        "status": (cells[5] if len(cells) > 5 else "").strip(),
                    }
                    if symbol and order["code"] and symbol not in order["code"]:
                        continue
                    if side and order["side"] and side not in order["side"]:
                        continue
                    orders.append(order)
            except Exception as table_err:
                logger.warning("解析期货委托/成交表格失败: %s", table_err)
            return orders
        except Exception as e:
            logger.error("查询期货委托/成交失败: %s", e)
            return []

    def query_account(self) -> Dict:
        """查询期货账户资金"""
        try:
            window = self._window
            window.child_window(**QUERY_CONTROLS["account_tab"]).click()
            time.sleep(0.5)
            window.child_window(**QUERY_CONTROLS["refresh_button"]).click()
            time.sleep(1.0)

            account = {
                "available": 0.0,
                "total": 0.0,
                "margin": 0.0,
            }
            return account
        except Exception as e:
            logger.error("查询期货账户失败: %s", e)
            return {}
