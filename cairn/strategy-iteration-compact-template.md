# 策略迭代压缩模板（Strategy Iteration Compact）

> 模板: 2026-08-21
> 来源: 借鉴 ECC `mle-workflow` 的 Iteration Compact 单页格式，字段映射到 A 股量化语境
> 用途: 每次策略/因子迭代前，先填本表，使迭代可被同行挑战、可回溯、有明确下线条件
> 互补: `cairn/factor-discovery-loop-engineering.md`（因子发现工程，内容更详尽）/ `cairn/live-trading-admission-criteria-20260811.md`（实盘准入）
> 约束: 填写后须通过 `ci_lookahead_guard.py` 前视偏差门禁 + `backtest_integrity.py` 回测完整性守卫

## 填写模板（复制本块，逐项填写）

```text
策略假设:
  要验证的市场异象/因子假设:

资金方/决策方:
  实盘 / 研究 / 回测:

策略负责人:

交易行为变化:
  选股 / 择时 / 仓位 / 对冲 / 执行:

成功指标:
  IC / IR / Sharpe / 超额收益 / 胜率:

护栏指标:
  最大回撤 / 年化波动 / 换手率 / 单边暴露 / 冲击成本 / 容量:

回测容错(可接受的指标偏离):

不可接受错误:
  前视偏差 / 过拟合 / 幸存者偏差 / look-ahead bias / mock alpha:

可接受错误:

假设:
  市场状态 / 数据可用性 / 交易成本 / 流动性:

约束:
  T+1 / 涨跌停 / 容量 / 资金 / 单标的上限:

标签与数据快照:
  收益标签定义 / 因子快照日 / 截断日(cutoff) / 数据源层级:

基线:
  当前生产策略 / 基准指数 / 前一迭代:

候选信号:
  新因子 / 新组合 / 新参数:

阈值/配置计划:

IC 分层(eval slices):
  按行业 / 市值 / 周期 / 风格 / 流动性 / 时段:

已知风险:

下一实验(可证伪):

策略下线条件/回退:
  触发指标 / 回退到哪个策略 / 熔断阈值:

前视偏差检查:
  [ ] ci_lookahead_guard.py 通过
  [ ] _detect_lookahead_tests.py 无违规
  [ ] walk-forward 验证通过

实盘准入(若目标为实盘):
  [ ] 满足 live-trading-admission-criteria-20260811.md
```

## 使用规则

1. **迭代前必填** — 任何策略/因子改动 PR 须附本表，否则按 `code-review-sop.md` 退回
2. **可证伪** — "下一实验"必须是可被回测证伪的具体实验，不是"提升模型"之类模糊目标
3. **下线先行** — "策略下线条件"未填的迭代不予合入（实盘策略尤其）
4. **不替代已有文档** — 本表是单页压缩，详细论证仍走 `factor-discovery-loop-engineering.md`
5. **沉淀** — 迭代完成后将本表追加到 `cairn/LOG.md` 顶部，结论沉淀为知识专题

## 与 ECC 原模板的字段映射

| ECC Iteration Compact | 本模板 | 映射理由 |
|---|---|---|
| Goal | 策略假设 | 量化迭代始于市场异象假设 |
| Mistake budget | 回测容错 | 量化语境下"错误预算"=可接受指标偏离 |
| Unacceptable mistakes | 不可接受错误(前视/过拟合/幸存者偏差) | 量化特有致命错误 |
| Labels and data snapshot | 标签与数据快照(含 cutoff) | 量化须明确截断日防前视 |
| Eval slices | IC 分层(行业/市值/周期/风格) | 量化须分层评估因子 |
| Rollback or fallback | 策略下线条件/回退 | 量化须先定下线再上线 |
| (无) | 前视偏差检查 / 实盘准入 | 量化系统已有门禁，显式引用 |

<!-- AUTO-GENERATED: 相关文档 -->
## 相关文档

- [代码质量提升外部资源评估](code-quality-external-resources-20260821.md) (相似度 11%)
- [实盘准入框架：12-31 上实盘的 7 项硬性门槛与 6 个决策门](live-trading-admission-criteria-20260811.md) (相似度 8%)
- [影子观察期真实性核查 (2026-08-24)](shadow-realness-audit-20260824.md) (相似度 8%)
- [回测标准](backtest-standards.md) (相似度 7%)
- [运维部署 + 灰度发布 (2026-08-24)](ops-gray-release-20260824.md) (相似度 6%)

<!-- 由 scripts/cairn_cross_ref.py 自动生成，请勿手动编辑此区块 -->
