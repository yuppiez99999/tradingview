# MVSK P5-1 启动前预研结论 (2026-09-01)

> 关联: W7.1.6 (ROADMAP line 315, ✅ DONE 2026-08-24) / `utils/universe/portfolio_builder.py` / `scripts/launch_shadow_30day.py`
> 触发: 09-05 B4 启动 + 09-13 shadow 30 天 cron 前需确认 378 日历史数据就绪
> LOG 指针: `cairn/LOG.md` 2026-09-01 条目

## 1. 代码就绪确认 ✅

| 项 | 状态 | 位置 |
|---|---|---|
| `apply_mvsk_shadow_to_mid_layer()` | ✅ 就绪 | `portfolio_builder.py:498` |
| `MVSKShadowResult` dataclass | ✅ 就绪 | `portfolio_builder.py:375` |
| `_load_mvsk_history()` 冷启动 | ✅ 就绪 (含 fallback) | `portfolio_builder.py:386` |
| `_compute_mvsk_weights()` | ✅ 就绪 (调 RiskBudgetOptimizer) | `portfolio_builder.py:433` |
| shadow 模式不修改 portfolio | ✅ 验证 (portfolio unchanged=True) | `portfolio_builder.py:578` |
| 差异记录到 jsonl | ✅ 就绪 | `portfolio_builder.py:481` |
| 单测 | ✅ 289 行 5 场景全绿 | `tests/utils/universe/test_portfolio_builder_mvsk.py` |
| `launch_shadow_30day.py` 集成 | ✅ 就绪 | `scripts/launch_shadow_30day.py:196` |

## 2. 关键发现：378 日真实历史数据未预加载 ⚠️

`_load_mvsk_history()` (`portfolio_builder.py:386`) 逻辑：

```python
if feature_store_path and feature_store_path.exists():
    df = pd.read_parquet(feature_store_path)       # 真实数据路径
    if len(df) < days_required: return None
    return df.values[-days_required:]
# ↓ fallback (无 feature_store_path 时)
rng = np.random.default_rng(42)
returns = rng.normal(0.0005, 0.02, (days_required, len(symbols)))  # 合成随机数据
return returns
```

**问题**：`launch_shadow_30day.py:196` 调用 `apply_mvsk_shadow_to_mid_layer()` **未传 `feature_store_path`** → 走合成随机 fallback。

**后果**：MVSK shadow 在 `Normal(0.0005, 0.02)` 合成数据上优化 → shadow diff 记录的 weight_diff_l2 / Δ夏普 **无统计意义**，09-13~10-13 shadow 30 天评估将无法判定 MVSK 是否优于 BL+MV。

**FeatureStore 现状**：`utils/feature_store/` 仅有 .py 源码，**无 parquet 数据文件**（G9 FeatureStore 物理分层在 Stage 3 10-13~10-31 才执行）。

## 3. Mid-layer 标的范围

- 中线层: **30 只标的** (`PortfolioBuildConfig.mid_count=30`), 总权重 30%
- 数据需求: 30 symbols × 378 交易日收益率矩阵 (~11,340 数据点)
- 获取难度: **低** — 单次 Wind/TDX/AKShare 批量拉取 30 标的 ~1.5 年日线即可

## 4. 建议方案 (09-05~09-12 窗口, Sprint 1 后半)

| 方案 | 工作量 | 说明 | 推荐 |
|---|---|---|---|
| **A: launch_shadow 传 path** | ~0.5 人天 | 修改 `launch_shadow_30day.py` 增加 `_fetch_mid_layer_returns()` 从数据源拉 30 标的 378 日 → 存 parquet → 传 `feature_store_path` | ✅ 推荐 |
| B: FeatureStore offline 构建 | ~1 人天 | 用 `offline_store.py` 构建 offline parquet (G9 Stage 3 提前子任务) | 可选 (提前 G9) |
| C: 接受合成 fallback | 0 | 09-13~10-13 MVSK shadow 评估标记 "statistically invalid"，仅 qlib shadow 有效 | ❌ 不推荐 (浪费 30 天窗口) |

**推荐方案 A**：在 09-05~09-12 窗口内执行（原 P5-1 排期窗口），不超支线预算。

## 5. 09-05 启动前检查清单

- [ ] 方案 A 落地：`launch_shadow_30day.py` 增加 `_fetch_mid_layer_returns()` + 传 `feature_store_path`
- [ ] 验证：`_load_mvsk_history()` 返回真实数据 (非 fallback)，`returns.shape == (378, 30)`
- [ ] 确认 mid-layer 30 标的列表 (从 `config/portfolio.yaml` 或运行时 portfolio 提取)
- [ ] 单测：`test_portfolio_builder_mvsk.py` 增加 "真实 feature_store_path" 场景

## 6. 风险

- 若 09-05 前未落地方案 A → 09-13 shadow 30 天 cron 启动后 MVSK 评估无效 → 10-13 决策点仅能评 qlib，MVSK P5-3 启用决策顺延
- G9 FeatureStore 物理分层 (Stage 3, 10-13~10-31) 与方案 A 不冲突 — 方案 A 是临时 parquet，G9 是正式 offline/online 分层

---
更新者: CodeArts (2026-09-01 预研)
下一步: 09-05~09-12 窗口执行方案 A (Sprint 1 后半); 09-13 shadow 30 天 cron 启动前验证真实数据加载