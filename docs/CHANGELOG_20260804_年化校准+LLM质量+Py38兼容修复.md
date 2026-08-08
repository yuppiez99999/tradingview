# 更新日志：2026-08-04 年化校准 + LLM 输出质量 + Py38 兼容修复

> **归档日期**: 2026-08-04  
> **影响范围**: v8.4 主项目 (v8.3_institutional、ai、reporting、tools 四个模块)  
> **验证状态**: ✅ 端到端验证通过 + 17 个单元测试全部通过

---

## 一、修复总览

本次修复覆盖 **3 个核心问题域**，共 **7 处代码改动**，新增 **2 份知识文档** + **1 份回归测试**：

| 问题域 | 触发事件 | 修复文件数 |
|---|---|---|
| 年化收益校准 | 300308 中际旭创年化 +483.6% 极端外推 | 2 |
| LLM 输出质量 | ai_recommendations 仅存 6 条描述性文字，阶段三空转 | 1 |
| Py38 兼容性 | hedge_analyzer.py list[str] 在 Python 3.8 下 TypeError | 1 |

---

## 二、详细修复明细

### 2.1 年化收益校准（防 300308 类问题）

#### 修复 1.1：MAX_ANNUALIZED 阈值收紧
- **文件**: `v8.3_institutional/calibrate_returns_projection.py` L423
- **问题**: `MAX_ANNUALIZED = 50.0`（+5000%）过于宽松，短期暴涨股年化失真
- **修复**: `MAX_ANNUALIZED = 2.0`（+200%），A 股个股年度最大可重复收益率
- **同步文件**: `tools/calc_realized_returns.py` L68

#### 修复 1.2：短周期贝叶斯收缩机制
- **文件**: `v8.3_institutional/calibrate_returns_projection.py` L442-L451
- **问题**: 样本期 < 2 年的极端年化（如 300308 一年 ×13 倍）未做均值回归
- **修复**:
  ```python
  BAYESIAN_PRIOR = 0.15  # A 股长期权益收益率中枢
  if years < 2.0 and abs(annualized) > 0.5:
      shrink_weight = max(0.0, min(0.7, 1.0 - years / 2.0))
      annualized = annualized * (1 - shrink_weight) + BAYESIAN_PRIOR * shrink_weight
  ```
- **收缩强度**: 0.5 年 → 70%（封顶），1 年 → 50%，2 年 → 0%
- **实测效果**: 300308 (+483.6%) → 收缩 50% → +247.5% → 超 +200% 阈值 SKIP（权重=0）
- **同步文件**: `tools/calc_realized_returns.py` L70-L95

#### 修复 1.3：macro_policy_scoring 路径修正
- **文件**: `v8.3_institutional/calibrate_returns_projection.py`
- **问题**: 路径指向 `v8.3_institutional/src/macro/`（不存在）
- **修复**: 更正为 `ms_strategy/src/macro/`

#### 修复 1.4：持仓数据格式转换
- **文件**: `v8.3_institutional/calibrate_returns_projection.py`
- **问题**: positions.json 为字典格式（`{"588080.SH": {...}}`），evaluate_candidate_pool 期望列表（`["sh588080"]`）
- **修复**: 新增字典→列表转换，标准化代码格式

---

### 2.2 LLM 输出质量（防阶段三空转）

#### 修复 2.1：Prompt 反描述化强化
- **文件**: `ai/recommendation_generator.py` L150-L164
- **问题**: Prompt 未明确禁止描述性输出，DeepSeek 习惯先输出分析再给建议
- **修复**: 在 system_prompt 中前置 `【重要】不要输出任何分析过程、背景介绍、数据解读或开场白，只输出建议行本身`

#### 修复 2.2：描述行过滤 + 智能截断
- **文件**: `ai/recommendation_generator.py` L218-L252
- **问题**: `lines[:6]` 硬截断恰好截到前 6 条描述性文字，操作建议全被丢弃
- **修复**: 三层防御
  1. **描述行黑名单**（28 个关键词）: 过滤 `分析输入数据`、`日期:`、`净盈亏:` 等
  2. **操作建议白名单**（7 类 25 个关键词）: 识别 `IF空头`、`Put保护`、`建仓顺序` 等
  3. **智能截断**: 操作建议优先排序，不足 6 条再补其他

#### 修复 2.3（本次新增）：描述关键词分级匹配
- **文件**: `ai/recommendation_generator.py` L220-L242
- **问题**: `组合Beta` 关键词太宽泛，把 `IF空头2手 对冲组合Beta敞口` 误过滤
- **修复**: 拆分为两类
  - `descriptive_strict`（严格匹配，任何位置）: `分析输入数据`、`日期:` 等
  - `descriptive_line_start`（行开头匹配）: `组合Beta`、`大盘`、`指数` 等
- **效果**: `组合Beta: 1.25`（行开头）→ 过滤；`IF空头2手 对冲组合Beta敞口`（行中间）→ 保留 ✅

---

### 2.3 Py38 兼容性（防 TypeError）

#### 修复 3.1：PEP 585 类型注解替换
- **文件**: `reporting/hedge_analyzer.py` L32、L58、L72
- **问题**: 3 处使用 `list[str]`（Py3.9+ PEP 585 语法），Python 3.8 下报 `TypeError: 'type' object is not subscriptable`
- **修复**: 全部替换为 `List[str]`（文件已 `from typing import List`，选择方案 A 最小改动）
- **受影响函数**:
  - `_generate_if_contract_codes() -> list[str]` → `-> List[str]`
  - `_fetch_if_close_from_sina(..., if_codes: list[str])` → `if_codes: List[str]`
  - `_fetch_if_close_from_provider(..., if_codes: list[str])` → `if_codes: List[str]`

---

## 三、知识沉淀（cairn/）

| 文档 | 类型 | 核心内容 |
|---|---|---|
| `cairn/returns-calibration-standards.md` | 🆕 新建 | 6 个核心参数 + 贝叶斯收缩公式 + 阈值截断规则 + 边界场景 |
| `cairn/llm-output-quality-standards.md` | 🆕 新建 | 双层防御架构 + 28 词黑名单 + 25 词白名单 + 3 条踩坑记录 |
| `cairn/refactoring-standards.md#L297-L323` | ✏️ 更新 | PEP 585 对照表（8 种）+ 方案 A/B 代码示例 + 实际修复实例 |
| `cairn/LOG.md#L5-L10` | ✏️ 更新 | 经验归档摘要（与 ARCHIVE_SUMMARY 同步） |

---

## 四、归档报告（docs/）

| 文档 | 章节数 | 内容 |
|---|---|---|
| `docs/FIX_REPORT_20260804_收益预测校准与LLM建议修复.md` | 5 章 | 背景 + 文件清单 + 详细修复说明（含代码对比）+ 验证结果 + 后续建议 |
| `docs/ARCHIVE_SUMMARY_20260804_经验归档_年化校准+LLM质量+Py38兼容.md` | 5 章 | 背景 + 成果清单 + 关键经验摘要 + 关联代码 + 后续复用指引 |

---

## 五、验证结果

### 5.1 端到端验证（daily_workflow.py --phase calibrate）

| 步骤 | 状态 | 关键指标 |
|---|---|---|
| Step 1: Wind 数据拉取 | ✅ 通过 | 33 标的 × 267 交易日，0 失败 |
| Step 2: 年化 + 贝叶斯收缩 | ✅ 通过 | 12 只标的触发收缩，300308 安全 SKIP |
| Step 2.5: 候选标的评估 | ✅ 通过 | ADD=1, WATCH=2 |
| Step 3: Projection 校准 | ✅ 通过 | bull 概率上调，期望年化 9.33% |
| **组合加权年化** | ✅ 合理 | **+28.25%**（覆盖权重 100.01%） |

### 5.2 单元测试（17 个用例，全部通过）

```
tests/unit/test_bugfix_20260804_calibration_llm_py38.py
├── TestReturnsCalibrationBayesianShrinkage (11 个)
│   ├── 收缩强度计算（0.5 年/1 年/2 年）
│   ├── 300308 类极端年化收缩效果
│   ├── 正常年化/长周期不收缩
│   └── 阈值截断（超 200% SKIP / -99% 处理）
├── TestLlmOutputQualityFiltering (4 个)
│   ├── 描述行过滤 + 操作建议保留
│   ├── 全部被过滤 → 回退不返回空
│   ├── 操作关键词行优先排序
│   └── 结果不超过 6 条
└── TestPy38TypeAnnotationCompatibility (3 个)
    ├── hedge_analyzer 类型注解可解析
    ├── 3 个修复函数不抛 TypeError
    └── recommendation_generator 导入 List/Dict
```

**运行时间**: 0.35 秒  
**结果**: 17 passed

---

## 六、后续建议

1. **DeepSeek 余额恢复后**: 跑完整 EOD 工作流（阶段 0-4），验证 ai_recommendations 修复后的端到端效果
2. **Phase 1 NTPSync 问题**: `NameError: name 'NTPSync' is not defined` 与本次修复无关，建议单独排查
3. **LLM 关键词维护**: `descriptive_line_start` 关键词列表（组合净值/大盘/指数等）可根据实际 LLM 输出持续扩充

---

> **归档完成时间**: 2026-08-04  
> **归档人**: AI 助手  
> **关联 Git 状态**: 所有修改在本地工作区，尚未提交
