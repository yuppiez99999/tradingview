# 工程地基修复升级计划 v9.0 (Engineering Foundation Repair)

> **生成时间**: 2026-08-06
> **系统版本**: v8.6.15 → v9.0 (工程地基修复版)
> **主目录**: `e:\各种PY程序\28-终极量化交易系统8.4`
> **依据**: 工业级成熟度评估报告 (`research_report_industrial_grade_evaluation.md`) + 升级计划对比分析
> **前置文档**: `docs/自我升级计划完成进度及后续工程_20260806.md` (功能路线规划，本计划为其补齐工程地基)

---

## 摘要

本计划是对现有 `自我升级计划完成进度及后续工程_20260806.md` 的**工程地基补充**。原计划聚焦功能补齐（U1-U9/Wave1-5/V/F/T/S），但深度审查发现系统存在 6 项**未被原计划覆盖的工程基础设施缺陷**，这些缺陷导致后续所有功能升级都建立在不可验证的地基上。本计划同时启动**陈旧版本与死代码清理**，降低维护负担。

**核心原则**: 先修地基，再盖高楼。所有功能升级（U7/F1/V1/W5）在本计划 P0-P1 完成前暂停推进。

---

## 一、问题诊断汇总（基于源码级审查实证）

| 编号 | 问题 | 严重度 | 实证 | 影响 |
|------|------|--------|------|------|
| E1 | CI 引用的 7 个脚本被归档到 `_archive/one_time_scripts/` | P0 | `_smoke_runner.py` 等已实证归档 | CI 跑不起来，代码变更无门禁 |
| E2 | 测试 collection 阶段 12 个 ERROR | P0 | `hedging`/`risk`/`dsr_bootstrap` 模块路径不匹配或已删除 | 测试无法运行，回归保护为空 |
| E3 | `utils/notify` 监控告警模块从未实现 | P0 | search 0 文件，被 `live_scheduler.py` 引用 | 异常无法外部通知运维 |
| E4 | `system_config.json` iFinD 残留不一致 | P1 | L88 仍写 `secondary_data_source: ifind_mcp` | 配置与文档声明矛盾 |
| E5 | 压力测试空跑（actual_pnl=0.0） | P1 | `reports/stress_test_20260726.json` | 极端情景风控不可信 |
| E6 | 根目录临时文件/备份堆积 | P2 | 10 个 `_*.py` + 7 个 `_*.txt` + 10 个 `.bak_*` | 文件卫生差，维护负担 |

---

## 二、陈旧版本与死代码清理清单

### 2.1 空壳目录（设计意图未落地）

| 目录 | 状态 | 处理 |
|------|------|------|
| `data_pipeline/ingestion/` | 空目录 | 删除（逻辑在 `utils/data/`） |
| `data_pipeline/cleaning/` | 空目录 | 删除 |
| `data_pipeline/features/` | 空目录 | 删除 |
| `data_pipeline/serving/` | 空目录 | 删除 |

> 理由: 四个子目录均为空，真实数据管道逻辑在 `utils/data/data_layer.py` + `utils/pipeline/data_cleaning.py`。空壳目录误导审查者以为有物理分层，实际没有。删除后如需重建物理分层，应在 `utils/pipeline/` 下重构。

### 2.2 根目录临时分析脚本（一次性产物）

以下 10 个 `_*.py` 文件均为历史分析/修复脚本，不属于生产代码，清理到 `_archive/root_tmp_scripts/`：

`_analyze_cli_modes_deps.py`, `_analyze_entry.py`, `_analyze_main_imports.py`, `_check_missing.py`, `_convert_entry_print.py`, `_demo_vol_regime_scenarios.py`, `_fix_entry_ble001.py`, `_inventory_modes.py`, `_restore_tracked.py`, `_verify_vol_regime.py`

以下 7 个 `_*.txt` 为语法检查临时输出，直接删除：
`_final_syntax.txt`, `_socket_test.txt`, `_syntax_errors.txt`, `_syntax_errors2.txt`, `_syntax_errors3.txt`, `_syntax_errors4.txt`, `_syntax_failed_files.txt`

### 2.3 备份文件堆积

10 个 `portfolio_return_projection.json.bak_*` 备份文件（08-04~08-06），保留最新 1 份，其余删除。

### 2.4 陈旧测试（引用已删除模块）

以下测试文件引用了**全系统已不存在的模块**，属于死测试：

| 测试文件 | 引用的缺失模块 | 处理 |
|----------|---------------|------|
| `tests/unit/test_hedge_coordinator_np_bug.py` | `hedging.hedge_coordinator` (在 v8.3 路径下不存在) | 归档（模块在 ms_strategy 下，测试路径过时） |
| `tests/unit/test_hedge_engine_v59_unit.py` | `hedging.hedge_engine_v59` + `risk.portfolio_risk_assessor` | 归档（risk 包已删除） |
| `tests/unit/test_hedge_rebalance_v59_unit.py` | `hedging.hedge_rebalance_v59` + `risk.portfolio_risk_assessor` | 归档 |
| `tests/unit/test_correlation_matrix_unit.py` | `risk.portfolio_risk_assessor` | 归档 |
| `tests/unit/test_coordinate_hwm_drawdown.py` | `hedging.hedge_coordinator` + `hedging.tail_risk_hedge` | 归档 |
| `tests/unit/test_t07_dsr_bootstrap.py` | `v8.3_institutional/src/validation/dsr_bootstrap.py`（文件不存在） | 归档 |

> 处理方式: 移到 `_archive/stale_tests/`，不直接删除（保留可追溯性）。这些测试引用的模块要么已被重构到 `ms_strategy/src/hedging/`，要么已彻底删除。如需恢复测试覆盖，应基于新模块路径重写。

### 2.5 `_archive` 内部清理评估

`_archive/` 已有良好的归档分类（`dead_code/` `legacy_analysis/` `one_time_scripts/` `tools_broken/`），**暂不深度清理**——其中 CI 需要的 7 个脚本会被恢复到 `scripts/`（见 P0-2），其余保留。

---

## 三、P0 级修复（阻断项，立即执行）

### P0-1 恢复 CI 归档脚本到 scripts/ 

**问题**: `.github/workflows/ci.yml` 和 `tdd-guard.yml` 引用 7 个脚本，但它们在 `_archive/one_time_scripts/` 下。

**涉及脚本**:
1. `_smoke_runner.py`
2. `_verify_phase3b_static_analysis.py`
3. `_select_tests_by_diff.py`
4. `_check_coverage_trend.py`
5. `_verify_reexport_compat.py`
6. `_run_v9_regression.py`
7. `_tdd_guard.py`

**修复方案**: 在 `scripts/` 下创建这些文件的**薄包装（thin wrapper）**，转发到 `_archive/one_time_scripts/` 中的实际实现。这样既不破坏归档结构，又让 CI 路径可解析。

**验收**: `python scripts/_smoke_runner.py --help` 等 7 个命令不报 FileNotFoundError。

### P0-2 修复测试 collection ERROR

**问题**: 6 个测试文件引用已删除的模块（`hedging.*`/`risk.*`/`dsr_bootstrap`），导致 pytest 收集阶段 12 个 ERROR，中断整个测试运行。

**修复方案**: 将这 6 个陈旧测试移到 `_archive/stale_tests/`（见 2.4），消除 collection ERROR，让剩余有效测试能正常运行。

**验收**: `pytest tests/ --collect-only` 退出码 0，无 ERROR。

### P0-3 实现 utils/notify 监控告警模块

**问题**: `utils/notify` 模块被 `live_scheduler.py` 等引用但从未实现，ImportError 时 fail-open（只 log warning）。

**修复方案**: 创建 `utils/notify.py`，实现基于环境变量的多通道告警：
- 钉钉 webhook（`DINGTALK_WEBHOOK_URL` 环境变量）
- 飞书 webhook（`FEISHU_WEBHOOK_URL` 环境变量）
- 控制台日志（兜底，无需配置）
- 统一接口 `send_alert(title, content, level='warning')` + `send_sms_alert(message)` 向后兼容

**设计原则**:
- 无配置时降级到控制台日志（不崩溃）
- 单次告警超时 5 秒（避免阻塞主流程）
- 告警失败只 log warning（fail-open，不阻断交易）

**验收**: `python -c "from utils.notify import send_alert; send_alert('test', 'hello')"` 不报 ImportError。

### P0-4 修复 system_config.json iFinD 残留

**问题**: `system_config.json` L88 仍写 `secondary_data_source: ifind_mcp`，但 2026-08-03 已声明从核心降级链剔除 iFinD。

**修复方案**: 将 `secondary_data_source` 改为 `"tdx"`（与 AGENTS.md 降级链一致），移除 iFinD 相关安全说明注释。

**验收**: `system_config.json` 中无 `ifind` 字样（`_security_note` 除外）。

---

## 四、P1 级修复（08-07~08-10）

### P1-1 修复压力测试空持仓

**问题**: `reports/stress_test_20260726.json` 显示 4 大场景 `actual_pnl=0.0`，因运行时持仓列表为空。

**修复方案**: 审查 `utils/stress_test_runner.py`（或对应模块），确保从 `config/positions.json` 加载真实持仓而非空列表。

### P1-2 mypy 基线缩减

**问题**: mypy 有 681 错误（515 error），CI 声称"mypy 阻断合并"但实际不达标。

**修复方案**: 本阶段不追求清零，而是将 ci.yml 的 mypy 检查改为**基线模式**——记录当前错误数，只阻断"新增错误"，存量错误逐步消化。

### P1-3 覆盖率报告产物

**问题**: 文档声称覆盖率 65.20% 但无 htmlcov/coverage.xml 产物。

**修复方案**: 在 CI 中添加 `pytest --cov=. --cov-report=xml:coverage.xml`，并上传为 artifact。

---

## 五、执行顺序与里程碑

```
2026-08-06 (今日)
├── 清理陈旧版本与死代码 (第 2 节)
├── P0-4 修复 system_config.json iFinD 残留
├── P0-1 恢复 CI 脚本 (薄包装)
├── P0-2 归档陈旧测试
├── P0-3 实现 utils/notify
└── 验证 + 生成完成报告

2026-08-07~08-10
├── P1-1 修复压力测试空持仓
├── P1-2 mypy 基线模式
└── P1-3 覆盖率报告产物

2026-08-10 后
└── 恢复原计划功能升级 (U7/F1/V1/W5)，此时地基已稳
```

---

## 六、与原计划的关系

本计划**不替代** `自我升级计划完成进度及后续工程_20260806.md`，而是为其补齐工程地基。完成后：
- 原计划的 U7/F1/V1/W5 等功能升级可以**在可测试、可 CI 验证、可告警**的基础上推进
- 原计划的 U8（期权执行链）和 U9（DriftShadowIntegrator）根据代码实证**已完成或部分完成**，原计划文档需同步更新状态
- 原计划的 S1/S2（C++/Rust、PTP 硬件）维持"远期储备"，本阶段不启动

---

## 七、验收清单

- [ ] `data_pipeline/` 四个空壳目录已删除
- [ ] 根目录 10 个 `_*.py` 已移至 `_archive/root_tmp_scripts/`
- [ ] 根目录 7 个 `_*.txt` 已删除
- [ ] 9 个冗余 `.bak_*` 备份已删除（保留最新 1 份）
- [ ] 6 个陈旧测试已移至 `_archive/stale_tests/`
- [ ] `scripts/` 下 7 个 CI 脚本薄包装已创建
- [ ] `pytest tests/ --collect-only` 无 ERROR
- [ ] `utils/notify.py` 已创建，`send_alert`/`send_sms_alert` 可调用
- [ ] `system_config.json` 无 iFinD 残留（降级链与文档一致）
- [ ] 完成报告已生成
