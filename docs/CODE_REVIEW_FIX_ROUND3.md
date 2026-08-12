# 代码审查修复批次 · 第三轮（2026-08-11）

> 第三轮定向扫描：聚焦前两轮（缓存投毒 / NaN 污染）之外、**静态工具全绿但会静默产生错误数值**的缺陷——
> 除零、空序列、权重漂移、陈旧数据当新鲜用、裸 except 吞异常。
> 扫描方式：ruff 定向真实缺陷规则（B/F/E/PLW）+ 人工核查高危模块
> （`black_litterman_optimizer` / `data/data_layer` / `risk_metrics` 等）。

## 本轮回退验证

- 全源码 ruff 定向规则（B006/B007/B025/B013/B904/F522/F523/F524/F706/F707/E711~E714/E721/E722/E741/F841/PLW2901/PLW1508）：**仅风格项命中**（PLW2901 循环变量覆盖、F841 未用变量、B007、E741），**无真实逻辑 bug**。
- 除零路径核查：`multi_factor_signal` / `fast_backtest` / `data_quality_monitor` 的 `mean/std` 均已加 `std>0` / `std<1e-10` / `IC_STD_FLOOR` 守卫 → **已正确防护，非本轮回退点**。

## 已修复缺陷

### F-7 🔴 资金安全（静默 NaN 权重）— `utils/black_litterman_optimizer.py`
- **位置**：`__init__`（原 115 行附近）+ `_mean_variance_optimize` 第 340 行 `w_unconstrained = cov_inv @ expected_returns / self.delta`
- **问题**：`__init__` 只校验了 `default_confidence ∈ [0,1]`，**未校验 `risk_aversion(δ) > 0`**。若构造时传入 `delta=0`（配置误填），numpy 下 `数组 / 0.0` 返回 **inf 且不抛异常** → `inf.sum() > 0` 成立 → 返回 `inf/inf = NaN` 权重 → 下游仓位全 NaN → 实盘灾难，**全程不报错**。
- **修复**：`__init__` 中增加 `if risk_aversion <= 0: raise ValueError(...)`，把静默 NaN 变成明确报错。
- **佐证**：旧批次 `CODE_REVIEW_FIX_BATCH_20260811.md` 的 H-6 已指出"BL 优化权重未校验"，本修复与之呼应。

### F-8 🟡 数据正确性（陈旧数据误报满分）— `utils/data/data_layer.py`
- **位置**：`_query_with_fallback` 中 P6 缓存命中分支（Feature Flag `USE_INTEGRATED_DATA_LAYER` 开启时生效，默认 False）
- **问题**：P6 缓存兜底（最多 24h 前的陈旧数据）命中时 `quality_score` 被硬编码 **100.0** 且跳过 DataGate，但模块 docstring 承诺"stale 警告"却从未打印。下游若信任 `quality_score` 会把旧数据当实时数据用于交易。
- **修复**：P6 命中时 `quality_score = STALE_QUALITY_SCORE (0.0)` 并 `logger.warning("使用 P6 陈旧缓存兜底…请勿作为实时数据用于交易决策")`，履行 stale 警告契约。权威陈旧信号仍是 `QueryResult.from_cache=True`。

### F-9 💭 鲁棒性（裸 except 吞异常）— `cli/modes/gemma_analyze.py`
- **位置**：第 88 行原 `except:`（裸 except）
- **问题**：裸 `except:` 会吞掉 `KeyboardInterrupt` / `SystemExit` 之外的**所有**异常，掩盖真实错误，调试困难。
- **修复**：改为 `except (ValueError, AttributeError):`（JSON 解析失败专属），仅跳过错误详情解析，不掩盖其他异常。

## 已判定为误报 / 不修复项

| 项 | 结论 |
|---|---|
| ruff F811 `POSITION_SYMBOLS` 重复导入（`lgb_enhanced_trainer.py:62` & `:151`） | **误报**：两次均来自同一模块 `autolearn_trainer`，值完全相同，仅冗余 |
| ruff E721 类型比较（`quant_modules/ai_hedge_fund/utils/llm.py`） | 逻辑瑕疵，非资金路径，记为 💭 nit，未改 |
| ruff 大量 F841/B007/PLW2901/E741 | 风格噪声，非本轮回退点，建议统一 lint 周期清理 |

## 回归测试

新增 `tests/regression/test_silent_numeric_bugs.py`（4 用例全过）：
- `test_black_litterman_rejects_nonpositive_delta`：δ≤0 构造抛 ValueError
- `test_black_litterman_valid_delta_yields_finite_weights`：有效 δ 下权重全有限（无 inf/NaN）
- `test_p6_cache_hit_marks_stale`：P6 命中 `from_cache=True` 且 `quality_score==STALE_QUALITY_SCORE`
- `test_p6_cache_hit_logs_stale_warning`：P6 命中打印 stale 告警

## 验证结果

- 改动文件 `py_compile` 通过
- 回归 + 单元测试全绿：**51 passed, 1 skipped**（含前两轮 F-3/F-4/F-5/F-6 锁定测试，无回退）

## 建议纳入统一标准的量化专项规则（供 CODE_REVIEW_STANDARD 补充）

1. **相关系数 / IC 调用必须 `np.isfinite` 守卫**（F-4/F-5/F-6 同类）
2. **除零路径必须守卫**：`mean/std`、`w/δ`、`x/y` 在分母可能为 0（常数序列/配置误填）时必须 `if denom==0` 或 `np.where`
3. **风险厌恶 / 缩放系数等分母类参数，构造期必须校验 > 0**（F-7）
4. **陈旧/缓存数据必须显式标记 stale 并降级质量分**，不得 `quality_score=100`（F-8）
5. **禁止裸 `except:`**，catch 应精确到异常类型（F-9）
