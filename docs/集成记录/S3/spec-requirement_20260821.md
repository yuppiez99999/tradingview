# S3 spec-requirement — ai-berkshire 决策纪律层

> 2026-08-21 | 集成到 `utils/glm5_decision_engine.py` (v5.8, 1004行)

## 1. 现状与缺口

**主系统现状** (`glm5_decision_engine.py`):
- `GLM5DecisionEngine.make_decisions(market_data, portfolio_data, scene)` 场景路由
- 输出 `DecisionResult{trading_signals: List[TradingSignal], ai_confidence, raw_analysis}`
- `TradingSignal{action: BUY/SELL/HOLD/REDUCE, confidence: 0-1, reason}`
- 场景: intraday_decision / rebalancing_analysis / 宏观 / 报告 / 舆情

**缺口**:
1. AI 输出"一方面...另一方面"骑墙分析, 缺强制结论/价格区间/分层建议
2. 单一视角, 缺多大师对抗产生的真实张力
3. 无硬性否决机制 (能力圈外/质量不达标仍给 HOLD)
4. 无"镜子测试"约束 (论点是否 5 句话说得清)

## 2. 源项目能力 (ai-berkshire 18 skills)

| Skill | 机制 | 提取点 |
|-------|------|--------|
| `investment-team.md` | 四角色并行 (段永平商业模式/巴菲特财务估值/芒格行业竞争/李录风险管理层) + 信息丰富度 A/B/C 评级 | 角色定义 + 评级逻辑 |
| `investment-checklist.md` | 六关 Checklist, ★1-5 评分, 硬性否决 | 评分标准 + 否决规则 |
| `dyp-ask.md` | 段永平思想: 买股=买未来现金流折现; 好生意=差异化+护城河+定价权+轻资产; Stop doing list | 思想体系 prompt |
| `quality-screen.md` | 7 去劣指标 + 3 豁免规则 (10年ROE<8%否决, 5年FCF为否否决, 利息覆盖<2否决...) | 指标表 + 豁免规则 |

## 3. 需求 (EARS 格式)

### 3.1 功能需求

- **REQ-1 四大师对抗分析**: 当系统对单只标的生成深度决策时, 系统应并行调用四大师视角分析, 并返回 `{buffett, munger, dyp, lixu}` 各自评分(0-5)与理由, 加权汇总为共识度(0-1)。
- **REQ-2 强制结论**: 当四大师分析完成时, 系统应输出 `verdict ∈ {pass, fail, grey}` + 价格区间 + 分层建议(激进/稳建/保守), 不得返回无结论的平衡分析。
- **REQ-3 镜子测试**: 当论点(thesis)无法在 5 句话内说清时, 系统应标记 `mirror_test=False` 并将 action 降级为 HOLD, 理由注明"论点不可压缩"。
- **REQ-4 去劣硬否决**: 当标的触发 7 去劣指标任一且不满足 3 豁免规则时, 系统应将 action 强制为 REDUCE/HOLD, 并在 risk_alerts 追加 `QUALITY_FAIL` 预警。
- **REQ-5 信息丰富度评级**: 当标的上市年数/覆盖度已知时, 系统应标注 A/B/C 级, 并调整四大师策略 (A级重反面检验, C级转第一性原理)。
- **REQ-6 A股语境适配**: 当标的为 A 股时, 巴菲特/芒格视角应本地化 (政策护城河/国企牌照/十五五规划对齐), 段永平/李录保留原视角。

### 3.2 集成需求

- **REQ-7 出口叠加**: 当 `GLM5DecisionEngine.make_decisions` 返回 `DecisionResult` 时, 若 `config/value_discipline.yaml` 启用, 系统应叠加纪律层, 不破坏原 `TradingSignal` 字段, 仅增强 confidence/reason/urgency。
- **REQ-8 融合权重**: 最终 `confidence = base_confidence × masters_consensus × mirror_pass`, 其中 masters_consensus = 四大师评分加权均值/5, mirror_pass ∈ {0, 1}。
- **REQ-9 可开关**: 当 `value_discipline.yaml.enabled=false` 时, 系统应完全旁路纪律层, 行为与集成前一致 (向后兼容)。
- **REQ-10 性能预算**: 单标的纪律层耗时 < 3s (四大师并行), 批量 15 标的 < 30s。

### 3.3 非功能需求

- **REQ-11 不可变性**: 纪律层不原地改输入 dataclass, 返回新 `DecisionResult` (AGENTS.md §5.1)。
- **REQ-12 优雅降级**: 四大师任一 LLM 调用失败时, 该视角标记 `unavailable`, 不阻塞其余视角, 共识度按可用视角计算。
- **REQ-13 模块规模**: `value_discipline_layer.py` 200-400 行, 单测覆盖率 ≥ 80% (AGENTS.md §5.3, §7)。

## 4. 输入/输出契约

### 输入
```python
apply_discipline(
    signal: TradingSignal,           # GLM5 原始信号
    financial_data: Dict,            # ROE/FCF/毛利率/利息覆盖/净利率/股本膨胀
    market_meta: Dict,               # 上市年数/覆盖度/市场(A/H/US)
    config: ValueDisciplineConfig,
) -> DisciplinedSignal
```

### 输出
```python
@dataclass
class DisciplinedSignal:
    signal: TradingSignal            # 增强后的信号 (confidence/reason/urgency 已更新)
    verdict: str                     # "pass" / "fail" / "grey"
    price_band: Optional[Tuple[float, float]]  # 价格区间
    tier_advice: Dict[str, str]      # {"激进": ..., "稳建": ..., "保守": ...}
    masters: Dict[str, MasterView]   # {buffett/munger/dyp/lixu: {score, reason}}
    consensus: float                 # 0-1
    mirror_pass: bool
    info_grade: str                  # "A" / "B" / "C"
    quality_screen: ScreenResult     # 7 指标 + 豁免命中
```

## 5. 验收标准

- [ ] 15 持仓标的启用纪律层后, `verdict` 100% 非空, 无骑墙结论
- [ ] 触发去劣指标的标的 action 被强制 REDUCE/HOLD
- [ ] 论点超 5 句的标的 mirror_pass=False 且 action=HOLD
- [ ] `enabled=false` 时输出与集成前字节级一致 (回归)
- [ ] 单测覆盖四大师/镜子/去劣/评级/融合 5 模块, 覆盖率 ≥ 80%
- [ ] pre-commit + ruff + mypy 通过

## 6. 不做 (YAGNI)

- 不做四大师的 WebSearch 联网研究 (主系统已有数据层, 复用 financial_data)
- 不做 Team 多 Agent 编排 (主系统已有 LangGraph AI Hedge Fund, 纪律层用单次 LLM 多视角 prompt 即可)
- 不做投资报告生成 (复用主系统报告模块)
- 不做美股/港股专用适配 (本次聚焦 A 股 15 标的, 跨市场留后续)