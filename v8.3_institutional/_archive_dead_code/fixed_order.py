"""
同花顺期货通下单脚本 - 修正版
============================

先确保期货通窗口处于前台，再执行下单。
"""
import time

import pyautogui
from pywinauto import Application

print("=" * 60)
print("同花顺期货通下单脚本 - 修正版")
print("=" * 60)

app = Application(backend="win32").connect(title_re=".*同花顺期货通.*")
windows = app.windows()
main_window = None
for win in windows:
    text = win.window_text()
    print(f"窗口: '{text}'")
    if "期货通" in text:
        main_window = win
        break
if not main_window and windows:
    main_window = windows[0]

if main_window:
    main_window.set_focus()
    main_window.maximize()
    time.sleep(1)

    rect = main_window.rectangle()
    left = rect.left
    top = rect.top
    width = rect.right - rect.left
    height = rect.bottom - rect.top
    print(f"窗口坐标: left={left}, top={top}, width={width}, height={height}")
else:
    print("未找到期货通窗口")
    exit(1)

def screenshot(name):
    filepath = f"fixed_{name}_{int(time.time())}.png"
    pyautogui.screenshot(filepath)
    print(f"截图: {filepath}")

print("\n[1] 确保期货通窗口在前台")
pyautogui.click(left + 100, top + 100)
time.sleep(0.5)
screenshot("after_focus")

print("\n[2] 点击期权下单标签")
options_tab_x = left + width * 0.29
options_tab_y = top + height * 0.686
print(f"点击: ({int(options_tab_x)}, {int(options_tab_y)})")
pyautogui.click(int(options_tab_x), int(options_tab_y))
time.sleep(1)
screenshot("after_options_tab")

print("\n[3] 点击合约代码输入框")
contract_x = left + width * 0.15
contract_y = top + height * 0.74
print(f"点击: ({int(contract_x)}, {int(contract_y)})")
pyautogui.click(int(contract_x), int(contract_y))
time.sleep(0.5)

pyautogui.hotkey('ctrl', 'a')
time.sleep(0.2)
pyautogui.press('backspace')
time.sleep(0.2)

print("\n[4] 输入合约代码 y2608-C-9000")
pyautogui.typewrite("y2608-C-9000", interval=0.1)
time.sleep(0.5)
screenshot("after_contract")

print("\n[5] Tab切换到手数")
pyautogui.press('tab')
time.sleep(0.3)

pyautogui.hotkey('ctrl', 'a')
time.sleep(0.2)
pyautogui.press('backspace')
time.sleep(0.2)

print("\n[6] 输入手数 1")
pyautogui.typewrite("1", interval=0.1)
time.sleep(0.5)
screenshot("after_qty")

print("\n[7] Tab切换到价格")
pyautogui.press('tab')
time.sleep(0.3)

pyautogui.hotkey('ctrl', 'a')
time.sleep(0.2)
pyautogui.press('backspace')
time.sleep(0.2)

print("\n[8] 输入价格 512.5")
pyautogui.typewrite("512.5", interval=0.1)
time.sleep(0.5)
screenshot("after_price")

print("\n[9] 点击买多按钮")
buy_x = left + width * 0.15
buy_y = top + height * 0.81
print(f"点击: ({int(buy_x)}, {int(buy_y)})")
pyautogui.click(int(buy_x), int(buy_y))
time.sleep(1)
screenshot("after_buy")

print("\n[10] 点击确认按钮")
confirm_x = left + width * 0.5
confirm_y = top + height * 0.65
print(f"点击: ({int(confirm_x)}, {int(confirm_y)})")
pyautogui.click(int(confirm_x), int(confirm_y))
time.sleep(1)
screenshot("after_confirm")

print("\n" + "=" * 60)
print("下单完成，请检查委托列表")
print("=" * 60)
