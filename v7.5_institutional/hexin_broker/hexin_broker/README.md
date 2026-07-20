# 同花顺模拟交易接入说明

## 已创建文件

```text
v7.5_institutional/hexin_broker/
├── __init__.py
├── hexin_config.py      # 同花顺客户端路径、窗口名、控件映射
├── utils.py             # 重试/安全调用/窗口查找
├── stock_trader.py      # 股票客户端自动化
├── futures_trader.py    # 期货客户端自动化
├── hexin_broker.py      # 主适配类（实现 BrokerAPI）
└── query.py             # 统一查询封装
```

```text
v7.5_institutional/
└── run_hexin_broker_demo.py  # 工装演示脚本
```

## 接入方式

- 在 `daily_workflow.py` / `main.py` 中替换为：
  ```python
  from execution import HexinBroker
  broker = HexinBroker(enable_client=True, fallback_to_sim=True)
  broker.connect()
  ```
- `SmartOrderRouter` 无需修改，直接传入 `HexinBroker` 即可。

## 客户端配置

请先修改 `hexin_broker/hexin_config.py` 中的：
- `HEXIN_STOCK_EXE`
- `HEXIN_FUTURES_EXE`
- `STOCK_ORDER_CONTROLS`
- `FUTURES_ORDER_CONTROLS`

并确保已安装：
```bash
pip install pywinauto
```

## 注意事项

- 同花顺客户端路径/控件名可能因版本不同而变化，需按实际 UI 调整映射。
- 当前实现优先走客户端自动化，失败自动回退到 `SimulatedBroker`。
