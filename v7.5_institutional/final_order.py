"""
同花顺期货通下单脚本 - 精准版
============================

根据截图分析，调整坐标定位。
"""
import time
import pyautogui
from pywinauto import Application

print("=" * 60)
print("同花顺期货通下单脚本 - 精准版")
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
    filepath = f"final_{name}_{int(time.time())}.png"
    pyautogui.screenshot(filepath)
    print(f"截图: {filepath}")
    return filepath

print("\n[1] 切换到期权下单面板")
options_tab_x = left + width * 0.285  
options_tab_y = top + height * 0.688  
print(f"点击: ({int(options_tab_x)}, {int(options_tab_y)})")
pyautogui.click(int(options_tab_x), int(options_tab_y))
time.sleep(1)
screenshot("after_options_tab")

print("\n[2] 点击合约代码输入框")
contract_x = left + width * 0.145
contract_y = top + height * 0.735
print(f"点击: ({int(contract_x)}, {int(contract_y)})")
pyautogui.click(int(contract_x), int(contract_y))
time.sleep(0.5)

pyautogui.hotkey('ctrl', 'a')
time.sleep(0.2)
pyautogui.press('backspace')
time.sleep(0.2)

print("\n[3] 输入合约代码 y2608-C-9000")
pyautogui.typewrite("y2608-C-9000", interval=0.1)
time.sleep(0.5)
screenshot("after_contract")

print("\n[4] Tab切换到手数")
pyautogui.press('tab')
time.sleep(0.3)

pyautogui.hotkey('ctrl', 'a')
time.sleep(0.2)
pyautogui.press('backspace')
time.sleep(0.2)

print("\n[5] 输入手数 1")
pyautogui.typewrite("1", interval=0.1)
time.sleep(0.5)
screenshot("after_qty")

print("\n[6] Tab切换到价格")
pyautogui.press('tab')
time.sleep(0.3)

pyautogui.hotkey('ctrl', 'a')
time.sleep(0.2)
pyautogui.press('backspace')
time.sleep(0.2)

print("\n[7] 输入价格 512.5")
pyautogui.typewrite("512.5", interval=0.1)
time.sleep(0.5)
screenshot("after_price")

print("\n[8] 点击买多按钮")
buy_x = left + width * 0.145
buy_y = top + height * 0.815
print(f"点击: ({int(buy_x)}, {int(buy_y)})")
pyautogui.click(int(buy_x), int(buy_y))
time.sleep(1)
screenshot("after_buy")

print("\n[9] 检查确认弹窗")
confirm_x = left + width * 0.5
confirm_y = top + height * 0.6
print(f"点击: ({int(confirm_x)}, {int(confirm_y)})")
pyautogui.click(int(confirm_x), int(confirm_y))
time.sleep(1)
screenshot("after_confirm")

print("\n" + "=" * 60)
print("下单完成，请检查委托列表")
print("=" * 60)
