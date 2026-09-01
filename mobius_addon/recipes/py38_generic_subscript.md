# Python 3.8 泛型下标运行时报错（cast(list[Any]) / dict[str, Any]）

## 症状
Python 3.8 下运行含 `cast(list[Any], x)`、`cast(dict[str, Any], r)` 或
`Dict[str, int]` 运行时使用的代码，抛
`TypeError: 'type' object is not subscriptable`，导致依赖该模块的测试整批失败。

## 根因
虽然文件顶部有 `from __future__ import annotations`，但该 future import **只影响
函数注解的字符串化**，无法豁免 `cast()` 实参的运行时求值——`cast(Generic[X], val)`
的 `Generic[X]` 在 3.8 仍是非法运行时下标。变量已是 list/dict 时 `cast` 本就冗余。

## 修复
- 用 `typing` 模块的 `List[...]` / `Dict[...]` 代替内置泛型下标；
- 或变量已是 list/dict 时直接 `return` 并加 `# type: ignore[return-value]` 去掉多余 cast；
- `cast(Any, ...)` 合法保留。

## 验证命令
```python
from typing import List
cast(List[int], [])          # 3.8 下通过
# cast(list[int], [])        # 3.8 下抛 TypeError
```
原函数单元测试应由失败转 passed。

## 防复发
凡 `cast(list[...])` / `dict[...]` / `X[Y]` 在 3.8 须用 `List` / `Dict` 或去掉 cast；
不能因文件有 `from __future__ import annotations` 就以为泛型下标在运行时安全。
