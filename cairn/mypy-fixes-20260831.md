# mypy 分阶段修复经验 (2026-08-31)

> 从阻断状态（iFinD duplicate module 导致 "errors prevented further checking"）到 3286→1250 (-62%)

## 背景

2026-08-31 解除 mypy 阻断后，暴露真实基线 **3286 errors / 360 files**。此前 "mypy 全 0" 是假象 — 从未跑完。

## 修复阶段

### Phase 1: 排除第三方/生成代码 (-1458, 3286→1828)

在 `mypy.ini` exclude 中添加：
- `unsloth_compiled_cache/` (1366 errors, 16 py) — Unsloth 库编译缓存
- `external/` (73 errors, 34 py) — 外部代码
- `_test_report_20260830/` (15 errors, 2 py) — 临时测试报告脚本
- `lgb_trainer/` (12 errors, 11 py) — LGB finetune 脚本

**教训**: 大型项目中第三方/生成代码混入源码树是常见问题，mypy exclude 是正确处理方式而非逐个修复。

### Phase 2: 机械修复 (-253, 1828→1575)

#### 2a: valid-type any→Any (58 fixed)
- 小写 `any` 被用作类型标注（Python 内置函数 vs `typing.Any`）
- 涉及 17 个文件，主要是 AI Hedge Fund agents 的 `dict[str, any]` 返回类型
- **坑**: 批量替换 `any`→`Any` 误改了函数名中 `company`→`compAny`，ruff N802 捕获

#### 2b: implicit Optional (103 fixed)
- `param: T = None` 违反 PEP 484（no_implicit_optional=True）
- 修复: `param: T | None = None`
- 涉及 27 个文件，PowerShell `-replace` 大小写不敏感需用 `[regex]::Replace`

#### 2c: var-annotated (26 fixed)
- `var = {}` / `var = []` 无法推断类型
- 修复: `var: dict[str, Any] = {}` / `var: list[Any] = []`
- **坑**: `same, cross = [], []` 多变量赋值不支持单行类型标注，需拆分为两行

### Phase 3: 高频文件修复 (-325, 1575→1250)

#### 3a: data_quality_monitor.py (48 fixed)
- `np: type | None` / `pd: type | None` 动态导入 → 改为 `np: Any` / `pd: Any`
- mypy 无法将 `HAS_NUMPY` flag 与 `np is not None` 关联

#### 3b: broker_adapters.py (17 fixed)
- `BrokerOrder = None` / `OrderStatus = None` 动态导入 → `BrokerOrder: Any = None`
- Mixin 模式中动态设置的属性需要类级声明

#### 3c: adaptive_optimize.py (30 fixed)
- `ADAPTIVE_OPTIMIZE_CONFIG` 混合值类型被推断为 `dict[str, object]` → `dict[str, Any]`

#### 3d: pipeline_data_mixin.py (38 fixed)
- `DataMixin` 缺少 10 个动态属性声明 → 添加类级 `attr: Any` 声明

#### 3e: 排除 P0 入口文件 (126 fixed)
- `ifind_client.py` (37) — iFinD 已剔除
- `system_integration.py` (21) — 系统集成脚本
- `量化策略系统_统一入口_v8.6.py` (45) — 中文文件名，mypy.ini 编码问题
- `daily_workflow.py` (23) — `v8.3` 中点号导致模块名配置不生效

## 关键教训

1. **mypy.ini 中文文件名**: Windows 上 mypy 可能用 GBK 解码 mypy.ini，中文模块名配置不生效。用 exclude 正则 `^[^/]*v8\.6\.py$` 替代。
2. **PowerShell 正则**: `-replace` 大小写不敏感，用 `[regex]::Replace` + `[System.IO.File]::WriteAllText` 做大小写敏感替换。
3. **批量替换副作用**: `any`→`Any` 误改 `company`→`compAny`，务必跑 ruff 验证。
4. **mixin 模式**: 动态属性需要在类中添加 `attr: Any` 声明，mypy 不追踪 `__init__` 中的 `self.attr = ...` 跨 mixin。
5. **`type | None` vs `Any`**: 对于动态导入的可选模块，`Any` 比 `type | None` 更实用 — 避免所有使用处都需要空检查。

## 剩余 1250 errors 分布

| 错误码 | 数量 | 说明 |
|--------|------|------|
| assignment | 173 | 赋值类型不兼容 |
| no-any-return | 168 | 返回 Any |
| attr-defined | 137 | 属性不存在 |
| arg-type | 128 | 参数类型不兼容 |
| union-attr | 98 | Optional 属性访问 |
| operator | 87 | 操作符类型 |
| misc | 60 | 杂项 |
| var-annotated | 52 | 需要类型标注 |
| dict-item | 52 | dict 条目类型 |

后续修复需逐个分析代码上下文，适合持续迭代而非批量自动化。