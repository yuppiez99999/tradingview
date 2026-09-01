# sys.path.insert(0, 子目录) 导致 namespace 包劫持

## 症状
`pytest` 全量收集崩溃 `ImportError: attempted relative import beyond top-level package`
或 import 到错误模块；但单文件收集 `pytest tests/foo.py` 不崩，差异诡异。

## 根因
某模块在模块级 `sys.path.insert(0, 子目录)` 把子目录（如 `qlib/`）置于 sys.path 最前。
项目根 `tests/` 是 namespace 包（无 `__init__.py`），而同名顶层包目录（如 `qlib/tests/`）
有 `__init__.py`，常规包优先级高于 namespace 包，于是 `import tests` 被劫持到子目录的
`tests`，其 `__init__.py` 的相对导入越界报错。

## 修复
把 `sys.path.insert(0, 子目录)` 改为**条件追加**：仅当项目根与子目录均不在 sys.path 时
才 `sys.path.append(子目录)`，避免把子目录顶到最前。

## 验证命令
```bash
pytest tests/ -q --co     # 应 0 errors 收集全部用例
```

## 防复发
排查“全量 vs 单文件收集”差异优先查 sys.path 污染而非 conftest 拦截；
路径插入一律用条件 append，绝不用 `insert(0, 子目录)`。
