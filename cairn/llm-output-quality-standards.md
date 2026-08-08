# LLM 输出质量控制标准 v2.0

> **创建**: 2026-08-04 | **最后更新**: 2026-08-07 (v2.0 全面升级)
> **关联文件**: `ai/recommendation_generator.py`, `ai_decision/debate_engine.py`, `utils/glm5_decision_engine.py`, `utils/external_strategy_adapter.py`, `tools/apply_llm_decisions_to_plan.py`
> **触发背景**: 全系统提示词审查升级

---

## 1. 适用范围

本标准适用于所有调用 LLM 生成结构化建议的模块：

- `ai/recommendation_generator.py` — AI 交易建议生成
- `ai_decision/debate_engine.py` — Bull/Bear/Judge 辩论
- `utils/glm5_decision_engine.py` — 五场景决策系统
- `utils/external_strategy_adapter.py` — 外部策略适配
- `cli/modes/gemma_analyze.py` — 本地 LLM 分析
- 任何需要 LLM 输出可被下游程序自动解析的场景

---

## 2. 核心原则

### 2.1 输出必须可被程序解析

LLM 输出不是给人看的报告，而是**下游程序的输入数据**。每一条建议都必须包含可被正则/关键词匹配识别的操作指令。

### 2.2 禁止描述性输出

分析过程、背景介绍、数据解读等描述性文字**对下游程序毫无价值**，只会占用 token 和存储，且极易被硬截断误删有效建议。

### 2.3 质量分层防御

仅靠 Prompt 不够（LLM 不总是遵守指令），必须在**输出解析层**再加一道防御：

```
Prompt 层 (第 1 道) → 输出解析层 (第 2 道) → 结果
```

---

## 3. Prompt 设计规范

### 3.1 必须包含的要素

```python
system_prompt = (
    "<身份定义> 你是 <角色级别> 的 <角色>, 负责 <具体职责>。"
    "<任务定义> 基于 <输入数据>, 直接输出 <N-M> 条结构化 <建议类型>。"
    "<反描述化禁令> 【重要】不要输出任何分析过程、背景介绍、数据解读或开场白,"
    " 只输出建议行本身。"
    "<关键词约束> 每条建议必须包含具体操作指令, 使用以下关键词之一:"
    "  <关键词列表> "
    "<输出格式> 每行一条建议, 不编号, 不带前缀符号, 直接输出建议文本。"
    "<质量要求> 建议必须基于数据, 量化, 可执行, 避免空泛表述。"
)
```

### 3.2 反描述化禁令必须前置

`【重要】不要输出任何分析过程...` 这段必须放在 Prompt 的前半部分（LLM 对开头和结尾的注意力最强）。

### 3.3 关键词必须与下游解析器对齐

Prompt 中的关键词白名单必须与 `apply_llm_decisions_to_plan.py` 等下游解析器使用的关键词完全一致，否则会出现"LLM 输出了建议但解析器识别不到"的假阴性。

---

## 4. 输出解析层规范

### 4.1 描述性行过滤器（黑名单）

以下关键词的行应被过滤（不进入最终结果）：

```python
descriptive_keywords = [
    # 数据描述类
    "分析输入数据", "输入数据", "数据分析", "数据解读",
    "日期：", "日期:", "净盈亏：", "净盈亏:",
    "持仓数：", "持仓数:", "对冲有效性：", "对冲有效性:",
    "组合Beta", "组合 beta", "portfolio beta",
    # 叙述类
    "市场环境", "行情分析", "盘面分析", "盘面回顾",
    "以下是", "基于以上", "综合分析", "综上所述",
    "今日", "本周", "本月", "本季度",
    # 元信息类
    "条建议", "共", "总计", "总结",
]
```

过滤逻辑：行中包含任意一个关键词即视为描述行，跳过。

### 4.2 操作建议识别器（白名单）

以下关键词表明该行包含可执行操作指令，应优先保留：

```python
action_keywords = [
    # 期货对冲类
    "IF空头", "增加期货", "提升Beta对冲效率",
    # Put保护类
    "510050 Put", "510300 Put", "Put保护", "买入Put",
    # 建仓类
    "建仓顺序", "优先建仓", "调整建仓",
    # 止损类
    "止损", "移动止损",
    # 仓位类
    "减持", "加仓", "仓位调整",
    # 板块类
    "板块权重", "增加", "降低",
]
```

### 4.3 智能截断流程

```python
# 步骤 1: 分行 + 去空白
lines = [l.strip() for l in raw_text.split("\n") if l.strip()]

# 步骤 2: 过滤描述行
filtered = [l for l in lines if not _is_descriptive(l)]

# 步骤 3: 操作建议优先排序
action_lines = [l for l in filtered if _has_action_keyword(l)]
non_action_lines = [l for l in filtered if not _has_action_keyword(l)]

# 步骤 4: 智能截断（操作建议在前，不足补其他）
result = (action_lines + non_action_lines)[:MAX_RECOMMENDATIONS]
```

### 4.4 日志可观测性

必须输出质量统计日志：

```python
logger.info(
    f"AI 建议质量: 原始={len(lines)} 条 → "
    f"过滤描述性={len(lines)-len(filtered)} 条 → "
    f"保留操作建议={len(action_lines)} 条 (含关键词)"
)
```

---

## 5. 质量验收标准

| 指标 | 合格标准 | 说明 |
|---|---|---|
| 描述行过滤率 | ≥ 95% | 100 条描述行最多 5 条漏网 |
| 操作建议保留率 | ≥ 90% | 10 条操作建议最多 1 条被误删 |
| 最终结果中操作建议占比 | ≥ 80% | 6 条最终建议至少 5 条含操作关键词 |
| 下游解析器命中率 | ≥ 70% | 10 条建议中 apply_llm 能识别 7 条以上 |

---

## 6. 踩坑记录

### 6.1 硬截断 = 丢建议

contains: llm-truncation-gotcha

**坑**: `return lines[:6]` 看起来无害，但 LLM 习惯先输出描述再给建议。结果：前 6 条全是描述，操作建议全在 7 条以后，被截断丢弃。阶段三 LLM 决策链路**形式上通、实质上空转**。

**解**: 先过滤描述行 + 操作建议优先排序，再截断。

### 6.2 Prompt 不是银弹

contains: llm-prompt-gotcha

**坑**: 以为在 Prompt 里写了"不要输出描述"就万事大吉。实际测试：DeepSeek 仍有 30-50% 的概率先输出 2-3 条描述，然后才给建议。

**解**: Prompt + 输出解析层**双层防御**，缺一不可。

### 6.3 关键词必须对齐

contains: llm-keyword-alignment-gotcha

**坑**: Prompt 里写"使用 IF空头"，但下游解析器匹配的是"IF 空头"（中间有空格），导致识别率为 0。

**解**: 维护一份**统一的关键词清单**，Prompt 和所有解析器共用。

---

## 7. 变更记录

| 版本 | 日期 | 变更内容 | 触发事件 |
|---|---|---|---|
| v2.0 | 2026-08-07 | 全系统提示词审查升级: 扩展适用范围, 新增 JSON 输出标准、温度规范、Few-shot 规范、Token 预算规范 | 12 文件全量审查 |
| v1.0 | 2026-08-04 | 初始版本 | ai_recommendations 仅存 6 条描述性文字，阶段三 LLM 决策链路空转 |

---

## 8. v2.0 新增规范

### 8.1 JSON 结构化输出标准

辩论引擎、策略适配器等需要精确信号提取的场景，强制 JSON 输出：

```python
# system_prompt 中明确
"输出格式 (严格 JSON，不要其他文字)"
"```json"
"{... 字段定义 ...}"
"```"
```

解析器必须 JSON 优先 + 文本回退：

```python
def parse(text):
    parsed = try_parse_json(text)  # 优先
    if parsed: return parsed
    return fallback_text_parse(text)  # 回退
```

### 8.2 温度 (Temperature) 规范

| 场景 | 温度 | 理由 |
|---|---|---|
| 格式敏感 (JSON 输出、关键词匹配) | 0.1-0.2 | 降低格式变异 |
| 分析决策 (再平衡、宏观) | 0.2-0.3 | 平衡创造性与一致性 |
| 报告生成、总结 | 0.3-0.5 | 需要一定创造性 |

### 8.3 Few-shot 规范

关键决策场景至少包含 1 条 few-shot 示例：

```python
system_prompt = (
    "..."
    "示例输出 (参考格式):\n"
    "..."
)
```

示例必须是**真实场景**的压缩版，不是虚构的通用模板。

### 8.4 Token 预算规范

每个 system_prompt 应标注预期 max_tokens：

```
**建议温度: 0.15 | max_tokens: 800**
```

防止 LLM 输出过长浪费成本和延迟。

### 8.5 系统约束注入规范

每个决策相关 system_prompt 必须注入**当前系统的硬约束参数**，而非通用风控常识：

```
"硬约束: 单标的≤10%, 单板块≤25%, VaR95≤1.5%, 现金≥5%, 止损-8%, 止盈+20%"
```

### 8.6 数据注入摘要化

大 JSON dump → 可读摘要文本，节省 token 并提高 LLM 理解质量：

```python
# ❌ 旧: JSON dump 全部数据
f"```json\n{json.dumps(data)}\n```"

# ✅ 新: 提取关键指标摘要化
f"Top5 持仓:\n{holding_summary}"
```

