# 深度 P0-A 修复 — 因子流水线真实接入生产（影子账户层）+ Phase 10 target_weights bug 修复

## Context

**审计背景**：2026-07-26 顶级对冲基金审计发现 v8.6.3 因子流水线"方法学闭环"是文字游戏 — `PipelineResult.factor_combinations` 字段在研究目录内部生成，但全项目无任何生产代码消费它。审计 P0-A 已通过 README 措辞修订标记为"已修复"，但深度接入生产的工作未完成。

**新发现的隐藏 P0 bug**：在制定接入方案时发现 Phase 10 `phase_shadow_monitor()` 读取 `signal_phase.get("target_weights", {})`，但 `phase_signal()` 从未设置 `target_weights` 字段（只设置 `morning_orders` / `afternoon_orders`）。这导致：
- 影子账户 NAV 恒为 1.0（`daily_return=0.0`）
- fail-fast 触发器（3%/5%）**永远无法触发** — 影子账户"运行"实际是空转
- 14 天最小运行周期通过后，会基于"零数据"推进到 Stage 2，构成 P0 级风险

**本次修复目标**：
1. 真实接入生产（影子账户层）— `factor_combinations` 真实影响影子账户决策（不影响 500万 实盘）
2. 修复 Phase 10 target_weights bug — 使影子账户能真实跟踪组合收益
3. 创建缺失文件 `utils/portfolio_optimizer.py`（审计 P1-B）
4. 更新文档标记 P0-A 深度接入完成

## 关键设计决策

### 决策 1：接入影子账户而非实盘（避免实盘风险）
- ✅ 不影响 500万 实盘（仍用 trade_plan）
- ✅ 影子账户 ¥500,000 作为 OOS 验证场地
- ✅ 14 天 Stage 1 周期自然验证因子组合真实表现
- ✅ 符合审计建议："生产接入前需先在影子账户中验证因子组合的 OOS 表现"

### 决策 2：离线计算 + 在线应用模式（性能隔离）
PipelineOrchestrator.run() 耗时数分钟，不能在 daily_workflow 同步运行。采用与 LGB 增强信号一致的模式：
- 离线脚本（06:00 触发）：调用 PipelineOrchestrator → 保存 JSON
- 在线 daily_workflow（07:00 触发）：加载 JSON → 应用调整

### 决策 3：保守权重 0.05（避免因子信号过度冲击）
新因子源 `pipeline_factor_weight=0.05`（vs alpha=0.70 / llm=0.10 / etf=0.12 / macro=0.08）。从 0.05 起步，影子账户 OOS 验证通过后可逐步上调。

## 实施方案

### Step 1：修复 phase_signal() target_weights 计算 bug（P0 关键 bug）

**文件**：[v8.3_institutional/daily_workflow.py](file:///e:/各种PY程序/28-终极量化交易系统8.4/v8.3_institutional/daily_workflow.py) `phase_signal()` 方法（L3566-3800）

**修改点**：在 `signal` 字典构建后（约 L3683-3699），新增 `target_weights` 计算逻辑：

```python
# === 计算目标权重（供 Phase 10 影子账户使用）===
target_weights = {}
grand_total = float(signal.get("grand_amount", 0)) or 1.0
for order in adjusted_morning + adjusted_afternoon:
    code = str(order.get("code", ""))
    if not code:
        continue
    amount = float(order.get("est_amount", 0))
    side = order.get("side", "buy").lower()
    sign = +1.0 if side in ("buy", "build", "open_long") else -1.0
    target_weights[code] = target_weights.get(code, 0.0) + sign * (amount / grand_total)
signal["target_weights"] = target_weights
self.state["phases"]["signal"]["target_weights"] = target_weights
logger.info(f"目标权重计算: {len(target_weights)} 个标的, 总暴露={sum(abs(w) for w in target_weights.values()):.4f}")
```

**验收**：Phase 10 能读到非空 target_weights，daily_return 不再恒为 0。

### Step 2：创建 utils/portfolio_optimizer.py（P1-B + P0-A 关键组件）

**文件**：`utils/portfolio_optimizer.py`（新建）

```python
class PortfolioOptimizer:
    """组合优化器 — 消费 PipelineResult.factor_combinations 调整目标权重
    
    职责:
    - 加载离线生成的因子信号 JSON
    - 用因子信号调整目标权重（保守权重 0.05）
    - 提供 run_offline_pipeline() 离线触发接口
    """
    
    def __init__(self, signals_dir: str = "models/pipeline_factor_signals"): ...
    def load_factor_signals(self, trade_date: str) -> Dict[str, float]: ...
    def adjust_target_weights(self, base_weights: Dict[str, float], 
                              factor_signals: Dict[str, float],
                              alpha: float = 0.05) -> Dict[str, float]: ...
    def run_offline_pipeline(self, price_data, fundamentals, ...) -> bool: ...
```

**关键方法**：
- `load_factor_signals(date)`：读取 `models/pipeline_factor_signals/pipeline_factor_signals_{date}.json`，新鲜度检查
- `adjust_target_weights(base, signals, alpha=0.05)`：`adjusted = base * (1 - alpha) + signal * alpha`，归一化后返回
- `run_offline_pipeline(...)`：薄包装器，调用 PipelineOrchestrator.run()，提取 `factor_combinations[0]`，转换为 `{symbol: signal}` 保存

### Step 3：创建 scripts/run_pipeline_factor_offline.py（P0-A 离线脚本）

**文件**：`scripts/run_pipeline_factor_offline.py`（新建）

**职责**：
- 加载真实 price_data + fundamentals + fundamentals_history（复用 `research/vibe_trading_factor_analysis/scripts/real_data_loader.py`）
- 调用 `PortfolioOptimizer.run_offline_pipeline()`
- 输出 JSON 到 `models/pipeline_factor_signals/pipeline_factor_signals_{YYYY-MM-DD}.json`
- JSON 格式参考 `models/lgb_enhanced/lgb_enhanced_signals.json`：

```json
{
  "trade_date": "2026-07-27",
  "generated_at": "2026-07-27T06:00:00",
  "factor_combination": {
    "factor_a": "VT_MICRO_VOL_SKEW_INV",
    "factor_b": "VT_QUALTREND_MARGIN_EXP",
    "method": "ic_weighted",
    "lookback": 10,
    "combined_ic_ir": 0.5840,
    "live_dsr": 2.2033
  },
  "signals": {
    "588000": {"signal": 0.32, "name": "中芯国际"},
    "300308": {"signal": -0.15, "name": "中际旭创"}
  }
}
```

### Step 4：修改 utils/signal_fusion.py（P0-A 集成）

**文件**：[utils/signal_fusion.py](file:///e:/各种PY程序/28-终极量化交易系统8.4/utils/signal_fusion.py)

**修改点**：
1. `__init__` 新增参数 `pipeline_factor_weight: float = 0.05`
2. 新增方法 `inject_pipeline_factor_signals(signals: Dict[str, float])`（参考 `inject_qlib_signal` 模式）
3. `_fuse_symbol()` 将 pipeline_factor 信号作为第 5 个信号源
4. `_dynamic_weights()` 增加 pipeline_factor 源的 IC 计算

### Step 5：修改 daily_workflow.py phase_signal() 集成 PortfolioOptimizer

**文件**：[v8.3_institutional/daily_workflow.py](file:///e:/各种PY程序/28-终极量化交易系统8.4/v8.3_institutional/daily_workflow.py) `phase_signal()` 末尾

**新增逻辑**（在 Step 1 的 target_weights 计算之后）：
```python
# === 加载 Pipeline 因子信号（v8.6.4 深度 P0-A 修复） ===
pipeline_signals = {}
try:
    from utils.portfolio_optimizer import PortfolioOptimizer
    opt = PortfolioOptimizer()
    pipeline_signals = opt.load_factor_signals(self.trade_date)
    if pipeline_signals:
        # 用因子信号调整目标权重
        target_weights = opt.adjust_target_weights(target_weights, pipeline_signals, alpha=0.05)
        signal["target_weights"] = target_weights
        signal["pipeline_factor_count"] = len(pipeline_signals)
        signal["pipeline_factor_applied"] = True
        logger.info(f"Pipeline 因子信号加载: {len(pipeline_signals)} 个标的, 已调整目标权重")
    else:
        signal["pipeline_factor_applied"] = False
        logger.info("Pipeline 因子信号未加载（可能未生成或非今日）")
except Exception as e:
    logger.warning(f"Pipeline 因子信号加载失败（不影响主流程）: {e}")
    signal["pipeline_factor_applied"] = False
```

**关键**：失败不阻断主流程，与 LGB 信号加载一致的安全降级模式。

### Step 6：注册新 Windows 任务 QuantPipelineFactor_06AM

**修改文件**：`v8.3_institutional/setup_scheduled_tasks.bat`

**新增任务**：每日 06:00 运行 `python scripts/run_pipeline_factor_offline.py`，确保 07:00 daily_workflow 触发前生成最新因子信号。

```batch
schtasks /create /tn "QuantPipelineFactor_06AM" /tr "py -3 scripts\run_pipeline_factor_offline.py" /sc weekly /d MON,TUE,WED,THU,FRI /st 06:00 /f
```

直接以管理员权限执行 `schtasks /create` 注册（与 P0-B 修复模式一致）。

### Step 7：更新文档

**修改文件**：
1. `README.md` v8.6.3 章节 — 移除「🔴 待办（P0）」标记，改为「✅ 已接入生产（影子账户层）」，新增 v8.6.4 章节记录深度修复
2. `research/vibe_trading_factor_analysis/README.md` 6.3 节 — 移除审计警示框，改为「✅ 已接入生产（影子账户层）」
3. `docs/AUDIT_FIX_CHANGELOG_2026-07-26.md` — 追加深度 P0-A 修复记录
4. `docs/HEDGE_FUND_AUDIT_2026-07-26_SYSTEM_BUGS_AND_AUTOMATION.md` — 追加隐藏 P0 bug（target_weights）

## 关键文件清单

| 文件 | 操作 | 行数估计 | 优先级 |
|------|------|---------|--------|
| `v8.3_institutional/daily_workflow.py` | 修改 phase_signal() + 末尾集成 PortfolioOptimizer | +40 行 | P0 |
| `utils/portfolio_optimizer.py` | 新建 | ~200 行 | P0 |
| `scripts/run_pipeline_factor_offline.py` | 新建 | ~150 行 | P0 |
| `utils/signal_fusion.py` | 新增 inject_pipeline_factor_signals 方法 + __init__ 参数 | +50 行 | P0 |
| `v8.3_institutional/setup_scheduled_tasks.bat` | 新增 QuantPipelineFactor_06AM 任务 | +3 行 | P1 |
| `README.md` | 移除待办标记，新增 v8.6.4 章节 | +20 行 | P1 |
| `research/vibe_trading_factor_analysis/README.md` | 移除审计警示框 | -10 行 | P1 |
| `docs/AUDIT_FIX_CHANGELOG_2026-07-26.md` | 追加深度修复记录 | +50 行 | P1 |

## 复用的现有代码

- **PipelineOrchestrator API**：[research/vibe_trading_factor_analysis/pipeline/pipeline_orchestrator.py:312](file:///e:/各种PY程序/28-终极量化交易系统8.4/research/vibe_trading_factor_analysis/pipeline/pipeline_orchestrator.py#L312) `run()` 方法
- **real_data_loader**：`research/vibe_trading_factor_analysis/scripts/real_data_loader.py`（复用其 price_data + fundamentals 加载逻辑）
- **LGB 信号加载模式**：[v8.3_institutional/daily_workflow.py:4373](file:///e:/各种PY程序/28-终极量化交易系统8.4/v8.3_institutional/daily_workflow.py#L4373) `_load_lgb_enhanced_signals()` — 复用其 JSON 格式 + 新鲜度检查 + 安全降级模式
- **SignalFusionEngine.inject_qlib_signal**：[utils/signal_fusion.py:142](file:///e:/各种PY程序/28-终极量化交易系统8.4/utils/signal_fusion.py#L142) — 复用其注入模式实现 `inject_pipeline_factor_signals`
- **MarketDataProvider**：`utils/data_provider.py`（Phase 10 已使用）

## 风险控制

| 风险 | 缓解措施 |
|------|---------|
| 因子组合 OOS 表现差导致影子账户亏损 | 保守权重 0.05，影响有限；fail-fast 3%/5% 真实可触发 |
| PipelineOrchestrator 离线脚本失败 | daily_workflow 检测到无信号时降级为原始权重，不阻断主流程 |
| 影响实盘交易 | 完全隔离 — 仅影响 Phase 10 影子账户，Phase 5 实盘订单不变 |
| Windows 任务执行失败 | 与现有 QuantWorkflow_07AM 一致的失败处理（重试 + fallback） |

## 验收标准

1. **离线脚本可用**：`python scripts/run_pipeline_factor_offline.py` 成功生成 `models/pipeline_factor_signals/pipeline_factor_signals_{date}.json`
2. **portfolio_optimizer 可导入**：`from utils.portfolio_optimizer import PortfolioOptimizer` 成功
3. **signal_fusion 集成**：`SignalFusionEngine().inject_pipeline_factor_signals({...})` 不抛异常
4. **daily_workflow 集成**：`python v8.3_institutional/daily_workflow.py --phase signal` 输出 `Pipeline 因子信号加载: N 个标的`
5. **Phase 10 bug 修复**：`shadow_state.json` 中 `daily_nav` 数组新增记录的 `daily_return` 不再恒为 0
6. **Windows 任务注册**：`schtasks /query /tn "QuantPipelineFactor_06AM"` 返回 Ready
7. **README 更新**：v8.6.3 章节移除「🔴 待办（P0）」，新增 v8.6.4 章节

## 验证步骤

### 步骤 1：单元验证
```powershell
# 验证导入
python -c "from utils.portfolio_optimizer import PortfolioOptimizer; opt = PortfolioOptimizer(); print('OK')"
python -c "from utils.signal_fusion import SignalFusionEngine; e = SignalFusionEngine(); e.inject_pipeline_factor_signals({'588000': 0.3}); print('OK')"
```

### 步骤 2：离线脚本验证
```powershell
python scripts/run_pipeline_factor_offline.py
# 检查输出
type models\pipeline_factor_signals\pipeline_factor_signals_2026-07-27.json
```

### 步骤 3：daily_workflow 集成验证
```powershell
# 单独运行 Phase 5（需准备 trade_plan）
python v8.3_institutional\daily_workflow.py --phase signal
# 检查 state 中 target_weights 非空
python -c "import json; s = json.load(open('output/daily_workflow_state.json')); print(s['phases']['signal'].get('target_weights', {}))"
```

### 步骤 4：Windows 任务注册
```powershell
schtasks /query /tn "QuantPipelineFactor_06AM"
```

### 步骤 5：影子账户验证（2026-07-27 07:00 后）
```powershell
# 等待 QuantWorkflow_07AM 触发后
type output\shadow_account\shadow_state.json | findstr daily_return
# 应看到非零 daily_return
```

## 不修复的项

- **P2-A 持续运行**：仍需等待 14 天自然周期，无法人工加速
- **GROWTH_ACCEL 重新设计**：需新数据源（分析师预期/研报情感/业绩预告），不在本次修复范围
- **v8.6.3 接入 500万 实盘**：明确不做，需影子账户 14 天 OOS 验证通过后才能考虑
