"""
同花顺期货通模拟盘下单测试

截图中的订单：y2608-C-9000（豆油2608认购9000），1手，买多价512.5

关键策略：
- 修复坐标类型问题（需要整数）
- 使用win32api直接操作
- 尝试查找Edit类型控件
"""
import logging
import sys
import time

logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s')
logger = logging.getLogger(__name__)


def connect_futures_app():
    """连接同花顺期货通"""
    try:
        from pywinauto import Application

        app = Application(backend="win32").connect(title_re=".*同花顺期货通.*")
        logger.info("成功连接同花顺期货通")

        windows = app.windows()

        target_win = None
        for win in windows:
            if win.window_text() == "同花顺期货通":
                target_win = win
                break

        if not target_win:
            target_win = windows[0]

        return app, target_win

    except Exception as e:
        logger.error("连接期货通失败: %s", e, exc_info=True)
        return None, None


def try_find_edit_controls(window):
    """尝试查找Edit控件"""
    try:
        all_controls = window.descendants()

        edit_controls = []
        for i, ctrl in enumerate(all_controls):
            try:
                class_name = ctrl.class_name()
                if "Edit" in class_name or "Text" in class_name or "Input" in class_name:
                    edit_controls.append((i, class_name, ctrl))
            except Exception:
                pass

        logger.info(f"找到 {len(edit_controls)} 个编辑类控件")
        for i, class_name, ctrl in edit_controls:
            logger.info(f"  控件[{i}]: class='{class_name}'")

        return edit_controls

    except Exception as e:
        logger.error("查找控件失败: %s", e, exc_info=True)
        return []


def mouse_click_int(main_window, x, y):
    """鼠标点击（整数坐标）"""
    try:
        from pywinauto import mouse

        main_window.set_focus()
        time.sleep(0.5)

        mouse.click(coords=(int(x), int(y)))
        time.sleep(0.5)

        logger.info(f"点击位置: ({int(x)}, {int(y)})")
        return True

    except Exception as e:
        logger.error("鼠标点击失败: %s", e, exc_info=True)
        return False


def send_keys_to_window(text, pause=0.1):
    """发送键盘输入"""
    try:
        from pywinauto.keyboard import send_keys

        send_keys(text, pause=pause)
        logger.info(f"发送键盘输入: {text}")
        time.sleep(0.3)

        return True

    except Exception as e:
        logger.error("发送键盘输入失败: %s", e, exc_info=True)
        return False


def fill_and_submit_order(main_window, contract_code, quantity, price):
    """填写并提交订单"""
    try:
        rect = main_window.rectangle()
        logger.info(f"窗口区域: {rect}")

        actual_width = rect.right - rect.left
        actual_height = rect.bottom - rect.top
        actual_left = rect.left
        actual_top = rect.top

        logger.info(f"有效区域: width={actual_width}, height={actual_height}")

        # 点击合约输入框（假设在左侧上半部分）
        contract_x = actual_left + actual_width * 0.25
        contract_y = actual_top + actual_height * 0.35
        mouse_click_int(main_window, contract_x, contract_y)

        send_keys_to_window(contract_code)

        # Tab切换到手数
        send_keys_to_window('{TAB}')

        send_keys_to_window(str(quantity))

        # Tab切换到价格
        send_keys_to_window('{TAB}')

        send_keys_to_window(str(price))

        # Tab切换到买多按钮
        send_keys_to_window('{TAB}')
        send_keys_to_window('{TAB}')

        # Enter确认
        send_keys_to_window('{ENTER}')

        time.sleep(2)

        logger.info("订单提交完成！")
        return True

    except Exception as e:
        logger.error("填写订单失败: %s", e, exc_info=True)
        return False


def try_windows_api_direct():
    """尝试使用Windows API直接操作"""
    try:
        import win32api
        import win32con
        import win32gui

        def find_window(title):
            def callback(handle, extra):
                if title.lower() in win32gui.GetWindowText(handle).lower():
                    extra.append(handle)
                return True

            windows = []
            win32gui.EnumWindows(callback, windows)
            return windows

        futures_windows = find_window("同花顺期货通")
        if futures_windows:
            hwnd = futures_windows[0]
            logger.info(f"找到期货通窗口: {hwnd}")

            win32gui.SetForegroundWindow(hwnd)
            time.sleep(1)

            # 发送消息模拟输入
            win32api.keybd_event(0x09, 0, 0, 0)
            win32api.keybd_event(0x09, 0, win32con.KEYEVENTF_KEYUP, 0)
            time.sleep(0.3)

            logger.info("Windows API操作完成")

            return True

        return False

    except Exception as e:
        logger.error("Windows API操作失败: %s", e, exc_info=True)
        return False


if __name__ == "__main__":
    logger.info("=" * 60)
    logger.info("同花顺期货通模拟盘下单测试")
    logger.info("=" * 60)

    # 订单参数
    contract_code = "y2608-C-9000"
    quantity = 1
    price = 512.5

    # 步骤1: 连接期货通
    app, main_window = connect_futures_app()

    if not app or not main_window:
        logger.error("无法连接期货通")
        sys.exit(1)

    # 步骤2: 尝试查找Edit控件
    edit_controls = try_find_edit_controls(main_window)

    if edit_controls:
        logger.info("找到编辑控件，尝试使用控件操作")
        # 使用第一个编辑控件
        first_edit = edit_controls[0][2]
        first_edit.set_focus()
        time.sleep(0.5)
        first_edit.type_keys(contract_code)
        logger.info(f"已在编辑控件输入: {contract_code}")
    else:
        logger.info("未找到编辑控件，使用坐标+键盘方式")

        # 步骤3: 填写并提交订单
        fill_and_submit_order(main_window, contract_code, quantity, price)

    logger.info("\n下单流程已完成，请查看期货通界面确认订单是否提交成功")
