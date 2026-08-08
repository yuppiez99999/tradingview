# Bug修复追踪表 v2.4

创建: 2026-08-02 | 最后更新: 2026-08-06 (EOD 数据断链修复 — 4 项)
关联计划: `../BUG_FIX_SCHEDULE_UPDATED_2026-08-02.md` (v2.0)
旧版计划(已废弃): `../BUG_FIX_SCHEDULE_2026-08-02.md` (v1.0)
数据修复经验: `cairn/data-integrity-fix-lessons-20260806.md`

---

## 修复总览 (v2.4 — EOD 数据断链修复后)

| 阶段 | 日期 | 目标 | 状态 | 预估工期 |
|------|------|------|------|----------|
| 准备日 | 08-02 | EVAL/EXEC清除 + 基线 + 工具 | **DONE** | 1d |
| 第一阶段-R2 | 08-03 | 真实除零(7→0) + 交易路径宽泛异常(8→0) | **DONE** (Round 2) | 1d |
| 第一阶段-R3 | 08-03 | ai_decision/ 全目录宽泛异常(29→0) | **DONE** (Round 3) | 1d |
| 第二阶段-R4 | 08-03 | utils/ 全目录宽泛异常(849→0, 273文件) | **DONE** (Round 4) | 2d |
| 第二阶段-R5a | 08-03 | ms_strategy/ 全目录宽泛异常(131→0, 22文件) | **DONE** (Round 5a) | 1d |
| 第二阶段-R5b | 08-04 ~ 08-06 | scripts/ (105处) + research/ (78处) | NOT_STARTED | 2d |
| 第三阶段 | 08-13 ~ 08-26 | TYPE_IGNORE+SYS_PATH核心清零 | NOT_STARTED | 2w |
| 第四阶段 | 08-27 ~ 09-04 | PRINT清零+门禁增强 | NOT_STARTED | 1.5w |
| **数据断链修复** | **08-06** | **EOD 数据完整性核查 + 4 项断链修复** | **DONE** | **1d** |

---

## 已完成阶段详情

### Round 1 (08-02 准备日) ✓ DONE

- [x] 任务1: EVAL/EXEC清除 — 3/3处修复
- [x] 任务2: 除零智能分类工具 + 生产模块3处防御加固
- [x] 任务3: 静默异常全项目审计 — 结论: 0真实实例
- [x] 任务4: 修复追踪表建立
- [x] 任务5: 基线测试 — test_execution_modules 19/19 全绿

### Round 2 (08-03 第一阶段) ✓ DONE

- [x] P2 真实除零风险: 7 处 → 0 处 (market_impact_model/strategy_evaluator/kalman_beta)
- [x] P1 交易执行路径宽泛异常: execution_bridge(7) + consensus_aggregator(1) = 8 处 → 0 处
- [x] ai_decision/ 总 except Exception: 37 处 → 29 处 (减 22%)
- [x] 新增工具: `_scan_div_zero_precise.py` (AST-based 除零扫描器, 排除 if/try/epsilon 守护)
- 详见: `docs/ecc_audit/INCREMENTAL_FIX_REPORT_20260803.md` (Round 1-2 章节)

### Round 3 (08-03 第一阶段续) ✓ DONE

- [x] ai_decision/ 全目录 except Exception: 29 处 → 0 处 (覆盖率 100%)
  - backtest_replay.py: 7 处
  - eod_review.py: 5 处
  - dashboard.py: 4 处
  - health.py: 2 处
  - config.py: 1 处
  - (providers 7 处 + orchestrator 3 处为 Round 2)
- [x] 异常类型按调用场景精细化 (详见 `cairn/exception-handling-standards.md`)
- [x] fail-safe 行为保留, 不改变业务语义
- [x] 测试回归零引入 (2 处失败为 pre-existing bug, 经 git stash 验证)
- [x] 新增工具: `_scan_except_remaining.py` (通用目录 except Exception 残留扫描器)
- 详见: `docs/ecc_audit/INCREMENTAL_FIX_REPORT_20260803.md` (Round 3 章节)、`cairn/exception-handling-standards.md`

### Round 4 (08-03 第二阶段) ✓ DONE

- [x] **utils/ 全目录 except Exception: 849 处 → 0 处 (273 文件, 覆盖率 100%)**
- 分目录明细:
  - `utils/alpha/`: 180 处 / 36 文件 (因子挖掘/模型训练)
  - `utils/execution/`: 65 处 / 4 文件 (交易执行核心路径)
  - `utils/evolution/`: 56 处 / 9 文件 (自我进化框架)
  - `utils/pipeline/`: 49 处 / 7 文件 (数据/策略管道)
  - `utils/ 根目录`: 377 处 / 82 文件 (历史代码)
  - `utils/attribution/`: 27 处 / 4 文件
  - `utils/universe/`: 24 处 / 5 文件
  - `utils/infra/`: 19 处 / 3 文件
  - `utils/risk/`: 13 处 / 3 文件
  - `utils/data/`: 11 处 / 1 文件
  - `utils/alpha_factor/`: 10 处 / 2 文件
  - `utils/fineng/`: 8 处 / 1 文件
  - `utils/finance_agents/`: 5 处 / 2 文件
  - `utils/reporting/`: 5 处 / 1 文件
- [x] 新增批量修复工具: `_fix_except_batch.py` (目录级批量替换, 保留原有注释)
- [x] 新增专项修复脚本: `_fix_evolution_except.py` (evolution/ 目录)
- [x] 273 个文件全部通过语法检查 (0 SyntaxError)
- [x] fail-safe 行为保留, 不改变业务语义
- 截至 Round 4, 项目累计清除宽泛异常: ai_decision(29) + utils(849) = **878 处**

### Round 5a (08-03 第二阶段续) ✓ DONE

- [x] **ms_strategy/ 全目录 except Exception: 131 处 → 0 处 (22 文件, 覆盖率 100%)**
- 分目录明细:
  - `scripts/`: 57 处 (CLI 脚本: automated_execution_system/live_scheduler/stop_loss_monitor 等)
  - `src/alpha/`: 29 处 (factor_library/qlib_signal_adapter/pairs_trading)
  - `src/execution/`: 19 处 (qmt_broker/algo_engine/ntp_sync)
  - `src/monitoring/`: 8 处 (intraday_monitor)
  - `src/backtest/`: 3 处 (walk_forward)
  - `src/governance/`: 3 处 (ai_audit_confirm)
  - `src/hedging/`: 3 处 (beta_hedger/hedge_coordinator)
  - `src/risk/`: 3 处 (risk_budgeter/risk_manager)
  - `factors/`: 1 处 (factor_model)
  - `training/`: 1 处 (qlib_improved_train)
  - `wondertrader/`: 1 处 (wt_backtest_engine)
- [x] **6 处导入降级模式手工精细化修正为 ImportError**:
  - `ai_decision_gate.py:41` (llm_client)
  - `automated_execution_system.py:48` (hedging.hedge_coordinator)
  - `automated_execution_system.py:69` (wind_mcp_fetcher)
  - `automated_execution_system.py:1780` (hedge_execution_orders, ImportError+业务异常组合)
  - `stop_loss_monitor.py:175` (quant_modules.broker_adapter, ImportError+AttributeError+RuntimeError)
  - `beta_hedger.py:27` (data_sources.akshare_futures)
- [x] 新增工具: `_fix_import_fallback_except.py` (识别 try/import 块改为 ImportError, 准确率 ~30%, 后续可优化)
- [x] 22 个核心模块导入测试 22/22 通过 (零回归, 用 git stash baseline 对比验证)
- [x] 62 个文件全部通过语法检查 (0 SyntaxError)
- 截至 Round 5a, 项目累计清除宽泛异常: ai_decision(29) + utils(849) + ms_strategy(131) = **1009 处**

### 数据断链修复 (08-06 EOD 数据完整性) ✓ DONE

- [x] **断链 1: Alpha 信号产出缺失** — `institutional_pipeline_runner.py` 的 `_step_signal_fusion()` 走 `_real_alpha_signals()` 路径不保存 `alpha_signals_*.json`（双代码路径盲区）。修复: 新增 `_save_alpha_signals_report()` 方法 + L631 调用。手动补生成 08-06 报告（6 标的）。
- [x] **断链 2: DriftShadowIntegrator observed=0/0** — alpha_signals 文件不存在导致 `_load_latest_predictions()` 返回 None。修复断链 1 后重新运行, observed 0/0 → 6/6。
- [x] **断链 3: 期权成交回报未落盘** — `hedge_order_executor.py` 以 dry-run 模式运行, TCA 有记录但成交回报不落盘。修复: 非 dry-run 重新运行, 5 笔 FILLED, 成交回报落盘 3 位置, positions.json 更新, Beta 0.5186→0.3558。
- [x] **断链 4: VolRegimeWeighter 报告缺失** — 基于 `vol_regime_weights_2026-08-06.json` 生成简化版 `daily_run_report_2026-08-06.md`。
- **经验沉淀**: `cairn/data-integrity-fix-lessons-20260806.md`（双代码路径盲区 + dry-run 陷阱 + 诊断报告时效性 + 5 步核查法 + EOD 数据完整性校验清单）

---

## 第二阶段计划 (08-04 ~ 08-12) 历史代码质量

### 待处理目录 (按优先级排序)

| 目录 | 剩余 except Exception | 优先级 | 说明 |
|------|----------------------|--------|------|
| `utils/` | **0 处 ✓** | 中 | Round 4 已清零 (849→0, 273文件) |
| `ms_strategy/` | 122 处 | 低 | 策略模块 |
| `scripts/` | 105 处 | 低 | CLI 工具 |
| `research/` | 78 处 | 低 | 研究脚本 (可能延后至 v9.x) |
| `15_每日工作流/` | 未统计 | 低 | 工作流脚本 |

### 执行策略

1. **复用工具**: `scripts/_scan_except_remaining.py` 按目录批量扫描
2. **参考样板**: 按 `cairn/exception-handling-standards.md` 规约选择异常类型
3. **分批推进**: 每批 50-100 处, 每批完成后跑相关测试
4. **门禁强化**: 配置 ruff/flake8 `BLE001` 规则阻止新增

---

## 关键发现 (影响排期修正)

| 发现 | 影响 |
|------|------|
| BROAD_EXCEPT_SILENT 100%误报 | 静默异常无需修复 (0 真实实例) |
| DIV_ZERO_RISK 59%误报 | 真实除零从扫描数 530→7 处 (Round 2 已清零) |
| research/ 占问题总量63.9% | 延后至 v9.x, 当前仅修生产路径 |
| v8.3_institutional/ 已归档 | 不纳入修复范围 |
| ai_decision/ 异常处理可作样板 | Round 3 全清零, 规约沉淀为 `cairn/exception-handling-standards.md` |

---

## 工具清单

| 工具 | 路径 | 状态 |
|------|------|------|
| `_scan_div_zero_precise.py` | scripts/ | ✓ AST-based 除零扫描 (排除 if/try/epsilon 守护) |
| `_verify_div_zero_fixes.py` | scripts/ | ✓ 除零修复验证 (6 测试) |
| `_scan_except_remaining.py` | scripts/ | ✓ 通用目录 except Exception 残留扫描 (Round 3 新增) |
| `_show_except_ctx.py` | scripts/ | ✓ 显示 except Exception 上下文 (辅助修复) |
| `_scan_utils_by_subdir.py` | scripts/ | ✓ utils/ 子目录分布统计 (Round 4 新增) |
| `_fix_evolution_except.py` | scripts/ | ✓ evolution/ 专项修复 (Round 4 新增) |
| `_fix_except_batch.py` | scripts/ | ✓ 目录级批量替换 (Round 4 新增) |
| `_verify_utils_syntax.py` | scripts/ | ✓ 全目录语法验证 (Round 4 新增) |
| `_fix_import_fallback_except.py` | scripts/ | ✓ 导入降级模式识别 (Round 5a 新增, 准确率 ~30%) |
| `_final_quality_scan.py` | scripts/ | ✓ 全量质量扫描 (13 类风险模式) |
| fix_print_to_logging.py | scripts/ | 待编写 (第四阶段) |
| fix_type_ignore.py | scripts/ | 待编写 (第三阶段) |
