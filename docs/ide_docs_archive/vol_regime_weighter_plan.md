# 基于波动率 Regime 动态调整权重的自我进化模块

## Context

用户对 5 年量化交易系统的评级为 2.2/5（中性偏谨慎），主要问题是「配置比例与主轴错配」「执行严重偏离计划」「完全缺乏量化回测」。当前系统已有 `VolTargetController` 仅缩放建仓预算、`dynamic_hedge_policy` 四档仅用于期货对冲，**现货权重完全静态**——这是「配置与主轴错配」的技术根因。

本模块在自我进化框架内新增「按波动率 Regime（bull/neutral/bear/crisis 四档）动态调整 8 类风格权重」的能力，Phase 0（至 2026-08-20）只读建议模式，输出量化报告为 ②校正配置 / ④对齐对冲 / ⑤补量化测算 三项改进提供数据支撑。

## 设计决策（用户已确认）

| 决策点 | 选择 |
|---|---|
| 调整粒度 | 按「风格大类」8 类（科技/新能源/医药/金融/宽基/资源/防御/现金） |
| 运行模式 | Phase 0 只读建议（输出到 `reports/evolution/`，不修改 portfolio.yaml） |
| Regime 分档 | 四档对齐 `portfolio.yaml` 的 `dynamic_hedge_policy`（VIX<20/20-30/30-40/>40） |
| Feature Flag | `USE_VOL_REGIME_WEIGHTER`，默认 False |

## 实现步骤

### 步骤 1: 新建核心模块 `utils/alpha/vol_regime_weighter.py`

**关键类与数据结构**：

```python
@dataclass
class VolRegime:
    label: str            # "bull"/"neutral"/"bear"/"crisis"
    confidence: float     # 0.0-1.0
    source: str           # "vix"/"realized_vol"/"fallback"
    indicators: dict      # {"vix": 25.3, "realized_vol": 0.18, ...}
    aligned_hedge_ratio: float  # 对齐 dynamic_hedge_policy 的对应档位

@dataclass
class WeightSuggestion:
    timestamp: str
    regime: VolRegime
    current_weights: dict[str, float]   # style → weight
    suggested_weights: dict[str, float] # style → weight (经约束调整)
    multipliers: dict[str, float]       # style → multiplier
    deltas: dict[str, float]            # style → delta
    constraints_applied: list[str]
    confidence: float
    trigger_reason: str
    observation_phase: bool = True

class VolRegimeWeighter:
    # 4×8 调整矩阵常量（配置可覆盖）
    DEFAULT_WEIGHT_MATRIX = {
        "bull":     {"科技": 1.20, "新能源": 1.15, "医药": 1.10, "金融": 1.05, "宽基": 1.05, "资源": 1.00, "防御": 0.90, "现金": 0.50},
        "neutral":  {"科技": 1.00, "新能源": 1.00, "医药": 1.00, "金融": 1.00, "宽基": 1.00, "资源": 1.00, "防御": 1.00, "现金": 1.00},
        "bear":     {"科技": 0.60, "新能源": 0.65, "医药": 0.80, "金融": 0.90, "宽基": 0.95, "资源": 1.10, "防御": 1.30, "现金": 2.00},
        "crisis":   {"科技": 0.30, "新能源": 0.35, "医药": 0.60, "金融": 0.70, "宽基": 0.85, "资源": 1.20, "防御": 1.50, "现金": 3.00},
    }

    def __init__(self, feature_flag_name="USE_VOL_REGIME_WEIGHTER", vol_controller=None, reports_dir=None): ...
    def sense_regime(self, vix_value=None, daily_returns=None, psi_value=None, current_drawdown=None) -> VolRegime: ...
    def compute_weights(self, current_weights, regime=None, vix_value=None, daily_returns=None) -> WeightSuggestion: ...
    def enforce_constraints(self, suggested_weights, max_single_position=0.08, max_sector_exposure=0.30, cash_floor=0.05) -> tuple[dict, list]: ...
    def emit_suggestion(self, suggestion, reports_dir=None) -> Path: ...
    def run_cycle(self, portfolio_snapshot, vix_value=None, daily_returns=None, orchestrator=None) -> dict: ...
```

**核心算法**：

1. **Regime 识别**（`sense_regime`）：
   - 主指标：VIX（<20=bull, 20-30=neutral, 30-40=bear, >40=crisis）
   - 备选：`realized_vol = VolTargetController.calc_realized_vol(daily_returns)`（复用，不重写）
   - 一致性修正：VIX 与 RV 分类不一致时取更保守档，confidence 降至 0.5
   - 辅助修正：PSI>0.25 减 0.2 confidence；drawdown>0.05 floor 到 neutral；drawdown>0.12 floor 到 bear

2. **权重计算**（`compute_weights`）：
   - 按 regime 查表得 8 类倍数
   - `suggested = current × multiplier`
   - 调用 `enforce_constraints` 强制约束：单标的≤8%、单一风格≤30%、现金≥5%、总和=1.0（差额归现金）

3. **输出**（`emit_suggestion`）：
   - 写入 `reports/evolution/vol_regime_weights_YYYY-MM-DD.json`
   - 若提供 `orchestrator`，调用 `orchestrator.log_decision(action="evaluate_only", reason=..., extra_payload=suggestion.to_dict())`

### 步骤 2: 新建配置文件 `configs/vol_regime_weighter.yaml`

```yaml
# 阈值（可覆盖默认值）
regime_thresholds:
  vix: {bull: 20, neutral: 30, bear: 40}
  realized_vol: {bull: 0.15, neutral: 0.25, bear: 0.40}

# 4×8 矩阵覆写（留空则用 DEFAULT_WEIGHT_MATRIX）
weight_matrix: {}

# 约束（对齐 portfolio.yaml risk_parameters）
constraints:
  max_single_position: 0.08
  max_sector_exposure: 0.30
  cash_floor: 0.05
  min_weight: 0.01

# 降级策略
degradation:
  vix_missing_fallback: realized_vol
  both_missing: neutral  # 默认中性，confidence=0.3
```

### 步骤 3: 注册 Feature Flag

在 `configs/feature_flags.yaml` 的「自我进化框架」分组末尾追加：

```yaml
USE_VOL_REGIME_WEIGHTER:
  default: false
  description: "启用波动率 Regime 权重建议器 (按 bull/neutral/bear/crisis 四档动态调整 8 类风格权重, Phase 0 只读建议模式)"
  requires: "dual_signature"
  rollback_seconds: 30
```

### 步骤 4: 最小侵入式集成到 `EvolutionOrchestrator`

**修改 `utils/alpha/evolution_orchestrator.py`**：

1. `log_decision()` 新增可选参数 `extra_payload: dict[str, Any] | None = None`：
   - 若提供，`record.evaluator_report.update(extra_payload)`
   - 现有调用方不传该参数，零影响（向后兼容）

2. `run_observation_cycle()` 末尾新增 vol_regime 分支：
   ```python
   # 现有 collect → evaluate → log 流程不变（第 549-569 行）
   
   # 新增：vol_regime 权重建议（flag 控制可选）
   if is_enabled("USE_VOL_REGIME_WEIGHTER"):
       try:
           from utils.alpha.vol_regime_weighter import VolRegimeWeighter
           weighter = VolRegimeWeighter()
           portfolio_snapshot = self._read_portfolio_snapshot()  # 只读 portfolio.yaml
           vix = self._fetch_vix()  # 复用 DataLayer 降级链
           weighter.run_cycle(
               portfolio_snapshot=portfolio_snapshot,
               vix_value=vix,
               daily_returns=metrics.daily_returns if metrics else None,
               orchestrator=self,
           )
       except Exception as e:
           logger.warning("VolRegimeWeighter 失败, 不阻塞主流程: %s", e)
   ```

3. 新增两个私有 helper：`_read_portfolio_snapshot()`（只读 portfolio.yaml 的 assets 列表）、`_fetch_vix()`（通过 Wind MCP → AKShare → 缓存降级链）

### 步骤 5: 单元测试 `tests/unit/test_vol_regime_weighter.py`

按 `tests/unit/test_macro_indicator.py` 风格组织，覆盖：

- **常量测试**：矩阵 4×8 完整性、bull 科技×1.20、crisis 现金×3.00
- **Regime 分类**：VIX 四档边界、RV 备选、VIX 与 RV 不一致取保守、PSI/drawdown 修正
- **权重计算**：bull 加仓科技、crisis 减仓科技、现金吸收差额
- **约束执行**：max_single_position 裁剪、max_sector_exposure 裁剪、cash_floor 抬升、sum_to_one
- **主类流程**：flag=False 返回 noop、Phase 0 不修改 portfolio.yaml、orchestrator 集成调用 log_decision
- **降级路径**：VIX 缺失→RV、VIX+RV 都缺失→neutral+低 confidence

### 步骤 6: 端到端集成测试 `tests/integration/test_vol_regime_phase0_e2e.py`

3 个关键场景：
1. `test_phase0_e2e_vix_bear_regime`：VIX=32.5 → bear → 生成报告 + 写 decisions.jsonl + portfolio.yaml 未变
2. `test_phase0_e2e_vix_missing_fallback_to_rv`：VIX 缺失，RV=0.32 → bear
3. `test_phase0_e2e_flag_disabled_noop`：Flag=False 零影响

## 复用清单（不重写）

| 复用组件 | 文件路径 | 复用方式 |
|---|---|---|
| `VolTargetController.calc_realized_vol()` | [utils/vol_target_controller.py](file:///e:/各种PY程序/28-终极量化交易系统8.4/utils/vol_target_controller.py#L80-L114) | 依赖注入，计算已实现波动率 |
| `EvolutionOrchestrator.log_decision()` | [utils/alpha/evolution_orchestrator.py](file:///e:/各种PY程序/28-终极量化交易系统8.4/utils/alpha/evolution_orchestrator.py#L451-L524) | 新增 `extra_payload` 参数，复用审计链 |
| `is_enabled()` | [utils/infra/feature_flags.py](file:///e:/各种PY程序/28-终极量化交易系统8.4/utils/infra/feature_flags.py) | Flag 检查（HC-1 透传） |
| `portfolio.yaml` 的 `dynamic_hedge_policy` | [configs/portfolio.yaml](file:///e:/各种PY程序/28-终极量化交易系统8.4/configs/portfolio.yaml#L142-L161) | 只读对齐四档 hedge_ratio |
| `risk_parameters` 约束 | [configs/portfolio.yaml](file:///e:/各种PY程序/28-终极量化交易系统8.4/configs/portfolio.yaml#L311-L327) | max_single_position=0.08, max_sector_exposure=0.30 |

## 输出示例

`reports/evolution/vol_regime_weights_2026-08-05.json`（bear regime 示例）：

```json
{
  "regime": {"label": "bear", "confidence": 0.82, "source": "vix", "aligned_hedge_ratio": 0.75},
  "current_weights": {"科技": 0.235, "新能源": 0.09, "医药": 0.15, "现金": 0.05, ...},
  "suggested_weights": {"科技": 0.141, "新能源": 0.0585, "医药": 0.120, "现金": 0.3005, ...},
  "multipliers": {"科技": 0.60, "新能源": 0.65, "现金": 2.00, ...},
  "deltas": {"科技": -0.094, "现金": 0.2505, ...},
  "constraints_applied": ["max_sector_exposure: 科技 0.141 < 0.30 ✓", "sum_to_one: 差额 0.2005 归现金"],
  "trigger_reason": "VIX=32.5 触发 bear 档, 减仓进攻类, 加仓防御",
  "audit": {"decision_logged": true, "portfolio_yaml_untouched": true}
}
```

## 验证方案

### 自动化验证

```bash
# 单元测试
py -3.8 -m pytest tests/unit/test_vol_regime_weighter.py -v

# 端到端测试
py -3.8 -m pytest tests/integration/test_vol_regime_phase0_e2e.py -v

# 手动运行一次（Flag=True 临时启用）
py -3.8 -c "from utils.alpha.vol_regime_weighter import VolRegimeWeighter; w=VolRegimeWeighter(); print(w.run_cycle(portfolio_snapshot={'科技':0.235,'现金':0.05}, vix_value=32.5))"
```

### Phase 0 内手动验证流程（每周一次）

1. 启用 flag：`reports/flag_overrides/USE_VOL_REGIME_WEIGHTER.json` 写入 `{"enabled": true, "signer": "..."}`
2. 运行 `python -m utils.alpha.vol_regime_weighter`
3. 检查 `reports/evolution/vol_regime_weights_YYYY-MM-DD.json`
4. 对比当日实际持仓（`config/positions.json`），若偏离>10% 记录到 `cairn/LOG.md`

### 与 ShadowAccount 一致性验证

加载 `reports/shadow/daily_returns.jsonl` 历史数据，对每个时点计算 VolRegimeWeighter 建议的 regime，对比 ShadowAccount risk_managed 模式的实际 scaler：bear/crisis 时 scaler 应 <1.0，bull 时 ≈1.0（一致性校验，不要求精确匹配）。

## 知识沉淀

实现完成后在 `cairn/LOG.md` 顶部追加一条（摘要 + 指针），并在 `cairn/self-evolution-framework.md` 的「核心组件」章节新增「VolRegimeWeighter」小节，标注 Phase 0 只读模式状态。

## 与用户 5 项改进的对应关系

| 用户改进项 | 本模块贡献 | Phase 0 内可见价值 |
|---|---|---|
| ② 校正配置 | **直接**：suggested_weights 是量化校正依据 | 每周一份建议报告 |
| ④ 对齐对冲 | **直接**：aligned_hedge_ratio 字段对齐 hedge_policy | 报告显示对冲比率是否匹配 |
| ⑤ 补量化测算 | **直接**：4×8 矩阵 + confidence + 约束校验 | 完整量化输出 + decisions.jsonl 审计链 |
| ① 核对真实持仓 | 间接：current_weights 强制每日刷新 | 为后续核对留入口 |
| ③ 收敛标的 | 间接：constraints_applied 暴露超限标的 | 为收敛提供数据 |

## 扩展点（Phase 1+ 预留）

- `apply_to_portfolio(yaml_path, dual_signature)` 方法签名预留（Phase 1 双签后写入）
- `backtest(historical_returns, historical_vix)` 方法签名预留（Phase 2 接入 fast_backtest.py）
- `VolRegime` 可序列化，Phase 2 可触发 AutoRetrainScheduler（高波动下因子衰减更快）
