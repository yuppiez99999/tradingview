# Sprint3 资本升级门 Checklist（R-5，2026-09-05 预备）

> **来源**: ROADMAP 结构审查 #5（"100 万 → 200 万不应只由策略结果决定"）→ 拍板 R-5 第三项。
> **适用**: p9_200w_preset 灰度路线（09-02 决策 4 / 09-03 D-4）——Sprint3-1 20 万测试 → Sprint3-2 100 万灰度 shadow 30 天 → Sprint3-3 200 万正式（**实施时点 = 01-02~01-09 生产切换窗，R-3 修订**）。
> **基础**: `docs/实盘前工作清单与推进计划_20260824.md` 十条工作线 + Production Edition §7.2。
> **判定规则**: 每级升级 = 上一级全部 checklist PASS + 本级准入条件满足；**任一项 FAIL 即停留本级**，不得跳级、不得以"策略收益好"豁免（I-05：NAV 无法 reconciliation 时不得升级资金）。

---

## 一、Sprint3-1 准入门（20 万测试，D11 PASS 后解锁）

| # | 项 | 判据 | 核对方式 |
|---|---|------|---------|
| 1 | D11 双条件 PASS | stable≥7 AND samples≥20 | `python scripts/engineering_debt_gate.py`（09-18 复验） |
| 2 | preset 加载验证 | `v9_200w_preset` 段加载 + ETF Score 6 维权重生效 + S12 防御层配置加载与再平衡行为正常（D-4） | Sprint3-1 期内执行配置加载验证脚本 |
| 3 | 期权保护参数就位 | DTE/Delta/OTM 参数与回测口径一致 | 对照 `config/portfolio.yaml` v9_200w_preset 段 |
| 4 | 资金 20 万划转 | 独立账户 + 初始快照留档（NAV 基准日） | 账户截图/对账单入 `reports/capital_gates/` |

**升级到 Sprint3-2 条件**: 20 万测试 ≥7 天稳定 + 端到端跑通（信号→订单→成交→落盘→PnL）+ 无 P0 告警。

## 二、Sprint3-2 期中监控（100 万灰度 shadow 30 天）

| # | 项 | 判据 |
|---|---|------|
| 5 | shadow 30 天连续运行 | `run_s12_shadow.py --status` / EOD 任务 16:30 无断档（同 S12 口径） |
| 6 | 对照 v5.10 组合 | 每日权重/收益差异记录到 shadow diff jsonl |
| 7 | 分布带检验 | 影子年化落回测滚动 30 日年化 [P5, P95] 带内（同 P3.3 口径） |
| 8 | 回撤 < 15% | 30 日窗口内最大回撤 |

## 三、Sprint3-3 Go/No-Go 门（200 万正式，生产切换窗 01-02~01-09）

> **十项硬条件（审查 #5 口径）+ 人工签批，全部 PASS 才可实施**。四项生产切换（资金/S12/MVSK/qlib）各自独立判定、独立回滚预案、逐项实施不捆绑（R-3）。

| # | 项 | 判据 | 核对方式 |
|---|---|------|---------|
| 1 | 30D shadow PASS | Sprint3-2 全程 PASS | §二 1-4 项复核 |
| 2 | 最大回撤符合预算 | 30 日滚动回撤 ≤ 预算线 | S12 防御口径 ≤5%；主组合口径按 preset 风险限制 |
| 3 | 实际成本 < budget | 成本偏差 actual/expected ∈ [0.5, 2.0]（P3.3 口径，2026-09-05 已实现 `evaluate_consistency`） | `run_p33_evaluation.py` cost_deviation |
| 4 | NAV reconciliation 100% | 逐日 NAV 链路 + capital 换算自洽 | 同上 nav_reconciliation（rtol 1e-6） |
| 5 | position reconciliation 100% | 计划单 vs 实际持仓 drift 在容差内 | T13/T17 对账器输出 |
| 6 | zero unexplained fills | 逐笔成交有 source 标记（live_route/sim_route） | FillsStore 按日核查 |
| 7 | zero critical alert | 观察窗内无 CRITICAL 级未处置告警 | `utils/notify` 告警记录 |
| 8 | backup ≥ 7 days | T4 备份链连续成功 + manifest SHA256 校验 | D 盘备份目录 + manifest |
| 9 | recovery drill PASS | 恢复演练通过且备份链此后无变更 | T4 演练记录 |
| 10 | manual emergency stop PASS | 人工紧急停止演练（kill_switch 手动触发 → 全停止 → 恢复） | 演练记录入 `reports/capital_gates/` |
| 11 | **final human approval** | 用户签批（唯一可豁免技术项 FAIL 的权限，且 NAV/对账类不可豁免——I-05） | 签批记录 |

## 四、回滚预案（每级预置，不临时编写）

- 资金回退: 200 万 → 100 万级 = 平仓至目标敞口 + NAV 快照 + 对账确认（T+1 可执行）
- 配置回退: preset 切换回退 = `config/feature_flags.yaml` 关闭新 flag + 重启 EOD 任务（rollback_seconds 见各 flag 注册）
- 模型回退: MVSK/qlib 切换回退 = 信号源切回 V9 / BL+MV(252)（`alpha_weight=0.4` 不变）
- 触发条件（任一即回滚）: NAV 偏离 shadow 预期 > 2 倍标准差 / 连续 2 日 reconciliation FAIL / 未解释成交 > 0

---

> **维护**: 每级门通过后在 `reports/capital_gates/` 留档（证据 + 日期 + 核对人），并在 `cairn/LOG.md` 登记。
> **关联**: ROADMAP §NEXT 14 DAYS R-5 / §生产切换窗；`docs/ROADMAP结构审查回应_发布治理_20260905.md` §三 R-5。
