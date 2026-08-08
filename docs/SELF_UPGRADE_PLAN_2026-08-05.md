# 后续系统自我升级计划 — 2026-08-05

> **制定依据**: 2026-08-05 全库代码审查修复闭环揭示的遗留升级点 + 现有自我进化框架 + `cairn/ROADMAP.md` 四波计划
> **关联**: `docs/WORK_REPORT_2026-08-05_代码审查修复闭环.md` | `cairn/code-review-lessons-v8.4.md` | `cairn/self-evolution-framework.md` | `cairn/ROADMAP.md`
> **状态**: 与 ROADMAP 四波计划（Wave 1-4）衔接，新增本次修复派生的升级项

---

## 一、背景

2026-08-05 完成 25 项代码审查问题修复后，系统在回测可信度、资金安全、数据口径三大维度已实质性强化。本次修复**派生出 6 个新的升级点**，它们无法在本次修复中一并解决（或属于更深层的长期任务），需纳入后续升级计划。

本计划将这些新升级点融入现有自我进化框架（Phase 0 观察期 → Phase 4.5 工程加固）和 ROADMAP 四波计划，形成 **"现有计划 + 本次派生项"** 的完整升级路线。

---

## 二、升级点总览

| # | 升级点 | 来源 | 优先级 | 建议窗口 |
|:--|:-------|:-----|:-------|:---------|
| U1 | 完整时序 IC/ICIR（基于因子历史序列） | P0-2 派生的深度项 | P0 | 08-05~08-15 |
| U2 | 回测涨跌停/停牌数据接入 | P2-2 派生的数据项 | P1 | 08-08~08-20 |
| U3 | 复权因子支持（除权日精确对齐） | P2-1 派生的数据项 | P1 | 08-10~08-25 |
| U4 | DeepSeek API Key 平台轮换收尾 | P1-1 派生的运维项 | P1 | 立即 |
| U5 | GAP-2 E2E 补齐（full_pipeline + shadow） | ROADMAP Wave 3 遗留 | P1 | 与 Wave 3 并行 |
| U6 | 诚实回测三件套（CPCV/DSR/Noise） | ROADMAP Wave 4 | P2 | 09-05~10-31 |
| U7 | 自我进化 Phase B 渐进启用 | ROADMAP Wave 2 | P2 | 08-20 决策后 |

---

## 三、升级点详情

### U1 完整时序 IC/ICIR（P0，最高优先）

**背景**: 本次 P0-2 将 `calc_ic` 从"过去收益近似"改为"单点 forward return"，消除了自相关伪 IC。但完整正确的多日时序 IC/ICIR 仍需基于**因子历史序列**（`factor_history_builder` 已产出）逐日计算。

**方案**:
- 复用 `gate1_validation.calc_ic_series` 的滚动窗口方案（已正确实现）
- 将 `base.evaluate_factors` 从单点 IC 升级为基于 `factor_history` 的时序 IC/ICIR
- 与 `portfolio_optimizer.run_offline_pipeline` 的 Shadow 审批衔接

**验收**: 因子 IC/ICIR 基于横截面未来收益逐日计算，样本内 ICIR 与 `gate1_validation` 结果一致

### U2 回测涨跌停/停牌数据接入（P1）

**背景**: 本次 P2-2 在 `wt_backtest_engine.run()` 增加了 limit_up/down/suspended 字段支持（向后兼容），但**数据源尚未提供**这些字段，约束实际未激活。

**方案**:
- `data_provider`/`akshare_data_source` 下载涨跌停价 + 停牌标记
- 生成 `limit_up_prices`/`limit_down_prices`/`suspended` 字段注入 `day_data`
- A股涨跌停规则：主板±10%、ST±5%、创业板/科创板±20%、新股首日

**验收**: 回测数据含真实涨跌停/停牌字段，涨停禁买/跌停禁卖/停牌冻结生效

### U3 复权因子支持（P1）

**背景**: 本次 P2-1 统一历史 K 线为 hfq，实时为未复权。二者通过复权因子关联，但当前系统**未下载/使用复权因子**，除权日精确对齐仍缺失。

**方案**:
- akshare `stock_zh_a_daily(adjust="hfq-factor")` 获取复权因子
- 建立 hfq历史 ↔ 未复权实时 的因子映射，供换仓/止盈在除权日正确对齐

**验收**: 除权日前后 hfq 历史价与未复权实时价通过因子对齐，无跳空偏差

### U4 DeepSeek API Key 轮换收尾（P1，立即）

**背景**: 本次 P1-1 已将 `.env` 泄露的真实 Key 替换为占位符，但**需用户到 DeepSeek 平台重新生成 Key 填入**，否则 AI 决策功能无法调用 DeepSeek。

**动作**（需用户操作）:
- 到 platform.deepseek.com 重新生成 API Key
- 填入 `.env` 的 `DEEPSEEK_API_KEY` 或注入环境变量
- 验证 `--ai-decision` 等 AI 功能恢复正常

### U5 GAP-2 E2E 补齐（P1，与 Wave 3 并行）

**背景**: ROADMAP Wave 3 遗留项。

**方案**:
- full_pipeline 端到端测试（数据→因子→组合→回测→报告）
- shadow_account_lifecycle 生命周期测试（开户→验证→灰度→全量→回退）

### U6 诚实回测三件套（P2，Wave 4）

**背景**: ROADMAP Wave 4 T06-T08，与本次修复的"回测可信度"主题一致。

**方案**:
- CPCV（组合式交叉验证）— 用 Purged K-Fold 避免时序数据泄漏
- DSR（防御性夏普率）— 修正回测夏普的随机性，防过拟合
- Noise 残差注入 — 评估策略对噪声的稳健性

### U7 自我进化 Phase B 渐进启用（P2，Wave 2）

**背景**: 自我进化框架 Phase 0 观察期（08-13 满期，建议延至 08-20），观察期满后渐进启用 Phase B。

**方案**（ROADMAP Wave 2）:
- B1 `USE_DRIFT_DETECTOR=true` 仅告警
- B2 `USE_FEEDBACK_LOOP` 自动接入
- B3 `USE_AUTO_RETRAIN=true` + 降级护栏
- B4 `USE_MLOPS_PIPELINE=true` 完整外层循环

---

## 四、与现有计划衔接

```
本次修复 (08-05) ✅ 完成
    ↓
08-05~08-15  U1 完整时序IC (P0) + U4 Key轮换 (P1)
    ↓
08-08~08-25  U2 涨跌停数据 (P1) + U3 复权因子 (P1)
    ↓
08-20        自我进化决策日 (观察期延长)
    ↓
08-20~09-05  U7 Phase B 渐进启用 (Wave 2)
    ↓
09-05~10-31  U6 诚实回测三件套 + Wave 4 工程化达标
```

**并行**: U5 GAP-2 E2E 与 Wave 3 代码质量持续修复并行（08-04~09-04）

---

## 五、风险与依赖

| 升级点 | 依赖 | 风险 |
|:-------|:-----|:-----|
| U1 | 因子历史序列数据完整性 | 历史序列长度不足时 ICIR 不稳定（需 ≥20 样本） |
| U2 | 数据源提供涨跌停/停牌字段 | 部分数据源不支持，需多源兜底 |
| U3 | 复权因子接口可用性 | akshare 因子接口可能变化 |
| U4 | 用户平台操作 | 需用户配合，无法自动完成 |
| U6 | 计算资源（CPCV 交叉验证） | 大组合下耗时增加 |

---

## 六、优先级与排期总结

| 优先级 | 升级点 | 时间 |
|:-------|:-------|:-----|
| **P0** | U1 完整时序 IC/ICIR | 08-05~08-15 |
| **P1** | U4 Key 轮换 | 立即（用户操作） |
| **P1** | U2 涨跌停数据 / U3 复权因子 / U5 GAP-2 | 08-08~08-25 |
| **P2** | U6 诚实回测三件套 / U7 Phase B | 09-05~10-31 |

**核心原则**: 延续本次修复的**回测可信度**与**资金安全**主线，先补强数据/回测基础（U1-U3），再推进自我进化与工程化达标（U5-U7）。
