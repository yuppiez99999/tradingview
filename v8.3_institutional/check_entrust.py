"""
检查期权委托列表脚本
================================

检查期权订单是否已提交到委托列表。
"""
import time

import pyautogui
from pywinauto import Application

print("=" * 60)
print("检查期权委托列表脚本")
print("=" * 60)

app = Application(backend="win32").connect(title_re=".*同花顺期货通.*")
windows = app.windows()
main_window = None
for win in windows:
    if "期货通" in win.window_text():
        main_window = win
        break
if not main_window and windows:
    main_window = windows[0]

if main_window:
    main_window.set_focus()
    time.sleep(2)
    rect = main_window.rectangle()
    left = rect.left
    top = rect.top
    width = rect.right - rect.left
    height = rect.bottom - rect.top
    print(f"窗口: left={left}, top={top}, width={width}, height={height}")
else:
    print("未找到期货通窗口")
    exit(1)

def screenshot(name):
    filepath = f"check_entrust_{name}_{int(time.time())}.png"
    pyautogui.screenshot(filepath)
    print(f"截图: {filepath}")

print("\n[1] 切换到期权下单标签")
options_tab_x = left + width * 0.288
options_tab_y = top + height * 0.683
pyautogui.click(int(options_tab_x), int(options_tab_y))
time.sleep(1)
screenshot("after_options_tab")

print("\n[2] 点击委托列表")
entrust_tab_x = left + width * 0.405
entrust_tab_y = top + height * 0.683
pyautogui.click(int(entrust_tab_x), int(entrust_tab_y))
time.sleep(2)
screenshot("after_entrust")

print("\n[3] 点击成交列表")
trade_tab_x = left + width * 0.455
trade_tab_y = top + height * 0.683
pyautogui.click(int(trade_tab_x), int(trade_tab_y))
time.sleep(2)
screenshot("after_trade")

print("\n[4] 点击持仓列表")
position_tab_x = left + width * 0.355
position_tab_y = top + height * 0.683
pyautogui.click(int(position_tab_x), int(position_tab_y))
time.sleep(2)
screenshot("after_position")

print("\n" + "=" * 60)
print("检查完成，请查看截图确认订单状态")
print("=" * 60)
