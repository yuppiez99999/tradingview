# DeepFund 防泄漏评估基准 — 归档目录

> **文献依据**: #22 DeepFund: Live Fund Benchmark (NeurIPS 2025)
> **论文**: "Time Travel is Cheating: Going Live with DeepFund for Real-Time Fund Investment Benchmarking"
> **开源**: https://github.com/HKUSTDial/DeepFund
> **实现**: `tests/eval/deepfund_harness.py` (LIT-1.3, 2026-08-23)

## 核心问题

LLM 通过预训练可能"记住"了未来信息 (look-ahead bias / time travel)。
本基准在真实时间点给 LLM 截止该时点的历史数据, 检测其决策是否"异常准确"。

## 评估方法

1. **时序检查**: 决策是否按时间顺序生成 (无未来决策混入)
2. **信息边界检查**: LLM 推理过程是否引用了未来日期
3. **统计异常检测**: Sharpe > 3.0 或胜率 > 75% 视为可疑
4. **对照组对比**: 与随机决策基准对比, 超越 5 倍视为泄漏

## 9 个 LLM

GPT-4o · GPT-4o-mini · GPT-4-turbo · Claude-3.5-Sonnet · Claude-3.5-Haiku ·
DeepSeek-V3 · DeepSeek-R1 · GLM-5 · Qwen2.5-72B

## 运行方式

```bash
# mock 模式 (无需 API key, 用于 CI 验证)
python tests/eval/deepfund_harness.py

# 编程接口
from tests.eval.deepfund_harness import DeepFundHarness, LLMAdapter
harness = DeepFundHarness()
adapters = [LLMAdapter(provider="mock", model=f"mock-{i}", mock=True) for i in range(9)]
report = harness.run_evaluation(adapters, "2024-01-01", "2024-06-30")
harness.save_report(report)
```

## 归档文件

- `deepfund_eval_YYYYMMDD_HHMMSS.json` — 评估结果 JSON