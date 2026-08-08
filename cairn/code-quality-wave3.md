---
type: project_topic
status: active
authoring_mode: ai_generated
created: 2026-08-04
updated: 2026-08-04
---

# Wave 3 代码质量持续修复 — 第三阶段 TYPE_IGNORE + SYS_PATH 核心清零

> 本文档记录 Wave 3 第三阶段 (2026-08-04) 的方法学、决策与踩坑记录。LOG 指针: `cairn/LOG.md` 顶部 2026-08-04 条目。

## 1. 任务定位

**目标**: 系统性处理项目中广泛存在的 `# type: ignore` 裸注释和 `sys.path` 硬编码问题，提升代码质量和可维护性。

**背景**: 项目历经多次重构 (v7.5 → v8.3 → v8.5 → v8.6.14)，历史代码中遗留大量 `sys.path.insert` 硬编码和 `# type: ignore` 裸注释。前者导致路径管理分散、维护成本高；后者使 mypy 类型检查失效，掩盖真实类型错误。

## 2. SYS_PATH 统一化方案

### 2.1 方案选型

| 方案 | 优点 | 缺点 | 决策 |
|---|---|---|---|
| A. `pip install -e .` 包结构 | 彻底解决，符合 Python 标准 | 需建包结构，改动面大 | ❌ 暂缓 |
| B. 统一 `setup_sys_path()` 函数 | 改动小，零行为变更 | 保留 sys.path 调用 | ✅ 选定 |
| C. 完全删除 sys.path 调用 | 最干净 | 破坏现有 import 机制 | ❌ 不可行 |

**决策**: 方案 B。扩展 `utils/path_config.py` 的 `setup_sys_path()` 从 3 路径扩展到 4 路径，20 个入口脚本统一调用。

### 2.2 setup_sys_path() 注入路径

```python
_roots = [
    str(_PROJECT_ROOT),              # 项目根 (utils 包根目录)
    str(get_v8_root_dir()),          # v8.3_institutional/ (autolearn_trainer 等根模块)
    str(get_v8_src_dir()),           # v8.3_institutional/src/ (hedging/signals/risk 等)
    str(get_utils_dir()),            # utils/
]
```

**关键决策**: 新增 `v8.3_institutional/` 根目录注入。原 `setup_sys_path()` 只注入 `v8.3_institutional/src/`，但 `lgb_enhanced_trainer.py` 等文件依赖 `from autolearn_trainer import` (该模块位于 `v8.3_institutional/` 根，而非 `src/` 下)。扩展后 4 路径覆盖所有业务模块导入需求。

### 2.3 改造模式

**模式 1: 多路径硬编码 → setup_sys_path()**
```python
# 改造前
sys.path.insert(0, str(BASE_DIR))
sys.path.insert(0, str(BASE_DIR / "v8.3_institutional"))
sys.path.insert(0, str(BASE_DIR / "utils"))

# 改造后
sys.path.insert(0, str(BASE_DIR))  # bootstrap: 确保 utils 包可导入
from utils.path_config import setup_sys_path  # noqa: E402
setup_sys_path()  # noqa: E402  # 统一注入 v8.3 根 / v8.3 src / utils
```

**模式 2: 单 bootstrap → 保留 + setup_sys_path()**
```python
# 改造前
PROJECT_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(PROJECT_ROOT))

# 改造后
PROJECT_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(PROJECT_ROOT))  # bootstrap: 确保 utils 包可导入
from utils.path_config import setup_sys_path  # noqa: E402
setup_sys_path()  # noqa: E402
```

**模式 3: 相对路径 "." → 绝对路径 bootstrap**
```python
# 改造前 (从其他目录运行会失败)
sys.path.insert(0, ".")

# 改造后
sys.path.insert(0, str(Path(__file__).resolve().parent))
```

### 2.4 保留未改的调用

| 类型 | 文件 | 原因 |
|---|---|---|
| importlib 兜底分支 | automated_execution_system.py / daily_build_and_hedge.py / rebalance_execution_orders.py | 动态加载的 `except ImportError` 兜底，必需保留 |
| 跨项目目录 | generate_daily_report.py (15_每日工作流) / stop_loss_monitor.py (11_量化策略) / 量化策略系统_统一入口_v8.6.py (03_投研) / launch_shadow_account.py (validation 子目录) | 不在 setup_sys_path 范畴 |
| 字符串字面量 | core_modules_check.py (4 处) | 正则误报，实际是字符串内容 |

## 3. TYPE_IGNORE 清零现状

### 3.1 分类处理策略

| 类型 | 处理方式 | 错误码示例 |
|---|---|---|
| importlib 动态加载 | `[misc]` / `[union-attr]` | `module_from_spec` / `loader.exec_module` |
| 动态模块属性 | `[attr-defined]` | `_mod.XXX` / `_m.XXX` |
| 比较运算 | `[operator]` | `target_date > xxx` |
| 字典索引 | `[index]` | `xxx["yyy"]` / `xxx[0]` |
| None 赋值 | `[assignment]` | `= None` |
| Optional 属性 | `[union-attr]` | `self._xxx = ...` |

### 3.2 清零成果

- **项目业务代码**: 裸注释 **0 处** (全部带错误码)
- **第三方库** (`qlib_env/Lib/site-packages/`): 5 处裸注释 (pydantic/setuptools)，不应修改
- **带错误码注释**: 300+ 处覆盖业务代码

## 4. 踩坑记录

### contains: 批量替换导致行合并

**问题**: 早期批量脚本使用 `re.MULTILINE` 模式 + `\s*$` 贪婪匹配，导致多行代码被合并到同一行，引发 `IndentationError: expected an indented block`。

**根因**: `\s*` 会吞掉换行符 `\n`，导致下一行代码被合并到当前行尾。

**修复**: 
1. 改用 `[ \t]*$` (只匹配空格和制表符，不匹配换行符)
2. 逐行处理模式，只替换行尾注释
3. 开发 `_fix_merged_lines.py` 修复已合并的行

### contains: setup_sys_path 路径不足

**问题**: 原 `setup_sys_path()` 只注入 3 路径 (项目根 + v8.3 src + utils)，但 `lgb_enhanced_trainer.py` 的 `from autolearn_trainer import` 失败。

**根因**: `autolearn_trainer.py` 位于 `v8.3_institutional/` 根目录，而非 `v8.3_institutional/src/`。原函数未注入该路径。

**修复**: 扩展 `setup_sys_path()` 为 4 路径，新增 `v8.3_institutional/` 根目录注入。

### contains: verify_b33_hedge_refactor.py 死代码

**问题**: 改造后运行时验证发现 `from src.hedging.hedge_engine_v59 import` 失败。

**根因**: `v8.3_institutional/src/hedging/` 目录不存在 (已迁移到 `utils/hedge_engine.py`)。该文件是 v59 重构前的历史遗留验证脚本，当前项目结构下无法运行。

**决策**: 不在本次清零范围。sys.path 改造仍正确 (统一化目标达成)，死代码清理留待后续。

## 5. 验证清单

- [x] 19 改造文件 `py_compile` 全 OK
- [x] 运行时 import 测试通过 (4 路径注入 + autolearn_trainer + utils 包 + bridges 模块)
- [x] 项目业务代码 TYPE_IGNORE 裸注释 0 处
- [x] ROADMAP.md 第三阶段标记 ✅ DONE
- [x] LOG.md 顶部追加条目
- [x] 本知识专题文档创建

## 6. 后续待办

- [ ] Wave 3 第四阶段: PRINT 清零 + ruff/flake8 BLE001 门禁增强
- [ ] DQC Phase 1: 模块骨架 + P2 检查点实现 (与 Wave 4 对齐)
- [ ] 死代码清理: `verify_b33_hedge_refactor.py` 等历史遗留验证脚本归档
- [ ] `pip install -e .` 包结构方案评估 (长期)
