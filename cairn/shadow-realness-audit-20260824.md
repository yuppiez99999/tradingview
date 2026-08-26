# 影子观察期真实性核查 (2026-08-24)

> 工作线 C: 影子观察期重新评估
> 核查工具: debugging-and-error-recovery (三查法)
> 目的: 核查 trade_log 空疑点、评估准入条件是否过时、判断 stage_2 推进可行性

## 0. 核查结论 (核心)

**影子账户存在重大真实性缺口: trade_log 恒空 = 影子观察期没有真实策略撮合。**

影子账户 (launch_shadow_account.py / ShadowAccountAdapter) 的净值是 **NAV 回算**
(daily_workflow Phase 10 从 daily_returns.jsonl 读收益累计), **从不执行真实策略成交**,
因此 trade_log 始终为空。这样的影子观察期无法验证策略在真实行情下的滑点/成交率/
流动性冲击, 是实盘前必须解决的重大事项。

## 1. 实测状态 (2026-08-24 22:07)

| 项 | 值 | 说明 |
|---|---|---|
| 账户 ID | shadow_v9_20260725 | 启动 2026-07-25 |
| 状态 | RUNNING | — |
| 阶段 | stage_1 (0) | 停在灰度第 1 阶段 |
| 初始资金 | ¥500,000 | 10% 资金 |
| 当前资金 | ¥494,262 | — |
| 当前净值 | 0.9885 | — |
| 运行天数 | 23 天 | 已远超 14 天推进条件 |
| Fail-fast | 未触发 | 单日/3日回撤阈值均未破 |
| 累计收益 | **-1.03%** | — |
| **trade_log** | **[] 空** | **核心疑点** |
| daily_nav | 23 条 | 净值回算记录 |

## 2. trade_log 空根因 (debugging 三查法)

- **Reproduce**: `launch_shadow_account.py --status` → 运行天数 23, trade_log 空。
- **Localize**: `init_shadow_account()` (launch_shadow_account.py L144) 创建 `trade_log: []`,
  注释 L163 明确"**daily_workflow Phase 10 将每日记录净值并检查 fail-fast**"。
- **Root Cause**: 影子账户是 **NAV 回算模式**。daily_workflow Phase 10 读 `reports/shadow/daily_returns.jsonl`
  的收益序列累计 NAV, **从不调用任何策略下单/撮合**, 因此 trade_log 恒空。`advance_stage()` (L214)
  只检查"天数≥14 + 未 fail-fast", **不检查 trade_log/绩效/成交**。

## 3. 两套影子系统并存 (架构疑点)

| 系统 | 状态文件 | 用途 | 状态 |
|---|---|---|---|
| `launch_shadow_account.py` | output/shadow_account/shadow_state.json | NAV 回算 + 灰度推进 | RUNNING (23天, trade_log空) |
| `shadow_admission_launcher.py` | reports/shadow/admission_state.json | T2.4 准入评估 (6+11项) | **从未启动** (文件不存在) |

**矛盾**: T2.4 准入流程 (shadow_admission_launcher) 从未启动, 而灰度推进 (launch_shadow_account)
不需要准入评估。两套流程未打通, 准入条件形同虚设。

## 4. 准入条件过时评估

T2.4 原准入 6 项 (观察期≥14天/fail_fast=false/DSR≥5/年化≥15%/回撤≤10%/Sharpe CV<1.0) 与
T5.7/T5.8 的 P3 准入 11 项, **当前无法有效评估**, 因为:
1. trade_log 空 → 无真实成交数据, DSR/Sharpe/年化 均基于回算 NAV, 非真实策略绩效。
2. 观察期天数虽达标 (23), 但无成交验证, "通过观察期"不代表"策略实盘可行"。
3. 推进条件只查天数+fail-fast, 绩效不达标 (-1.03%) 也不阻塞推进。

## 5. 影响与修复方向

**影响 (实盘前重大)**: 若直接基于当前影子 NAV 推进灰度并上实盘, 策略在真实行情下的
滑点/成交率/流动性冲击完全未经验证, 存在实盘表现显著偏离回测的风险。

**修复方向 (对应后续工作线)**:
1. **影子账户接入真实撮合**: 让影子账户执行策略信号并通过 SimulatedBroker/FillsStore
   撮合落盘 (与 DTE-1 建仓接入撮合链同源), 使 trade_log 有真实成交, NAV 基于成交而非回算。
2. **打通 T2.4 准入流程**: 启动 shadow_admission_launcher, 使准入评估覆盖真实成交数据。
3. **推进条件加强**: advance_stage 增加"trade_log 非空 + 绩效达标"检查, 而非仅天数+fail-fast。

## 6. 状态

- 核查完成, 根因定位 (trade_log 空 = NAV 回算无真实撮合)。
- 影子真实性修复作为**实盘前 P0 阻断项**, 依赖 DTE-1 建仓撮合链 / G1 QMT 接线工作线。
- 建议: 在 DTE-1 完成后, 让影子账户复用同一撮合链, 积累真实成交后再评估准入。
