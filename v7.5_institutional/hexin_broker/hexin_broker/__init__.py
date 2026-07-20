"""
v7.5 同花顺客户端自动化适配层
================================

将同花顺期货/股票模拟交易软件接入现有交易执行架构。

设计原则:
    1. 客户端自动化：通过窗口句柄/控件自动化操控同花顺客户端
    2. 最小侵入：新增 HexinBroker，不改动现有 SmartOrderRouter 接口
    3. 股票+期货双市场覆盖
    4. 异常兜底：客户端异常时自动回退到 SimulatedBroker

依赖:
    - pywinauto (Windows GUI 自动化)
    - pyautogui (备用鼠标键盘控制)

文件结构:
    hexin_broker/
    ├── __init__.py
    ├── hexin_broker.py       # 主适配类，实现 BrokerAPI
    ├── hexin_config.py       # 同花顺客户端路径、窗口名、控件映射
    ├── stock_trader.py       # 股票交易自动化
    ├── futures_trader.py     # 期货交易自动化
    ├── query.py              # 持仓/资金查询
    └── utils.py              # 通用工具函数

接入点:
    - daily_workflow.py / main.py:
        broker = HexinBroker() if 客户端可用 else SimulatedBroker()
    - smart_order_router.py:
        无需修改，HexinBroker 已实现 BrokerAPI 接口
"""

try:
    from .hexin_broker import HexinBroker
except Exception:
    HexinBroker = None  # type: ignore

__all__ = ["HexinBroker"]
