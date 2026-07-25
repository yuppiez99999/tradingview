# S4 第二批次根因分析报告

> 批次：`second_batch_20260725_105236`
> 生成时间：2026-07-25
> 决策依据：CIO 决议 §6.4（首批次 ARCHITECTURE_v1.0 锁定）

---

## 1. P0 改进落地验证（首批次 ARCHITECTURE P0 项）

| P0 项 | 首批次状态 | 第二批次状态 | 验证证据 |
|--------|-----------|-------------|----------|
| `compute_factor_history()` | 缺失，用 `[values]*90` 占位 | **已落地** | `pipeline_state.json` audit_trail: `Stage1.5_FactorHistory completed, factors_with_history=16, valid_days=120` |
| Gate2 真实 120d 滚动 IC_IR | 单期经验映射 `|ic|/(1-|ic|)` | **已落地** | `g2_ic_stability.method = "real_120d_rolling"`, `n_days = 120` |
| Gate3/Shadow 真实日频因子值 | 占位 | **已落地** | `_gate3_dsr` 与 `_stage_shadow` 均接收 `factor_history` 参数 |

**结论**：P0 改进的代码改造全部生效，首批次 §6.2 列出的 4 项「简化实现风险」已全部消除。

---

## 2. G2 通过率分析（核心议题）

### 2.1 总体通过率

| Gate | 首批次 | 第二批次 | 变化 |
|------|--------|---------|------|
| G1 正交性 | 9/16 = 56.2% | 9/16 = 56.2% | 持平（数据未变） |
| **G2 IC 稳定性** | 0/16 = 0% | **0/16 = 0%** | 仍不达标 |
| G3 DSR | 0/16 = 0% | 0/16 = 0% | G2 阻断，未触发 |
| 启动 S5 阈值 | >30% | — | **未达，S5 不启动** |

### 2.2 通过 G1 的 9 个因子 IC_IR 明细（真实 120d 滚动）

| 因子 | IC | IC_IR | IC_std | IC_decay | 失败原因 |
|------|----|-------|--------|----------|---------|
| VT_MOM_HIGH_LOW_60D | +0.0132 | +0.0460 | 0.287 | 4.92 ⚠ | IC_IR < 0.3 |
| VT_REV_OVERREACTION | -0.0246 | -0.1324 | 0.186 | -4.09 ⚠ | IC_IR < 0.3 |
| VT_LIQ_AMIHUD_SCALED | +0.0092 | +0.0818 | 0.113 | 0.19 | IC_IR < 0.3 |
| VT_LIQ_TURNOVER_REGIME | +0.0368 | +0.1348 | 0.273 | 8.31 ⚠ | IC_IR < 0.3 |
| VT_VAL_COMPOSITE | 0.0 | 0.0 | 0.0 | 0.5 | IC=0（fundamentals 代理） |
| VT_VAL_EARNINGS_YIELD_SCALED | 0.0 | 0.0 | 0.0 | 0.5 | IC=0（fundamentals 代理） |
| VT_QUA_COMPOSITE | 0.0 | 0.0 | 0.0 | 0.5 | IC=0（fundamentals 代理） |
| VT_SIZE_LOG_NORMALIZED | 0.0 | 0.0 | 0.0 | 0.5 | IC=0（fundamentals 代理） |
| VT_GROWTH_COMPOSITE | 0.0 | 0.0 | 0.0 | 0.5 | IC=0（fundamentals 代理） |

**IC_IR 绝对值最高的因子仅 0.1348**（VT_LIQ_TURNOVER_REGIME），距 0.3 阈值仍差 0.165。

---

## 3. 根因诊断（3 条）

### 根因 A：标的样本量严重不足（结构性问题）

- **现象**：所有动量/反转/流动性类因子的 IC_std 都很大（0.113~0.287），导致 IC_IR = mean(IC)/std(IC) 在数学上无法达到 0.3。
- **原理**：cross-sectional IC 在 N 个标的下的标准误约为 `1/√(N-3)`。
  - N=23 → 标准误 ≈ 0.221
  - N=100 → 标准误 ≈ 0.102
  - N=300 → 标准误 ≈ 0.058
- **影响**：即便因子的「真实」IC=0.10，在 N=23 下 IC_IR 上界约为 `0.10/0.22 ≈ 0.45`，仍接近阈值；若 IC=0.05，IC_IR 仅 0.23。
- **结论**：**23 标的的样本量本身不具备统计显著性，IC_IR 0.3 阈值在小样本下不可达**。

### 根因 B：Fundamentals 使用价格代理（数据质量缺陷）

- **现象**：VT_VAL_*/VT_QUA_*/VT_SIZE_*/VT_GROWTH_* 5 个因子的 IC=0，IC_IR=0。
- **原因**：`real_data_loader.py` 因 Wind/iFinD 真实财务数据未接入，使用 `pe = 1/close`、`pb = 1/close`、`roe = 0.1` 等价格代理。
- **机制**：
  - PE 代理 = 1/close 与 close 完全共线，cross-sectional IC 与价格自身的 IC 完全抵消（factor 与 forward return 都来自 close，导致 IC=0）。
  - 这 5 个因子在首批次就被标记为「Value/Quality/Size 类 IC=0 from fundamentals proxy issue」，第二批次依然未解决。
- **结论**：5/16 因子（31%）在引入真实 fundamentals 之前**完全无法评估**，结构性拖累 G2 通过率。

### 根因 C：IC 衰减率计算异常（实现缺陷）

- **现象**：`ic_decay_estimated` 出现 4.92、8.31、-4.09 等数值，理论值域应为 `[-1, 1]`（衰减率 = 1 - 近期/长期 IC 均值）。
- **原因**：`compute_ic_decay` 在 `longer_ic ≈ 0` 时回退到占位 0.5，但当 `longer_ic` 极小且非零、`recent_ic/longer_ic` 比值爆炸时未做截断。
- **影响**：虽未直接阻断 G2（G2 主要看 IC_IR），但影响 Enhancement Regime 评分，需在下批次修复。
- **建议**：在 `compute_ic_decay` 末尾添加 `decay = max(-1.0, min(1.0, decay))` 截断。

---

## 4. 决议（CIO 视角）

### 4.1 启动 S5 决议

**结论：不启动 S5**。

依据 CIO 决议 §6.4：
> 「若通过率过低（< 10%），需先修复简化实现风险，再跑第二批次。」

第二批次 G2 通过率 0% < 10%，且 P0 改进已全部落地但无法解决根因 A（样本量）与根因 B（fundamentals 代理）。

### 4.2 进入 S6 持续监控决议

**结论：继续 S6 持续监控**。

依据：
- 首批次已验证 67 个因子的 KillSwitch 状态机运转正常（4 种状态转移全部触发）。
- S6 不依赖 G2 通过率，可对现有 51 因子库 + 16 候选独立运行。
- **行动**：将 S6 集成到 `daily_workflow.py` Phase 9，每日自动更新因子状态。

### 4.3 下批次改进路线（P1）

| 优先级 | 改进项 | 预期效果 | 阻塞 |
|--------|-------|---------|------|
| P1.1 | 扩展标的至 ≥100 | IC 标准误从 0.22 降至 0.10，IC_IR 上界可达 1.0+ | Wind MCP 单次拉取上限 |
| P1.2 | 引入真实 fundamentals（PE/PB/ROE/营收增速） | 解锁 5 个 Value/Quality/Size 因子 | Wind/iFinD API 接入 |
| P1.3 | 补齐 510300 ETF 真实数据 | 基准代理 -> 真实基准，Regime 划分更准 | Wind MCP 拉取 |
| P1.4 | 修复 `compute_ic_decay` 异常值截断 | decay 落入 [-1,1] 域 | 无 |
| P1.5 | 重新设计候选因子（避免与现有 50+ 因子 corr ≥ 0.7） | G1 通过率从 56% 提升至 80%+ | 需因子研究 |

---

## 5. 状态归档

- 本批次报告：`reports/vibe_trading/second_batch_20260725_105236/`
- 流水线状态：`pipeline_state.json`（含 16 因子完整 Gate 详情）
- 决议归档：本报告 §4
- **不写入生产因子库**（S5 暂缓）
- **启动 S6 持续监控集成**（见 `daily_workflow.py` Phase 9）

---

*本报告由 S4 根因分析流程自动生成，遵循 DECISION_v1.0 锁定参数与 CIO 决议 §6.4。*
