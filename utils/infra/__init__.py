"""L1 基础设施层 — 配置/日志/单例/异常/Feature Flag.

模块整合 8.4 — ARCHITECTURE §2.1
当前阶段：T1.2 占位（仅创建目录结构）
后续任务：
  - T1.3: feature_flags.py 实现
  - T1.5: bootstrap.py 实现
  - T1.6: core.py 实现
  - 后续迁移: config_manager.py / logger.py / trading_env.py

向后兼容：通过 utils/__init__.py re-export 保持旧路径可用（T1.4 实现）
"""
