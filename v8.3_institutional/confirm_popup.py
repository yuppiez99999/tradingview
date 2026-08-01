"""
同花顺期货通下单脚本 - 确认弹窗处理版
================================

处理委托确认弹窗。
"""
import time

import pyautogui
from pywinauto import Application

print("=" * 60)
print("同花顺期货通下单脚本 - 确认弹窗处理版")
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
    filepath = f"confirm_popup_{name}_{int(time.time())}.png"
    pyautogui.screenshot(filepath)
    print(f"截图: {filepath}")

print("\n[1] 点击确认弹窗中的确定按钮")
confirm_x = left + width * 0.52
confirm_y = top + height * 0.65
print(f"点击: ({int(confirm_x)}, {int(confirm_y)})")
pyautogui.click(int(confirm_x), int(confirm_y))
time.sleep(2)
screenshot("after_confirm")

print("\n[2] 检查委托列表")
time.sleep(1)
screenshot("final_check")

print("\n" + "=" * 60)
print("下单完成，请检查委托列表确认订单状态")
print("=" * 60)
