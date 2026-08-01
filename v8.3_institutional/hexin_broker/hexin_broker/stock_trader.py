"""
v7.5 同花顺客户端自动化 — 股票交易

职责:
    - 连接同花顺股票客户端（或模拟炒股窗口）
    - 执行股票买入/卖出
    - 查询股票持仓
    - 查询账户资金
"""

import logging
import time
from typing import Any, Dict, List
from dataclasses import dataclass

from .hexin_config import (
    HEXIN_STOCK_WINDOW_TITLES,
    HEXIN_SIM_STOCK_TITLES,
    ORDER_SUBMIT_TIMEOUT,
    MAX_RETRY,
)
from .utils import find_window, retry, parse_static_pairs

logger = logging.getLogger(__name__)


@dataclass
class StockOrderRequest:
    """股票下单请求"""
    symbol: str          # 如 600519 或 600519.SH
    qty: int             # 数量（股），需为100的整数倍
    side: str            # BUY / SELL
    price: float = 0.0   # 限价，0表示市价
    order_type: str = "LIMIT"


class HexinStockTrader:
    """同花顺股票客户端自动化"""

    def __init__(self, app=None):
        self.app = app
        self._connected = False

    def connect(self) -> bool:
        """连接到同花顺股票客户端"""
        self._window = None
        try:
            from pywinauto import Application, findwindows
            candidates = findwindows.find_elements(
                title_re=".*同花顺.*|.*交易.*|.*xiadan.*", visible_only=False, backend="uia"
            )
            if not candidates:
                raise RuntimeError("未发现同花顺相关窗口")
            matched = []
            for el in candidates:
                title = getattr(el, "window_text", lambda: "")() or getattr(el, "name", "")
                if any(k in title for k in HEXIN_STOCK_WINDOW_TITLES):
                    matched.append((el, title))
            if matched:
                # 优先选择股票交易主窗口，而不是资讯/行情窗口
                trade_window = None
                for el, title in matched:
                    if "网上股票交易系统" in title or "买入" in title or "卖出" in title:
                        trade_window = (el, title)
                        break
                if not trade_window:
                    trade_window = matched[0]
                el, title = trade_window
                self.app = Application(backend="uia").connect(process=el.process_id)
                self._window = self._pick_stock_window(self.app)
                self._connected = True
                logger.info("已连接到同花顺股票客户端: %s", title)
                return True
            self.app = Application(backend="uia").connect(process=candidates[0].process_id)
            self._window = self._pick_stock_window(self.app)
            self._connected = True
            logger.info("已连接到同花顺股票客户端(fallback)")
            return True
        except Exception as e:
            logger.error("连接同花顺股票客户端失败: %s", e)
            self._connected = False
            return False

    def _pick_stock_window(self, app):
        """优先选择股票交易主窗口，而不是资讯/行情窗口"""
        preferred = ["网上股票交易系统", "买入", "卖出", "下单", "代码编辑", "价格"]
        try:
            for w in app.windows(visible_only=False):
                try:
                    title = w.window_text() or ""
                except Exception:
                    continue
                if any(k in title for k in preferred):
                    return w
        except Exception:
            pass
        return find_window(app, HEXIN_STOCK_WINDOW_TITLES + HEXIN_SIM_STOCK_TITLES) or app.top_window()

    def is_connected(self) -> bool:
        return self._connected

    def _focus(self):
        if not self._connected or not self._window:
            raise RuntimeError("未连接到同花顺客户端")
        self._window.set_focus()

    @retry(max_retries=MAX_RETRY)
    def place_order(self, req: StockOrderRequest) -> Dict:
        """执行股票下单（键盘模拟方案）"""
        self._focus()

        from pywinauto.keyboard import send_keys

        # 切换到买入/卖出面板
        send_keys("{F1}" if req.side == "BUY" else "{F2}")
        time.sleep(0.5)

        raw_code = req.symbol.split(".")[0] if "." in req.symbol else req.symbol
        code = raw_code[2:] if raw_code[:2] in ("sz", "sh") else raw_code

        # 输入代码
        send_keys("^a")
        time.sleep(0.2)
        send_keys(code)
        time.sleep(0.3)

        # 输入价格
        send_keys("{TAB}")
        time.sleep(0.2)
        send_keys("^a")
        time.sleep(0.2)
        send_keys(str(req.price) if req.price > 0 else "0")
        time.sleep(0.3)

        # 输入数量
        send_keys("{TAB}")
        time.sleep(0.2)
        send_keys("^a")
        time.sleep(0.2)
        send_keys(str(req.qty))
        time.sleep(0.3)

        # 提交订单（按 Enter 或点击下单按钮）
        send_keys("{ENTER}")
        time.sleep(ORDER_SUBMIT_TIMEOUT)

        return {
            "symbol": req.symbol,
            "qty": req.qty,
            "side": req.side,
            "price": req.price,
            "status": "SUBMITTED",
        }

    def cancel_order(self, order_id: str) -> bool:
        """撤单（键盘模拟方案，F3切换撤单面板）"""
        try:
            self._focus()
            from pywinauto.keyboard import send_keys
            send_keys("{F3}")
            time.sleep(0.5)
            logger.info("已切换到撤单面板")
            return True
        except Exception as e:
            logger.error("撤单失败: %s", e)
            return False

    def query_positions(self) -> Dict[str, Dict]:
        """查询股票持仓（键盘模拟方案，F4切换查询面板）"""
        try:
            self._focus()
            from pywinauto.keyboard import send_keys
            send_keys("{F4}")
            time.sleep(1.0)
            logger.info("已切换到查询面板")
            return {}
        except Exception as e:
            logger.error("查询股票持仓失败: %s", e)
            return {}

    def query_orders(self, symbol: str = "", side: str = "") -> List[Dict[str, str]]:
        """查询股票委托/成交（键盘模拟方案，F8 读取文本）"""
        try:
            self._focus()
            window = self._window
            from pywinauto.keyboard import send_keys

            send_keys("{F8}")
            time.sleep(1.0)

            orders: List[Dict[str, str]] = []
            raw_text = (window.window_text() or "").strip()
            if raw_text:
                lines = [line.strip() for line in raw_text.splitlines() if line.strip()]
                orders = _parse_order_lines(lines)
            else:
                logger.warning("股票委托/成交面板文本为空")

            if symbol:
                orders = [o for o in orders if symbol in (o.get("code") or "")]
            if side:
                orders = [o for o in orders if side in (o.get("side") or "")]

            logger.info("股票委托/成交查询完成: %s", orders)
            return orders
        except Exception as e:
            logger.error("查询股票委托/成交失败: %s", e)
            return []

    def query_account(self) -> Dict:
        """查询股票账户资金"""
        try:
            if not self._connected or not hasattr(self, '_window'):
                return {}
            self._focus()
            window = self._window

            account: Dict[str, Any] = {
                "available": 0.0,
                "total": 0.0,
                "market_value": 0.0,
            }
            try:
                # 直接解析当前窗口的文本，无需点击标签页
                raw_text = window.window_text() or ""
                account = _parse_account_text(raw_text)
                if not any(v for v in account.values()):
                    # 如果 window_text 解析失败，尝试递归收集所有控件文本
                    account = _parse_account_from_tree(window)
            except Exception as parse_err:
                logger.warning("解析股票账户资金失败: %s", parse_err)
            return account
        except Exception as e:
            logger.error("查询股票账户失败: %s", e)
            return {}


def _parse_order_lines(lines: List[str]) -> List[Dict[str, str]]:
    """将同花顺委托/成交面板文本行解析为结构化订单"""
    if not lines:
        return []

    header_keywords = ["代码", "证券代码", "名称", "买卖", "委托价", "价格", "委托量", "数量", "状态"]
    header_idx = None
    for idx, line in enumerate(lines):
        if any(key in line for key in header_keywords):
            header_idx = idx
            break

    if header_idx is None:
        return []

    orders: List[Dict[str, str]] = []
    data_lines = lines[header_idx + 1:]
    for line in data_lines:
        if not line or line.startswith("合计") or line.startswith("总计"):
            continue
        parts = [p.strip() for p in line.split() if p.strip()]
        if len(parts) < 4:
            continue
        orders.append({
            "code": parts[0] if len(parts) > 0 else "",
            "name": parts[1] if len(parts) > 1 else "",
            "side": parts[2] if len(parts) > 2 else "",
            "price": parts[3] if len(parts) > 3 else "",
            "qty": parts[4] if len(parts) > 4 else "",
            "status": parts[5] if len(parts) > 5 else "",
        })
    return orders



def _parse_account_text(text: str) -> Dict[str, float]:
    account = {"available": 0.0, "total": 0.0, "market_value": 0.0}
    try:
        pairs = parse_static_pairs(None)
        tokens = [t.strip() for t in text.replace("\n", " ").split(" ") if t.strip()]
        pairs = {}
        i = 0
        while i < len(tokens):
            token = tokens[i]
            try:
                float(token.replace(",", ""))
                i += 1
                continue
            except ValueError:
                pass
            pairs[token] = tokens[i + 1] if i + 1 < len(tokens) else None
            i += 2
        mapping = {
            "可用金额": "available",
            "总 资 产": "total",
            "总资产": "total",
            "股票市值": "market_value",
        }
        for key, field in mapping.items():
            value = pairs.get(key)
            if value:
                try:
                    account[field] = float(value.replace(",", ""))
                except ValueError:
                    pass
    except Exception:
        pass
    return account


def _parse_account_from_tree(window) -> Dict[str, float]:
    """递归遍历控件树，基于 auto_id 映射解析资金信息"""
    from .utils import iter_children

    account = {"available": 0.0, "total": 0.0, "market_value": 0.0}

    # 收集所有节点的 auto_id -> text 映射
    auto_id_text: Dict[str, str] = {}
    for child in iter_children(window, max_depth=20):
        try:
            txt = (child.window_text() or "").strip()
            auto_id = ""
            try:
                auto_id = child.automation_id() or ""
            except Exception:
                pass
            if auto_id and txt:
                auto_id_text[auto_id] = txt
        except Exception:
            pass

    # 基于 auto_id 映射提取数值
    value_map = {
        "1012": "available",   # 可用金额数值
        "1016": "withdrawable",# 可取金额数值（作为可用金额补充）
        "1015": "total",       # 总资产数值
        "1014": "market_value",# 股票市值数值
        "1013": "frozen",      # 冻结金额数值
    }
    for auto_id, field in value_map.items():
        txt = auto_id_text.get(auto_id, "")
        if txt:
            try:
                account[field] = float(txt.replace(",", ""))
            except ValueError:
                pass

    # 如果没有可用金额，尝试从其他数值推断
    if account.get("available", 0.0) == 0.0:
        for _auto_id, txt in auto_id_text.items():
            try:
                value = float(txt.replace(",", ""))
                if value > 0:
                    account["available"] = value
                    break
            except ValueError:
                pass

    return account
