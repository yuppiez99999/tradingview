# 工业级防复发机制 (Industrial Grade Anti-Regression Framework)

> **创建日期**: 2026-08-06
> **版本**: v9.0 防护层
> **触发场景**: 工业级成熟度评估后发现"计划完了差距还大"的根因分析
> **适用范围**: 量化交易系统 v8.6+ (可推广到任何Python工程项目)

---

## 背景

2026-08-06 对量化交易系统进行工业级成熟度评估后发现：系统功能极其丰富（12大类因子、六种执行算法、四层风控框架），但工程地基存在系统性短板（测试跑不起来、CI跑不起来、告警没实现、研究生产未隔离）。更深层的问题是：**这些问题长期存在却从未被发现**，因为它们都是"沉默失败"——系统不会崩溃报错，只是默默地不工作或给出无效结果。

根因分析发现五类问题模式：
1. **视角盲区**：自下而上的代码审查派生计划，看不到自上而下的工业级短板
2. **删除不清理**：删模块/改类名时不检查下游引用，留下沉默失败
3. **功能与债务赛跑**：持续加功能但不还工程债务，在沙子上盖楼
4. **文档与代码脱节**：人工维护计划状态，代码改了文档没更新
5. **沉默失败不可见**：代码能跑但输出是空的/错的，传统测试抓不到

本文档沉淀五条防复发机制，每条都有对应的可执行脚本。

---

## 五条防复发机制

### 机制 #1: 工业级判据常驻检查

**防什么**: 自下而上的代码审查看不到全局短板（视角盲区）

**脚本**: `scripts/industrial_grade_check.py`

**原理**: 定义 9 项工业级判据（C1-C9），每次 PR 或盘前自检自动跑一遍。任何判据退化即告警。

**9 项判据**:
| 代码 | 判据 | FAIL 条件 | WARN 条件 |
|------|------|----------|----------|
| C1 | 执行闭环完整性 | 无真实券商下单通道 | QMT已实现但dry_run=true |
| C2 | 数据管道分层 | — | data_pipeline有空壳子目录 |
| C3 | 环境隔离 | — | 生产模块有import research.* |
| C4 | 信号执行分离 | — | 缺少alpha/execution/selector任一 |
| C5 | 测试可运行性 | 测试引用已删除模块 | — |
| C6 | CI可运行性 | CI引用的脚本不存在 | — |
| C7 | 监控告警实现 | utils/notify不存在 | 接口不完整 |
| C8 | 券商接入状态 | 同C1 | 同C1 |
| C9 | 陈旧测试堆积 | 同C5 | — |

**用法**: `python scripts/industrial_grade_check.py [--strict] [--json]`

**集成建议**: 加入 CI 的第一个 job，任何 FAIL 阻断合并；WARN 只报告不阻断。

---

### 机制 #2: 删除时下游依赖检查

**防什么**: 删模块/改类名时留下沉默失败（删除不清理）

**脚本**: `scripts/check_dangling_refs.py`

**原理**: 用 AST 提取所有 `import` 语句，对比已知删除模块清单和已重命名符号清单，发现悬挂引用即报告。

**已知删除模块清单** (新增已删除模块时在此追加):
```python
KNOWN_DELETED_MODULES = {
    "hedging.hedge_coordinator",  # 已移到 ms_strategy/src/hedging/
    "hedging.hedge_engine_v59",
    "hedging.hedge_rebalance_v59",
    "hedging.tail_risk_hedge",
    "risk.portfolio_risk_assessor",  # 已删除
    "src.risk.unified_risk_cockpit",  # 已删除
}
```

**已知重命名符号清单**:
```python
KNOWN_RENAMED_SYMBOLS = {
    "FusionSignal": "已重命名, 检查 utils/signal_fusion.py",
    "get_report_dir": "已删除, 检查 utils/path_config.py",
    "get_log_dir": "已删除, 检查 utils/path_config.py",
    "FLAG_NAME": "已从 attribution 模块删除",
}
```

**用法**: `python scripts/check_dangling_refs.py [--staged]`

**集成**: 已加入 `scripts/pre_commit_check.py` 第二道门禁，每次 git commit 自动检查。

**维护**: 删除模块或重命名类/函数时，必须同步更新 `KNOWN_DELETED_MODULES` 或 `KNOWN_RENAMED_SYMBOLS`，然后运行此脚本确认无悬挂引用。

---

### 机制 #3: 工程债务门槛检查

**防什么**: 功能持续增加但工程债务无人还（在沙子上盖楼）

**脚本**: `scripts/engineering_debt_gate.py`

**原理**: 检查 5 项工程债务指标（T1-T5），计算债务等级（GREEN/YELLOW/RED），RED 时建议冻结新功能。

**债务等级**:
| 等级 | 条件 | 建议 |
|------|------|------|
| GREEN | 全部 PASS | 可推进功能升级 |
| YELLOW | 1-2 项 FAIL | 功能升级需谨慎，同步还债 |
| RED | 3+ 项 FAIL | 冻结新功能，优先还债 |

**5 项债务指标**:
- T1 测试 collection 是否 0 errors
- T2 CI 引用脚本是否都存在
- T3 utils/notify 是否存在（告警能力）
- T4 生产模块是否有 research.* import（环境隔离）
- T5 陈旧测试数量

**用法**: `python scripts/engineering_debt_gate.py`

**集成建议**: 在 `config/feature_flags.yaml` 中新增 `engineering_debt_level` 字段，RED 时所有 Feature Flag 拒绝启用。

---

### 机制 #4: 计划文档代码驱动更新

**防什么**: 计划文档与代码状态脱节（文档说"未启动"但代码已做）

**脚本**: `scripts/sync_upgrade_status.py`

**原理**: 扫描 git log 中的 `[U1]` `[G3]` `[P0-2]` 等标记，对比计划文档中的状态表，发现不一致即报告。

**约定**: commit message 必须带升级项标记:
```
feat(hedge): [U8] 期权执行链补齐 OptionsSimBroker 撮合
fix(notify): [P0-4] 实现 utils/notify 监控告警模块
refactor(data): [G5] 移除 portfolio_optimizer 对 research.* 的 import
```

**用法**: `python scripts/sync_upgrade_status.py [--plan docs/XXX.md] [--update]`

**集成建议**: 在 CI 的文档检查 job 中运行，不一致时 WARN（不阻断）。

---

### 机制 #5: 沉默失败主动探测

**防什么**: 代码能跑但输出是空的/错的（沉默失败不可见）

**脚本**: `scripts/assert_data_validity.py`

**原理**: 对关键输出做"非零断言"——不检查逻辑对不对，只检查输出是不是空的。任何断言失败即调用 `utils/notify` 发告警。

**7 项断言**:
| 代码 | 断言 | FAIL 说明 |
|------|------|----------|
| D1 | 压力测试 actual_pnl 不全为0 | 持仓为空，用了模拟持仓 |
| D2 | alpha_signals n_stocks 不为0 | 因子计算全失败 |
| D3 | DriftShadow observed 不为0/0 | 信号链断裂 |
| D4 | positions.json 持仓数不为0 | 空仓状态异常 |
| D5 | hedge_execution_fill orders 不为空 | 对冲未执行 |
| D6 | daily_returns.jsonl 不连续缺失 | 观察期数据断档 |
| D7 | VIX 值不出现两个口径差异>50% | 数据源口径不一致 |

**用法**: `python scripts/assert_data_validity.py [--date YYYY-MM-DD]`

**集成建议**: 加入 `scripts/run_p0_startup_check.py`，每天盘前自动跑一次。失败时通过 `utils/notify` 发钉钉/飞书告警。

---

### 机制 #6: ruff 增量门禁对"新文件不在基线"的阻断陷阱

**防什么**: 专项新增/改动模块若不在 ruff 基线文件中，任何 BLOCKING 类规则（含 N803/N806 命名规范）命中都会零容忍阻断 CI，即使该文件是本次专项新增的合法模块。

**脚本**: `scripts/ruff_incremental_gate.py` + `scripts/ruff_baseline_gen.py`

**原理**:
- `ruff` 的增量门禁通过 `--baseline` 只扫描**基线外**的代码。新文件不在基线中，因此所有 BLOCKING 规则都会生效。
- `per-file-ignores` 对**新文件不生效**——ruff 的 per-file-ignores 只对已存在于基线中的文件路径匹配生效，新文件路径未在基线中登记，豁免规则不覆盖。
- 结果：专项新增的合法模块（如 `hedge_order_executor.py`）若触发 N803（函数名应为小写）或 N806（变量名应为小写），CI 直接 FAIL，即使代码逻辑完全正确。

**铁律**:
- 新增 Python 文件后，**必须**重跑 `ruff_baseline_gen.py` 重新冻结基线，否则 CI 增量门禁会零容忍阻断。
- 专项收尾阶段新增的模块，应在合并前完成基线重冻结，避免"最后一步被门禁卡住"。
- 若新增文件需要豁免特定规则（如 T201 print），应在 `ruff.toml` 的 `[lint.per-file-ignores]` 中预先配置，而非依赖基线豁免。

**用法**:
```bash
# 重冻结 ruff 基线（新增/改动文件后必须执行）
python scripts/ruff_baseline_gen.py

# 验证增量门禁
python scripts/ruff_incremental_gate.py
```

**集成建议**:
- 在 CI 的 `incremental-static` job 中，若检测到新增文件（`git diff --name-only ${{ github.event.before }} ${{ github.sha }}` 中有未在基线中的 `.py` 文件），自动触发基线重冻结或至少 WARN 提示。
- 专项合并前检查清单新增一项："新增文件是否已纳入 ruff 基线"。

---

## 集成架构

```
盘前自检 (run_p0_startup_check.py)
├── 原有 8 大类 40 项检查
├── [新增] 沉默失败探测 (assert_data_validity.py) → 失败时 send_alert
└── [新增] 工业级判据检查 (industrial_grade_check.py) → 退化时告警

Pre-commit 钩子 (pre_commit_check.py)
├── 第一道: 硬编码路径扫描 (check_hardcoded_paths.py)
├── [新增] 第二道: 悬挂引用检查 (check_dangling_refs.py) → FAIL 阻断提交
└── 第三道: P0 启动自检 (run_p0_startup_check.py --skip-datasource)

CI 流水线 (ci.yml)
├── [新增] Job 0: 工业级判据检查 (industrial_grade_check.py --strict)
├── [新增] Job 0.5: 工程债务门槛 (engineering_debt_gate.py)
├── Job 1: lint-typecheck
├── Job 2: smoke (_smoke_runner.py)
├── Job 3: unit tests
├── Job 4: integration tests
└── [新增] Final: 计划文档同步检查 (sync_upgrade_status.py)

Feature Flag 启用检查
└── [建议] engineering_debt_level=RED 时拒绝启用新 Feature Flag
```

---

## 验证结果 (2026-08-06)

| 脚本 | 验证结果 | 发现的问题 |
|------|---------|-----------|
| industrial_grade_check.py | 6 PASS, 3 WARN, 0 FAIL | C1(执行未接线)/C3(6处import research.*)被正确识别 |
| check_dangling_refs.py | 13 处悬挂引用 | 发现 test_code_quality_extreme_market.py + 5个FLAG_NAME引用 |
| assert_data_validity.py | 6 PASS, 1 FAIL | D1(压力测试actual_pnl=0)被正确识别, 告警已发送 |
| sync_upgrade_status.py | 一致 (无git历史) | 正常运行无报错 |
| engineering_debt_gate.py | YELLOW | T4(6处import research*) FAIL, 其余PASS |

所有脚本均已验证可运行，且能正确发现真实问题。

---

## 维护指南

### 新增已删除模块时
1. 在 `check_dangling_refs.py` 的 `KNOWN_DELETED_MODULES` 中追加模块名
2. 运行 `python scripts/check_dangling_refs.py` 确认无悬挂引用
3. 如果有引用，更新 import 路径或归档引用方

### 新增重命名符号时
1. 在 `check_dangling_refs.py` 的 `KNOWN_RENAMED_SYMBOLS` 中追加 `{旧名: 说明}`
2. 全局搜索旧名并更新

### 新增工业级判据时
1. 在 `industrial_grade_check.py` 中新增 `check_cX_xxx()` 函数
2. 在 `run_all_checks()` 中注册
3. 更新本文档的判据表

### 新增沉默失败断言时
1. 在 `assert_data_validity.py` 中新增 `check_dX_xxx()` 函数
2. 在 `main()` 的 results 列表中注册
3. 更新本文档的断言表

---

## 经验教训

1. **一次性评估不够**：工业级评估做完就走了，下次再评估可能半年后。必须常态化检查才能持续防护。
2. **沉默失败最危险**：代码能跑不等于跑对了。空输出伪装成正常运行，比崩溃更难发现。
3. **功能与债务必须平衡**：每加一个功能就多一条需要测试覆盖的路径。债务不还，功能越多越脆弱。
4. **文档不能人工维护**：人工维护的文档必然和代码脱节。代码驱动的自动同步才可靠。
5. **删除是高风险操作**：删一行代码可能搞挂下游10个引用方。删除时必须检查依赖。

---

## 相关文件

| 文件 | 用途 |
|------|------|
| `scripts/industrial_grade_check.py` | 机制#1 工业级9判据检查 |
| `scripts/check_dangling_refs.py` | 机制#2 悬挂引用检查 |
| `scripts/engineering_debt_gate.py` | 机制#3 工程债务门槛 |
| `scripts/sync_upgrade_status.py` | 机制#4 计划文档同步 |
| `scripts/assert_data_validity.py` | 机制#5 沉默失败探测 |
| `scripts/pre_commit_check.py` | 集成了机制#2的pre-commit钩子 |
| `utils/notify.py` | 告警发送模块(机制#5调用) |
| `docs/GAP_ASSESSMENT_v9.1_工业级达标计划_20260806.md` | 工业级差距评估与达标计划 |
| `docs/COMPLETION_REPORT_v9.0_工程地基修复_20260806.md` | v9.0工程地基修复完成报告 |
