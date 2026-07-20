"""
同花顺期货通期权下单脚本 - 分步调试版
========================================

每一步都有详细等待和验证，确保期权下单成功。
"""
import time
import pyautogui
from pywinauto import Application

print("=" * 60)
print("同花顺期货通期权下单脚本 - 分步调试版")
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
    time.sleep(3)
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
    filepath = f"debug_options_{name}_{int(time.time())}.png"
    pyautogui.screenshot(filepath)
    print(f"截图: {filepath}")

print("\n[1] 等待3秒后切换到期权下单标签")
time.sleep(3)
options_tab_x = left + width * 0.30
options_tab_y = top + height * 0.688
print(f"点击期权下单标签: ({int(options_tab_x)}, {int(options_tab_y)})")
pyautogui.click(int(options_tab_x), int(options_tab_y))
time.sleep(3)
screenshot("step1_after_options_tab")

print("\n[2] 再次点击期权下单标签")
pyautogui.click(int(options_tab_x), int(options_tab_y))
time.sleep(3)
screenshot("step2_after_options_tab_2")

print("\n[3] 点击合约输入框")
contract_x = left + width * 0.36
contract_y = top + height * 0.755
print(f"点击合约输入框: ({int(contract_x)}, {int(contract_y)})")
pyautogui.click(int(contract_x), int(contract_y))
time.sleep(1)

pyautogui.hotkey('ctrl', 'a')
time.sleep(0.3)
pyautogui.press('backspace')
time.sleep(0.3)

print("\n[4] 输入合约代码 y2608-C-9000")
pyautogui.typewrite("y2608-C-9000", interval=0.3)
time.sleep(3)
screenshot("step4_after_contract")

print("\n[5] 按Tab键切换到手数输入框")
pyautogui.press('tab')
time.sleep(0.5)
pyautogui.press('tab')
time.sleep(0.5)
pyautogui.press('tab')
time.sleep(0.5)

pyautogui.hotkey('ctrl', 'a')
time.sleep(0.3)
pyautogui.press('backspace')
time.sleep(0.3)

print("\n[6] 输入手数 1")
pyautogui.typewrite("1", interval=0.1)
time.sleep(1)
screenshot("step6_after_qty")

print("\n[7] 按Tab键切换到价格输入框")
pyautogui.press('tab')
time.sleep(0.5)

pyautogui.hotkey('ctrl', 'a')
time.sleep(0.3)
pyautogui.press('backspace')
time.sleep(0.3)

print("\n[8] 输入价格 512.5")
pyautogui.typewrite("512.5", interval=0.1)
time.sleep(1)
screenshot("step8_after_price")

print("\n[9] 按Tab键切换到买多按钮")
pyautogui.press('tab')
time.sleep(0.5)
pyautogui.press('tab')
time.sleep(0.5)
pyautogui.press('tab')
time.sleep(0.5)

print("\n[10] 按Enter键提交订单")
pyautogui.press('enter')
time.sleep(3)
screenshot("step10_after_enter")

print("\n[11] 等待确认弹窗并按Enter确认")
time.sleep(2)
pyautogui.press('enter')
time.sleep(3)
screenshot("step11_after_confirm")

print("\n[12] 检查期权持仓")
position_tab_x = left + width * 0.355
position_tab_y = top + height * 0.683
print(f"点击持仓标签: ({int(position_tab_x)}, {int(position_tab_y)})")
pyautogui.click(int(position_tab_x), int(position_tab_y))
time.sleep(2)
screenshot("step12_final_position")

print("\n" + "=" * 60)
print("下单完成，请检查持仓列表确认期权持仓")
print("=" * 60)
