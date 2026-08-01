"""
同花顺期货通期权下单脚本 - 控件遍历版
========================================

使用pywinauto遍历控件，找到准确的期权下单标签位置。
"""
import time

import pyautogui
from pywinauto import Application

print("=" * 60)
print("同花顺期货通期权下单脚本 - 控件遍历版")
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
    filepath = f"control_scan_{name}_{int(time.time())}.png"
    pyautogui.screenshot(filepath)
    print(f"截图: {filepath}")

print("\n[1] 遍历控件查找期权下单标签")
try:
    all_controls = main_window.descendants()
    print(f"找到 {len(all_controls)} 个控件")

    option_tabs = []
    for i, ctrl in enumerate(all_controls[:100]):
        try:
            text = ctrl.window_text()
            if text and ('期权' in text or 'Option' in text):
                option_tabs.append((i, text, ctrl.rectangle()))
                print(f"控件 {i}: '{text}' - {ctrl.rectangle()}")
        except Exception:
            pass

    if option_tabs:
        print(f"\n找到 {len(option_tabs)} 个期权相关控件:")
        for idx, text, rect in option_tabs:
            print(f"  {idx}: '{text}' - {rect}")
except Exception as e:
    print(f"遍历控件失败: {e}")

print("\n[2] 使用坐标点击期权下单标签")
options_tab_x = left + width * 0.285
options_tab_y = top + height * 0.680
print(f"点击期权下单标签: ({int(options_tab_x)}, {int(options_tab_y)})")
pyautogui.click(int(options_tab_x), int(options_tab_y))
time.sleep(2)
screenshot("after_click_options")

print("\n[3] 再次点击期权下单标签")
pyautogui.click(int(options_tab_x), int(options_tab_y))
time.sleep(2)
screenshot("after_click_options_2")

print("\n[4] 检查当前面板")
current_panel_x = left + width * 0.285
current_panel_y = top + height * 0.680
print(f"检查当前面板: ({int(current_panel_x)}, {int(current_panel_y)})")
screenshot("current_panel")

print("\n" + "=" * 60)
print("请查看截图确认期权面板是否切换成功")
print("=" * 60)
