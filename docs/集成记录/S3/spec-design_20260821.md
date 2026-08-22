# S3 spec-design — value_discipline_layer.py

> 2026-08-21 | 依赖 spec-requirement_20260821.md

## 1. 模块结构

```
utils/value_discipline_layer.py          # 主模块 (~350行)
utils/value_discipline/
  ├── prompts.py                         # 4 大师 prompt 模板 (~200行)
  ├── quality_screen.py                  # 7 去劣指标 + 3 豁免 (~120行)
  ├── mirror_test.py                     # 5 句话压缩测试 (~60行)
  └── info_grade.py                      # A/B/C 信息丰富度评级 (~50行)
config/value_discipline.yaml             # 配置
tests/test_value_discipline_layer.py     # 单测
```

## 2. 接口签名

```python
# utils/value_discipline_layer.py
from dataclasses import dataclass
from typing import Optional, Tuple, Dict, List
from utils.glm5_decision_engine import TradingSignal, RiskAlert

@dataclass
class MasterView:
    master: str               # "buffett" / "munger" / "dyp" / "lixu"
    score: float              # 0-5
    reason: str
    available: bool = True    # LLM 失败时 False

@dataclass
class ScreenResult:
    triggered: List[str]      # 触发的去劣指标编号 ["1","3"]
    exemptions: List[str]     # 命中的豁免 ["A","C"]
    hard_fail: bool           # 触发且无豁免
    detail: Dict[str, str]

@dataclass
class DisciplinedSignal:
    signal: TradingSignal                     # 增强后 (confidence/reason/urgency 已更新)
    verdict: str                              # "pass" / "fail" / "grey"
    price_band: Optional[Tuple[float, float]]
    tier_advice: Dict[str, str]               # {"激进":..., "稳健":..., "保守":...}
    masters: Dict[str, MasterView]
    consensus: float                          # 0-1
    mirror_pass: bool
    info_grade: str                           # "A"/"B"/"C"
    quality_screen: ScreenResult
    extra_alerts: List[RiskAlert]             # QUALITY_FAIL 等

class ValueDisciplineLayer:
    def __init__(self, config_path: str = "config/value_discipline.yaml"): ...
    def apply(self, signal: TradingSignal, financial_data: Dict, market_meta: Dict) -> DisciplinedSignal: ...
    def apply_batch(self, signals: List[TradingSignal], fin_data_map: Dict, meta_map: Dict) -> List[DisciplinedSignal]: ...
```

## 3. 内部流程 (apply)

```
signal + fin_data + meta
  │
  ├─① info_grade = grade_info(meta)                    # A/B/C
  ├─② quality = screen_quality(fin_data)               # 7指标+3豁免 → hard_fail?
  ├─③ masters = run_four_masters(signal, fin_data, info_grade)  # 并行 4 LLM
  ├─④ consensus = weighted_avg(masters) / 5
  ├─⑤ mirror = mirror_test(signal.reason)              # 5句压缩
  ├─⑥ verdict = decide_verdict(quality, consensus, mirror)
  ├─⑦ price_band, tier_advice = extract_advice(masters)
  └─⑧ signal' = enhance_signal(signal, verdict, consensus, mirror, quality)
       signal'.confidence = base × consensus × (1 if mirror else 0)
       if quality.hard_fail: signal'.action = "REDUCE"; extra_alerts += QUALITY_FAIL
       if not mirror: signal'.action = "HOLD"; reason += "[论点不可压缩]"
return DisciplinedSignal(...)
```

## 4. 四大师 Prompt 模板 (prompts.py)

### 4.1 段永平 (商业模式)
```
你是段永平, 从商业模式视角评估 {name}。
信仰: 买股票=买公司未来现金流折现。
好生意四特征: 差异化 / 护城河(品牌/转换成本/网络效应/规模) / 定价权 / 轻资产。
Stop doing: 不懂不投 / 不做空 / 不借钱 / 不频繁交易 / 不看宏观 / 不预测股价。

财务数据: {financial_data}
当前信号: {signal}

输出 JSON: {"score": 1-5, "reason": "≤80字, 必须点明是否符合好生意四特征之一"}
```

### 4.2 巴菲特 (财务估值, A股本地化)
```
你是巴菲特, 从财务与估值视角评估 {name} (A股语境)。
关注: ROE(>15%优秀) / 毛利率(>40%定价权) / 自由现金流(持续正≈净利润) / 安全边际。
A股补充: 政策护城河(牌照/准入) / 国企牌照价值 / 十五五规划对齐度。

财务数据: {financial_data}
输出 JSON: {"score": 1-5, "reason": "≤80字"}
```

### 4.3 芒格 (行业竞争)
```
你是芒格, 从行业与竞争视角评估 {name}。
关注: 市场规模/增速 / 竞争格局 / 护城河可持续性 / 产业链价值分配 / 10年确定性。
多元思维: 不只看财务, 看行业演进/技术变革/政策/人性。
输出 JSON: {"score": 1-5, "reason": "≤80字"}
```

### 4.4 李录 (风险与管理层)
```
你是李录, 从风险与管理层视角评估 {name}。
关注: 管理层诚信/资本配置能力 / 监管风险 / 治理结构 / 关联交易 / 10年后会被颠覆吗。
中国视角: 国企治理 / 政策风险 / 股东回报文化。
输出 JSON: {"score": 1-5, "reason": "≤80字"}
```

**信息丰富度调节** (info_grade):
- A级: prompt 追加 "警惕共识陷阱, 重点找反面证据"
- B级: 追加 "推算数据标注置信度"
- C级: 追加 "转第一性原理, 聚焦商业本质, 允许留白"

## 5. 去劣指标 (quality_screen.py)

| # | 指标 | 否决 | 豁免 |
|---|------|------|------|
| 1 | 10年ROE均值 | <8% | A: 上市<10年 & 毛利>30% & 近2年经营CF为正 |
| 2 | 5年累计FCF | <0 | 无 |
| 3 | 利息覆盖(EBIT/利息) | <2 | 银行/保险不适用 |
| 4 | 长期毛利率 | <15% | C: ROE>20% & CF/净利>1 & 高周转薄利模式 |
| 5 | 经营CF/净利润(5年) | <0.7 | 无 |
| 6 | 长期净利率 | <5% | B: 毛利>30% & 近2年净利回升≥5%; C同上 |
| 7 | 5年股本膨胀 | >20%(非并购) | 无 |

`hard_fail = triggered 且无对应豁免`

## 6. 镜子测试 (mirror_test.py)

```python
def mirror_test(thesis: str) -> bool:
    # 用 LLM 判断 thesis 能否压缩到 5 句话且保留核心论点
    # prompt: "以下投资论点能否在5句话内说清且不丢核心? 仅返回 {\"compressible\": bool}"
    # 失败降级: 若 LLM 不可用, 默认 True (不阻塞)
```

## 7. 配置 schema (config/value_discipline.yaml)

```yaml
enabled: true
scenes: ["rebalancing_analysis", "深度分析"]   # 仅这些场景启用, 盘中决策不开
masters:
  buffett: {weight: 0.25}
  munger: {weight: 0.25}
  dyp: {weight: 0.30}        # 段永平权重略高 (A股语境)
  lixu: {weight: 0.20}
quality_screen:
  enabled: true
  hard_fail_action: "REDUCE"  # 或 "HOLD"
mirror_test:
  enabled: true
  max_sentences: 5
  llm_fallback: true           # LLM 不可用时默认通过
llm:
  router: "multi_model_router" # 复用主系统 ModelRouter
  timeout_per_master: 8
  parallel: true
performance:
  batch_timeout: 30
```

## 8. 集成点 (glm5_decision_engine.py)

在 `make_decisions` (line 277) 内, line 592 `return DecisionResult(...)` 之前插入:

```python
# 纪律层叠加 (向后兼容, 可开关)
if self._discipline_layer and scene in self._discipline_layer.config.scenes:
    disciplined = self._discipline_layer.apply_batch(
        decision.trading_signals, fin_data_map, meta_map
    )
    decision.trading_signals = [d.signal for d in disciplined]
    decision.risk_alerts += [a for d in disciplined for a in d.extra_alerts]
    decision.ai_confidence *= float(np.mean([d.consensus * (1 if d.mirror_pass else 0) for d in disciplined]))
    decision.raw_analysis += self._render_discipline_summary(disciplined)
```

`GLM5DecisionEngine.__init__` 增 `self._discipline_layer = ValueDisciplineLayer() if config_enabled else None` (try/except 降级, 失败不阻塞主流程)。

## 9. 数据流

```
market_data + portfolio_data
  → GLM5 原始决策 (trading_signals)
  → ValueDisciplineLayer.apply_batch
    ├─ quality_screen (纯规则, 无 LLM)
    ├─ four_masters (4 LLM 并行, 复用 ModelRouter)
    ├─ mirror_test (1 LLM)
    └─ enhance_signal (纯计算)
  → 增强 DecisionResult (signals + alerts + confidence + summary)
```

## 10. 降级策略

- `value_discipline.yaml` 不存在 → 旁路, 日志 warning
- 任一大师 LLM 超时/失败 → `MasterView.available=False`, consensus 按可用视角计算, 日志 warning
- mirror_test LLM 失败 → `mirror_pass=True` (不阻塞)
- quality_screen 缺字段 → 跳过该指标, 不硬否决
- 整层异常 → 旁路返回原 signal, 日志 error, 不影响主决策