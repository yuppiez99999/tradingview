# P0 大文件拆分方法论 (2026-08-23)

> 本文档记录将超2000行门禁的P0文件拆分为≤2000行的两种安全模式。
> 适用场景: 机构级代码质量门禁要求单文件≤2000行，需, 不改API# 模式一: Mixin 继承拆分

`contains: mixin-pattern` `contains: backward-compat` `contains: mro`

## 适用条件

- 单个巨型类 (>2000行) 包'数据加载'、'计算'、'训练' 等可分组方法
- 外部调用+ 测试均通过 `ClassName.method()` 访问E 不关心方法定义位置

## 步骤

1. **识别方法组**: 按职责将方法分组（如 数据加载组、信号计算组、训练组）
2. **创建 Mixin 文件**: 每组一个 `utils/xxx_mixin.py`，定义 `class XxxMixin:` 包含该组所有方法
3. **处理模块级依赖**: Mixin 文件需自行导入所需常量/标志/logger（与主文件相同的 try/except 模式）
4. **修改主文件**:
   - 添加 `from utils.xxx_mixin import XxxMixin`
   - 修改类定义: `class%MixinB, MixinC):`
   - 删除已提取的方法定义
5. **配置 ruff**: 在 `ruff) = ["BLE001", "ANN", ...]`
6. **验证**: `ast.parse` 语法检查 → import 检查 → MRO 检查 → 测试

## 关键注意

- **Mixin 方法引用的模块级常量**: Python 方法在 mixin 模块中定义时，globals 查找的是 mixin 模块的 globals，不是主文件的 globals。必须在每个 mixin 文件中重新导入/计算B 不可用时降级为动量信号，mixin 文件需自行判断 `_HAS_LGB`
A, B, C, object] — D 优先，方法查找从左到右

## 实例

`institutional_pipeline_runner.py` 2529→1841行:
- `utils/pipeline_data_mixin.py` (382行D 数据加载 + 快照 + Alpha信号
- `utils/pipeline_lgb_mixin.py` (361行GB Walk-forward 训练 + 模型缓存
- `utils/pipeline_signal_mixin.py` (363行): 技术指标 + 多源真实信号

---

# 模式二: 独立组件抽取

`contains: component-extraction` `contains: re-export` `contains: test-mock-fix`

## 适用条件

- 文件包含多个**互相独立**的类（非同一类的不同方法）
- 这些类被下游类使用，但彼此之间无继承/组合关系

## 步骤

1. **识别独立类**: 找到不互相依赖的类（如 TradingCalendar、MarketStateEvaluator、ExecutionStrategy）
2. **创建组件文件**: `utils/xxx/yyy_components.py`，包含所有抽取的类
3. **处理 TypedDict/常量**: 若被抽取类使用 TypedDict，一并移到组件文件
4. **修改主文件**:
   - 添加 `from utils.xxx.yyy_components import ClassA, ClassB, ...`
   - 删除已提取的类定义
5. **re-export 向后兼容**: 若外部代码 `from main_module import TypedDictX`，在主文件 import 中加 `# noqa: F401 — re-export for backward compat`
6. **修正测试 mock 路径**: 若测试 `patch("main_module.datetime")` 但类已移到组件文件，需改为 `patch("components_module.datetime")`
7. **配置 ruff**: 组件文件添加 per-file-ignores

## 关键注意

- **re-export**: ruff --fix 会移除"未使用"的 import，但 re-export 需要保留 → 加 `# noqa: F401`
- **mock 路径**: `patch(".datetime")` patch 的是模块级 `datetime`，类移到新文件后，方法的 globals 变了，mock 路径必须更新
- **TypedDict**: 若 TypedDict 被主文件和组件文件都用，定义放组件文件，主文件 re-export

## 实例

`automated_execution_system.py` 2691→1860行:
- `utils/execution/execution_components.py` (763行): TradingCalendar + MarketStateEvaluator + ExecutionStrategy + SpecialDayEntry
- 主文件 re-import 三个类 + SpecialDayEntry (F401 豁免)
- 测试 `test_g7_automated_execution_boost.py` mock 路径从 `automated_execution_system.datetime` 改为 `execution_components.datetime`

---

# 通用最佳实践

`contains: best-practices` `contains: ruff-config` `contains: verification-checklist`

## ruff.toml 配置模式

```toml
# 新 mixin/组件文件: 继承主文件的豁免规则
"utils/pipeline_data_mixin.py" = ["BLE001", "ANN"]
"utils/execution/execution_components.py" = ["BLE001", "C901"]
```

## 验证清单

1. `python -c "import ast; ast.parse(open(f).read())"` — 语法检查
2. `python -c "from module import Class"` — import 检查
3. `python -c "from module import Class; print(Class.__mro__)"` — MRO 检查 (Mixin模式)
4. `python -m ruff check <files>` — 零违规
5. `python -m pytest tests/... -q` — 相关测试全通过
6. `git commit` — pre-commit 全部门禁通过

## 行数目标

- 主文件: ≤2000行 (门禁要求)
- Mixin/组件文件: 200-400行 (AGENTS.md 典型范围)
- 最大: 800行 (AGENTS.md 上限)

## 不可变性原则

- 拆分不改 API: 外部调用方式完全不变
- 拆分不改测试: 除 mock 路径外，测试代码不变
- 拆分不改行为: 方法逻辑原封不动移到新文件

---

# 踩坑记录

`contains: pitfall` `contains: gotcha`

## 坑1: ruff --fix 移除 re-export import

**现象**: `from components import SpecialDayEntry` 被 ruff --fix 移除，导致外部 `from main_module import SpecialDayEntry` 失败
**根因**: ruff 认为主文件未直接使用 SpecialDayEntry (仅 re-export)
**修复**: 添加 `# noqa: F401 — re-export for backward compat` 注释

## 坑2: mock 路径失效

**现象**: 测试 `patch("main_module.datetime")` 后，移到组件文件的方法仍使用真实 datetime
**根因**: Python 方法的 `__globals__` 绑定定义模块，不是调用模块。`patch` 只替换指定模块的属性
**修复**: `patch("components_module.datetime")` — patch 方法实际所在的模块

## 坑3: PowerShell 变量转义

**现象**: PowerShell 中 `$lines` 等变量被 shell 解释，导致 Python 脚本执行失败
**修复**: 改用 `python -c "..."` 直接执行 Python 代码，或写临时 .py 文件

## 坑4: 行号偏移

**现象**: 添加 import 行后，后续行号全部偏移，导致按行号删除出错
**修复**: 每次编辑后重新 grep 确认行号，或使用 Python 脚本按内容匹配删除

<!-- AUTO-GENERATED: 相关文档 -->
## 相关文档

- [daily_workflow.py 拆分复盘报告 (5 轮 · 2026-08-12)](daily-workflow-split-retrospective.md) (相似度 13%)
- [daily_workflow.py 拆分计划（门禁 ≤3000 行 · 长期架构重构）](daily-workflow-split-plan.md) (相似度 9%)
- [测试健康度治理经验（2026-08-19）](test-health-20260819.md) (相似度 9%)
- [代码质量工业级差距审计 — 2026-08-19](code-quality-industrial-gap-20260819.md) (相似度 7%)
- [异常处理规约 (Exception Handling Standards)](exception-handling-standards.md) (相似度 6%)

<!-- 由 scripts/cairn_cross_ref.py 自动生成，请勿手动编辑此区块 -->
