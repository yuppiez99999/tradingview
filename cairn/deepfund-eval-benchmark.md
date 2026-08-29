# DeepFund 防泄漏评估基准 (LIT-1.3)

> **创建**: 2026-08-23
> **状态**: 已实现 + 31 单元测试全绿 + 9 LLM mock 端到端验证通过
> **文献**: #22 DeepFund (NeurIPS 2025) — "Time Travel is Cheating"
> **开源**: https://github.com/HKUSTDial/DeepFund
> **实现**: `tests/eval/deepfund_harness.py` (~775行) + `tests/unit/test_deepfund_harness_unit.py` (~385行)
> **LOG 指针**: 2026-08-23 LIT-1.3 DeepFund 防泄漏评估基准

---

## 1. 核心问题

LLM 通过预训练可能"记住"了未来信息 (look-ahead bias / time travel)。
DeepFund 论文标题 **"Time Travel is Cheating"** 直指这一问题：如果 LLM 在预训练
阶段见过 2024-06 的价格，那么在 2024-01 让它做决策时，它可能"回忆"出未来信息，
导致回测表现异常优异但实盘无法复现。

## 2. 检测方法 (四层防线)

| 层 | 方法 | 实现 |
|----|------|------|
| L1 时序检查 | 决策是否按时间顺序生成 | `_check_chronological_order` |
| L2 信息边界 | LLM reasoning 是否引用未来日期 | `_check_info_boundary` (正则提取日期) |
| L3 统计异常 | Sharpe > 3.0 或 胜率 > 75% | anomaly_score 累加 + p_value |
| L4 对照对比 | 超越随机基准 5 倍 | benchmark_metrics 对比 |

**判定逻辑**: `is_leaked = (anomaly_score >= 0.3) or 时序违反 or 边界违反`
单个强异常信号即可触发（Sharpe>3.0 给 0.4 分，胜率>75% 给 0.3 分）。

## 3. 架构

```
tests/eval/deepfund_harness.py
├── LLMAdapter              — 统一 LLM 接口 (复用 utils/llm_client.py + mock)
├── BenchmarkDataLoader     — 合成数据 (GBM) + 严格时序切分
├── TimeLeakageDetector     — 四层防线检测器
├── DeepFundHarness         — 多 LLM 竞技场主评估器
└── main()                  — CLI 入口
```

### 3.1 LLMAdapter

- **mock 模式**: 无 API key 时自动降级，确定性伪随机（基于 prompt 哈希），保证 CI 可复现
- **真实模式**: 复用 `utils/llm_client.py` 统一客户端，支持 5 个 provider
- **9 LLM 注册表**: GPT-4o/4o-mini/4-turbo · Claude-3.5-Sonnet/Haiku · DeepSeek-V3/R1 · GLM-5 · Qwen2.5-72B

### 3.2 BenchmarkDataLoader

- **合成数据**: 几何布朗运动 (GBM)，可复现（seed=42）
- **严格时序切分**: `load_market_data(date)` 只返回 `<= date` 的数据
- **缓存**: 同一 date 重复加载返回同一对象

### 3.3 TimeLeakageDetector (核心创新)

- **L1 时序检查**: 遍历 decisions，检查 `date[i] >= date[i-1]`
- **L2 信息边界**: 正则提取 reasoning 中的 YYYY-MM-DD 日期，检查是否 > decision.date
- **L3 统计异常**: Sharpe > 3.0 → anomaly += 0.4, p=0.005; 胜率 > 75% → anomaly += 0.3, p=0.02
- **L4 对照对比**: 超越随机基准 Sharpe 5 倍 → anomaly += 0.3, p=0.01

## 4. 验收结果 (2026-08-23)

### 4.1 单元测试
- **31 测试全绿** (0.82s)
- 覆盖: LLMAdapter (5) + BenchmarkDataLoader (5) + TimeLeakageDetector (8) + DeepFundHarness (7) + 数据结构 (6)

### 4.2 端到端验证 (mock 模式)
- **9 LLM 全部评估完成**
- **全部通过泄漏检测** (p=1.0, anomaly=0.0) — mock 是伪随机，表现正常
- **报告已归档**: `reports/eval/deepfund/deepfund_eval_20260823_184651.json` (4.7KB)

### 4.3 mock 模式性能
- 6 标的 × 60 评估日 = 360 决策/LLM
- 9 LLM 总耗时 < 3 秒
- Sharpe 范围: -1.35 ~ 1.34 (伪随机预期)

## 5. 与 DeepFund 原论文的对应

| 原论文 | 本实现 | 差异 |
|--------|--------|------|
| LangGraph 多智能体 | 单轮 chat 决策 | 简化为评估基准，不做多智能体编排 |
| Supabase/SQLite 数据库 | JSON 报告归档 | 评估基准无需持久化数据库 |
| 实时数据 API | 合成 GBM 数据 | 保证可复现，生产可子类化接入真实数据 |
| 9 LLM 实测 | 9 LLM mock + 真实可选 | 无 API key 时 mock 降级 |
| 时间穿越检测 | 四层防线 | 增强了 L2 信息边界检查 (正则提取日期) |

## 6. 后续扩展 (LIT-1.4 衔接)

- **LIT-1.4 AI-Trader**: 实时未污染基准，将复用本 harness 的 TimeLeakageDetector
- **真实数据接入**: 子类化 BenchmarkDataLoader，重写 load_market_data 接入 Wind/TDX
- **多智能体**: 可选集成 LangGraph，复用 `quant_modules/ai_hedge_fund/` 的 20 分析师

## 7. 踩坑记录

- **ruff W292**: 文件末尾无换行 → `--fix` 自动修复
- **ruff I001**: import 排序 → `--fix` 自动修复
- **ruff UP015**: `open("r")` 多余 mode 参数 → `--fix` 自动修复
- **异常阈值**: 初始 `anomaly_score >= 0.5` 导致单信号不触发，改为 `>= 0.3`
- **PowerShell 编码**: 中文输出乱码（终端问题，不影响功能/报告 JSON 正确）

## 8. 文件清单

| 文件 | 行数 | 用途 |
|------|------|------|
| `tests/eval/deepfund_harness.py` | 775 | 核心实现 |
| `tests/eval/__init__.py` | 0 | 包标记 |
| `tests/unit/test_deepfund_harness_unit.py` | 385 | 单元测试 (31) |
| `reports/eval/deepfund/README.md` | 50 | 归档目录说明 |
| `reports/eval/deepfund/deepfund_eval_*.json` | 4.7KB | 评估报告 |

<!-- AUTO-GENERATED: 相关文档 -->
## 相关文档

- [AI-Trader 实时未污染评估基准 (LIT-1.4)](ai-trader-eval-benchmark.md) (相似度 24%)
- [KTD-Fin 记忆控制评估基准](ktd-fin-eval.md) (相似度 20%)
- [v8.7 发布 — LIT-5.6 全量集成验收](v87-release.md) (相似度 17%)
- [R&D-Agent-Quant 多智能体因子挖掘引擎 (LIT-1.1)](rd-agent-quant.md) (相似度 14%)
- [AlphaCFG 语法引导因子发现 (LIT-1.5)](alpha-cfg-discovery.md) (相似度 11%)

<!-- 由 scripts/cairn_cross_ref.py 自动生成，请勿手动编辑此区块 -->
