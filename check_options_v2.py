"""
检查期权持仓脚本 v2.1
================================

切换到期权页面检查持仓。
"""
import time
import pyautogui
from pywinauto import Application, findwindows

print("=" * 60)
print("检查期权持仓脚本 v2.1")
print("=" * 60)

main_window = None

elements = findwindows.find_elements(title_re=".*期货通.*")
for elem in elements:
    if 'iFinD' in elem.name:
        continue
    rect = elem.rectangle
    width = rect.right - rect.left
    if width < 800:
        continue
    
    app = Application(backend="win32").connect(process=elem.process_id)
    main_window = app.window(handle=elem.handle)
    main_window.set_focus()
    time.sleep(2)
    main_window.maximize()
    time.sleep(1)
    print(f"已连接: 标题='{elem.name}', 尺寸={width}x{rect.bottom-rect.top}, 进程ID={elem.process_id}")
    break

if not main_window:
    print("未找到期货通窗口")
    exit(1)

rect = main_window.rectangle()
left = rect.left
top = rect.top
width = rect.right - rect.left
height = rect.bottom - rect.top
print(f"窗口: left={left}, top={top}, width={width}, height={height}")

def screenshot(name):
    filepath = f"check_options_{name}_{int(time.time())}.png"
    pyautogui.screenshot(filepath)
    print(f"截图: {filepath}")

print("\n[1] 点击顶部期权标签")
options_tab_x = left + width * 0.195
options_tab_y = top + height * 0.015
print(f"点击顶部期权标签: ({int(options_tab_x)}, {int(options_tab_y)})")
pyautogui.click(int(options_tab_x), int(options_tab_y))
time.sleep(2)
screenshot("after_top_options_tab")

print("\n[2] 点击期权下单标签")
options_order_tab_x = left + width * 0.285
options_order_tab_y = top + height * 0.685
print(f"点击期权下单标签: ({int(options_order_tab_x)}, {int(options_order_tab_y)})")
pyautogui.click(int(options_order_tab_x), int(options_order_tab_y))
time.sleep(1)
screenshot("after_options_order_tab")

print("\n[3] 点击持仓标签")
position_tab_x = left + width * 0.355
position_tab_y = top + height * 0.685
print(f"点击持仓标签: ({int(position_tab_x)}, {int(position_tab_y)})")
pyautogui.click(int(position_tab_x), int(position_tab_y))
time.sleep(2)
screenshot("after_options_position")

print("\n[4] 点击委托标签")
entrust_tab_x = left + width * 0.405
entrust_tab_y = top + height * 0.685
print(f"点击委托标签: ({int(entrust_tab_x)}, {int(entrust_tab_y)})")
pyautogui.click(int(entrust_tab_x), int(entrust_tab_y))
time.sleep(2)
screenshot("after_options_entrust")

print("\n[5] 点击成交标签")
trade_tab_x = left + width * 0.455
trade_tab_y = top + height * 0.685
print(f"点击成交标签: ({int(trade_tab_x)}, {int(trade_tab_y)})")
pyautogui.click(int(trade_tab_x), int(trade_tab_y))
time.sleep(2)
screenshot("after_options_trade")

print("\n" + "=" * 60)
print("检查完成，请查看截图确认期权持仓状态")
print("=" * 60)