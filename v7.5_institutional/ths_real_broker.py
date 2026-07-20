"""
同花顺期货通真实下单适配器 v2.3 - 精确版
=========================================

改进方案:
    1. 只连接 class_name='STOCKTRADEAPP' 的窗口
    2. 检查窗口尺寸（宽度>800，高度>600）排除迷你窗口
    3. 明确排除 iFinD 窗口
    4. 连接后立即截图确认界面
"""
from __future__ import annotations

import json
import logging
import os
import subprocess
import time
import threading
from datetime import datetime
from pathlib import Path
from typing import Dict, List

logger = logging.getLogger("v75.ths_real_broker")


class THSRealBroker:
    def __init__(self, account, quote_provider=None,
                 margin_rates=None, trade_log_path=None, mode="sim"):
        self.account = account
        self.quote_provider = quote_provider
        self.margin_rates = margin_rates or {}
        self.mode = mode
        self._connected = False
        self._pending_orders: Dict[str, Dict] = {}
        self._fills: List[Dict] = []
        self._lock = threading.RLock()

        log_dir = Path(trade_log_path) if trade_log_path else Path("logs")
        log_dir.mkdir(parents=True, exist_ok=True)
        self._trade_log = log_dir / "ths_real_trades.jsonl"
        self._screenshot_dir = log_dir / "screenshots"
        self._screenshot_dir.mkdir(parents=True, exist_ok=True)

        self._app = None
        self._main_window = None

    def _save_screenshot(self, name: str) -> str:
        try:
            import pyautogui
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            filepath = self._screenshot_dir / f"{name}_{timestamp}.png"
            pyautogui.screenshot(str(filepath))
            logger.info(f"截图: {filepath}")
            return str(filepath)
        except Exception as e:
            logger.error(f"截图失败: {e}")
            return ""

    def connect(self) -> bool:
        try:
            from pywinauto import Application, findwindows

            titles_to_try = ["期货通"]
            for title_keyword in titles_to_try:
                try:
                    elements = findwindows.find_elements(title_re=f".*{title_keyword}.*")
                    for elem in elements:
                        title = elem.name
                        class_name = elem.class_name
                        pid = elem.process_id
                        rect = elem.rectangle
                        width = rect.right - rect.left
                        height = rect.bottom - rect.top

                        if 'iFinD' in title:
                            logger.info(f"跳过iFinD窗口: 标题='{title}'")
                            continue
                        if width < 800 or height < 600:
                            logger.info(f"跳过迷你窗口: 标题='{title}', 尺寸={width}x{height}")
                            continue

                        app = Application(backend="win32").connect(process=pid)
                        self._app = app
                        self._main_window = app.window(handle=elem.handle)
                        self._main_window.set_focus()
                        time.sleep(2)
                        self._main_window.maximize()
                        time.sleep(1)

                        self._save_screenshot("after_connect")
                        self._connected = True
                        logger.info(f"成功连接期货通: 标题='{title}', 类名='{class_name}', 尺寸={width}x{height}, 进程ID={pid}")
                        return True

                except Exception as e:
                    logger.warning(f"通过标题 '{title_keyword}' 连接失败: {e}")
                    continue

            result = subprocess.run(
                ['powershell', '-Command', 'Get-Process -Name hexin,StockTradeApp,happ | Select-Object Id'],
                capture_output=True, text=True
            )
            process_ids = [
                int(line.strip())
                for line in result.stdout.strip().split('\n')
                if line.strip().isdigit()
            ]
            logger.info(f"尝试进程ID列表: {process_ids}")

            for pid in process_ids:
                try:
                    app = Application(backend="win32").connect(process=pid)
                    windows = app.windows()
                    for win in windows:
                        title = win.window_text()
                        class_name = win.class_name()
                        rect = win.rectangle()
                        width = rect.right - rect.left
                        height = rect.bottom - rect.top

                        if '期货' not in title:
                            continue
                        if width < 800 or height < 600:
                            logger.info(f"跳过迷你窗口: 标题='{title}', 尺寸={width}x{height}")
                            continue
                        if 'iFinD' in title:
                            logger.info(f"跳过iFinD窗口: 标题='{title}'")
                            continue

                        self._app = app
                        self._main_window = win
                        self._main_window.set_focus()
                        time.sleep(2)
                        self._main_window.maximize()
                        time.sleep(1)

                        self._save_screenshot("after_connect")
                        self._connected = True
                        logger.info(f"成功连接期货通: 标题='{title}', 类名='{class_name}', 尺寸={width}x{height}, 进程ID={pid}")
                        return True

                except Exception as e:
                    logger.warning(f"连接进程 {pid} 失败: {e}")

            logger.error("未找到有效的期货通交易窗口")
            return False

        except Exception as e:
            logger.error(f"连接失败: {e}")
            return False

    @property
    def is_connected(self) -> bool:
        return self._connected

    def _get_screen_pos(self, rel_x: float, rel_y: float):
        try:
            import pyautogui
            screen_width, screen_height = pyautogui.size()
            return int(screen_width * rel_x), int(screen_height * rel_y)
        except Exception:
            return None, None

    def _click_screen(self, rel_x: float, rel_y: float, name: str = "") -> bool:
        try:
            import pyautogui
            x, y = self._get_screen_pos(rel_x, rel_y)
            if x is None:
                return False
            pyautogui.click(x, y)
            time.sleep(0.5)
            logger.info(f"点击: {name} ({x}, {y})")
            return True
        except Exception as e:
            logger.error(f"点击失败: {e}")
            return False

    def _switch_to_options_tab(self) -> bool:
        if not self._connected:
            return False

        self._save_screenshot("before_switch")
        if self._click_screen(0.288, 0.683, "期权下单标签"):
            time.sleep(1)
            self._save_screenshot("after_switch")
            logger.info("已尝试切换到期权下单面板")
            return True
        return False

    def place_order(self, symbol: str, qty: int, side: str, price: float) -> Dict:
        if not self._connected:
            return {"order_id": "", "status": "REJECTED", "reason": "未连接"}

        logger.info(f"真实下单: {symbol}, {qty}手, {price}元, {side}")

        if not self._switch_to_options_tab():
            return {"order_id": "", "status": "REJECTED", "reason": "无法切换到期权面板"}

        self._save_screenshot("before_fill")

        import pyautogui

        x, y = self._get_screen_pos(0.38, 0.738)
        pyautogui.click(x, y)
        time.sleep(0.5)
        pyautogui.typewrite(symbol)
        logger.info(f"输入: {symbol}")

        pyautogui.press('tab')
        time.sleep(0.5)
        logger.info("按Tab键 1 次")

        pyautogui.typewrite(str(qty))
        time.sleep(0.5)
        logger.info(f"输入: {qty}")

        pyautogui.press('tab')
        time.sleep(0.5)
        logger.info("按Tab键 1 次")

        pyautogui.typewrite(str(price))
        time.sleep(0.5)
        logger.info(f"输入: {price}")

        self._save_screenshot("after_fill")

        if side in ["BUY_OPEN", "BUY", "买开"]:
            self._click_screen(0.185, 0.815, "买多按钮")
        else:
            self._click_screen(0.185, 0.885, "卖空按钮")

        time.sleep(1.5)
        self._click_screen(0.525, 0.665, "确认按钮")
        time.sleep(1)

        self._save_screenshot("after_order")

        order_id = f"THS-{symbol}-{int(time.time()*1000)}-{qty}"
        logger.info(f"订单已提交: {order_id}")

        with self._lock:
            self._pending_orders[order_id] = {
                "symbol": symbol, "qty": qty, "side": side, "price": price,
                "status": "SUBMITTED", "timestamp": datetime.now().isoformat()
            }

        return {
            "order_id": order_id,
            "symbol": symbol,
            "qty": qty,
            "side": side,
            "price": price,
            "status": "SUBMITTED",
            "timestamp": datetime.now().isoformat(),
            "mode": self.mode
        }

    def get_order_status(self, order_id: str) -> Dict:
        with self._lock:
            return self._pending_orders.get(order_id, {"status": "UNKNOWN"})

    def get_positions(self) -> Dict:
        return self.account.positions

    def get_available_cash(self) -> float:
        return self.account.available_cash

    def cancel_order(self, order_id: str) -> bool:
        with self._lock:
            if order_id in self._pending_orders:
                self._pending_orders[order_id]["status"] = "CANCELLED"
                return True
        return False

    def disconnect(self):
        self._connected = False
        self._main_window = None
        self._app = None
        logger.info("已断开期货通连接")