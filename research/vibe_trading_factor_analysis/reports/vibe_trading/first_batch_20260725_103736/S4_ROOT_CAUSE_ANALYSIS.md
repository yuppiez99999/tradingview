# S4 首批次流水线根因分析报告

> CIO 视角深度根因分析（v1.0）
> 生成时间：2026-07-25
> 批次：first_batch_20260725_103736

## 1. 核心结论

| 维度 | 结果 | 评估 |
|------|------|------|
| 架构验证 | ✓ 8 级状态机正常流转，0 异常崩溃 | 通过 |
| 数据真实性 | ✓ 23 标的真实 A 股 OHLCV，2 年 504 天 | 通过 |
| G1 正交性 | 9/16 (56.2%) | 合理 |
| G2 IC 稳定性 | 0/16 (0.0%) | **全部失败** |
| 准入因子 | 0 | 不进入 S5 |

**核心结论：流水线架构验证通过，但 G2 简化实现导致全部因子被拦截，需 P0 改进后再跑第二批次。**

## 2. G1 失败根因（7 因子被拦截）

候选因子与现有 51 个生产因子的相关性如下（按 |corr| 降序）：

| # | 候选因子 | |corr| | 最相关现有因子 | 解读 |
|---|---------|-------|--------------|------|
| 1 | `VT_MOM_GAP` | 1.0000 | `LIQ_SPREAD` | 与现有因子高度重叠 |
| 2 | `VT_REV_SHORT_TERM` | 1.0000 | `MOM_REVERSAL_5D` | 与现有因子高度重叠 |
| 3 | `VT_REV_VOLUME_SPIKE` | 0.9545 | `MOM_REVERSAL_5D` | 与现有因子高度重叠 |
| 4 | `VT_MOM_VOLUME_WEIGHTED_20D` | 0.9281 | `MOM_20D` | 与现有因子高度重叠 |
| 5 | `VT_MOM_ILLIQUID_60D` | 0.8518 | `MOM_60D` | 与现有因子高度重叠 |
| 6 | `VT_VOL_REGIME` | 0.7811 | `MOM_60D` | 与现有因子高度重叠 |
| 7 | `VT_VOL_DOWNSIDE_RATIO` | 0.7596 | `VOL_SKEW` | 与现有因子高度重叠 |

**G1 失败根因分析**：
- VT_MOM_GAP / VT_REV_SHORT_TERM 与现有因子相关性 1.0，本质是同一因子的不同实现
- VT_MOM_ILLIQUID_60D 与现有 MOM_60D 相关性 0.85，是动量因子族冗余
- 建议：剔除与现有因子 |corr| > 0.9 的候选，避免重复计算

## 3. G2 失败根因（9 因子尝试，0 通过）

候选因子的 IC / IC_IR 详情（按 IC_IR 升序）：

| # | 候选因子 | IC | IC_IR（估计） | 阈值 | gap |
|---|---------|----|--------------|------|-----|
| 1 | `VT_VAL_COMPOSITE` | +0.0000 | +0.0000 | 0.3000 | +0.3000 |
| 2 | `VT_VAL_EARNINGS_YIELD_SCALED` | +0.0000 | +0.0000 | 0.3000 | +0.3000 |
| 3 | `VT_SIZE_LOG_NORMALIZED` | +0.0000 | +0.0000 | 0.3000 | +0.3000 |
| 4 | `VT_GROWTH_COMPOSITE` | +0.0000 | +0.0000 | 0.3000 | +0.3000 |
| 5 | `VT_MOM_HIGH_LOW_60D` | +0.0673 | +0.0721 | 0.3000 | +0.2279 |
| 6 | `VT_LIQ_TURNOVER_REGIME` | +0.1173 | +0.1329 | 0.3000 | +0.1671 |
| 7 | `VT_LIQ_AMIHUD_SCALED` | -0.1395 | +0.1621 | 0.3000 | +0.1379 |
| 8 | `VT_REV_OVERREACTION` | -0.1703 | +0.2053 | 0.3000 | +0.0947 |

**G2 失败根因分析**（三层归因）：

### 3.1 简化实现缺陷（主因，P0 修复）
当前 `_gate2_ic_stability()` 实现为：
1. 用最后一日的 cross-sectional 5 日 forward return
2. 与候选因子值做单期 Pearson 相关
3. 经验映射 `ic_ir = |ic| / (1 - |ic|)`

缺陷：
- **未做 120d 滚动**：单期 IC 噪声极大，与 IC_IR 不是同一概念
- **forward return 窗口固定 5d**：未做多种窗口对比（1d/5d/10d/20d）
- **经验映射公式偏差大**：IC=0.1 实际 IC_IR 通常 0.3-0.5，但本公式映射出 0.11

### 3.2 样本量不足（次因，P1 修复）
- 23 个标的的 cross-sectional IC 标准误约 0.2，单期 IC 几乎无统计意义
- Renaissance/AQR 等机构 IC_IR 验证通常使用 ≥300 标的
- 当前 23 标的不足以做出可信 IC_IR 判断

### 3.3 候选因子本身可能无 alpha（次因，P2 修复）
- VT_MOM_GAP IC 接近 0，可能本身无方向性 alpha
- VT_REV_VOLUME_SPIKE 等极端因子在 23 标的中可能未触发
- 需更大样本验证

## 4. 因子类别通过率

| 类别 | 总数 | G1 通过 | G1 通过率 |
|------|------|---------|----------|
| Momentum | 4 | 1 | 25.0% |
| Reversal | 3 | 1 | 33.3% |
| Liquidity | 2 | 2 | 100.0% |
| Volatility | 2 | 0 | 0.0% |
| Value | 2 | 2 | 100.0% |
| Other | 2 | 2 | 100.0% |
| Size | 1 | 1 | 100.0% |

**类别分布观察**：
- Momentum / Reversal 类因子 G1 通过率最低（与现有动量库强相关）
- Liquidity / Volatility 类因子有正交补集空间（值得下批次重点挖掘）
- 建议第二批次增加 Sentiment / Technical 类因子（现有 51 因子中此类较少）

## 5. 改进路线图（按优先级）

### P0 - 必须修复（架构性问题）
1. **实现 `compute_factor_history(price_data, factor_def, window=120)`**
   - 返回日频因子值序列（120d 滚动窗口）
   - 用于 Gate2 IC_IR / Gate3 DSR / Shadow 90d / Regime 条件化
2. **Gate2 改为真实 120d 滚动 IC_IR**
   - 参考 `utils/alpha_factor_library.py` 现有 IC_IR 实现
   - 计算 IC 序列的 mean / std，IC_IR = mean(IC) / std(IC)
3. **Gate3 / Shadow 用真实日频因子值历史**
   - 替代 `[candidate.values] * 90` 占位
   - 避免高估 live_DSR

### P1 - 应该修复（数据完整性）
4. **扩展标的至 ≥100**（优先消费、医药、新能源板块）
5. **补齐 510300 ETF 真实数据**（用 wind-mcp-skill）
6. **引入真实 fundamentals**（PE/PB/ROE 从 wind 拉取）

### P2 - 可以修复（覆盖完整性）
7. 增加 Sentiment / Technical 类候选因子
8. Gate4 经济逻辑评分引入 LLM 评分（替代规则评分）

## 6. 准入决议

### 6.1 S5 决议：暂不执行
- 原因：本批次 0 个因子通过 G2，无因子可写入 alpha_factor_library.py
- 替代动作：完成 P0 改进后跑第二批次，若 G2 通过率 > 30% 再启动 S5

### 6.2 S6 决议：可执行
- 原因：FactorKillSwitch 不依赖具体因子通过，可作为基础设施先行部署
- 范围：部署 FactorKillSwitch 监控 51 个现有生产因子 + 16 个候选因子的实时状态
- 验收：触发条件配置就绪，监控仪表盘上线

### 6.3 整体进度
- S1-S3: ✓ 完成（数据加载 + 流水线跑通）
- S4: ✓ 完成（根因分析报告）
- S5: ⏸ 暂缓（待 P0 改进 + 第二批次验证）
- S6: → 启动（与 P0 改进并行）

---
*本报告由 S4 根因分析器自动生成，基于 pipeline_state.json 完整数据。*
