"""
同花顺期货通下单脚本 - 手动确认版
================================

在关键步骤等待用户确认，确保每一步都正确。
"""
import time
import pyautogui
from pywinauto import Application

print("=" * 60)
print("同花顺期货通下单脚本 - 手动确认版")
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
    filepath = f"manual_{name}_{int(time.time())}.png"
    pyautogui.screenshot(filepath)
    print(f"截图: {filepath}")

print("\n[1] 点击期权下单标签")
options_tab_x = left + width * 0.288
options_tab_y = top + height * 0.683
print(f"点击: ({int(options_tab_x)}, {int(options_tab_y)})")
pyautogui.click(int(options_tab_x), int(options_tab_y))
time.sleep(1)
screenshot("after_options_tab")

input("期权下单面板是否已打开？按Enter继续...")

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

input("合约代码是否正确输入？按Enter继续...")

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

input("手数是否正确输入？按Enter继续...")

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

input("价格是否正确输入？按Enter继续...")

print("\n[8] 点击期权面板的买多按钮")
options_buy_x = left + width * 0.38
options_buy_y = top + height * 0.812
print(f"点击: ({int(options_buy_x)}, {int(options_buy_y)})")
pyautogui.click(int(options_buy_x), int(options_buy_y))
time.sleep(2)
screenshot("after_buy")

print("\n[9] 再次点击买多按钮")
pyautogui.click(int(options_buy_x), int(options_buy_y))
time.sleep(2)
screenshot("after_buy_again")

print("\n[10] 按Enter键确认")
pyautogui.press('enter')
time.sleep(1)
screenshot("after_enter")

input("订单是否已提交？请检查委托列表，按Enter继续...")

print("\n" + "=" * 60)
print("下单流程完成")
print("=" * 60)
