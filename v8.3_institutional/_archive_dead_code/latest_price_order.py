"""
同花顺期货通期权下单脚本 - 最新价版
========================================

使用最新价按钮获取当前市场价格，确保订单能成交。
"""
import time

import pyautogui
from pywinauto import Application

print("=" * 60)
print("同花顺期货通期权下单脚本 - 最新价版")
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
    filepath = f"latest_price_{name}_{int(time.time())}.png"
    pyautogui.screenshot(filepath)
    print(f"截图: {filepath}")

print("\n[1] 切换到期权下单标签")
options_tab_x = left + width * 0.288
options_tab_y = top + height * 0.683
print(f"点击期权下单标签: ({int(options_tab_x)}, {int(options_tab_y)})")
pyautogui.click(int(options_tab_x), int(options_tab_y))
time.sleep(2)
screenshot("after_options_tab")

print("\n[2] 点击合约输入框")
contract_x = left + width * 0.33
contract_y = top + height * 0.728
print(f"点击合约输入框: ({int(contract_x)}, {int(contract_y)})")
pyautogui.click(int(contract_x), int(contract_y))
time.sleep(0.5)

pyautogui.hotkey('ctrl', 'a')
time.sleep(0.2)
pyautogui.press('backspace')
time.sleep(0.2)

print("\n[3] 输入合约代码 y2608-C-9000")
pyautogui.typewrite("y2608-C-9000", interval=0.2)
time.sleep(3)
screenshot("after_contract")

print("\n[4] 等待系统加载合约信息")
time.sleep(3)
screenshot("after_load_info")

print("\n[5] 点击最新价按钮")
latest_price_x = left + width * 0.33
latest_price_y = top + height * 0.755
print(f"点击最新价按钮: ({int(latest_price_x)}, {int(latest_price_y)})")
pyautogui.click(int(latest_price_x), int(latest_price_y))
time.sleep(2)
screenshot("after_latest_price")

print("\n[6] 按Tab切换到手数输入框")
pyautogui.press('tab')
time.sleep(0.5)
pyautogui.press('tab')
time.sleep(0.5)

pyautogui.hotkey('ctrl', 'a')
time.sleep(0.2)
pyautogui.press('backspace')
time.sleep(0.2)

pyautogui.typewrite("1", interval=0.1)
time.sleep(0.5)
screenshot("after_qty")

print("\n[7] 点击期权买入按钮")
buy_x = left + width * 0.18
buy_y = top + height * 0.785
print(f"点击买入按钮: ({int(buy_x)}, {int(buy_y)})")
pyautogui.click(int(buy_x), int(buy_y))
time.sleep(3)
screenshot("after_buy")

print("\n[8] 等待确认弹窗并点击确定")
time.sleep(2)
confirm_x = left + width * 0.525
confirm_y = top + height * 0.665
print(f"点击确认弹窗确定: ({int(confirm_x)}, {int(confirm_y)})")
pyautogui.click(int(confirm_x), int(confirm_y))
time.sleep(2)

print("\n[9] 按Enter键确认")
pyautogui.press('enter')
time.sleep(2)
screenshot("after_confirm")

print("\n[10] 等待3秒检查成交")
time.sleep(3)
screenshot("after_wait_trade")

print("\n[11] 检查持仓列表")
position_tab_x = left + width * 0.355
position_tab_y = top + height * 0.683
pyautogui.click(int(position_tab_x), int(position_tab_y))
time.sleep(2)
screenshot("final_position")

print("\n" + "=" * 60)
print("下单完成，请检查持仓列表确认期权持仓")
print("=" * 60)
