"""兼容层 — 旧路径 re-export.

模块整合 8.4 — ARCHITECTURE §2.2
当前阶段：T1.2 占位
后续任务：
  - T1.4: 实现 utils/__init__.py 完整 re-export
  - 此处保留作为兼容层 namespace，未来迁移完成后可逐步废弃

设计原则：
  - 严禁删除（破坏 V9 基线）
  - 旧路径 from utils import kill_switch 仍可用
  - 新代码应优先使用 utils/risk/kill_switch 等新路径
"""
