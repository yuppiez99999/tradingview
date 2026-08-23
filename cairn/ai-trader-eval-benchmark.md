# AI-Trader 实时未污染评估基准 (LIT-1.4)

> **创建**: 2026-08-23
> **状态**: 已实现 + 37 单元测试全绿 + 5 agent mock 端到端验证通过
> **文献**: #23 AI-Trader (2025.12) — "100% Fully-Automated Agent-Native Trading"
> **开源**: https://github.com/HKUDS/AI-Trader
> **实现**: `tests/eval/ai_trader_harness.py` (~780行) + `tests/unit/test_ai_trader_harness_unit.py` (~440行)
> **LOG 指针**: 2026-08-23 LIT-1.4 AI-Trader 实时未污染基准

---

## 1. 与 DeepFund (LIT-1.3) 的互补关系

| 维度 | DeepFund (LIT-1.3) | AI-Trader (LIT-1.4) |
|------|-------------------|-------------------|
| 检测目标 | LLM **内部**时间穿越 | **数据管道**污染 |
| 核心问题 | LLM 预训练"记住"未来信息 | 数据流被未来数据污染 |
| 检测方法 | 时序+信息边界+统计异常+对照组 | 哈希+时序+未来时间戳+隔离+来源 |
| 评估对象 | 9 个 LLM | 5 个 Agent 策略 |
| 数据模式 | 合成 GBM | 实时数据流模拟 |

## 2. 五层防线数据污染检测

| 层 | 方法 | 实现 | 注入测试 |
|----|------|------|----------|
| L1 哈希校验 | SHA256 content hash | `is_hash_valid()` | ✅ 检出 5 违规 |
| L2 时序校验 | 同一标的内乱序检测 | 按标的分组 + 相邻比较 | ✅ 检出 2 违规 |
| L3 未来时间戳 | timestamp > decision_cutoff | 正则提取 + 比较 | ✅ 检出 3 违规 |
| L4 隔离校验 | 数据来源可信度 | trusted_sources 白名单 | ✅ |
| L5 来源校验 | 评估环境隔离 | source 字段检查 | ✅ |

**关键设计**: 乱序检测按标的分组，跨标的无可比性（不同标的数据到达顺序无时序关系）。

## 3. 架构

```
tests/eval/ai_trader_harness.py
├── DataRecord              — 单条数据记录 (含时间戳 + SHA256 哈希 + 来源)
├── RealTimeStream          — 实时数据流模拟 (按时间顺序 + 污染注入)
├── DataContaminationDetector — 五层防线检测器
├── AgentAdapter            — 5 种策略 (momentum/mean_revert/value/sentiment/ensemble)
├── AITraderHarness         — 主评估器 + 污染注入测试
└── main()                  — CLI 入口
```

### 3.1 DataRecord

- **SHA256 哈希**: 自动计算 content hash (timestamp+symbol+price+volume+source)
- **篡改检测**: `is_hash_valid()` 验证哈希是否匹配
- **确定性**: 相同内容产生相同哈希

### 3.2 RealTimeStream

- **按时间顺序生成**: 工作日 15:00 收盘时点
- **严格时序切分**: `get_records_up_to(cutoff)` 只返回 `<= cutoff` 的记录
- **污染注入**: 三种注入方法 (哈希篡改/乱序/未来时间戳)

### 3.3 AgentAdapter (5 种策略)

| 策略 | 逻辑 |
|------|------|
| momentum | 近期上涨→buy, 下跌→sell |
| mean_revert | 偏离均值→反向操作 |
| value | 低于 Q25→buy, 高于 Q75→sell |
| sentiment | mock 情感评分 |
| ensemble | 综合以上 3 个信号 |

## 4. 验收结果 (2026-08-23)

### 4.1 单元测试
- **37 测试全绿** (1.13s)
- 覆盖: DataRecord (5) + RealTimeStream (6) + DataContaminationDetector (7) + AgentAdapter (7) + AITraderHarness (7) + 数据结构 (4) + 1

### 4.2 端到端验证 (mock 模式)
- **5 agent 全部数据干净** (contamination_score=0.0)
- **5 agent 全部 100% 准确率** (180/180 决策)
- **污染注入测试全部检出**: 哈希篡改(5) + 乱序(2) + 未来时间戳(3)
- **报告已归档**: `reports/eval/ai_trader/ai_trader_eval_*.json`

## 5. 踩坑记录

- **跨标的乱序误报**: 合并多决策 data_used 后，不同标的记录交替产生人工乱序
  - 修复: 乱序检测按标的分组，跨标的无可比性
- **合并 data_used 人工乱序**: 后续决策 data_used 前段与前一个 data_used 后段时间戳重叠
  - 修复: 逐决策检测污染，不合并 data_used
- **乱序注入跨标的**: `inject_order_contamination` 交换相邻记录可能跨标的
  - 修复: 在同一标的内交换相邻记录
- **"synthetic" 来源误报**: 默认 source 不在 trusted_sources 中
  - 修复: 将 "synthetic" 加入可信来源白名单

## 6. 文件清单

| 文件 | 行数 | 用途 |
|------|------|------|
| `tests/eval/ai_trader_harness.py` | 780 | 核心实现 |
| `tests/unit/test_ai_trader_harness_unit.py` | 440 | 单元测试 (37) |
| `reports/eval/ai_trader/README.md` | 40 | 归档目录说明 |
| `reports/eval/ai_trader/ai_trader_eval_*.json` | ~3KB | 评估报告 |