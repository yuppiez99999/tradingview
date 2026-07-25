# ALIGNMENT — Vibe-Trading 因子分析项目（6A 阶段 1：Align）

> 对齐文档：分析现有系统结构、明确本任务范围、列出关键决策点。
> 创建日期：2026-07-25

## 1. 现状分析

### 1.1 项目结构

- utils/ — 核心交易系统模块
  - alpha_factor_library.py — 现有 Alpha 因子库（50+ 因子，6 大类）
  - alpha_evaluator.py — 因子评估器（IC/IC_IR/换手率/衰减）
- research/ — 研究与实验
  - backtest_runner.py — 回测运行器
  - vibe_trading_factor_analysis/ — ★ 本任务项目
- docs/ — 6A 工作流文档
- reports/ — 运行时报告

### 1.2 技术栈

- 语言：Python 3.8+
- 数值栈：numpy / pandas
- 现有因子库：utils/alpha_factor_library.py（AlphaFactorLibrary.compute_all）
- 因子评估：utils/alpha_evaluator.py（AlphaEvaluator，IC/IC_IR/衰减检测）

### 1.3 架构模式

- 三层 Alpha 架构：决策 Alpha / 因子 Alpha / 执行 Alpha
- 本任务属于因子 Alpha 层，只读接入候选因子
- 现有因子库采用类别分簇 + 中性化模式（动量/价值/质量/低波/规模/流动性）

## 2. 任务范围与目标

### 2.1 范围

将 Vibe-Trading 开源项目的 450+ 因子中的代表性 13 个（覆盖 8 大类）桥接到现有系统，作为候选因子池，通过四道关卡验证后才可进入生产因子库。

### 2.2 目标（验收标准）

| 编号 | 目标 | 验收标准 | 状态 |
|------|------|----------|------|
| G1 | 候选因子桥接器 | VibeTradingFactorAdapter 实现 8 类因子计算 | 完成 |
| G2 | 正交性闸口 | Gate 1：|corr| < 0.7 | 完成 |
| G3 | IC 稳定性闸口 | Gate 2：IC_IR >= 0.3，衰减 < 0.6 | 基础版完成 |
| G4 | 元数据目录 | factor_mapping.json + candidate_factors_catalog.json | 完成 |
| G5 | 烟雾测试 | _smoke_test_adapter.py 通过 | 完成 |
| G6 | 防过拟合闸口 | Gate 3：DSR n_trials >= 5 | 阶段二 |
| G7 | 影子账户 | 60 日观察 + 自动 DSR | 阶段二 |


## 3. 关键决策点（需用户确认）

以下决策影响后续架构（阶段 2 Architect），需在进入阶段 2 前明确。

### D1. 正交性阈值

- 当前：|corr| < 0.7
- 候选 A：0.7（宽松，保留更多候选）— 当前默认
- 候选 B：0.5（严格，与现有因子高度相似的一律剔除）
- 候选 C：分级（动量类 0.5，价值类 0.7）

### D2. IC 稳定性验证窗口

- 当前：基础版未设滚动窗口
- 候选 A：60 日滚动 IC_IR（与现有 AlphaEvaluator 一致）
- 候选 B：120 日滚动（更稳定但响应慢）

### D3. 影子账户准入条件

- 当前：阶段二未实现
- 候选 A：通过 Gate1+Gate2 即进影子账户观察 60 日
- 候选 B：通过 Gate1+Gate2+Gate3（DSR）才进影子账户
- 建议：B（防过拟合优先）

### D4. 候选因子接入生产因子库的最终审批

- 候选 A：自动准入（通过四道关卡 + 影子账户 DSR 达标）
- 候选 B：人工审批（因子委员会评审）
- 候选 C：多 Agent 决策治理（投资委员会模式）
- 建议：C（与系统多 Agent 架构对齐）

## 4. 约束与风险

### 4.1 硬约束（来自 project_memory）

- 候选因子计算必须使用真实 OHLCV，禁止 synthesize_ohlcv_from_returns()
- 正交性检查使用真实 forward returns，禁止 bfill()
- 进入生产因子库前必须通过四道关卡 + 60 日影子观察
- 系统继续使用模拟交易环境，实盘集成延后

### 4.2 风险

| 风险 | 影响 | 缓解 |
|------|------|------|
| 候选因子与现有因子高度共线 | 因子冗余，无增量信息 | Gate 1 正交性闸口 |
| 候选因子在 A 股过拟合 | 实盘失效 | Gate 3 DSR + 影子账户 |
| Vibe-Trading 公式实现偏差 | 计算错误 | 烟雾测试 + 单元测试 |
| 外部项目依赖变更 | 接口断裂 | 适配器模式隔离 |

## 5. 下一步（阶段 2：Architect）

进入 6A 阶段 2 前，需用户确认上述 D1-D4 决策点。

---

## 6. 决策结果（已确定，对冲基金视角，2026-07-25）

经以世界顶级对冲基金（Renaissance / Two Sigma / AQR / Citadel）视角审议，D1-D4 决策如下：

| 决策 | 选择 | 依据 |
|------|------|------|
| D1 正交性 | |corr| < 0.5 严格 | AQR/Two Sigma 标准；>0.5 即 >25% 共享方差，0.5-0.7 进监控池 |
| D2 IC 窗口 | 双窗口 60d告警/120d准入 | Citadel 模式；快速衰减检测 + 稳定准入 |
| D3 影子准入 | 必须先过 Gate3(DSR) | Renaissance；多重检验偏差防线，450+候选下不可妥协 |
| D4 审批 | 多 Agent 委员会 | 5 专家 Agent + Chair 各否决权，避免单点偏差 |

追加 5 项对冲基金级增强：
- E1 容量分析：因子须能部署 >= 组合 2%
- E2 换手率感知 IC 阈值：高换手率需更高 IC
- E3 Regime 条件化：全 regime IC_IR > 0.2（直击 V6.2 bull regime 失败根因）
- E4 相关性压力测试：尾部事件相关性断裂检测
- E5 因子 Kill Switch：连续 5 日 IC<0.02 -> degraded，10 日 IC<0 -> 禁用

---

## 7. 终版决策（世界顶级对冲基金视角，2026-07-25 二次审议）

经 Renaissance / Two Sigma / AQR / Citadel / Bridgewater 五大基金视角二次审议，识别出 5 项关键缺失，追加决策 D5-D9。

### 7.1 二次审议发现的缺失

| 基金视角 | 发现的缺失 | 严重度 |
|----------|------------|--------|
| Renaissance | 仅 DSR 不够，缺 PBO（Probability of Backtest Overfitting） | P0 |
| Two Sigma | 因子组合方法未定义（通过委员会后如何加权？） | P0 |
| AQR | Gate4 经济逻辑仅定性判断 | P1 |
| Citadel | 影子账户无 fail-fast，极端情况下持续亏损 | P0 |
| Bridgewater | 缺因子逻辑回归测试（IC 失效可能因逻辑变化） | P1 |

### 7.2 追加决策 D5-D9

| 决策 | 选择 | 依据 | 影响 |
|------|------|------|------|
| **D5 PBO 检验** | 影子准入前 PBO < 0.5（Bailey 2017） | Renaissance；PBO 是 DSR 的概率补充，量化"过拟合概率" | 影响 Gate3 + T1 |
| **D6 因子组合** | 通过 Robust Risk Parity 加权（Candelon & Jouini 2012） | Two Sigma；等权忽视因子风险贡献差异 | 影响 T4 + 生产集成 |
| **D7 经济逻辑量化** | Gate4 改为 3 维评分：学术文献(0-4)+A股适配(0-4)+跨市场稳定性(0-2)，总分 >= 7 | AQR；定性 Gate4 易受主观偏差 | 影响 Gate4 |
| **D8 影子 fail-fast** | 单日回撤 > 3% 立即终止 + 3 日累计回撤 > 5% 终止 + 触发后冷却 30 日 | Citadel；防止影子账户变成实盘损失模拟器 | 影响 T3 ShadowAccount |
| **D9 逻辑回归测试** | 每周自动回归检验因子经济逻辑（IC 失效时验证逻辑是否还成立） | Bridgewater；IC 失效 ≠ 逻辑失效，但需区分 | 影响 T7 KillSwitch |

### 7.3 对 T3 ShadowAccount 的直接约束（D8）

D8 fail-fast 决策直接影响 T3 ShadowAccount 的实现，需在 60 日纸面交易中加入：

1. **每日止损线**：单日 PnL < -3% * portfolio_value -> 立即终止，标记 `terminated_by: "daily_stop_loss"`
2. **滚动止损线**：3 日累计 PnL < -5% * portfolio_value -> 立即终止，标记 `terminated_by: "rolling_stop_loss"`
3. **冷却期**：触发 fail-fast 后，因子进入 30 日冷却池，期间不可重新进入影子
4. **审计**：每次 fail-fast 触发必须生成 `shadow_fail_fast_event.json`，包含触发日、累计回撤、持仓快照

### 7.4 对 T1 DSRValidator 的直接约束（D5）

D5 PBO 决策要求 T1 DSRValidator 扩展 PBO 计算：

1. **CSCV 实现**：Combinatorially Symmetric Cross-Validation（Bailey 2017）
2. **PBO 阈值**：PBO < 0.5 -> 非过拟合；PBO >= 0.5 -> 过拟合
3. **联合判定**：DSR > 0 AND PBO < 0.5 -> Gate3 通过

### 7.5 决策影响矩阵

| 任务 | 受 D5-D9 影响项 | 优先级调整 |
|------|-----------------|------------|
| T1 DSRValidator | D5 PBO 扩展 | P0 优先（已实现 DSR，需补 PBO） |
| T3 ShadowAccount | D8 fail-fast | P0 优先（实现时直接集成） |
| T4 FactorCommittee | D6 Robust Risk Parity | P1（委员会决策后用于组合） |
| T7 FactorKillSwitch | D9 逻辑回归测试 | P2（KillSwitch 触发时调用） |

详细技术方案见 ARCHITECTURE_vibe_trading_factor_analysis.md，原子化任务见 TASK_vibe_trading_factor_analysis.md。

