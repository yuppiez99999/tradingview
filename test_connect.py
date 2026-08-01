import subprocess
from pywinauto import Application

result = subprocess.run(
    ["powershell", "-Command", "Get-Process -Name hexin,StockTradeApp | Select-Object Id"],
    capture_output=True,
    text=True,
)
print(f"powershell输出: {result.stdout!r}")

process_ids = [int(line.strip()) for line in result.stdout.strip().split("\n") if line.strip().isdigit()]
print(f"解析出的进程ID: {process_ids}")

if process_ids:
    for pid in process_ids:
        try:
            app = Application(backend="win32").connect(process=pid)
            windows = app.windows()
            print(f"\n进程 {pid} 的窗口:")
            for win in windows:
                title = win.window_text()
                class_name = win.class_name()
                rect = win.rectangle()
                width = rect.right - rect.left
                height = rect.bottom - rect.top
                print(f"  标题: '{title}', 类名: '{class_name}', 尺寸: {width}x{height}")

                if "期货" in title:
                    print("  >>> 找到期货通窗口!")
                    win.set_focus()
                    win.maximize()
        except Exception as e:
            print(f"连接进程 {pid} 失败: {e}")
