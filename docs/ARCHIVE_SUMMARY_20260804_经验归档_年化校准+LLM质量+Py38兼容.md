# 经验归档总结报告：年化校准 + LLM 输出质量 + Py38 兼容

> **归档日期**: 2026-08-04  
> **归档范围**: 年化收益校准标准、LLM 输出质量控制标准、Python 3.8 兼容性指南

---

## 1. 归档背景

本次修复暴露了三项缺乏标准化的经验：

1. **年化收益校准无标准**：`MAX_ANNUALIZED` 初值为 +5000%（过于宽松），300308 中际旭创短期暴涨导致年化 +483.6% 的极端外推，组合年化失真。
2. **LLM 输出质量无防线**：仅靠 Prompt 约束，`lines[:6]` 硬截断导致 DeepSeek 返回的操作建议全被丢弃，阶段三 LLM 决策链路**形式上通、实质上空转**。
3. **Python 3.8 兼容性无完整指南**：`refactoring-standards.md` 仅覆盖 `tuple[list[dict]]` 单案例，`list[str]` 等 8 种常见 PEP 585 类型无对照参考。

---

## 2. 归档成果清单

| # | 文档路径 | 类型 | 页数估算 | 核心沉淀 |
|---|---|---|---|---|
| 1 | `cairn/returns-calibration-standards.md` | 🆕 新建专题 | ~6 页 | 6 个核心参数（全部列出）+ 贝叶斯收缩公式 + 阈值截断规则 + 日志规范 + 边界场景 |
| 2 | `cairn/llm-output-quality-standards.md` | 🆕 新建专题 | ~8 页 | Prompt 5 要素模板 + 28 词黑名单 + 25 词白名单 + 智能截断流程 + 4 项验收指标 + 3 条踩坑记录 |
| 3 | `cairn/refactoring-standards.md#L297-L323` | ✏️ 更新第 9.2 节 | 新增 2 页 | 完整 PEP 585 对照表（8 种类型映射）+ 两种解决方案选择建议 + 方案 A/B 代码示例 + hedge_analyzer.py 实际修复实例 |
| 4 | `cairn/LOG.md#L5-L10` | ✏️ 追加日志 | 1 条 | 经验归档摘要记录（含 docs 总结报告指针） |

**文件点击直达**：
- [returns-calibration-standards.md](file:///e:/各种PY程序/28-终极量化交易系统8.4/cairn/returns-calibration-standards.md)
- [llm-output-quality-standards.md](file:///e:/各种PY程序/28-终极量化交易系统8.4/cairn/llm-output-quality-standards.md)
- [refactoring-standards.md#L297-L323](file:///e:/各种PY程序/28-终极量化交易系统8.4/cairn/refactoring-standards.md#L297-L323)
- [LOG.md#L5-L10](file:///e:/各种PY程序/28-终极量化交易系统8.4/cairn/LOG.md#L5-L10)

---

## 3. 关键经验摘要

### 3.1 年化收益校准核心参数

| 参数 | 值 | 物理含义 |
|---|---|---|
| `MAX_ANNUALIZED` | **2.0** (+200%) | A 股个股年度最大可重复收益率 |
| `MIN_ANNUALIZED` | **-0.99** (-99%) | 个股最大年度跌幅 |
| `BAYESIAN_PRIOR` | **0.15** (+15%) | A 股长期权益收益率中枢 |
| `SHRINK_THRESHOLD` | **±0.5** (±50%) | 触发收缩的年化绝对值阈值 |
| `SAMPLE_PERIOD_THRESHOLD` | **2.0 年** | 短周期判定阈值 |
| `MAX_SHRINK_WEIGHT` | **0.7** (70%) | 最大收缩强度（保留 30% 原始信号） |

**收缩公式**：
```
shrink_weight = max(0, min(0.7, 1 - years / 2.0))
annualized = annualized * (1 - shrink_weight) + 0.15 * shrink_weight
```

**300308 实测效果**：原始年化 +483.6% → 贝叶斯收缩 +271.0% → 超出 +200% 阈值 SKIP（权重=0，不影响组合）。

---

### 3.2 LLM 输出质量双层防御

```
Prompt 层 (第 1 道)
  ├─ 身份 + 任务 + 【反描述化禁令】+ 关键词约束 + 格式
  └─ 反描述化禁令前置 + 关键词与下游解析器对齐
        ↓
输出解析层 (第 2 道)  ← 仅靠 Prompt 不够
  ├─ 描述行过滤器（28 词黑名单）
  ├─ 操作建议识别器（7 类 25 词白名单）
  └─ 智能截断：操作建议优先排序 + 不足补其他
```

**质量验收标准**：
- 描述行过滤率 ≥ 95%
- 操作建议保留率 ≥ 90%
- 最终结果操作建议占比 ≥ 80%
- 下游解析器命中率 ≥ 70%

---

### 3.3 Python 3.8 PEP 585 兼容对照

| PEP 585 (Py3.9+) | `typing` 模块 (Py3.8 兼容) |
|---|---|
| `list[str]` | `List[str]` |
| `dict[str, int]` | `Dict[str, int]` |
| `tuple[list[dict], int]` | `Tuple[List[Dict], int]` |
| `set[str]` | `Set[str]` |
| `frozenset[str]` | `FrozenSet[str]` |
| `type[MyClass]` | `Type[MyClass]` |

**选择建议**：少量注解用方案 A（显式替换，改动小）；大量注解用方案 B（`from __future__ import annotations`，一次 import 全解决）。

#### 3.3.1 方案 A 代码示例（显式替换）

**修复前**（PEP 585，Py3.9+ 语法，Py3.8 报错）：

```python
# ✗ Python 3.8: TypeError: 'type' object is not subscriptable

def _generate_if_contract_codes() -> list[str]:
    codes: list[str] = []
    for month in ["03", "06", "09", "12"]:
        codes.append(f"IF{month}")
    return codes

def _fetch_if_close_from_sina(
    futures_price: float,
    if_codes: list[str]
) -> float:
    ...
```

**修复后**（`typing` 模块，Py3.8 兼容）：

```python
# ✓ Python 3.8 正常运行

from typing import List

def _generate_if_contract_codes() -> List[str]:
    codes: List[str] = []
    for month in ["03", "06", "09", "12"]:
        codes.append(f"IF{month}")
    return codes

def _fetch_if_close_from_sina(
    futures_price: float,
    if_codes: List[str]
) -> float:
    ...
```

#### 3.3.2 方案 B 代码示例（`__future__` 延迟求值）

**修复后**（PEP 585 语法保留，仅加一行 import）：

```python
# ✓ Python 3.8 正常运行 — 所有类型注解延迟求值为字符串

from __future__ import annotations   # 必须是文件第一行（在 docstring 之后）

def _generate_if_contract_codes() -> list[str]:   # PEP 585 语法可直接使用
    codes: list[str] = []                            # 变量注解也支持
    return codes

def process_data(items: dict[str, list[int]]) -> tuple[bool, float]:
    ...
```

> **注意**：`from __future__ import annotations` 必须放在文件**第一行**（docstring 之后、其他 import 之前），否则不生效。

#### 3.3.3 本次修复实例：`reporting/hedge_analyzer.py`

| 行号 | 修复前 | 修复后 |
|---|---|---|
| L32 | `def _generate_if_contract_codes() -> list[str]:` | `def _generate_if_contract_codes() -> List[str]:` |
| L58 | `def _fetch_if_close_from_sina(..., if_codes: list[str]) -> float:` | `def _fetch_if_close_from_sina(..., if_codes: List[str]) -> float:` |
| L72 | `def _fetch_if_close_from_provider(..., if_codes: list[str], ...) -> float:` | `def _fetch_if_close_from_provider(..., if_codes: List[str], ...) -> float:` |

文件已 `from typing import List`，故选择方案 A（显式替换，最小改动）。

---

## 4. 关联修复代码

| 文件 | 修复点 | 验证结果 |
|---|---|---|
| `v8.3_institutional/calibrate_returns_projection.py` | 贝叶斯收缩 + 阈值 + 路径 + 持仓格式 | ✅ 33 标的 0 失败，12 只收缩生效 |
| `tools/calc_realized_returns.py` | 同步 MAX_ANNUALIZED + 贝叶斯收缩 | ✅ 语法验证通过 |
| `ai/recommendation_generator.py` | Prompt 强化 + 描述过滤 + 智能截断 | ✅ 描述过滤率 100%，操作保留率 100% |
| `reporting/hedge_analyzer.py` | 3 处 `list[str]` → `List[str]` | ✅ Py38 兼容 |

---

## 5. 后续复用指引

### 5.1 新增年化收益率计算

任何新增模块需要计算年化收益率时，**必须**：
1. 引用 `cairn/returns-calibration-standards.md` 的参数与流程
2. 不允许直接使用 `MAX_ANNUALIZED > 2.0` 或跳过贝叶斯收缩
3. 日志必须包含收缩前后对比和收缩强度

### 5.2 新增 LLM 建议生成

任何新增 LLM 调用需要下游程序解析时，**必须**：
1. 使用 `cairn/llm-output-quality-standards.md` 的 Prompt 模板
2. 描述黑名单 + 操作白名单 + 智能截断流程三层缺一不可
3. 关键词必须与下游解析器共用同一份清单

### 5.3 Py38 兼容检查

任何新增模块或修改时使用类型注解，**必须**：
1. 检查目标运行环境是否支持 Python 3.8
2. 如需要 Py38 兼容，按 `refactoring-standards.md#L297-L323` 选择方案 A 或 B
3. 不允许混合使用（部分 PEP 585 + 部分 typing），保持文件内一致

---

> **归档完成时间**: 2026-08-04  
> **归档执行人**: AI 助手  
> **归档位置**: `docs/ARCHIVE_SUMMARY_20260804_经验归档_年化校准+LLM质量+Py38兼容.md`
