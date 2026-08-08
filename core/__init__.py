# -*- coding: utf-8 -*-
"""core 包 — 统一上下文聚合层入口

修复日期: 2026-08-04
修复原因: core/ 目录缺失, cli/modes/__init__.py 导入 hypothesis.py 时
          from core.context import 失败 (ModuleNotFoundError: No module named 'core'),
          导致整个 CLI 入口不可用。
"""
