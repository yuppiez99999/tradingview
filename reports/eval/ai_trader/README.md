# AI-Trader 实时未污染评估基准 — 归档目录

> **文献依据**: #23 AI-Trader: Real-Time Benchmark (2025.12)
> **开源**: https://github.com/HKUDS/AI-Trader
> **实现**: `tests/eval/ai_trader_harness.py` (LIT-1.4, 2026-08-23)

## 与 DeepFund (LIT-1.3) 的互补关系

- **DeepFund**: 检测 LLM **内部**时间穿越 (预训练"记住"未来信息)
- **AI-Trader**: 检测**数据管道**污染 (实时数据流是否被未来数据污染)

## 五层防线数据污染检测

1. **哈希校验**: 检测数据篡改 (content hash 不匹配)
2. **时序校验**: 检测同一标的内数据到达顺序 (乱序)
3. **未来时间戳**: 检测未来时间戳混入
4. **隔离校验**: 检测数据来源可信度
5. **来源校验**: 检测评估环境与训练数据隔离

## 5 个 Agent 策略

momentum · mean_revert · value · sentiment · ensemble

## 运行方式

```bash
# mock 模式 (无需 API key)
python tests/eval/ai_trader_harness.py
```

## 归档文件

- `ai_trader_eval_YYYYMMDD_HHMMSS.json` — 评估结果 JSON
