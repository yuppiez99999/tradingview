#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""薄包装: 让 CI 的 scripts/_smoke_runner.py 路径可解析。

实际实现在 _archive/one_time_scripts/_smoke_runner.py。
此文件同时支持两种调用方式:
    1. 直接运行: python scripts/_smoke_runner.py  → 执行归档脚本的 __main__
    2. 被 import: import _smoke_runner  → 不执行副作用, 暴露归档脚本的符号

创建: 2026-08-06 工程地基修复 v9.0 (UPGRADE_PLAN_v9.0)
"""
from __future__ import annotations

import importlib.util
import runpy
import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_ARCHIVE_SCRIPT = _PROJECT_ROOT / "_archive" / "one_time_scripts" / "_smoke_runner.py"

# 预注入项目根路径, 使归档脚本内的 sys.path 操作基于正确路径
for _p in [_PROJECT_ROOT, _PROJECT_ROOT / "v8.3_institutional", _PROJECT_ROOT / "v8.3_institutional" / "src"]:
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))


def _load_archive_module():
    """用 importlib 加载归档脚本为模块 (不执行 __main__)."""
    spec = importlib.util.spec_from_file_location("_smoke_runner_impl", str(_ARCHIVE_SCRIPT))
    if spec is None or spec.loader is None:
        raise ImportError(f"无法加载归档脚本: {_ARCHIVE_SCRIPT}")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# 被 import 时: 加载归档模块并暴露其所有符号 (不执行 __main__)
_archive_mod = _load_archive_module()
for _name in dir(_archive_mod):
    if not _name.startswith("__"):
        globals()[_name] = getattr(_archive_mod, _name)


if __name__ == "__main__":
    # 直接运行时: 执行归档脚本的 __main__ 逻辑
    sys.argv[0] = str(_ARCHIVE_SCRIPT)
    runpy.run_path(str(_ARCHIVE_SCRIPT), run_name="__main__")
