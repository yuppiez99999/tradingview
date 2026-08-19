# LGB 增强模型集成到 Alpha 评估步骤 — 实施计划

## 摘要

将 LGB 增强模型以 **Walk-forward 重训** 方式集成到 `institutional_pipeline_runner.py` 的 Alpha 评估步骤，替换当前低效的 60 日动量信号。采用 **LGB 为主（80%）+ 动量兜底（20%）** 的融合策略。预期将年化收益从 0.29% 提升至 3-6%。

---

## 当前状态分析

### 现有 Alpha 评估流程
- **文件**: [institutional_pipeline_runner.py](file:///e:/各种PY程序/28-终极量化交易系统8.4/institutional_pipeline_runner.py)
- **方法**: `_real_alpha_evaluation()` (L266-316) 计算 60 日动量因子 IC
- **问题**: 60 日动量 IC 多为负值，导致 Alpha 信号无效，年化收益仅 0.29%
- **下游**: `_step_signal_fusion()` (L322-342) 融合 Alpha 信号 → `_step_portfolio_optimization()` (L348-385) 生成权重

### LGB 增强模型现状
- **文件**: [lgb_enhanced_trainer.py](file:///e:/各种PY程序/28-终极量化交易系统8.4/lgb_enhanced_trainer.py)
- **模型文件**: `models/lgb_enhanced/{symbol}/{symbol}_lgb_enhanced_model.pkl` + `{symbol}_meta.json`
- **23 个标的全部已训练**，CV IC 0.06-0.27，final IC 0.10-0.27
- **训练期**: 2024-09 至 2026-07（与回测期 2024-01~2025-12 重叠 → 必须 Walk-forward 重训）

### 回测运行器
- **文件**: [research/backtest_runner.py](file:///e:/各种PY程序/28-终极量化交易系统8.4/research/backtest_runner.py)
- **频率**: 月度（每月第一个交易日，`freq="BMS"`）
- **流程**: 每个日期创建新的 `InstitutionalPipelineRunner` 实例 → 调用 `run()` → 获取 `target_weights`
- **支持断点续跑**: 优先复用已落盘的 `pipeline_backtest.json`

### 关键约束
1. **前视偏差守卫**: [ci_lookahead_guard.py](file:///e:/各种PY程序/28-终极量化交易系统8.4/ci_lookahead_guard.py) 检测 bfill/shift(-N)/train_test_split/全样本标准化
2. **回测完整性**: [utils/backtest_integrity.py](file:///e:/各种PY程序/28-终极量化交易系统8.4/utils/backtest_integrity.py) 校验价格前视偏差 + Alpha provenance
3. **特征工程依赖**: 技术因子 + 截面因子 + 行业相对强度 + 资金流向 + 跨市场信号 + 情绪因子
4. **跨市场代理标的**: 518880(黄金) / 600036(银行) / 588000(科技) / 515180(红利) — 需预加载

---

## 实施方案

### 决策记录
| 决策项 | 选择 | 理由 |
|--------|------|------|
| 前视偏差处理 | Walk-forward 月度重训 | 用户指定；消除模型训练数据泄漏 |
| LGB 与动量关系 | LGB 80% + 动量 20% | 用户指定；LGB 为主，动量增强鲁棒性 |
| 重训频率 | 月度（与回测频率一致） | 平衡训练时间与模型新鲜度 |
| 情绪因子 | 跳过（用空 dict） | 历史 新闻数据不可回溯；特征工程已处理缺失 |
| IC 计算方式 | 使用 CV IC（训练产出） | OOS 指标，无需额外预测历史日期 |
| 模型存储 | 内存缓存（不落盘） | 避免污染已训练的生产模型 |

---

### 变更 1: 新增 LGB Walk-forward 管理器

**文件**: `institutional_pipeline_runner.py`（在类 `InstitutionalPipelineRunner` 中新增方法）

**新增方法**:

#### 1.1 `_lgb_walkforward_train() -> Dict[str, Any]`
- **职责**: 用 `_historical_cache` 数据（已截断到 cutoff）训练所有标的的 LGB 模型
- **流程**:
  1. 构建 `ohlcv_dict` from `_historical_cache`（确保包含全部 POSITION_SYMBOLS + 跨市场代理）
  2. 调用特征工程管线（复用 `lgb_enhanced_trainer.py` 的函数）:
     - `add_technical_features(df)` — 逐标的
     - `add_cross_sectional_features(ohlcv_dict)` — 截面排名
     - `add_industry_relative_strength_features(featured_dict)` — 行业相对强度
     - `add_capital_flow_features(featured_dict)` — 资金流向
     - `add_cross_market_features(featured_dict)` — 跨市场信号
     - `add_sentiment_features(featured_dict, {})` — 空情绪（跳过新闻）
  3. 逐标的调用 `train_symbol_enhanced(symbol, featured_dict[symbol], config)`
  4. 缓存结果到 `self._lgb_models: Dict[str, Dict]`（含 model, selected_features, cv_metrics, signal）
  5. 返回训练摘要 `{trained: N, failed: N, results: {...}}`
- **配置**: 复用 `LGB_ENHANCED_CONFIG`，但 `n_estimators` 降至 1000、`early_stopping_rounds` 降至 100（加速训练，月度重训无需 2000 轮）

#### 1.2 `_lgb_get_signal(symbol: str) -> Tuple[float, float]`
- **职责**: 获取指定标的的 LGB 信号
- **返回**: `(signal_strength, confidence)`
  - `signal_strength`: `tanh(raw_pred * 100)`，范围 [-1, 1]
  - `confidence`: `min(1.0, abs(cv_ic) + 0.2)`，基于 CV IC
- **降级**: 若模型未训练成功，返回 `(0.0, 0.0)`

#### 1.3 `_build_lgb_feature_dict() -> Dict[str, pd.DataFrame]`
- **职责**: 从 `_historical_cache` 构建完整特征字典
- **流程**:
  1. 确保 `_historical_cache` 包含全部 POSITION_SYMBOLS（缺失的临时拉取）
  2. 添加跨市场代理标的（518880/600036/588000/515180）到缓存
  3. 按训练管线顺序应用特征工程函数
  4. 返回 `featured_dict: {symbol: DataFrame}`
- **截断**: 所有数据严格截断到 `cutoff = pd.Timestamp(self.ctx.report_date)`

---

### 变更 2: 修改 Alpha 评估步骤

**文件**: `institutional_pipeline_runner.py`

#### 2.1 修改 `_step_alpha_evaluation()` (L252-264)
- 在 backtest 模式下，先调用 `_lgb_walkforward_train()`
- 然后调用 `_real_alpha_evaluation()`（内部改为优先使用 LGB）
- 记录 LGB 训练日志

#### 2.2 修改 `_real_alpha_evaluation()` (L266-316)
- **优先路径**: 若 `self._lgb_models` 非空，使用 LGB 模型的 CV IC 构建 evaluations
  ```python
  for symbol, model_info in self._lgb_models.items():
      cv_metrics = model_info["cv_after_selection"]
      ic = cv_metrics["mean_ic"]
      ic_ir = ic / (cv_metrics["std_ic"] + 1e-9)
      evaluations.append({
          "factor_name": f"lgb_{symbol}",
          "ic_1d": float(ic),
          "ic_ir": float(ic_ir),
          "category": "real",
          "symbol": symbol,  # 新增: 供 _build_alpha_signals 正确映射
      })
  ```
- **降级路径**: LGB 不可用时回退到原 60 日动量 IC 计算
- **category**: LGB 成功时 `"real"`，否则原逻辑

---

### 变更 3: 修改信号生成

**文件**: `institutional_pipeline_runner.py`

#### 3.1 修改 `_real_alpha_signals()` (L561-676)
- **LGB 信号（80%权重）**: 调用 `_lgb_get_signal(symbol)` 获取信号
- **动量信号（20%权重）**: 保留现有动量计算逻辑
- **融合公式**:
  ```python
  lgb_strength, lgb_conf = self._lgb_get_signal(symbol)
  mom_strength, mom_conf = momentum_signal  # 现有逻辑
  final_strength = 0.8 * lgb_strength + 0.2 * mom_strength
  final_conf = min(1.0, 0.8 * lgb_conf + 0.2 * mom_conf)
  ```
- **降级**: LGB 信号为 0 时，自动使用 100% 动量信号

#### 3.2 修复 `_build_alpha_signals()` (L535-550)
- **当前 BUG**: 所有 evaluations 的信号被应用到所有 symbols（最后一个 evaluation 覆盖全部）
- **修复**: 使用 evaluation 中的 `symbol` 字段正确映射
  ```python
  for ev in evaluations:
      symbol = ev.get("symbol", "")
      if symbol and symbol in self.ctx.symbols:
          signals[symbol] = {"strength": strength, "confidence": confidence}
  ```
- **兼容**: 对旧的 `mom60_{symbol}` 格式，从 factor_name 提取 symbol

---

### 变更 4: 预加载历史数据补充

**文件**: `institutional_pipeline_runner.py`

#### 4.1 修改 `_preload_historical_data()` (L443-476)
- 新增预加载跨市场代理标的: `518880, 600036, 588000, 515180`
- 确保全部 POSITION_SYMBOLS 都被预加载（即使不在 `self.ctx.symbols` 中）
- 导入 `POSITION_SYMBOLS` from `lgb_enhanced_trainer.py`

#### 4.2 修改 `__init__()` (L100-131)
- 新增 `self._lgb_models: Dict[str, Dict] = {}` 初始化
- backtest 模式下导入 LGB 训练相关依赖

---

### 变更 5: 导入与依赖

**文件**: `institutional_pipeline_runner.py` 顶部

新增导入:
```python
# LGB Walk-forward 集成
from lgb_enhanced_trainer import (
    LGB_ENHANCED_CONFIG,
    POSITION_SYMBOLS,
    train_symbol_enhanced,
    add_technical_features,
    add_cross_sectional_features,
    add_industry_relative_strength_features,
    add_capital_flow_features,
    add_cross_market_features,
    add_sentiment_features,
)
```

注意: `add_technical_features` 和 `add_cross_sectional_features` 实际从 `autolearn_trainer` 导入，`lgb_enhanced_trainer` 已 re-export。

---

### 变更 6: Walk-forward 训练配置

**文件**: `institutional_pipeline_runner.py`

新增常量:
```python
# Walk-forward 训练配置（加速版：月度重训无需 2000 轮）
WALKFORWARD_LGB_CONFIG = {
    **LGB_ENHANCED_CONFIG,
    "lgb_params": {
        **LGB_ENHANCED_CONFIG["lgb_params"],
        "n_estimators": 1000,        # 2000 → 1000（加速）
    },
    "early_stopping_rounds": 100,     # 200 → 100（加速）
    "news_lookback_days": 0,          # 跳过新闻
    "adaptive_retrain_threshold": 5,
    "adaptive_retrain_lr": 0.001,
    "adaptive_retrain_n_estimators": 2000,
}
```

---

## 实施步骤（执行顺序）

### Step 1: 添加导入和初始化
- 在 `institutional_pipeline_runner.py` 顶部添加 LGB 相关导入
- 在 `__init__` 中添加 `self._lgb_models = {}`
- 添加 `WALKFORWARD_LGB_CONFIG` 常量
- 添加 `_CROSS_MARKET_PROXY_SYMBOLS` 常量

### Step 2: 实现特征构建方法
- 实现 `_build_lgb_feature_dict()` 方法
- 确保 `_historical_cache` 包含全部所需标的

### Step 3: 实现 Walk-forward 训练方法
- 实现 `_lgb_walkforward_train()` 方法
- 复用 `train_symbol_enhanced()` 进行训练
- 缓存结果到 `self._lgb_models`

### Step 4: 实现信号获取方法
- 实现 `_lgb_get_signal(symbol)` 方法
- 处理模型缺失的降级逻辑

### Step 5: 修改 Alpha 评估
- 修改 `_step_alpha_evaluation()` 调用 LGB 训练
- 修改 `_real_alpha_evaluation()` 使用 LGB CV IC

### Step 6: 修改信号生成
- 修改 `_real_alpha_signals()` 融合 LGB(80%) + 动量(20%)
- 修复 `_build_alpha_signals()` 的 symbol 映射 BUG

### Step 7: 修改预加载
- 修改 `_preload_historical_data()` 添加跨市场代理标的
- 确保全部 POSITION_SYMBOLS 被预加载

### Step 8: 验证
- 运行 CI 前视偏差守卫: `python ci_lookahead_guard.py`
- 运行单月 smoke 测试: `python institutional_pipeline_runner.py --mode backtest --symbols 688041 600519`
- 运行完整回测: `python -c "from research.backtest_runner import run_backtest; run_backtest(symbols=['688041','600519','600036','000858','600276','000063','601318','000001','600036','601398'], start='2024-01-01', end='2025-12-31', resume=False)"`
- 检查年化收益是否达到 3-6% 目标
- 验证回测完整性守卫通过

---

## 假设与决策

### 假设
1. **特征工程函数可复用**: `lgb_enhanced_trainer.py` 中的特征工程函数（`add_technical_features` 等）可在 pipeline 中直接调用
2. **训练数据充足**: 每个回测月份有足够的历史数据（≥150 日）进行训练
3. **训练时间可接受**: 月度重训 23 标的约需 2-4 分钟，24 个月总计 48-96 分钟
4. **跨市场代理标的可获取**: 518880/600036/588000/515180 的历史数据可通过 `data_provider` 获取

### 风险与缓解
| 风险 | 缓解措施 |
|------|----------|
| 早期月份训练数据不足（2024-01 仅 3 个月数据） | `min_samples=150` 会跳过，降级为动量信号 |
| 训练时间过长 | 使用 `WALKFORWARD_LGB_CONFIG`（1000 轮 + 早停 100） |
| 跨市场代理数据缺失 | 特征工程已处理缺失（填 0） |
| LGB 模型训练失败 | 降级为 100% 动量信号，记录警告 |
| `train_symbol_enhanced` 依赖 `lightgbm` 未安装 | 导入时 try-except，降级为动量 |

### 不做的事
- ❌ 不修改 `lgb_enhanced_trainer.py`（保持训练器不变）
- ❌ 不保存 walk-forward 模型到磁盘（避免污染生产模型）
- ❌ 不添加新闻情绪因子（历史数据不可回溯）
- ❌ 不修改 `backtest_runner.py` 的日期遍历逻辑（保持月度频率）
- ❌ 不修改 `ci_lookahead_guard.py`（LGB 训练在 pipeline 内，无新代码模式违规）

---

## 验证步骤

### 1. 前视偏差守卫
```bash
python ci_lookahead_guard.py
```
预期: 0 violations（无 bfill/shift(-N)/train_test_split/全样本标准化）

### 2. 单月 Smoke 测试
```bash
python institutional_pipeline_runner.py --mode backtest --symbols 688041 600519 --capital 3000000
```
预期:
- LGB 模型训练成功（日志可见 `[LGB-WF] trained 2/2`）
- Alpha provenance = "real"
- 信号强度非零

### 3. 完整回测
```python
from research.backtest_runner import run_backtest
result = run_backtest(
    symbols=['688041','600519','600036','000858','600276',
             '000063','601318','000001','601398','600900'],
    start='2024-01-01',
    end='2025-12-31',
    resume=False
)
```
预期:
- 年化收益 3-6%（目标）
- 最大回撤 ≤ 15%（维持）
- 回测完整性守卫通过

### 4. 回测完整性校验
检查 `pipeline_backtest.json` 中:
- `integrity.valid = true`
- `integrity.alpha_provenance = "real"`
- `integrity.issues = []`

### 5. Walk-forward 稳定性
检查 `validation_report_*.json` 中:
- `walk_forward_stability.is_stable` 改善（滚动夏普波动率降低）
- `prob_sharpe_positive` > 0.65

---

## V9 Regime-Specific 双模型 — 最终验收 (2026-07-25)

### 演进路径
- V6.2 (单一 LGB): 年化 14.35%, 回撤 7.90%, Sharpe 1.092, Sharpe CV 0.55 — 基线
- V7.1 (bull regime 高波动惩罚): Sharpe CV 0.55 → 0.74 — 失败 (波动率悖论)
- V7.2 (bull regime max_weight 10%→5%): Sharpe CV 0.71, DSR max_pass=5 — 权重后处理最优解
- V8 (动态权重融合 blend/exposure): 年化 14%→9%, 峰度 4→10+ — 失败
- V9 (Regime-Specific 双模型): 年化 19.62%, 回撤 9.95%, Sharpe CV 0.88, DSR max_pass=8 — **生产基线**

### V9 架构
- 510300 ETF 作为大盘代理, MA60 + 5 日斜率判断 regime (bull/bear/choppy/rebound)
- 为每个标的训练 bull / non-bull 两个独立 LGB 模型, 预测时按当前 regime 选择对应模型
- 每个 regime 子集最少 100 样本, 不足则降级为全样本模型 (fallback)
- 代码位置: [lgb_enhanced_trainer.py](file:///e:/各种PY程序/28-终极量化交易系统8.4/lgb_enhanced_trainer.py) `compute_regime_series()` + `train_symbol_regime_specific()`
- 集成位置: [institutional_pipeline_runner.py](file:///e:/各种PY程序/28-终极量化交易系统8.4/institutional_pipeline_runner.py) `_V9_REGIME_SPECIFIC_ENABLED=True`, `_lgb_walkforward_train()`

### 关键 Bug 修复
1. `_train_regime_subset` 使用 `config["min_samples"]=150` 作为阈值, 但 bull regime 子集通常只有 100-180 样本 → 添加 `min_samples_per_regime` 参数, 使用它而非 `config["min_samples"]` → bull 模型成功率 0% → 92.6%
2. 600276 LightGBM access violation 崩溃 → n_jobs=1 单线程 + 重试机制 (2 次, sleep+gc) + 训练后 `del df; gc.collect()` → 5/5 cutoff 全部成功

### 用户确认的综合评估标准 (2026-07-25)
Sharpe CV <0.5 目标对跨 regime 策略过于激进, 用户正式确认调整:
- **Sharpe CV 目标**: <0.5 → <1.0
- **综合评估标准**: DSR max_pass ≥ 5 + 年化 ≥ 15% + 最大回撤 ≤ 10% + Sharpe CV < 1.0

### V9 验收结果
| 指标 | 阈值 | V9 实测 | 结果 |
|------|------|---------|------|
| Sharpe CV | < 1.0 | 0.88 | PASS |
| DSR max_pass | ≥ 5 | 8 | PASS |
| 年化收益 | ≥ 15% | 19.62% | PASS |
| 最大回撤 | ≤ 10% | 9.95% | PASS |
| 胜率 | ≥ 60% | 66.67% | PASS |

V9 在新标准下全部达标, 正式定为当前生产基线。

### 兜底阈值说明
生产代码 `research/backtest_runner.py` 的 `MIN_ANNUAL_RETURN=0.08` / `MAX_DRAWDOWN_LIMIT=0.15` 是与 README 对齐的系统级最低要求, 保留不变。V9 验收标准 (15%/10%/DSR≥5/Sharpe CV<1.0) 作为后续策略版本准入门槛, 不影响现有兜底逻辑。

### 结果文件
- 回测: `output/validation_reports/v9_regime_specific_backtest_20260725_114943.json`
- DSR: `output/validation_reports/v9_dsr_maxpass_20260725.json`

### 归档方案
- V6.2/V7.1/V7.2/V8 的临时脚本 (`_run_lgb_backtest_*.py`, `_run_dsr_walkforward_*.py`, `_run_v8_dynamic_fusion.py`, `_run_v72_recompute.py` 等) 保留为历史实验记录, 不再维护
- V9 代码集成在生产路径 (`lgb_enhanced_trainer.py` + `institutional_pipeline_runner.py`), 通过 `_V9_REGIME_SPECIFIC_ENABLED` 开关控制, 可回退至 V6.2 单模型
- 后续优化方向 (若需要): 扩展标的至 ≥100 (解决 23 标本下 IC_IR 统计不可达问题), 接入真实 fundamentals (替代 price proxy), 回填 510300 ETF 真实数据
