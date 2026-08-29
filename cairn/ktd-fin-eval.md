# KTD-Fin 记忆控制评估基准

> **文献**: #25 KTD-Fin: Memory-Controlled Benchmark (2026.05, ★★★★)
> **任务**: LIT-2.4 KTD-Fin 记忆控制评估
> **状态**: ✅ 已完成 (2026-08-23)
> **LOG 指针**: `cairn/LOG.md` → "2026-08-23 · LIT-2.4"

## 核心问题

LLM 在金融决策中可能"记忆"训练数据中的未来信息, 导致评估泄漏。
KTD-Fin 通过数据侧掩码 + 归因分析检测这种记忆泄漏。

## 三大核心组件

### 1. DataMasker (数据侧掩码)

对未来时间窗口的数据进行掩码，4 种策略：
- `ZERO`: 零值掩码
- `MEAN`: 均值掩码 (用历史均值替换)
- `NOISE`: 随机噪声掩码
- `DROP`: 丢弃未来数据

### 2. BarraAttributor (Barra 风险因子归因)

6 个 Barra 风险因子：
- `market`: 市场因子
- `size`: 规模因子
- `value`: 价值因子
- `momentum`: 动量因子
- `volatility`: 波动率因子
- `beta`: Beta 因子

归因分析：简化 OLS 估计因子收益 + R² 拟合优度 + 异常暴露检测 (|exposure| > 2.0)。

### 3. MemoryLeakDetector (记忆泄漏检测)

- **Sharpe 阈值**: > 3.0 视为异常
- **胜率阈值**: > 75% 视为异常
- **决策差异**: 掩码前后 Sharpe 差异 > 2.0 视为异常
- **5 级严重程度**: NONE → LOW → MEDIUM → HIGH → CRITICAL

## 交付物

| 文件 | 行数 | 说明 |
|------|------|------|
| `tests/eval/ktd_fin.py` | ~660 | 核心实现 (3组件 + 基准框架 + MockAgent) |
| `tests/unit/test_ktd_fin_unit.py` | ~445 | 39 单元测试全绿 |

## 测试覆盖

- 枚举 (3 测试)
- 数据结构 (6 测试)
- DataMasker (6 测试: 4种策略 + 历史保留 + 摘要)
- BarraAttributor (6 测试: 归因 + 空数据 + 异常检测)
- MemoryLeakDetector (6 测试: 无泄漏 + 高Sharpe + 高胜率 + 差异 + 严重 + 归因)
- KTDFinBenchmark (6 测试: 评估 + 干净/泄漏代理 + 摘要 + 因子暴露)
- MockAgent (3 测试)
- 端到端 (2 测试)

## ruff.toml 豁免

```toml
# UP042: str+Enum 继承保留 Py3.8 兼容 (StrEnum 需 Py3.11+)
"tests/eval/ktd_fin.py" = ["UP042"]
```

## 后续方向

- **LIT-2.5**: FinGPT 系列集成（轻量 LoRA）
- **CSI300 验证**: 接入真实 CSI300 2024-2026 数据验证
- **与 DeepFund 互补**: DeepFund 检测 LLM 内部时间穿越, KTD-Fin 检测数据侧记忆泄漏

<!-- AUTO-GENERATED: 相关文档 -->
## 相关文档

- [FinGPT 系列集成 (轻量 LoRA + RLSP 训练管线)](fingpt-integration.md) (相似度 22%)
- [DeepFund 防泄漏评估基准 (LIT-1.3)](deepfund-eval-benchmark.md) (相似度 20%)
- [v8.7 发布 — LIT-5.6 全量集成验收](v87-release.md) (相似度 18%)
- [AI-Trader 实时未污染评估基准 (LIT-1.4)](ai-trader-eval-benchmark.md) (相似度 15%)
- [CN-Buzz2Portfolio 中国市场基准](cn-buzz2portfolio.md) (相似度 13%)

<!-- 由 scripts/cairn_cross_ref.py 自动生成，请勿手动编辑此区块 -->
