"""
同花顺期货通期权下单脚本 - 点击版
========================================

直接点击搜索结果中的豆油期权合约。
"""
import time

import pyautogui
from pywinauto import Application

print("=" * 60)
print("同花顺期货通期权下单脚本 - 点击版")
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
    filepath = f"click_order_{name}_{int(time.time())}.png"
    pyautogui.screenshot(filepath)
    print(f"截图: {filepath}")

print("\n[1] 点击顶部'期权'标签")
options_top_x = left + width * 0.155
options_top_y = top + height * 0.025
print(f"点击顶部期权标签: ({int(options_top_x)}, {int(options_top_y)})")
pyautogui.click(int(options_top_x), int(options_top_y))
time.sleep(3)
screenshot("after_top_options")

print("\n[2] 点击搜索框")
search_x = left + width * 0.55
search_y = top + height * 0.065
print(f"点击搜索框: ({int(search_x)}, {int(search_y)})")
pyautogui.click(int(search_x), int(search_y))
time.sleep(0.5)

pyautogui.hotkey('ctrl', 'a')
time.sleep(0.2)
pyautogui.press('backspace')
time.sleep(0.2)

print("\n[3] 输入豆油搜索 y2608")
pyautogui.typewrite("y2608", interval=0.2)
time.sleep(3)
screenshot("after_search")

print("\n[4] 点击搜索结果中的豆油期权")
oil_option_x = left + width * 0.90
oil_option_y = top + height * 0.90
print(f"点击豆油期权: ({int(oil_option_x)}, {int(oil_option_y)})")
pyautogui.click(int(oil_option_x), int(oil_option_y))
time.sleep(2)
screenshot("after_click_oil")

print("\n[5] 双击期权合约进入下单")
oil_option_x = left + width * 0.90
oil_option_y = top + height * 0.90
print(f"双击豆油期权: ({int(oil_option_x)}, {int(oil_option_y)})")
pyautogui.doubleClick(int(oil_option_x), int(oil_option_y))
time.sleep(3)
screenshot("after_double_click")

print("\n[6] 等待下单面板加载")
time.sleep(3)
screenshot("after_wait")

print("\n[7] 点击买入按钮")
buy_x = left + width * 0.175
buy_y = top + height * 0.825
print(f"点击买入按钮: ({int(buy_x)}, {int(buy_y)})")
pyautogui.click(int(buy_x), int(buy_y))
time.sleep(2)
screenshot("after_buy")

print("\n[8] 等待确认弹窗并点击确定")
time.sleep(2)
confirm_x = left + width * 0.525
confirm_y = top + height * 0.665
print(f"点击确认弹窗确定: ({int(confirm_x)}, {int(confirm_y)})")
pyautogui.click(int(confirm_x), int(confirm_y))
time.sleep(2)
screenshot("after_confirm")

print("\n[9] 检查持仓")
position_tab_x = left + width * 0.355
position_tab_y = top + height * 0.683
print(f"点击持仓标签: ({int(position_tab_x)}, {int(position_tab_y)})")
pyautogui.click(int(position_tab_x), int(position_tab_y))
time.sleep(2)
screenshot("final_position")

print("\n" + "=" * 60)
print("下单完成，请检查持仓列表确认期权持仓")
print("=" * 60)
