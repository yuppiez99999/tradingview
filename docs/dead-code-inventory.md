---
type: project_topic
status: active
authoring_mode: ai_generated
created: 2026-08-04
updated: 2026-08-04
---

# 死代码清单与归档记录

> 本文档记录项目死代码扫描结果、归档清单与归档脚本使用方法。
> 扫描日期: 2026-08-04 | 扫描工具: `_scan_dead_code.py` + `_refine_dead_code.py`

## 1. 扫描方法学

### 1.1 五维度识别

| 维度 | 检测内容 | 工具 |
|---|---|---|
| 破损 import | import 的模块在项目中不存在 | AST 解析 + 模块名比对 |
| 未被引用脚本 | verify_*/test_* 未被其他文件 import 或字符串引用 | 全文搜索交叉验证 |
| 临时脚本 | 根目录 _fix_*/_scan_*/_test_* 等临时脚本 | 文件名前缀匹配 |
| 归档遗留 | _archive/_legacy 目录内容 | 路径匹配 |
| 重复文件 | 内容完全相同的文件 | MD5 哈希比对 |

### 1.2 置信度分级

| 级别 | 判定标准 | 处理建议 |
|---|---|---|
| HIGH | 破损 import + 未被引用 / 根目录临时脚本 | 直接归档 |
| MEDIUM | 未被引用但 import 正常 | 人工确认后归档 |
| LOW | 可能误报 | 不处理 |

### 1.3 排除规则

以下目录/文件**不在本次清理范围**:

- `qlib/` `qlib_env/` — 第三方库（微软 qlib 量化框架）
- `research/references/` — 第三方参考代码（Vibe-Trading 等）
- `_archive/` — 已归档目录（已在 .gitignore 中，不进版本控制）
- `utils/_legacy/__init__.py` — 兼容层，标注"严禁删除（破坏 V9 基线）"
- `tests/` 下被 pytest 收集的 `test_*.py` — pytest 自动发现，不算死代码
- `utils/evolution/tests/` — 自我进化框架测试，被 pytest 收集

## 2. 最终归档清单（28 个文件）

### 2.1 高置信度 — 破损 import + 未被引用（4 个）

这些文件既未被其他文件引用，又 import 了项目中不存在的模块，确认为死代码。

| 文件 | 破损 import | 说明 |
|---|---|---|
| `tests/unit/test_gate_manager.py` | `gate_manager` | gate_manager 模块已删除/重命名 |
| `tests/unit/test_predict_annual_return_struct.py` | `predict_annual_return` | 模块已删除/重命名 |
| `tests/unit/test_predict_dynamic.py` | `gate_manager`, `predict_annual_return` | 两个模块都已删除/重命名 |
| `tests/verify_gtja191_risk_report.py` | `enhanced_risk_manager` | 模块已删除/重命名 |

### 2.2 高置信度 — 根目录临时脚本（17 个，实际归档 15 个）

这些是历次代码质量修复（Wave 3 第三阶段 TYPE_IGNORE/SYS_PATH 清零）产生的临时脚本，任务完成后即可归档。

| 文件 | 用途 | 完成状态 |
|---|---|---|
| `_analyze_syspath.py` | SYS_PATH 调用模式分析 | ✅ 已完成 |
| `_analyze_syspath_patterns.py` | SYS_PATH 路径模式深度分析 | ✅ 已完成 |
| `_analyze_type_ignore.py` | TYPE_IGNORE 错误码分布分析 | ✅ 已完成 |
| `_check_pkg_config.py` | 包配置检查 | ✅ 已完成 |
| `_check_syntax.py` | 语法检查 | ✅ 已完成 |
| `_fix_merged_lines.py` | 修复批量替换导致的行合并 | ✅ 已完成 |
| `_fix_root_type_ignore.py` | 根目录 TYPE_IGNORE 修复 | ✅ 已完成 |
| `_fix_type_ignore_batch.py` | 批量 TYPE_IGNORE 修复（早期版本） | ✅ 已完成 |
| `_fix_type_ignore_safe.py` | 安全批量 TYPE_IGNORE 修复 | ✅ 已完成 |
| `_scan_bare_only.py` | 裸注释扫描 | ✅ 已完成 |
| `_scan_dir.py` | 目录扫描 | ✅ 已完成 |
| `_scan_subtypes.py` | SYS_PATH 子类型分析 | ✅ 已完成 |
| `_scan_typeignore_syspath.py` | TYPE_IGNORE + SYS_PATH 联合扫描 | ✅ 已完成 |
| `_test_cninfo.py` | cninfo 接口测试 | ✅ 已完成 |
| `_test_fix.py` | 修复测试 | ✅ 已完成 |
| `_scan_dead_code.py` | 本次死代码扫描器（自身） | ✅ 已完成（执行归档时已删除，跳过） |
| `_refine_dead_code.py` | 本次死代码细化分析（自身） | ✅ 已完成（执行归档时已删除，跳过） |

### 2.3 中置信度 — 未被引用的 verify_* 脚本（7 个）

这些文件 import 正常但未被其他文件引用，属于历史验证脚本，建议人工确认后归档。

| 文件 | 说明 | 风险评估 |
|---|---|---|
| `verify_b33_hedge_refactor.py` | B3.3 HedgeEngine 重构验证 — **`hedge_engine_v59` 已迁移到 `utils/hedge_engine.py`** | 低（已确认 import 破损） |
| `verify_b35_lgb_refactor.py` | B3.5 LGB 训练器重构验证 | 低（重构已完成） |
| `scripts/verify_evolution_v2.py` | 自我进化框架 v2 验证 | 低（v2 已上线） |
| `scripts/verify_p0_fixes.py` | P0 修复验证 | 低（P0 已完成） |
| `scripts/verify_p1_fixes.py` | P1 修复验证 | 低（P1 已完成） |
| `scripts/verify_path_config.py` | 路径配置验证 | 低（path_config 已上线） |
| `scripts/verify_v867_fixes.py` | v8.6.7 修复验证 | 低（v8.6.7 已发布） |

## 3. 归档脚本使用方法

### 3.1 归档脚本位置

```
archive_dead_code.py  (项目根目录)
```

### 3.2 Dry-run 模式（默认，只打印不移动）

```bash
python archive_dead_code.py
```

### 3.3 执行归档（实际移动文件）

```bash
# 归档全部 28 个文件（HIGH + MEDIUM）
python archive_dead_code.py --execute

# 仅归档高置信度 21 个文件（排除 MEDIUM verify_* 脚本）
python archive_dead_code.py --execute --high-only
```

### 3.4 归档目标路径

```
_archive/dead_code/2026-08-04/
├── manifest.json                          # 归档清单（含 src/dst/reason）
├── tests/
│   ├── unit/
│   │   ├── test_gate_manager.py
│   │   ├── test_predict_annual_return_struct.py
│   │   └── test_predict_dynamic.py
│   └── verify_gtja191_risk_report.py
├── _analyze_syspath.py
├── _fix_type_ignore_safe.py
├── ... (其他临时脚本)
└── scripts/
    └── verify_*.py
```

### 3.5 回滚

```bash
# 从 manifest 恢复文件
python archive_dead_code.py --rollback _archive/dead_code/2026-08-04/manifest.json
```

## 4. 未处理的死代码（留待后续）

### 4.1 _archive/ 目录（216 个文件）

`_archive/` 目录已包含 216 个历史归档文件（已在 .gitignore 中），包括：

- `_archive/legacy_analysis/` — 113 个 v6/v7/v9 历史分析脚本
- `_archive/one_time_scripts/` — 87 个一次性测试脚本
- `_archive/tools_broken/` — 2 个已损坏工具

**处理建议**: 这些文件已在 .gitignore 中，不进版本控制，不占用 git 仓库空间。本地磁盘占用约 1.1 MB，可选择性物理删除，但建议保留作为历史参考。

### 4.2 qlib/ 目录（第三方库）

`qlib/` 目录是微软 qlib 量化框架的源码副本，包含大量测试文件（`qlib/tests/`），扫描显示 251 处破损 import（多为可选依赖如 `fire`/`ruamel`/`hyperopt`/`catboost` 等）。

**处理建议**: 不处理。qlib 是第三方库，其测试文件破损 import 是可选依赖未安装导致，非项目责任。

### 4.3 ms_strategy/ 子模块

`ms_strategy/src/hedging/beta_hedger.py` import 了 `data_sources` 模块（不存在），但该文件在 ms_strategy 子模块中，需单独评估。

**处理建议**: 留待 ms_strategy 模块单独清理时处理。

## 5. 归档记录

| 日期 | 操作 | 文件数 | 执行人 | 状态 |
|---|---|---|---|---|
| 2026-08-04 | 创建归档脚本 + 清单文档 | - | AI 协作 | ✅ 完成 |
| 2026-08-04 | 执行 `--execute --high-only` | 19（计划 21，跳过 2 已删除） | AI 协作 | ✅ 完成 |
| 待执行 | 执行 `--execute`（含 MEDIUM 7 个 verify_*） | 7 | 待人工确认 | ⏳ 待办 |

### 5.1 HIGH 置信度归档执行详情（2026-08-04）

- **归档路径**: `_archive/dead_code/2026-08-04/`
- **manifest.json**: 含 19 条记录（src/dst/reason/confidence/category）
- **类别分布**: `broken_unreferenced` 4 个 + `temp_script` 15 个
- **跳过 2 个**: `_scan_dead_code.py` / `_refine_dead_code.py`（扫描器自身，执行前已删除）
- **回滚命令**: `python archive_dead_code.py --rollback _archive/dead_code/2026-08-04/manifest.json`

## 6. 验证清单

归档执行后验证结果（2026-08-04）：

- [x] 17 个关键入口脚本 `py_compile` 全 OK（含 `量化策略系统_统一入口_v8.6.py` / `daily_runner.py` / `archive_dead_code.py` 等）
- [x] 全项目扫描 19 个已归档模块的 import 引用 = **0 悬空引用**
- [x] 19 个源文件全部从原位置移除确认（move 非 copy）
- [x] 归档目录实际文件数 = 19（与 manifest.json 完全一致）
- [x] `pytest tests/ --collect-only` 收集 3891 tests + 15 errors（15 errors 均为预先存在，与本次归档无关；4 个已归档 tests/ 文件未在错误列表中，归档未引入新错误）
- [ ] git status 显示文件删除（待 git add/commit 确认）

## 7. 相关文档

- `cairn/code-quality-wave3.md` — Wave 3 第三阶段方法学
- `cairn/ROADMAP.md` — Wave 3 第四阶段（PRINT 清零）后续任务
