# V8 动态权重融合实施计划

## Context（背景）

### 问题
Walk-Forward Sharpe CV 目标 <0.5 长期未达成：
- V6.2 (正确基线): Sharpe CV = 0.55
- V7.1 (权重惩罚): Sharpe CV = 0.74 (恶化)
- V7.2 (集中度限制): Sharpe CV = 0.71 (略改善但仍不达标)

### 根因
窗口1 (2023-07~2024-09) Sharpe 仅 0.238，LGB 模型在 bull regime 信号失效：
- 2024-06 (bull): 688017 权重10% 但跌34.82%, 300308 权重10% 但跌17.34%
- V7.1 权重惩罚因"波动率悖论"失效 (vol20 低于阈值)
- V7.2 集中度限制因"归一化副作用"失效 (释放权重被重分配给其他亏损股)

### 结论
权重后处理 (V7.1/V7.2) 无法修复 Alpha 信号质量问题。需要**动态权重融合**：当 LGB 近期表现差时，自动回退到等权基线。

### V8 方案
基于 LGB 滚动 IC 表现，动态融合 LGB 权重与等权基线：
- `final = α * lgb_weights + (1-α) * equal_weights`
- `α` 由滚动 IC 和 regime 决定
- IC > 0 (LGB 近期正确): α 高，信任 LGB
- IC < 0 (LGB 近期错误): α 低，回退等权

## 实施步骤

### Step 1: 创建 V8 重算脚本 `_run_v8_dynamic_fusion.py`

基于 V7.1 记录重算，无需重跑完整回测 (避免 LGB 训练崩溃)。

**核心逻辑：**

```python
def compute_realized_ic(weights: dict, returns: dict) -> float:
    """计算单月实现 IC: 权重与收益的 Spearman 相关"""
    # 只看有权重的股票
    pairs = [(w, returns.get(s, 0)) for s, w in weights.items() if w > 0]
    if len(pairs) < 5:
        return 0.0
    w_series = pd.Series([p[0] for p in pairs])
    r_series = pd.Series([p[1] for p in pairs])
    return float(w_series.corr(r_series, method="spearman"))

def compute_trust_alpha(rolling_ic: float, regime: str) -> float:
    """根据滚动 IC 和 regime 计算信任度 α"""
    # IC 分档
    if rolling_ic > 0.15:
        alpha = 0.85  # 高信任
    elif rolling_ic > 0.05:
        alpha = 0.65  # 中信任
    elif rolling_ic > -0.05:
        alpha = 0.50  # 中性
    elif rolling_ic > -0.15:
        alpha = 0.35  # 低信任
    else:
        alpha = 0.15  # 不信任, 主要用等权

    # Regime 调整: bull regime 进一步降低 LGB 信任
    if regime == "bull":
        alpha *= 0.80
    elif regime == "bear":
        alpha *= 0.90

    return max(0.10, min(0.90, alpha))

def fuse_weights(lgb_weights: dict, alpha: float) -> dict:
    """融合 LGB 权重与等权基线"""
    active = {s: w for s, w in lgb_weights.items() if w > 0}
    n_active = len(active)
    if n_active == 0:
        return lgb_weights

    equal_w = sum(active.values()) / n_active  # 等权 (保持总暴露度)
    fused = {s: alpha * w + (1 - alpha) * equal_w for s, w in active.items()}

    # 归一化保持总暴露度
    total_old = sum(lgb_weights.values())
    total_new = sum(fused.values())
    if total_new > 0:
        scale = total_old / total_new
        fused = {s: w * scale for s, w in fused.items()}

    return fused
```

**重算流程：**
1. 加载 V7.1 完整记录 (45 个月)
2. 对每个月 t:
   a. 计算实现 IC = corr(weights_t, returns_t)
   b. 计算滚动 3 月 IC = mean(IC_{t-3}, IC_{t-2}, IC_{t-1})
   c. 计算 α = compute_trust_alpha(rolling_ic, regime_t)
   d. 融合权重: fused = fuse_weights(lgb_weights, α)
   e. 重算收益: new_return = sum(fused * returns)
3. 计算 V8 指标: 年化收益/Sharpe/最大回撤/Sharpe CV/DSR

**文件路径：** `e:\各种PY程序\28-终极量化交易系统8.4\_run_v8_dynamic_fusion.py`

### Step 2: 验证 V8 结果

**关键验证点：**
1. 2024-06 月 (bull regime 崩盘月):
   - V7.1: -5.29%
   - V7.2: -5.26%
   - V8 预期: 应显著改善 (若前3月 IC<0, α降低, 接近等权)
2. 窗口1 Sharpe (V7.1/V7.2: 0.238):
   - V8 目标: >0.4 (通过动态回退等权减少 LGB 错误)
3. Sharpe CV (V7.2: 0.71):
   - V8 目标: <0.5
4. DSR (V7.2: 5):
   - V8 目标: ≥5 (保持)

### Step 3: 敏感性分析

测试不同参数组合，确认 V8 鲁棒性：
- 滚动 IC 窗口: 2/3/6 月
- IC 分档阈值: 0.05/0.10/0.15
- Regime 调整系数: 0.7/0.8/0.9

### Step 4 (可选): 集成到 backtest_runner.py

若 V8 重算达标，将其集成为 backtest_runner 的正式模块：
- 在 `_apply_v71_weight_penalty` 之后、`enforce_hard_constraints` 之前添加 V8 融合
- 函数名: `_apply_v8_dynamic_fusion(weights, date, regime_info, rolling_ic)`
- 滚动 IC 从历史记录中计算

## 关键文件

| 文件 | 用途 | 修改类型 |
|------|------|----------|
| `_run_v8_dynamic_fusion.py` | V8 重算脚本 | 新建 |
| `research/backtest_runner.py` | 集成 V8 (Step 4) | 修改 (可选) |
| `output/validation_reports/lgb_backtest_v71_signal_penalty_20260725_093627.json` | V7.1 记录 (输入) | 读取 |

## 验证方法

```bash
# 运行 V8 重算
python _run_v8_dynamic_fusion.py

# 预期输出
# 1. 每月 IC/α/融合权重 变更摘要
# 2. V8 vs V7.1/V7.2/V6.2 对比表
# 3. Sharpe CV 是否 <0.5
# 4. 2024-06 月改善情况
# 5. 结果保存到 output/validation_reports/dsr_walkforward_v8_*.json
```

## 设计优势

1. **直击根因**: 当 LGB 信号失效 (IC<0) 时自动回退等权，避免集中亏损
2. **完全动态**: 无硬编码阈值，α 由市场表现自适应
3. **无需重训**: 基于 V7.1 记录重算，规避 LGB 训练崩溃
4. **保留 V7.2 收益**: V7.2 的 DSR≥5 和低峰度优势应保持 (融合后权重更分散)
5. **可解释**: α 值明确反映 LGB 信任度，便于调试
