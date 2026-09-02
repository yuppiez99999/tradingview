# S13 Selection Alpha 注入设计 — 路径 A（因子综合分重算）

> 日期：2026-09-02 · 状态：设计定稿（用户已批准方向：信号源=主组合选股信号聚合 / 结构=核心-卫星 / 映射=Top-K 等权超参先验固定）
> 前序结论：S12 纯防御风险平价 = 17 资产池诚实下限（年化 7.48%/回撤 2.60%/Sharpe 1.71，DSR=0.50 卡线）。突破须池外注入 alpha，池内调参无效（S10/S11 论证链）。

## 一、目标

在保持 S12 底仓"零拟合 + 低回撤"特性的前提下，注入主组合多因子选股 alpha（池外信息源），验证能否将 DSR 从 0.50 抬升至 ≥0.95（三件套 HONEST）。

## 二、组合架构（S13 = 核心-卫星）

```
S13 = S12 底仓 70% + Alpha 卫星仓 30%（上限封顶）
      ├─ 底仓：黄金 518880 / 国债 511260 / 红利低波 512890
      │        逆波动率权重·月频再平衡（原封不动复用 _inverse_vol_weights）
      └─ 卫星仓：14 ETF 主池候选（宽基+行业，排除底仓三资产）
               按聚合信号截面排序 Top-3 等权·月频换仓
```

**超参仅 3 个，全部先验固定，不进任何回测调优循环**：

| 超参 | 值 | 依据 |
|---|---|---|
| 卫星仓上限 | 30% | 风险预算：底仓回撤 2.6% × 70% + 卫星仓极端 30%×30% ≈ 组合回撤 <12%，仍低于 15% 验收线 |
| Top-K | 3 | 行业 ETF 相关性高，K>3 边际分散有限且换手上升 |
| 再平衡频率 | 月频（月末） | 与 S12 对齐，换手成本可控 |

无信号强度门控（弱市退守）——默认版不设，作为 ablation 实验项后置。

## 三、信号管线（池外信息注入）

```
个股层因子综合分（截面）
  → 按流通市值加权聚合成 ETF 层信号（与指数编制一致，零优化）
    → 月末截面 Top-3 → 卫星仓等权持有
```

### 路径 A：因子综合分重算（本期实施）

对每只候选 ETF 的**历史成分股**（point-in-time，防幸存者偏差）逐月计算因子综合分，按成分权重（≈流通市值）聚合为 ETF 信号。

**复用现成组件（全部已验证存在）**：

| 环节 | 组件 | 位置 |
|---|---|---|
| 因子截面打分 | `batch_compute_factors()` + `cross_sectional_score()` | `utils/universe/factor_scorer.py` L149-366 |
| 因子 zoo | GTJA191（优先）/ qlib158 / alpha101，主题：momentum/reversal/volume/volatility/liquidity | `utils/gtja191_factors.py` |
| 指数成分股 | `get_hs300_constituents()` / `get_zz500_constituents()`（akshare csindex） | `utils/universe/stock_universe.py` L50-145 |
| 历史股票池（防幸存者偏差） | `SurvivorshipBiasFreeUniverse.get_universe_at_date()` + `data/universe/snapshots/` | `utils/universe/survivorship_free_universe.py` L116-169 |
| 个股 K 线 | `MarketDataProvider`（Wind>TDX>AKShare>sina）+ `data/cache/klines` 缓存 | `utils/data_provider.py` L116-128 |
| ETF 日线面板 | `data_cache/etf_phase2/all_etf_daily.parquet`（date, code, open, high, low, close, volume） | `scripts/fetch_etf_phase2_data.py` |
| 回测引擎与三件套 | S12 已注册的 `all_strategies`/`smap` + `run_p23_validation.py` | `data/etf_option_backtest/run_etf_option_backtest.py` |

### 路径 B（后置）：LGB walk-forward 重训

若 A 验证管线与显著性有效，再以季度代际 PurgedTSCV 重训 LGB 全历史信号做强化对比。本期不做。

## 四、诚实验证设计（防 S10 复辙）

1. **回测窗口**：2021-2026，与 S12 严格同窗口对照
2. **Ablation 必做**：纯 S12 vs S13 —— 隔离卫星仓真实贡献，防"β 混入 alpha"
3. **三件套**：DSR / CPCV / Noise，沿用 `run_p23_validation.py` 框架
4. **多重检验计数**：本次尝试计入家族计数（S1-S12 已 14 次），DSR 门槛按累计次数修正——S13 若 pass 需在报告中明示总尝试次数
5. **映射层零调参**：成分股→ETF 聚合用指数编制权重（流通市值），非优化选择
6. **point-in-time 纪律**：信号只用 ≤t-1 数据；成分股用历史快照而非当前快照（幸存者偏差会虚增动量类因子）

## 五、与 Phase 3 的衔接

- 09-06 shadow 启动基准 = 纯 S12（已验证的诚实下限）
- S13 若三件套通过 → 作为 shadow 第二并行策略（shadow 系统天然支持多策略）
- S13 若不通过 → shadow 跑纯 S12，S13 结论沉淀关闭该方向

## 六、验收标准

| 指标 | 门槛 | 说明 |
|---|---|---|
| 年化 | ≥ S12 的 7.48% + 1pp（即 ≥8.48%） | 否则 alpha 无经济意义 |
| 最大回撤 | ≤ 12% | 风险预算反推（见超参依据） |
| DSR | ≥ 0.95（经多重检验修正） | 三件套核心 |
| CPCV CV | < 0.5 且稳定 | 三件套 |
| ablation | S13 − S12 超额 ≥ 1pp 且来自卫星仓 | 防 β 混入 |

## 七、风险与缓解

| 风险 | 缓解 |
|---|---|
| 历史成分股快照缺失 2021-2022 | `build_daily_snapshots()` 批量补建；缺失月份回退用当月最近可得快照（记录降级） |
| 因子计算量爆炸（月频×5年×数百成分股） | 只算候选 ETF 重叠成分股并集去重；klines 缓存 + 并行（ScoringConfig 已有 n_workers） |
| AKShare 成分接口限流 | 快照落盘后复用，不重复拉取 |
| 聚合后信号同质化（ETF 间相关性高） | Top-3 而非 Top-1；若前 3 高度同源（同板块）由等权自然稀释 |

---

## 八、执行结果（2026-09-02 ✅ 已验证：路径 A 未通过，方向关闭）

> 报告: `data/etf_option_backtest/s13_validation_20260902_083405.md` · LOG 指针: `cairn/LOG.md` 2026-09-02 "S13 selection alpha 路径 A 验证" 条目

**实施偏差（相对 §三 设计，如实记录）**:
- `utils/vibe_trading_adapter.VibeTradingAdapter` 无因子计算能力（`list_factors` 不存在）→ 改用**自包含 15 因子**（GTJA191 风格：momentum×3 / reversal×3 / volume×3 / volatility×3 / liquidity×3，纯 pandas 滚动窗口，方向与主题权重先验固定）
- point-in-time 历史成分股不可得 → 用**当前成分快照**（akshare csindex/SZSE，11 ETF）+ baostock 全历史前复权 K 线 1822 只（K 线严格 point-in-time，月末截面仅用 ≤当日数据）
- 信号: `scripts/build_s13_signals.py` → `data/etf_option_backtest/s13_signals.parquet`（68 月份 × 11 ETF，每月截面 ~1800 只股票）

**验证结果（n_trials=15，与 S12 同窗口对照）**:

| 策略 | 年化% | 回撤% | Sharpe | DSR | CPCV | 判定 |
|---|---|---|---|---|---|---|
| S12 纯防御风险平价(对照) | 6.71 | 2.98 | 1.594 | 0.2469 | ✓ 稳定 | 基准 |
| S13 核心-卫星(因子信号) | 3.30 | 18.40 | 0.150 | 0.0000 | ✗ CV=1.976 | **FAIL（六项验收仅 noise_stable 达标）** |

Ablation: 年化 **-3.41pp**（门槛 +1pp），回撤 **+15.41pp**（门槛 ≤12%）。

**结论**:
1. **路径 A 信号无效** — 股票截面综合分聚合到 ETF 层后不产生可用的 selection alpha；卫星仓 30% 在 2021-2026 A 股弱势区间是纯拖累（回撤放大 5 倍，年化拉低 3.4pp）
2. **归因** — 卫星仓暴露的是权益 β 而非 α：因子截面分选出的"最强"ETF 仍是高 β 品种，弱市无信号强度门控（§二 明确后置的 ablation 项）放大了回撤；同时当前成分快照的幸存者偏差并未转化为动量增益，说明聚合信号本身区分度不足
3. **决策（按 §五）** — S13 不进入 shadow；**Phase 3（09-06）按纯 S12 启动**。路径 B（LGB walk-forward 重训）的前置条件"A 验证管线与显著性有效"未满足，亦不启动
4. 资产保留：K 线缓存（1822 只）、成分缓存、`build_s13_signals.py` 分片拉取与 `run_s13_validation.py` 验证管线可复用于后续 alpha 研究

*本文档为 S13 路径 A 的设计真相源；实施细节见对应实施计划，执行结论回写本文档 + cairn/LOG.md。*
