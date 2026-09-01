"""项目测试包。

将 tests/ 从 namespace 包提升为常规包, 防止 import tests 时被
sys.path 中其他同名 tests 目录 (如 qlib/tests) 劫持 (PEP 420
namespace 包优先级低于常规包)。2026-08-30 修复 pytest 全量收集
7 errors (qlib/tests/__init__.py → loguru 导入) 而加入。
"""
