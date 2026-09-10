"""统一入口 (量化策略系统_统一入口_v8.6.py) 的拆分模块包。

内容自入口文件字节级迁出 (2026-09-10, 审计 item 11), 零行为变更:
  - support.py          : 引导层 (路径/日志/单例/可用性开关)
  - helpers.py          : 通用辅助函数 (报告/归档/ML信号段/ETF资金流/执行日志/实盘闸门)
  - deprecated_modes.py : 19 个废弃模式占位 handler (方案A 回退)

注意: 本包不做 eager re-export, 以避免 import 顺序副作用; 请按需 from 子模块导入。

历史: cli/modes/ 为 2026-08-04 废弃的替代入口层 (依赖幻影模块后回退),
未被任何代码 import; 本次未复用该目录, 详见 cairn/LOG.md。
"""
