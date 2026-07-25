"""
同花顺期货通下单脚本 - 完整确认版
================================

添加确认弹窗处理和委托列表验证。
"""
import time
import pyautogui
from pywinauto import Application

print("=" * 60)
print("同花顺期货通下单脚本 - 完整确认版")
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
    filepath = f"confirm_{name}_{int(time.time())}.png"
    pyautogui.screenshot(filepath)
    print(f"截图: {filepath}")

print("\n[1] 点击期权下单标签")
options_tab_x = left + width * 0.288
options_tab_y = top + height * 0.683
print(f"点击: ({int(options_tab_x)}, {int(options_tab_y)})")
pyautogui.click(int(options_tab_x), int(options_tab_y))
time.sleep(1)
screenshot("after_options_tab")

print("\n[2] 点击期权面板的合约输入框")
options_contract_x = left + width * 0.38
options_contract_y = top + height * 0.738
print(f"点击: ({int(options_contract_x)}, {int(options_contract_y)})")
pyautogui.click(int(options_contract_x), int(options_contract_y))
time.sleep(0.5)

pyautogui.hotkey('ctrl', 'a')
time.sleep(0.2)
pyautogui.press('backspace')
time.sleep(0.2)

print("\n[3] 输入合约代码 y2608-C-9000")
pyautogui.typewrite("y2608-C-9000", interval=0.2)
time.sleep(1)
screenshot("after_contract")

print("\n[4] 按Tab切换到手数")
pyautogui.press('tab')
time.sleep(0.5)

pyautogui.hotkey('ctrl', 'a')
time.sleep(0.2)
pyautogui.press('backspace')
time.sleep(0.2)

print("\n[5] 输入手数 1")
pyautogui.typewrite("1", interval=0.1)
time.sleep(0.5)
screenshot("after_qty")

print("\n[6] 按Tab切换到价格")
pyautogui.press('tab')
time.sleep(0.5)

pyautogui.hotkey('ctrl', 'a')
time.sleep(0.2)
pyautogui.press('backspace')
time.sleep(0.2)

print("\n[7] 输入价格 512.5")
pyautogui.typewrite("512.5", interval=0.1)
time.sleep(0.5)
screenshot("after_price")

print("\n[8] 点击期权面板的买多按钮")
options_buy_x = left + width * 0.38
options_buy_y = top + height * 0.812
print(f"点击: ({int(options_buy_x)}, {int(options_buy_y)})")
pyautogui.click(int(options_buy_x), int(options_buy_y))
time.sleep(2)
screenshot("after_buy")

print("\n[9] 等待确认弹窗并点击确认")
confirm_x = left + width * 0.5
confirm_y = top + height * 0.55
print(f"点击确认: ({int(confirm_x)}, {int(confirm_y)})")
pyautogui.click(int(confirm_x), int(confirm_y))
time.sleep(1)
screenshot("after_confirm_1")

print("\n[10] 再次尝试按Enter键确认")
pyautogui.press('enter')
time.sleep(1)
screenshot("after_confirm_2")

print("\n[11] 检查委托列表")
time.sleep(1)
screenshot("final_check")

print("\n" + "=" * 60)
print("下单完成，请检查委托列表确认订单状态")
print("=" * 60)
