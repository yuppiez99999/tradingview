***
type: project\_topic
status: active
authoring\_mode: ai\_generated
created: 2026-08-02
updated: 2026-09-11
related:

- cairn/gnn-supply-chain-factor.md
- docs/ROADMAP结构审查回应_发布治理_20260905.md

***

# v8.7 Release Control Board（ROADMAP）

> **2026-09-05 结构重组（R-1，审查拍板）**：本文从"历史记录+计划+决策日志"875 行压缩为 **Release Control Board ~300 行**。当前事实只看本文件顶部 `CURRENT STATE`；全部历史叙述与过程修正下沉 `cairn/LOG.md`，本文仅留决策指针。
> **版本口径（三线命名，R-1 落定）**：`software = v8.7`（12-31 发布，权威源 README/CHANGELOG）｜`preset = p9_200w`（200 万生产组合配置，**原 "v9.0 preset" 命名废止**）｜`roadmap = r9.3`（计划文档迭代号）。不存在 v8.8；"v8.8 对冲调优"成果已归入 v8.7 发布清单（08-29 注记）。
> **路线图结构铁律**：`Release → Stream → Gate → Task` 四层；存量 Wave/Sprint/编号保留不重编，仅新增任务用四层。
>
> **旧章节引用兼容映射**（存量文档对本文旧章节名的引用按此读取；LOG 历史条目不回改）：
> §策略优化排期决策 / §ETF期权对冲排期 / §Wave 2 → `§CURRENT STATE` + `§CURRENT QUARTER` + `§ARCHIVED DECISIONS` 决策登记表 ｜ §稳定观察期与运营收敛决策 → `§CURRENT QUARTER`（Change Budget）+ 决策登记表 ｜ §v8.7.1 稳定性增强版本 → `§2027 PLAN` ｜ §v9.0 preset → `preset = p9_200w`（命名已废止）+ `§CURRENT STATE` 资金线 ｜ §云端自动开发任务池 → `§CURRENT QUARTER` 内同名节（保留）｜ §GitHub 集成 Wave 收敛区 → `§2027 PLAN` GitHub 集成 ｜ §Phase 3（ETF 影子）→ `§CURRENT STATE` etf_option_submodel + `cairn/p3-0-gate.md` ｜ §多策略组合优化 → `cairn/mvsk-higher-moment-optimization.md` ｜ §Wave 6 → `docs/Wave6_收尾报告_20261231.md`。

---

## CURRENT STATE（单一事实源，更新于 2026-09-08）

```yaml
release:
  target: v8.7
  software_release: 2026-12-31        # 仅软件发布, 资金/模型不动 (R-3)
  production_switch_window: 2027-01-02 ~ 01-09   # 200万资金 + S12 + MVSK, 独立 Go/No-Go (R-3 原四项; qlib 09-07 停跑归档 R-6, 修订 D-1/D-2/D-4)
  performance_targets:                # 实盘绩效目标 (用户拍板 2026-09-05, 取代"每天稳定盈利"口径)
    accounts: "证券 200 万 (p9_200w_preset) + 期货 100 万 (对冲/套利载体, 开户/接入/验收未排期 → 2027)"
    annual_return: "8% ~ 18% (组合口径 = (证券 PnL + 期货 PnL) / 期初实际到位总权益, 目标结构 300 万 = 200 + 100; 期望值非承诺)"
    max_drawdown: "≤ 10% (预算线, 风控四层 + Kill Switch 硬约束)"
    monthly_win_rate: "≥ 9/12 个月正 (75%) — 月正 = 费后净收益 > 0, 含期货端(就位后); 原「≥70%/8-9 月」括号口径作废 (F02/R-6)"
    daily_target: "废止 — 日度稳定盈利数学上不存在 (等价年化夏普 26), 允许亏损日, 单日损失由风控门约束"
    acceptance_basis: "真实资金影子绩效 (20万→100万→200万灰度), 非回测; 期货端定位对冲+套利 (IC/IM/IF Beta + 基差), 杠杆 ≤2 倍"
    capital_caliber: "权威口径 = 300 万 = 证券/ETF 腿 200 万 + 对冲腿 100 万 (R-11, 2026-09-11)。唯一维护点 = config/risk_thresholds.yaml → capital_base; 腿语义: total=风控预算 / stock_etf=再平衡链 / hedge=对冲链。运行时实际账本经 resolve_effective_capital 优先取用。禁止无腿限定的「总资金 500 万」表述"
    etf_option_subportfolio: "200万 ETF+期权子组合 (v9.1) 净年化目标 **4.25%** (自下而上, 见 §etf_option_submodel.p9_200w_v91, R-10; 09-11 修订: 成本对齐引擎实收 1.25% ⇒ 原 4.3% 系按 1.2% 近似) — 与上面 8%~18% **不是同一口径** (后者 = 证券200 + 期货100 合计), 禁止互相引用或相加"
  batch_plan:
    - "12-10 功能冻结"
    - "12-11~12-20 RC-1 只验证不改功能"
    - "12-21 RC-2 最终配置锁定"
    - "12-22~12-30 Release rehearsal 完整模拟生产"
    - "12-31 v8.7 软件发布 (资金保持旧配置)"
    - "01-02~01-09 生产切换窗 (逐项实施, 每项独立回滚预案)"

critical_gates:
  D11_phase_b_shadow:
    stable_days: "12/7 ✓ (09-07 EOD)"
    samples: "12/20 (09-07 EOD, 每交易日+1)"
    status: ON_TRACK
    pass_expected: 2026-09-17 EOD (20/20)
    reverify: 2026-09-18
    reverify_checklist: docs/d11_reverify_checklist_20260830.md   # R-5: 09-18 前扩展完整性子项, 代码判据保持双条件不变
  gates_trio:
    industrial_grade_check: "10 PASS / 2 WARN (C1 xtquant 未装=物理阻塞; C10 fills 最新为 09-10 非当日) / 0 FAIL — 09-11 实测"
    assert_data_validity: "12 PASS / 0 FAIL — 09-11 实测 (D1 压力测试遗留**已闭环**: stress_test_20260910.json 4 场景 0 为零)"
    engineering_debt_gate: "09-11 实测 BLOCK, 原因仍为 D11 PhaseB shadow 样本未满 (12/20; 09-17 EOD 达标后消解; T6 YELLOW 口径见 09-08 记)"
  daily_workflow: "2180 行 ≤3000 ✓ (D7 门禁, 09-08 实测)"
  coverage: "0.833 ≥0.80 ✓ (reports/ci/coverage_baseline.json 冻结)"

phase_b:                              # 权威源: scripts/phase_b_progressive_enabler.py --check
  B1_USE_DRIFT_DETECTOR: enabled (2026-08-26)
  B2_USE_FEEDBACK_LOOP: enabled (2026-08-27)
  B3_USE_AUTO_RETRAIN: enabled (2026-08-27)   # 阶段轨已达最终阶段 orchestrator (09-01)
  B4_USE_MLOPS_PIPELINE: enabled (2026-09-11)  # warmup 8/8 全闭环 + 双签启用 (phase_b_enabler/phase_b_health_gate); 首 EOD 验证待 09-11 EOD
    # B4 真实路径 = phase_b_b4_shadow_runner.py shadow 7 天 → 评估启用 (非 enabler --auto/--advance 直接推进)

shadow_lines:
  S12_P3_defensive: running                  # P3.2 每日 EOD (S12_Shadow_EOD 16:30), NAV 1.0117, 评估计算 10-13 (统一评估周)
  mvsk_30day: cron_registered                # Shadow30Day_EOD 16:35, 首触发 09-07, 窗口 09-13~10-12 = 30 自然日 (实际交易日 ~14-15, R-6 口径), preflight 9/9 绿; 仅 MVSK P5-2 (qlib 已停跑, 键名自 mvsk_qlib_30day 简化 R-6)
  gnn_s6_paper: cron_registered              # GNN_S6_Paper_EOD 16:50, 首触发 09-07, 窗口 09-13~10-12
  b4_llm_loop: running                       # b4_shadow_status.json, history/run_count 已幂等对齐 (09-05 治理)

erl_evolution_rebalance:
  er_1x: done (08-27, 三缺口代码全就绪)
  er_2x_flag_dual_sign: pending              # 09-13~09-18 执行 (D-3), 冻结窗前唯一空档
  er_3x: frozen_window_observe_only          # 12-31 后 Stage 2→3 (D-3)

etf_option_submodel:                          # 定位: S12 纯防御风险平价 "诚实下限" (D-4/D-5)
  s12_shadow: running (P3.2)
  p3_3_evaluation: script_ready (run_p33_evaluation.py, 评估计算 10-13; 2026-09-05 增量一致性四项)
  p3_3_acceptance: "收益正向 / 回撤<15% / 换手正常 / 影子年化落回测滚动30日分布带 [P5,P95] (实测 -5.12%~24.35%)"
  s12_defense_acceptance: "收益正向(≥ 同期 CPI 统计局口径) / 30日滚动回撤 ≤5% (回测2.60%) / 与主组合权益层相关性 <0.3"   # D-4
  production_path: 并入 p9_200w 灰度 (Sprint3-1/2/3), 无独立资金灰度
  p9_200w_v91:                                  # 200万 ETF+期权子组合 (v9.1「守正」, config/portfolio_200w_etf_v91.yaml) —— R-10 口径拍板
    target_annual_return: "净 4.25% = 毛 5.5% − Collar 1.25% (自下而上, 期望值口径非承诺; 成本 = collar.cost_target_pct [1.0%,1.5%] 中值, 护栏强制与引擎实收一致; 原 8% / 5.5%~6.5% 口径废止)"
    target_range: "[3.5%, 5.5%] 诊脉书卷四测算区间 — 目标只能取自下而上基据 (config target.target_basis)"
    max_drawdown: "15% (预算线, 范围 10%~18%); 静态权重实测 15.76% 属**未含 L1-L4 减仓的上界**"
    acceptance: "① 净年化 ∈ [3.5%,5.5%] ② 回撤 ≤15% (含 L1-L4 减仓后) ③ 价格基与 Wind 逐日一致 (零容差) ④ 门禁三件套 0 FAIL"
    evidence: "scripts/run_200w_etf_backtest.py (权重+L1-L4+Collar成本) + scripts/verify_etf_price_source.py (数据源交叉核验) + tests/unit/test_portfolio_v91_config.py"
    scope_note: "本口径仅适用 200万 ETF 子组合; 与 performance_targets.annual_return (证券200+期货100 合计 8%~18%) **不是一个口径**, 禁止互相引用"

mvsk_p5:
  p5_1_shadow_ready: done (08-24, W7.1.6)
  p5_2_30day: cron_registered (窗口 09-13~10-12)
  p5_3_switch: 判定于统一评估周 (10-13 计算起), 实施后置生产切换窗 (R-3 修订 D-1)

qlib_lgb_v2:
  model_ready: done (08-24, W7.1.8)
  shadow_30day: 停跑归档 (2026-09-07 R-6, 双设计缺陷口径 FAIL; 重开前提 = 先修接线错配, 另行评估)
  known_gap: "⚠ 双设计缺陷: ①接线错配(消费端 4 只 ETF vs 模型 86 只个股, 每日全走 fallback 随机数) ②信号静态(predictions 截至 2026-07-08, 非每日推理) → W7.2.9 的 Δ夏普对比统计上无意义, 10-13 评估判 FAIL(设计缺陷口径) + D-2 不切换; 决策材料 = docs/qlib_w729_gap_analysis_20260905.md; 09-07 停跑归档 (R-6)"
```

## RELEASE GATES（R-2 铁律，2026-09-05 拍板）

**RELEASE GATE ≠ RESEARCH GATE。** MVSK / GNN / ERL / S12 / Alpha Registry / regime risk budget **均不得成为 v8.7 发布的隐式前置条件**（qlib 已于 R-6 停跑归档，不再列入）。统一评估周（10-12 收尾 → 10-13 计算 → 10-14~10-16 判定）只产出**切换决策材料**，不产生发布义务。

**Release Blocker 白名单（仅以下可阻塞 12-31）**：

| # | Blocker | 当前状态 |
|---|---------|---------|
| G-1 | D11 双条件 PASS（stable≥7 AND samples≥20，`engineering_debt_gate.py:1094`） | ON_TRACK, 09-18 复验 |
| G-2 | 门禁三件套 0 FAIL（12-31 前 21 天观察窗自 12-10 冻结起算） | **观察窗 12-10 起算, 窗口内 0 FAIL**；D1 遗留 FAIL 在窗口外独立跟踪, 12-10 前闭环或显式豁免（豁免编号见决策登记 R-6） |
| G-3 | daily_workflow ≤3000 行（D7） | ✓ 2159 |
| G-4 | 覆盖率 ≥0.80 基线不退化（D9） | ✓ 0.833 |
| G-5 | 12-10 功能冻结执行 + Change Budget 遵守 | 待执行 |

**Health Score 定位铁律**：Health Score 是 **Dashboard 不是 Gate**——评分高不豁免任何硬门禁 FAIL；硬门禁 FAIL 时不得以"系统 X 分看起来健康"为由放行。

## DECISION NEEDED（pending 决策队列，每日查看路径内）

| 事项 | 决策人 | 截止日 | 现状 / 备选 |
|------|--------|--------|-------------|
| GitHub Actions 云端计费失败（所有 job 未启动） | 用户（Billing） | 09-19 冻结窗前 | `gh run list` 实测 09-08：CI/Quality Gate/ocr 全部触发即失败，报"payments failed or spending limit"；本地门禁全绿不受影响。备选：解除 billing / 或登记豁免转纯本地 CI 口径（影响 QC-1.2/1.4 与 ocr 线） |
| Wind MCP 服务余额不足（kline 返回「余额不足，请先充值」） | 用户（充值 / 替代源登记） | 尽快（影响 EOD/drift/DSR 数据链质量） | 09-11 实测；替代路径 = 腾讯 qfq（已验证）/ 新浪（抖动）/ TDX（K 线空）。当日 9/10 已用腾讯路径补齐 |
| 建仓流水处置：每日 09:00 盘前流返回假「已完成」（计划文件路径分裂） | 用户（恢复建仓 / 停用任务） | 尽快 | 已复现；详见 docs/代码质量与系统Bug审查_20260911.md §P1-1 |
| ~~资金口径唯一定义（现存 5M/5M/5M/2M/3M 五套并存）~~ | 主线 | ~~尽快~~ | **✅ 已拍板并实施 (2026-09-11)**：权威口径 = **300 万 = 证券 200 万 + 对冲 100 万**（对齐 `kill_switch.yaml total_margin` / `system_config.json` / `p9_200w_preset` / ROADMAP accounts）。5M 定性为 2026-07 建仓计划旧口径（审查 §P1-2 实测高估 82.4%、对冲 ~1.82×）。腿语义：`total` 风控预算 / `stock_etf` 再平衡链 / `hedge` 对冲预算；运行时实际账本经 `resolve_effective_capital` 优先取用。详见 `cairn/capital-caliber-decision-20260911.md` |
| ~~D1 压力测试 FAIL 处置~~ | 主线 + 用户拍板 | ~~12-10~~ | **✅ 已闭环 (2026-09-11)**：`assert_data_validity.py` 12 PASS / 0 FAIL，D1 = "stress_test_20260910.json 4 个场景 0 个为零"；G-2 观察窗口径见 R-6 |
| C1 WARN（xtquant 未装=物理阻塞） | 主线 | 12-10（冻结前） | 装 xtquant 或 登记豁免 + 影响评估 |
| qlib W7.2.9 接线修复后重开评估 | 主线 | 2027 Q1（默认不排） | R-6 已停跑归档；重开需先修接线错配（设计缺陷口径 ≠ 模型证伪） |
| ERL Kill 健康度阈值（N 日基线） | 研究侧 | ER-2.x 双签前（09-18） | Kill 判据三件套补全（R-6），阈值待定 |
| 生产切换窗 2027 扩窗/滑移规则 | 主线 | 2026-12 切换窗排期前 | 三项同窗需扩窗或分批滑移（2027 规划） |
| 2027 GitHub Waves 错峰 + 容量预算 | 主线 | 2026-12（Wave 9-GH/11-B/12-B 同日起排期冲突） | F12/F13 待排期审查 |

## NEXT 14 DAYS（09-05 ~ 09-18，至 D11 复验）

| 日期 | 事项 |
|------|------|
| 09-05（六） | ✅ R-1/R-2/R-3 落地（本文件重组 + 拆批次拍板）；✅ shadow 写源治理批次（Tier-2 补登记 + 空 date fail-closed + B4 history 幂等） |
| 09-07（一） | 双 cron 首触发验证（Shadow30Day_EOD 16:35 / GNN_S6_Paper_EOD 16:50；EOD 后 `python scripts/t3_post_market_check.py` **7 项一键**：⑤ 双 cron 产出 + S6 非 skeleton = sys.path 修复终验 / ⑥ C10 fills 新鲜度 / ⑦ B4+D11 进度；含 jsonl 落盘与幂等核对） |
| ~09-09（三） | B4 warmup 7/7 → `phase_b_progressive_enabler.py --check` 评估 USE_MLOPS_PIPELINE 启用 |
| 09-11/12（五/六） | Sprint 1 收尾判定材料：B1+B2 稳定 ≥7 天 + daily_workflow ✓ + R10 ✓（**不含 D11**，09-01 口径预修正已完成；09-12 为周六，材料可 09-11 交易日内预产出） => DONE 2026-09-11 (docs/sprint1_收尾判定材料_20260911.md: 三判据 PASS) |
| 09-13~09-18 | ER-2.x Flag 双签（D-3，冻结窗前唯一空档，双签动作 <0.5 人天） |
| 09-14（一） | shadow 窗口首交易日（MVSK P5-2 / GNN S6 双线；qlib W7.2.9 已 09-07 停跑归档 R-6，30 自然日窗口至 10-12） |
| 09-17（四） | D11 samples 满 20/20（EOD 后双条件达成） |
| 09-18（五） | **D11 复验（预期 PASS）→ 发布门禁 D1-D11 全绿 → 解锁 Sprint3-1（20 万测试，冻结豁免）** |
| 09-19 前 | R-4 落地：Change Budget 机械检查入 engineering_debt_gate + Kill Criteria 统一表 — ✅ **提前完成 09-05**（D12 检查 + 10 单测全绿，窗口外待激活） |
| 09-18 前 | R-5 落地：D11 复验清单刷新（数据/风险/执行/运营完整性子项）— ✅ **提前完成 09-05**（复验版清单 A~E 已入 d11_reverify_checklist） |

## CURRENT QUARTER（2026 Q4：09 ~ 12）

### 三线收敛原则（不变）
- **主线 A**：v8.7 发布与稳定（最高优先级）——Phase B 门禁、D11、真实成交积累、Release Blockers
- **支线 B**：研究增强（shadow/研究模式）——ETF 期权对冲 P3-P5、GNN、自我进化
- **支线 C**：工程化服务主线——测试/CI/文档资产，每周支线合计 ≤2 人天

### Stage 执行锚点（R-3 修订后）
- **Stage A 冻结前（09-03~09-18）**：双 cron 开跑（09-07，MVSK/GNN 双线）→ B4 评估（~09-09）→ Sprint 1 收尾（09-12）→ ER-2.x 双签（09-13~09-18）→ **D11 复验（09-18，预期 PASS）→ 解锁 Sprint3-1（20 万测试，冻结豁免；判据见 docs/sprint3_capital_upgrade_gate_20260905.md）**
- **Stage B 冻结窗（09-19~12-10）**：shadow 双线照常（MVSK P5-2 / GNN S6；qlib 已停跑归档）+ 资金线灰度（Sprint3-1 → Sprint3-2 100 万 shadow 30 天，冻结豁免）+ MVSK/GNN 只产决策材料 + ★ **统一评估周（10-12 窗口收尾 EOD → 10-13 数据落库与计算 → 10-14~10-16 判定）**（P3.3 + MVSK Δ夏普 + GNN S6 观察 + Sprint 2 收尾 → 一份合并评估报告，作为 **生产切换窗独立 Go/No-Go 的共同输入**）+ 12-10 功能冻结
- **Stage C 发布窗（12-10~12-31）**：RC-1（12-11~20）→ RC-2 配置锁定（12-21）→ Release rehearsal（12-22~30）→ **12-31 v8.7 软件发布（仅软件）**
- **生产切换窗（01-02~01-09）**：200 万资金升级（Sprint3-3）/ S12 防御层就位 / MVSK P5-3——**独立 Go/No-Go + 独立回滚预案，逐项实施不捆绑**（R-3 原四项，qlib 切换项已 09-07 停跑归档 R-6 撤销）
- **Stage D 2027**：G4 Alpha Registry + GNN S7 入库 + S12 参数优化 + ERL Stage 2→3 + G3 风险预算 regime（02-15~03-07 shadow → 03 月启用决策）

### 策略六线（2026-09 ~ 2027-06）

| 线 | 主题 | 2026 关键节点 | 2027 |
|---|------|--------------|------|
| 收益线 | MVSK P5（qlib W7.2.9 已 09-07 停跑归档 R-6） | shadow 30 天（09-13~10-12）→ 统一评估周（10-13 计算）→ **实施后置切换窗（R-3）** | 评估未过 → 证伪归档 |
| 防御线 | S12 纯防御风险平价 | P3.2 影子 → P3.3 评估（10-13 起统一评估周）→ **并入主组合灰度切换窗** | 参数优化 01~03 月 |
| 进化线 | ERL 进化→再平衡 | ER-2.x 双签 09-13~18；ER-3.x 冻结窗只观察 | 12-31 后 Stage 2→3 |
| 研究线 | GNN CHAIN_MOM_60D | S6 纸交易 30 天（09-13~10-12）；入库冻结 | S7 入库 01 月起 |
| 资金线 | p9_200w 灰度（冻结豁免） | Sprint3-1（D11 全绿后 20 万，判据见 docs/sprint3_capital_upgrade_gate_20260905.md）→ Sprint3-2（100 万 shadow 30 天，**启动锚点 ≤2026-11-09**，R-6 修正；强制记录成交滑点分布作 3-3 输入）→ **Sprint3-3 切换窗 01 月初（R-3；Go/No-Go 必答 = 容量/冲击成本复核，F16）** | — |
| 基建线 | v8.7.1 G3/G4 | Q4 运营件验收累积 | G4（01 月）→ G3 shadow（02-15~03-07）→ 启用决策（03 月） |

### Q4 Change Budget（09-19 ~ 12-10 冻结窗，R-4 成文；机械检查 09-19 前入 engineering_debt_gate）

```
生产代码:       0 个新 feature
feature flag:   0 个新增 enable
生产模型:       0 个新增
生产因子:       0 个新增 (GNN S6/S7 验证 shadow 继续, 入库后置 2027)
配置变更:       仅允许 risk / bug fix
shadow:         无限制
例外:           资金线灰度 (冻结豁免) / bug 修复 / 风险与性能优化
```

### Research Kill Criteria（R-4 成文，触发即归档不续期；R-6 补全阈值/判定人/证据三件套）

| 项目 | Kill 条件 | 阈值 / 判定人 / 证据 |
|------|----------|----------------------|
| GNN CHAIN_MOM_60D | S6 观察期 CPCV/稳定性不显著 | S6 窗口满期评估（10-13~10-16 判定）；CPCV/稳定性显著性判定人 = 研究侧审查；证据 = GNN_S6_Paper_EOD jsonl + 评估报告 |
| MVSK P5-3 | Δ夏普 ≤ 0 或异常换仓 | Δ夏普 ≥ +0.15 且统计显著（最小效应量，R-6 建议值）为不 Kill 前提；判定人 = 主线+研究侧；证据 = Shadow30Day_EOD jsonl + 归因报告 |
| qlib_lgb_v2 | ~~不优于当前生产信号源（原 V9）~~ | **已 Kill 09-07（R-6）**：双设计缺陷口径 FAIL → 停跑归档不续期；证据 = docs/qlib_w729_gap_analysis_20260905.md |
| S12 | 回撤或成本超预算（P3.3 验收） | P3.3 硬验收：回撤<15% / 换手正常 / 影子年化落回测分布带；判定人 = 主线；证据 = run_p33_evaluation.py 报告 |
| ERL | 灰度期稳定性下降（健康度指标） | 健康度连续 N 日低于基线（阈值待 ER-2.x 双签时定）；判定人 = 研究侧；证据 = Health Score 报表 |
| 新因子 | ICIR < 阈值（S1-S7 门禁） | 沿用 S1-S7 门禁既有阈值；判定人 = 门禁自动化；证据 = 门禁报告 |
| 新模型 | live degradation > 阈值（模型退役标准） | 沿用模型退役标准；判定人 = 监控自动化；证据 = 退役监控报表 |

### 开放问题（仍然开放的，2026-09-05 自旧版 8 条压缩；已解决项随对应任务闭环移除）

1. **情绪因子数据源质量** — 中文财经新闻覆盖率/时效性不足，需更高质量源或替代情绪指标
2. **策略容量天花板** — 当前规模下容量充足，扩大规模需重新评估冲击成本模型
3. **D1 压力测试遗留** — assert_data_validity 1 FAIL 独立跟踪（不阻塞发布；观察窗 12-10 起算前闭环或显式豁免，豁免登记见决策登记 R-6，不再指向 G-2 行注）
4. **T6 fail-safe 宽捕获 61 处**（09-08 实测；09-04 曾清至 29，09-06 ec99b61b 跟踪口径扩容带入存量回升，非新增行为风险）— AUTO-1 对口消化，目标 12-10 冻结前回 ≤30（不阻塞发布，T7 204≤250 GREEN）

> 已闭环：C++/Rust 重写（08-26 ROI 证伪搁置）/ daily_workflow 拆分（2159 行达标）/ Phase B 观察期（B1-B3 已启用）/ V9 上线时间表（被资金线灰度取代）/ CI 缺失脚本（R1+R4 完成）。

### 云端 NPC 自动开发任务池（roadmap-dev crontab 每工作日 16:00 接单）

> 任务约束：① 纯代码/测试/文档/配置；② 不触资金安全；③ 不依赖实盘数据；④ 云端可验证。**Q4 冻结期（09-19 起）只挑 `[稳定性]` 标签**。例外判定三问（R-6 成文）：*是否触 Change Budget？是否可拆为最小独立任务？云端能否闭环验证？* — 三问任一不过即退回。

| 编号 | 标签 | 任务 | 云端验证 |
| --- | --- | --- | --- |
| AUTO-1 | [稳定性] | R10/T6 裸宽捕获精确化批次3（复用 `scripts/_r10_refine_bare_excepts.py` 风格） | ruff BLE001 不新增 + py_compile |
| AUTO-2 | [功能] | LLM 权限边界规范文档 + CI 检测性（非阻断）门禁 | ruff 检查引用完整性 |
| AUTO-3 | [稳定性] | `utils/contracts` parse_symbol() 补 10+ 纯 stdlib 单测 | pytest tests/unit/test_contracts_symbols.py |
| AUTO-4 | [稳定性] | 未使用导入清理（`cli/` + `scripts/`，不碰生产核心） | ruff F401 范围清零 |
| AUTO-5 | [稳定性] | 类型注解渐进补全（独立模块） | mypy 报错数不增 |
| AUTO-6 | [功能] | cairn 知识层交叉引用（GH+-2 实现） | 脚本 --dry-run 可运行 |
| AUTO-7 | [稳定性] | Chaos 测试扩展 1~2 个纯 stdlib 故障注入单测 | pytest tests/chaos/ 全绿 |
| AUTO-8 | [稳定性] | `config/*.yaml` 轻量 schema 校验脚本 | 退出 0 |
| AUTO-9 | [稳定性] | 周期性静态体检（可重复）：BLE001/F401/F811 增量 + py_compile + 裸 except 审计，安全项直接修并建 PR | ruff 不新增 + 报告产出 |
| AUTO-10 | [稳定性] | cairn/docs UTF-8+mojibake 轻量检查（09-11 编码事故防复发；✅ 2026-09-11 已实现 `scripts/check_utf8_mojibake.py` + 12 单测，并入 AUTO-9 体检序列） | `python scripts/check_utf8_mojibake.py` 退出 0 |

> 接单约定：每日读本节 + `cairn/LOG.md` 最近 5 条 → 挑 1 项（优先最旧未完成）→ 最小改动 + 补测试 + 跑门禁 → 推分支建 PR（标题 `AUTO-x`）。

## PRODUCTION INVARIANTS（R-4 成文，2026-09-05；R-6 补验证档：◆机器检查 / ▲周期演练 / ●人工审计）

| # | 不变量 | 实现锚点 | 验证（R-6：◆机器检查 / ▲周期演练 / ●人工审计） |
|---|--------|---------|------|
| I-01 | 任何 LLM 不得直接产生 execution order（三条 LLM 路径均为建议/报告性质） | `ai_decision/decision_gate.py` 硬风控门；规范文档 = AUTO-2 | ◆ CI 门禁 + 单测 |
| I-02 | 任何 research module 不得被 production import ◆ | G5 双门禁 GREEN（08-11）；CI 隔离门禁 = v8.7.1 P2 项 | ◆ CI 隔离门禁 |
| I-03 | 任何 shadow strategy 不得修改 production portfolio | `apply_mvsk_shadow_to_mid_layer` portfolio unchanged=True | ◆ 单测断言 |
| I-04 | 任何数据异常不得产生正常交易信号（data_degraded → fail-closed） | `build_plan_executor.get_emergency_protocol` day_capital_multiplier=0.0 | ◆ fail-closed 单测 |
| I-05 | 任何 NAV 无法 reconciliation 时不得升级资金 | 生产切换窗 Go/No-Go 硬条件（R-3） | ▲ 切换窗 checklist 演练 |
| I-06 | 任何模型切换必须可 rollback | T18 GradualRolloutOrchestrator 回滚触发器 + 切换窗独立回滚预案 | ▲ 回滚演练（rehearsal） |
| I-07 | 任何 feature flag 必须可审计 | flag 注册表唯一权威 `config/feature_flags.yaml` + enabler 落盘 | ◆ 注册表校验 |
| I-08 | 任何生产配置必须可恢复 | T4 备份链（D 盘 17:30 + manifest SHA256）+ 恢复演练 | ▲ 恢复演练（连续 7 日） |
| I-09 | 任何交易必须存在可追溯 source→signal→decision→order→fill 链 | FillsStore 事实源（08-08）+ T14 风控审计 JSONL | ◆ FillsStore 审计单测 |
| I-10 | 任何单点数据源故障必须进入 fail-safe（P0-P6 降级链） | `utils/data_provider.py` 优先级链 + degradation_audit | ▲ 故障注入演练（Chaos） |

## 2027 PLAN

### v8.7.1（Core / Hardening 拆分，R-1 落定）
- **v8.7.1 Core**（2027-01~03）：P0 归因真实性 ✅（09-02）、P0 Chaos ✅（09-02，40 用例）、P1 风险预算 regime 打通（G3，02-15~03-07 shadow → 03 月启用决策）、P1 Alpha Registry 统一化（G4，01 月）
- **v8.7.1 Hardening**（与 Core 同窗，资源让位 Core）：LLM 权限边界规范 + CI 隔离门禁（I-02 ◆）、生产端成本模型核验（P3）
- **v8.7.2 性能**（待定）：Decimal 资金链改造、架构优化
- **运营件验收累积**（Q4 起）：Health Score 连续 5 日 / 备份连续 7 日 / 检查单 5 日

### GitHub 集成（全部后置 2027，冻结期内零引入）
- **Wave 9-GH**（01-04~04-30，4 Sprint 18 项）：`docs/Wave9_GitHub增补集成计划_20260825.md`
- **Wave 10-CTX B**（05-03~06-28）：`docs/Wave10_经验上下文层集成计划_20260828.md`
- **Wave 11-B/C**（01-04~02-14 / 02-15~02-28）：`docs/高价值项目集成排期_Wave11_20260829.md`
- **Wave 12-B**（01-04~03-21，预算 ~26 人天）：`docs/github_integration_plan_wave12_20260830.md`
- **Wave 13/14/15 裁决**（零代码）：`cairn/github-trending-wave13-20260903.md` / `cairn/github-trending-wave14-20260904.md` / `cairn/github-trending-wave15-20260905.md`
- **2027 候选池**（登记观察不自动进 Sprint）：TimesFM（头号，03 月 POC 3 人天）/ freqtrade / QuantConnect Lean / akquant / hikyuu / FinGPT / QuantDinger / AutoHedge / openai-agents-python / nanobot / ponytail / academic skills / **awesome-mcp-servers（书签·09-05 新增）** / **chrome-devtools-mcp（低优·09-05 新增，Apache-2.0，排位在 DrissionPage 之后）**。**准入前置检查表 = `pushed_at` 距今 ≤12 个月**（zipline/backtrader/tushare/wtpy/QuantMuse/abu 已劝退）
- **准入判据增补（09-05，Wave 15 沉淀）**：除 `pushed_at` 外，**许可证必须为宽松许可（MIT / Apache-2.0 / BSD / ISC）**；Copyleft（GPL/LGPL/AGPL/SSPL）默认劝退作为生产依赖，仅允许只读参考或独立进程外工具且不网络服务化集成。首例适用 = khoj（AGPL-3.0，37k 星标仍劝退）
- **09-05 vnpy 裁决**：**劝退，不引入**。落地指南 §3.2 定为 P0 的问题陈述已证伪 —— pyautogui/pywinauto 全仓仅存于注释（GUI 自动化从未接线），程序化下单通道已由 QMT/xtquant 就位（`utils/execution/broker_factory.py` + `ms_strategy/src/execution/qmt_broker.py` + `remote_qmt_broker.py`）。真实缺口 = W7.2.1 T15「QMT paper 验证未完成」（验证缺口，非能力缺口）。详见 `docs/vnpy_接入spec_20260905.md`
- **09-04 Wave 14 结论**：30 项 0 项进 2026 窗口（已在用 2 / 已有替代 4 / 2027 候选 5 / 劝退 3 / 无关 16）
- **09-05 Wave 15 结论**：30 项 0 项进 2026 窗口（已在用 1 / 已有替代 3 / 劝退 3 / 候选新增 2 / 候选复现 4 / 无关 17），**连续两轮零引入**；本轮实质新增 = khoj 许可劝退 + 准入判据增补
- **插件与工具链轨道（PLG，09-08 登记，后期分批加入系统）**：权威明细 = `docs/插件与工具链后期接入排期_20260908.md`。分层 = L0 VS Code 扩展（立即，`.vscode/extensions.json`）/ L1 Skills 已用维持 / L2 MCP 已用+候选后置 / L3 业务开源 ~10–15 项进 2027。Batch：**0** 文档+extensions（2026-09-08，零生产代码）→ **1** duckdb/tushare/工程 UX（≥2027-01-04，错峰切换窗）→ **2** 中文舆情情感+采集去重（2027 Q1–Q2，默认 shadow）→ **3** 挂靠 Wave 9/11/12 + TimesFM 03 月 POC + chrome-devtools-mcp Q2 → **4** 排除表（vnpy/AGPL/GUI 路径）。容量让路资金线与发布 Gate；任务标 `[PLG]`

## ARCHIVED DECISIONS & POINTERS

### 决策登记（时间序，详情见各指针）

| 决策 | 结论 | 修订 |
|------|------|------|
| 2026-09-02 决策 1-4 | Q4 冻结生产写入留 shadow / Wave 11-A 推迟 2027 / 零侵入两项提前 / 实盘 200 万口径 | — |
| 2026-09-03 D-1 | MVSK P5-3 切换随 12-31 发布实施 | **R-3 修订（09-05）：后置 01-02~01-09 生产切换窗** |
| 2026-09-03 D-2 | qlib 信号源切换（原 V9 → qlib_lgb_v2）同 D-1 口径 | **R-3 修订（09-05）：后置生产切换窗**；**R-6 撤销（09-07）：qlib 双设计缺陷口径 FAIL → 停跑归档，切换项不再实施** |
| 2026-09-03 D-3 | ER-2.x 双签提前 09-13~18；ER-3.x 冻结窗只观察 | — |
| 2026-09-03 D-4 | 取消 ETF 独立资金灰度，S12 并入 p9_200w 灰度路线 | **R-3 修订（09-05）：Sprint3-3 实施后置切换窗** |
| 2026-09-03 D-5 | ETF 子组合 = 主组合两层策略原型与 shadow 载体，不设独立实盘账户 | — |
| 2026-09-05 R-1 | ROADMAP → Release Control Board（本文件） | — |
| 2026-09-05 R-2 | Release Gate ≠ Research Gate 铁律 + Blocker 白名单 | — |
| 2026-09-05 R-3 | 12-31 拆批次：软件发布与生产切换解绑 | 修订 D-1/D-2/D-4 |
| 2026-09-05 R-4 | Invariants 成文 + Change Budget + Kill Criteria（机械检查 09-19 前） | — |
| 2026-09-05 R-5 | D11 复验清单扩展 + P3.3 硬验收 + Sprint3 资本升级门 | — |
| 2026-09-05 绩效目标拍板 | 实盘目标 = 年化 8~18% + 回撤 ≤10% + 月度胜率 ≥70%；"每天稳定盈利 1000"口径废止（数学不可行，等价年化夏普 26）；期货 100 万账户定位对冲/套利载体、杠杆 ≤2 倍；验收以真实资金灰度绩效为准 | **R-6 口径修订（09-07）：月正 ≥9/12（75%，因 8/12=66.7%<70% 不自洽）；分母 = 实际到位总权益；跑赢通胀 = ≥同期 CPI** |
| 2026-09-07 R-6 | ROADMAP 评审修复批次（对应 `roadmap优化改进评审报告_20260907.md` 18 项）：① G-2 观察窗口径澄清 + D1 豁免通道（P0 F01）② qlib 停跑归档、撤销切换项（F10）③ 绩效口径统一（F02/F09）④ Kill Criteria 三件套补全（F06）⑤ 统一评估周日历 + shadow 30 天口径标注（F03/F05）⑥ DECISION NEEDED 节 + 页脚真实路径（F07）⑦ NPC 例外三问 + PR 验收约定（F18）⑧ Invariants 验证档（F14）⑨ Sprint3-2 启动锚点 ≤11-09（F04）⑩ V9 命名一致性（F08）⑪ 期货账户状态键 + 对冲线（F15）⑫ frontmatter 转义保持仓内统一格式（54 文件同款 `\_`，非孤例，不修；C1 WARN 处置入 DECISION NEEDED）⑬ F16 容量/冲击成本复核入资金线 Sprint3-3 必答 + 3-2 滑点分布强制记录 | 修订 D-2 / 绩效目标拍板 |
| 2026-09-08 R-7 | MVSK fail-fast 阈值口径复核落地 — `MVSK_DIFF_THRESHOLD` 0.30→0.50（治理⑤子集口径放大约 4.25×，09-07 实测健康样本 0.2423 达旧阈值 80.8%；0.50/4.25≈0.118 恰为旧口径健康区间上限；旧注释"健康值 0.0-0.1"失真作废）。四件套：代码注释 + 测试同步 4 处 + 知识文档更正注记 + 复核材料存档。效果：09-13 起正常子集口径分歧不再误触发 latch 杀窗，真实背离（≈完全翻转 L2≈1.0）仍可靠拦截 | 依据 `docs/weight_diff_l2阈值口径复核_20260908.md` |
| 2026-09-08 R-8 | **插件与工具链后期接入排期**：适合本项目的 VS Code 扩展 / Agent Skills / MCP / 高价值开源集成纳入 Control Board，**后期分批加入系统**（不进 2026 生产代码窗）。L0 立即落盘 `.vscode/extensions.json`；L3 业务集成 Batch 1–3 自 2027-01-04 起与 Wave 9/11/12 错峰；vnpy/AGPL/GUI 路径维持排除。明细 = `docs/插件与工具链后期接入排期_20260908.md` | 落实用户「加入排期、后期加入系统」；服从 Q4 Change Budget 与 09-02 决策 2 |
| 2026-09-09 R-9 | **国债 ETF 权重口径拍板**：国债/货基类豁免个券 15% 上限 —— `config/risk.yaml` `thresholds.max_weight_by_style: {国债: 0.30}`（硬上限，贯通 EOD Guard6 违规判定与 Guard7 减仓单；缺省空则行为不变）；`TARGET_ALLOCATION["国债"]` 0.22→0.25 与 `tools/add_treasury_etf.py` 目标对齐，消除第三套口径。配套修复：同一标的的 max_weight 减仓单与风格单不再叠加（SELL 取股数最大 / BUY 取最小，目标冲突打 `needs_decision`），消除 511010 被两单叠加砸到约 8.3% 的超调 | 依据 `cairn/trendcast-integration-eod-findings-20260909.md` F-2；提交 `4aa26607`（上限口径）+ `19a04337`（订单合并） |
| 2026-09-11 R-10 | **200万 ETF+期权子组合（v9.1「守正」）目标口径拍板**：净年化 = **4.25%**（= 毛 5.5% − Collar **1.25%**，自下而上期望值；区间 [3.5%, 5.5%]），回撤预算 15%（范围 10%~18%）；原 8% / 5.5%~6.5% 口径废止并**留并存证**（`target.legacy_target_infeasible`）。护栏 9 条 `[口径]`（基据块存在 / 目标=净中枢 / **成本假设=collar.cost_target_pct 中值** / 认购行权价地板=目标+8pp / 情景概率加权=净中枢 / 禁删并存证 / 单位口径隔离…）；该口径**仅适用 ETF 子组合**，与 8%~18%（证券200+期货100 合计）不可互相引用。证据 = `scripts/run_200w_etf_backtest.py`（权重 + L1-L4 减仓 + Collar 成本入净值）+ `scripts/verify_etf_price_source.py`（价格基 vs Wind 零容差）。**09-11 修订**：成本对齐引擎实收 `cost_target_pct` 中值 1.25% ⇒ 净中枢 4.3%→**4.25%**（诊脉书 1.2% 系近似；护栏新增第 9 条防再脱钩） | 依据《ETF期权组合诊脉书_20260911》卷四 vs 卷六矛盾；见 `每日报告归档/2026-09-11/v9.1目标口径统一_自下而上4.3_20260911.md` || 2026-09-11 R-10 | **200万 ETF+期权子组合（v9.1「守正」）目标口径拍板**：净年化 = **4.25%**（= 毛 5.5% − Collar **1.25%**，自下而上期望值；区间 [3.5%, 5.5%]），回撤预算 15%（范围 10%~18%）；原 8% / 5.5%~6.5% 口径废止并**留并存证**（`target.legacy_target_infeasible`）。护栏 9 条 `[口径]`（基据块存在 / 目标=净中枢 / **成本假设=collar.cost_target_pct 中值** / 认购行权价地板=目标+8pp / 情景概率加权=净中枢 / 禁删并存证 / 单位口径隔离…）；该口径**仅适用 ETF 子组合**，与 8%~18%（证券200+期货100 合计）不可互相引用。证据 = `scripts/run_200w_etf_backtest.py`（权重 + L1-L4 减仓 + Collar 成本入净值）+ `scripts/verify_etf_price_source.py`（价格基 vs Wind 零容差）。**09-11 修订**：成本对齐引擎实收 `cost_target_pct` 中值 1.25% ⇒ 净中枢 4.3%→**4.25%**（诊脉书 1.2% 系近似；护栏新增第 9 条防再脱钩） | 依据《ETF期权组合诊脉书_20260911》卷四 vs 卷六矛盾；见 `每日报告归档/2026-09-11/v9.1目标口径统一_自下而上4.3_20260911.md` |
| 2026-09-11 R-11 | **资金口径拍板（P1-2 数值半边）**：权威总口径 5M → **3,000,000 = 证券/ETF 腿 2,000,000 + 对冲腿 1,000,000**。依据 = `kill_switch.yaml total_margin`（09-11 item 12 已定 3M，并定性 5M 为 v8.0 旧链历史头）/ `system_config.json` / `p9_200w_preset` / ROADMAP `performance_targets.accounts`（目标结构 300 万 = 200 + 100）。**腿语义唯一化**：`total` 风控预算·kill_switch·institutional pipeline；`stock_etf` 再平衡链基数·认沽被保护规模·L2 净值默认；`hedge` 对冲链预算。四处**语义修正**（再平衡 TARGET_TOTAL / 对冲 portfolio_value / put 预算基数 / hedge 引擎预算基数 由 total 改 stock_etf）+ 残余口径清理（glm5 第六套 500/400/100、kill_switch 两处 5M 回退、daily_build_and_hedge 500w/400w、auto_trading_system 500 万默认）。新增运行时解析器 `resolve_effective_capital`（positions meta/实时权益 > 静态基准 > 默认，带来源审计；非正值拒绝，不静默把静态基准当真实权益）。审查 §P1-2 实测高估 82.4% 归位 | 依据 `cairn/capital-caliber-decision-20260911.md`；Issue #13 |

### 专项文档指针（历史明细唯一入口）

| 主题 | 载体 |
|------|------|
| v9.3 统一升级计划 / 排期优化 | `docs/升级路线优化与排期_20260829.md`（8 项路线修正 + 12-10 冻结窗起源） |
| D11 复验清单 | `docs/d11_reverify_checklist_20260830.md`（09-18 前按 R-5 刷新） |
| 策略优化排期（D-1~D-5 审计记录） | `docs/策略优化排期计划_20260903.md` |
| Production Edition 200 万架构方案 | `docs/v8.7_Production_Edition_架构升级方案_200万实盘版_20260902.md`（T1-T5 ✅ 09-02） |
| 排期审查回应 / 架构审查回应 | `docs/排期审查回应_运营收敛_20260902.md` / `docs/架构审查回应_v8.7_20260902.md` |
| **ROADMAP 结构审查回应（R-1~R-5 依据）** | `docs/ROADMAP结构审查回应_发布治理_20260905.md` |
| Wave 6/7/8 明细（含 v8.7 发布验收清单） | `docs/高价值项目集成排期计划_20260811.md` §7 + `docs/Wave6_收尾报告_20261231.md` + `cairn/v87-release.md` |
| Wave 7-ERL | `cairn/evolution-rebalance-loop.md` §十三 |
| Wave 8-LIT（✅ 全部提前 08-24） | `docs/系统升级文献调研与排期_20260823.md` |
| ETF 期权对冲子模型 P1-P5 | 本文件 §etf_option_submodel + `cairn/etf-option-hedge-model.md`（§v9.1 = 口径 4.25% + 五年证据基座 + L1~L4 实证） |
| **跨线合并与门禁集成 playbook** | `cairn/merge-and-gate-playbook-20260911.md`（脏文件∩入站 / stash 备份 / LOG 取并集 / merge 期 DTZ005 / ast 插 import / 双端推送） |
| MVSK P1-P5 / shadow 30 天 | `cairn/mvsk-higher-moment-optimization.md` + `cairn/shadow-30day-validation.md` |
| GNN Wave 5 | `cairn/gnn-supply-chain-factor.md` + `cairn/gnn-supply-chain-factor-wave5-review.md` |
| 自我进化框架 | `docs/自我进化框架/`（FINENG_ACCEPTANCE_REPORT.md 等） |
| 影子账户 P3.0 门禁 | `cairn/p3-0-gate.md` |
| 实盘前工作清单（Sprint3 资本升级门基础） | `docs/实盘前工作清单与推进计划_20260824.md` |
| **Sprint3 资本升级门 checklist（R-5）** | `docs/sprint3_capital_upgrade_gate_20260905.md` |
| **ER-2.x 双签操作 checklist（D-3）** | `docs/er2x_dual_sign_checklist_20260905.md` |
| **qlib W7.2.9 缺口决策材料（09-07 停跑归档依据，R-6；双设计缺陷口径 FAIL）** | `docs/qlib_w729_gap_analysis_20260905.md` |
| **vnpy 准入裁决（劝退 + 修正 08-09 指南 §3.2）** | `docs/vnpy_接入spec_20260905.md` |
| **Wave 15 周榜裁决（khoj 许可劝退 + 判据增补）** | `docs/GitHub周热门项目集成_Wave15_20260905.md` + `cairn/github-trending-wave15-20260905.md` |
| **插件与工具链后期接入（R-8，L0–L3 / Batch 0–4）** | `docs/插件与工具链后期接入排期_20260908.md` + `.vscode/extensions.json` |
| 代码质量排期 Wave 7-QC | `docs/代码质量提升排期计划_20260821.md` |
| ECC skills 工作计划（W7.3.5 ECC 选择性安装, 10-13~10-26） | `docs/ECC赋能量化系统工作计划_20260821.md` |
| GitHub 三适配器待激活（unsloth/switchyard/openviking, 后置 2027） | `docs/GitHub周热门项目集成_20260821.md`（2026-09-05 注记） |

### 历史里程碑（仅完成态，过程见 LOG）

- [x] v8.6.14 因子库 GTJA191 对标；v8.5 全模块（A- 评级）；v8.6 三系统统一（08-03）
- [x] Wave 1-6 全部完成（自我进化收尾 / Phase B B1-B3 启用 / 代码质量五轮 / T09-T18 风控+实盘四件套 / GNN Gate1 PASS Gate2 证伪回退 / 12+ 生产模块 377+ 测试）
- [x] Wave 7 Sprint 1 主体（Phase B 启用 / daily_workflow 2159 行 / R10 清零 / G7 覆盖率 0.833 / MVSK+qlib shadow 就绪 / 真实 LightGBM 模型落盘）
- [x] 执行闭环四波修复（期权对冲 / 再平衡撮合 / fills 驱动 PnL / QMT 接线骨架）
- [x] Chaos 六场景（40 用例）+ Health Score 引擎 + 运营件四件套（09-02）
- [x] ETF P2 诚实验证（S6 HONEST 5/6 验收项）+ S12 纯防御风险平价（年化 7.48%/回撤 2.60%，DSR 0.50 未过 → 诚实下限定位）
- [ ] 详见未竟项：Wave 5 S6/S7、Wave 7 Sprint 2-4、G9 FeatureStore、W7.2.1 T15 QMT paper、ocr 三步固化、v8.7 发布（各节点见上文 Stage 锚点）

---

> **每日查看路径**：CURRENT STATE → DECISION NEEDED → NEXT 14 DAYS → RELEASE GATES → CURRENT QUARTER（含开放问题）。
> **修改纪律**：状态变更只改 `CURRENT STATE` YAML（附日期）；历史过程写 `cairn/LOG.md`；本文不再容纳"口径修正注记"——修正直接改 YAML 并在决策登记表登记。
