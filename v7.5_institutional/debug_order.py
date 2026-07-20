"""
同花顺期货通下单调试脚本 - 自动版
==============================

此脚本自动执行下单流程，每步都截图保存。
"""
import time
import pyautogui
from pywinauto import Application

print("=" * 60)
print("同花顺期货通下单调试脚本")
print("=" * 60)

screen_width, screen_height = pyautogui.size()
print(f"\n屏幕分辨率: {screen_width} x {screen_height}")

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
    print(f"已连接窗口: {main_window.window_text()}")
    
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
    filepath = f"debug_{name}_{int(time.time())}.png"
    pyautogui.screenshot(filepath)
    print(f"截图已保存: {filepath}")

print("\n[步骤1] 切换到期权下单面板")
options_tab_x = left + width * 0.32
options_tab_y = top + height * 0.685
print(f"点击位置: ({int(options_tab_x)}, {int(options_tab_y)})")
pyautogui.click(int(options_tab_x), int(options_tab_y))
time.sleep(1)
screenshot("after_switch")

print("\n[步骤2] 点击合约输入框")
contract_x = left + width * 0.15
contract_y = top + height * 0.75
print(f"点击位置: ({int(contract_x)}, {int(contract_y)})")
pyautogui.click(int(contract_x), int(contract_y))
time.sleep(0.5)

pyautogui.hotkey('ctrl', 'a')
time.sleep(0.2)
pyautogui.press('backspace')
time.sleep(0.2)

print("\n[步骤3] 输入合约代码 y2608-C-9000")
pyautogui.typewrite("y2608-C-9000", interval=0.1)
time.sleep(0.5)
screenshot("after_contract")

print("\n[步骤4] 按Tab切换到手数输入框")
pyautogui.press('tab')
time.sleep(0.3)

pyautogui.hotkey('ctrl', 'a')
time.sleep(0.2)
pyautogui.press('backspace')
time.sleep(0.2)

print("\n[步骤5] 输入手数 1")
pyautogui.typewrite("1", interval=0.1)
time.sleep(0.5)
screenshot("after_qty")

print("\n[步骤6] 按Tab切换到价格输入框")
pyautogui.press('tab')
time.sleep(0.3)

pyautogui.hotkey('ctrl', 'a')
time.sleep(0.2)
pyautogui.press('backspace')
time.sleep(0.2)

print("\n[步骤7] 输入价格 512.5")
pyautogui.typewrite("512.5", interval=0.1)
time.sleep(0.5)
screenshot("after_price")

print("\n[步骤8] 点击买多按钮")
buy_x = left + width * 0.15
buy_y = top + height * 0.82
print(f"点击位置: ({int(buy_x)}, {int(buy_y)})")
pyautogui.click(int(buy_x), int(buy_y))
time.sleep(1)
screenshot("after_buy")

print("\n[步骤9] 点击确认按钮（如果有弹窗）")
confirm_x = left + width * 0.5
confirm_y = top + height * 0.7
print(f"点击位置: ({int(confirm_x)}, {int(confirm_y)})")
pyautogui.click(int(confirm_x), int(confirm_y))
time.sleep(1)
screenshot("after_confirm")

print("\n" + "=" * 60)
print("下单流程完成，请检查委托列表确认订单状态")
print("=" * 60)
