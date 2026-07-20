"""
检查期权持仓脚本 v2.0
================================

检查同花顺期货通中的期权持仓状态。
"""
import time
import pyautogui
from pywinauto import Application, findwindows

print("=" * 60)
print("检查期权持仓脚本")
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

if main_window:
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
    filepath = f"check_options_{name}_{int(time.time())}.png"
    pyautogui.screenshot(filepath)
    print(f"截图: {filepath}")

print("\n[1] 切换到期权下单标签")
options_tab_x = left + width * 0.288
options_tab_y = top + height * 0.683
print(f"点击期权下单标签: ({int(options_tab_x)}, {int(options_tab_y)})")
pyautogui.click(int(options_tab_x), int(options_tab_y))
time.sleep(1)
screenshot("after_options_tab")

print("\n[2] 切换到期权持仓标签")
position_tab_x = left + width * 0.355
position_tab_y = top + height * 0.683
print(f"点击持仓标签: ({int(position_tab_x)}, {int(position_tab_y)})")
pyautogui.click(int(position_tab_x), int(position_tab_y))
time.sleep(1)
screenshot("after_position_tab")

print("\n[3] 检查委托列表")
entrust_tab_x = left + width * 0.405
entrust_tab_y = top + height * 0.683
print(f"点击委托标签: ({int(entrust_tab_x)}, {int(entrust_tab_y)})")
pyautogui.click(int(entrust_tab_x), int(entrust_tab_y))
time.sleep(1)
screenshot("after_entrust_tab")

print("\n[4] 检查成交列表")
trade_tab_x = left + width * 0.455
trade_tab_y = top + height * 0.683
print(f"点击成交标签: ({int(trade_tab_x)}, {int(trade_tab_y)})")
pyautogui.click(int(trade_tab_x), int(trade_tab_y))
time.sleep(1)
screenshot("after_trade_tab")

print("\n" + "=" * 60)
print("检查完成，请查看截图确认持仓状态")
print("=" * 60)