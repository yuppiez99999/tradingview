***
type: project_topic
status: active
authoring_mode: ai_generated
created: 2026-09-11
updated: 2026-09-11
related:
  - docs/代码质量与系统Bug审查_20260911.md
  - config/risk_thresholds.yaml
  - config/kill_switch.yaml
  - cairn/ROADMAP.md
***

# 资金口径拍板（P1-2 / SC-2 数值半边）— 2026-09-11

> 状态：**已拍板并实施**（Issue #13，分支 `npc/issue13-capital-caliber-20260911`）。
> 前置：PR #25（AUTO 批）已把 11 处消费点收敛到 `capital_base` 单一事实源（默认仍 5M）。
> 本文记录**数值切换的决策依据与腿语义**，供后续口径引用唯一入口。

---

## 1. 结论

| 腿 | 数值 | 语义 / 唯一消费面 |
|---|---|---|
| `total_capital` | **3,000,000** | 总口径 → 风控预算 / kill_switch / institutional pipeline / 组合级绩效分母。**不得**作再平衡单腿基数 |
| `stock_etf_capital` | **2,000,000** | 证券/ETF 腿 → **再平衡链基数** / 认沽保护被保护规模 / L2 净值默认 |
| `hedge_capital` | **1,000,000** | 对冲腿 → 对冲链预算基数 |

恒等式：`total = stock_etf + hedge`（测试断言锁定，防腿口径再次混用）。

运行时优先序（`resolve_effective_capital`）：
`positions meta / 实时权益` > `capital_base` 静态基准 > 模块内默认（带来源审计字符串）。

---

## 2. 为什么是 300 万（而非 5M / 2.74M / 2M）

依据**均为仓库内既有已拍板事实**，非本次新造：

1. **命名路线** = `p9_200w_preset`（证券 200 万实盘灰度）+ 期货 100 万 —— ROADMAP
   `performance_targets.accounts`；目标结构 300 万 = 200 + 100。
2. **`config/kill_switch.yaml` `total_margin = 3,000,000`** —— 2026-09-11 item 12 已按此拍板，
   并**显式**把 `positions.json meta` 的 5M 定性为「旧链历史头 (v8.0)」。
3. **`system_config.json` `total_capital = 3,000,000`**（2M + 1M）声明权威。
4. **8%~18% 绩效口径分母** = 期初实际到位总权益（证券 200 + 期货 100 合计）。
5. **Sprint3 资本升级门终点** = 200 万正式（`docs/sprint3_capital_upgrade_gate_20260905.md`）。

### 5,000,000 的处置

系 **2026-07 建仓计划**（证券 300w + 期货 200w）的**计划口径**，已被上述 4 项后续决策取代。
审查报告 §P1-2 量化：再平衡目标按 5M 生成 vs 实际账本 ≈274 万 → **高估 82.4%**（本次实跑复刻：
`5,000,000 / 2,741,928 - 1 = 82.4%`，与报告一致）；同链对冲手数 ~1.82× 过度对冲。
**不再作为权威口径。**

### 2.74M（实际账本）为什么不硬编码进配置

`2,741,928` 是**运行时真实值**（实际证券账本），随建仓 / 行情漂移。
硬编码会立刻过期 → 故设计为**运行时优先取用 + 静态基准兜底**，且**缺失时显式告警，
不得静默把静态基准冒充真实权益**（对齐项目铁律「缺数据 ≠ 通过」）。

---

## 3. 实施的四处**语义修正**（不只是改数值）

| 位置 | 原口径 | 修正后 | 理由 |
|---|---|---|---|
| `rebalance_execution_orders.TARGET_TOTAL` | `total_capital`(5M) | `stock_etf_capital`(2M) | 再平衡目标是**证券腿**目标金额；用含期货腿的 total = §P1-2 高估根因 |
| `rebalance_order_executor` 对冲 portfolio_value | `total_capital` | `stock_etf` 运行时口径 | AES 该字段被用作 `value_to_hedge = excess_beta × portfolio_value`，对冲对象是**权益 Beta** |
| `protective_put_engine.TOTAL_CAPITAL` | `total_capital` | `stock_etf_capital` | 保护对象 = `PROTECTION_TARGETS`（全为 ETF），即证券腿 |
| `hedge_execution_engine` 年度期权预算基数 | `total_capital` | `stock_etf_capital`（meta 兼容） | 期权成本是**保护证券腿**的成本，与 put engine 同源 |

附带清理的**残余口径**：`glm5_decision_engine` 回退值（第六套 500/400/100）、
`kill_switch` 两处 5M 回退、`daily_build_and_hedge` 500w/400w 回退、
`auto_trading_system` 默认参数 500 万 → 全部改经 `capital_base`。

---

## 4. 验证（沙箱实测）

- 新增 **12 例**回归（`TestEffectiveCapitalResolution` 7 + 腿语义/脚本 5）；
  同步 `t36` 迁移断言、`kill_switch` 默认断言、`put engine` 乘数用例（改为显式资金口径，
  使被测对象 = 乘数而非预算规模）。
- **先红后绿**：改前 4 例断言 5M/3M/2M 必红（t36、risk_thresholds ×2、put engine），
  改后全绿。
- unit 全量失败集与同环境基线**逐项 diff 为零新增**（在多个子集上验证）。
- ruff 改动文件 `All checks passed`。

---

## 5. 未做（如实声明）

- **`ms_strategy/scripts/rebalance_execution_orders.py`（5M）** / `tools/add_treasury_etf.py`（4M）/
  `research/optimize_portfolio.py`（4M）**未动** —— 属独立脚本/研究侧，与主链消费点不同；
  若纳入需单独评估（尤其 `add_treasury_etf` 与 R-9 国债口径联动）。
- **`alpha_hedge_engine` 兜底**为死代码（无消费者），已随收敛改源，但无行为验证价值。
- **【P】生产机运行时行为未观测** —— 沙箱无 `positions.json`，运行时优先序仅以单测覆盖。

---

## 6. 后续口径引用约定

- 任何引用资金口径处，**必须**注明是哪条腿（total / stock_etf / hedge），
  禁止再出现无腿限定的「总资金 500 万」表述。
- 口径变更**只改** `config/risk_thresholds.yaml → capital_base` 一处，断言自动跟随。
- 与 `p9_200w` / v9.1（ETF 子组合 4.25%）口径**不可互相引用或相加**（R-10：单位口径隔离）。
