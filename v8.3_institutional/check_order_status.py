"""
同花顺期货通下单脚本 - 订单状态检查版
================================

检查委托列表和成交列表确认订单状态。
"""
import time
import pyautogui
from pywinauto import Application

print("=" * 60)
print("同花顺期货通下单脚本 - 订单状态检查版")
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
    time.sleep(1)
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
    filepath = f"check_status_{name}_{int(time.time())}.png"
    pyautogui.screenshot(filepath)
    print(f"截图: {filepath}")

print("\n[1] 点击委托列表")
order_x = left + width * 0.395
order_y = top + height * 0.683
print(f"点击: ({int(order_x)}, {int(order_y)})")
pyautogui.click(int(order_x), int(order_y))
time.sleep(1)
screenshot("after_order_tab")

print("\n[2] 点击成交列表")
deal_x = left + width * 0.425
order_y = top + height * 0.683
print(f"点击: ({int(order_x)}, {int(order_y)})")
pyautogui.click(int(order_x), int(order_y))
time.sleep(1)
screenshot("after_deal_tab")

print("\n[3] 点击期权下单标签查看")
options_tab_x = left + width * 0.288
options_tab_y = top + height * 0.683
print(f"点击: ({int(options_tab_x)}, {int(options_tab_y)})")
pyautogui.click(int(options_tab_x), int(options_tab_y))
time.sleep(1)
screenshot("after_options_tab")

print("\n[4] 最终检查")
time.sleep(1)
screenshot("final_check")

print("\n" + "=" * 60)
print("检查完成，请查看截图确认订单状态")
print("=" * 60)
