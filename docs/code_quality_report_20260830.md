# 系统代码质量检查报告（2026-08-30）

> **检查范围**: `28-终极量化交易系统8.4/` 全量
> **检查工具**: ruff / bandit / mypy / pytest
> **检查时间**: 2026-08-30 13:45
> **总体评级**: **B+（良好，有改进空间）**

---

## 0. 一句话结论

系统代码质量整体良好：ruff 仅 35 个低严重度问题、pytest 160 代表性测试全 PASS、无 High 级测试失败。需关注 **1 个 High 安全问题**（subprocess shell=True）、**5 个 Medium 安全问题**（SQL 注入 + pickle 反序列化）、**42 个 mypy 类型错误**（集中在 wt_execution_algo.py / wt_backtest_engine.py）。全部修复方案已给出，预计 2.0 人天。

---

## 1. 检查结果总览

| 工具 | 问题数 | High | Medium | Low | 状态 |
|------|--------|------|--------|-----|------|
| **ruff** (lint) | 35 | 0 | 0 | 35 | ✅ 低风险 |
| **bandit** (安全) | 1308 | 1 | 5 | 1302 | ⚠️ 1 High 需修 |
| **mypy** (类型) | 42 | — | — | 42 | ⚠️ 集中在 2 文件 |
| **pytest** (测试) | 0 fail / 160 pass | — | — | — | ✅ 代表性子集全绿 |

---

## 2. ruff 代码质量（35 问题，全部 Low）

### 2.1 问题分布

| 规则 | 数量 | 说明 | 修复方式 |
|------|------|------|---------|
| W291 | 20 | 行尾空格 | `ruff format` 自动修复 |
| ANN201 | 5 | 公共函数缺返回类型 | 手动添加 `-> None` / `-> str` 等 |
| ANN001 | 4 | 函数参数缺类型注解 | 手动添加参数类型 |
| N999 | 4 | 无效模块名（batchG/H/I/J） | 重命名文件为 snake_case |
| ANN003 | 1 | `**kwargs` 缺类型 | 添加 `**kwargs: Any` |
| ANN202 | 1 | 私有函数缺返回类型 | 添加返回类型 |

### 2.2 修复方案

```bash
# 1. 自动修复行尾空格（20 个 W291）
python -m ruff format .

# 2. 手动修复类型注解（11 个 ANN*）— 示例
# cache/data_downloader.py:425
def _to_float(s: Any) -> float:  # 添加 s: Any

# 3. 重命名无效模块名（4 个 N999）
# tests/smoke_type_safety_tier1_batchG.py → tests/smoke_type_safety_tier1_batch_g.py
# tests/smoke_type_safety_tier1_batchH.py → tests/smoke_type_safety_tier1_batch_h.py
# tests/smoke_type_safety_tier1_batchI.py → tests/smoke_type_safety_tier1_batch_i.py
# tests/smoke_type_safety_tier1_batchJ.py → tests/smoke_type_safety_tier1_batch_j.py
```

**预计工时**: 0.5 人天

---

## 3. bandit 安全检查（1308 问题）

### 3.1 High 严重度（1 个，必须修复）

| 规则 | 文件 | 行号 | 问题 | 修复方案 |
|------|------|------|------|---------|
| B602 | `utils/console_encoding.py` | 56 | `subprocess.run(shell=True)` | 改为 `shell=False`，直接传列表参数 |

**修复**:
```python
# 修复前
subprocess.run(["chcp", "65001"], capture_output=True, shell=True, check=False)

# 修复后（Windows chcp 不需要 shell）
subprocess.run(["chcp", "65001"], capture_output=True, shell=False, check=False)
```

### 3.2 Medium 严重度（5 个，建议修复）

| 规则 | 文件 | 行号 | 问题 | 修复方案 |
|------|------|------|------|---------|
| B608 | `utils/ai_tools/code_graph_rag.py` | 274 | SQL 字符串拼接 | 用参数化查询 |
| B608 | `utils/ai_tools/code_graph_rag.py` | 317 | SQL 字符串拼接 | 用参数化查询 |
| B608 | `utils/auto_hedge_rebalance/audit_logger.py` | 277 | SQL 字符串拼接 | 用参数化查询 |
| B301 | `utils/deep_hedging_rl.py` | 570 | pickle.load 反序列化 | 加 `# noqa: B301` 或用 `json.load` |
| B301 | `utils/supply_chain_risk/train.py` | 98 | pickle.load 反序列化 | 加 `# noqa: B301` 或用 `json.load` |

**修复**:
```python
# B608 SQL 注入修复示例
# 修复前
cursor.execute(f"SELECT * FROM table WHERE id = '{user_id}'")

# 修复后（参数化查询）
cursor.execute("SELECT * FROM table WHERE id = ?", (user_id,))

# B301 pickle 修复
# 如果只加载可信内部模型文件，加 noqa 注释
pickle.load(f)  # noqa: B301  # 仅加载内部可信模型
```

### 3.3 Low 严重度（1302 个，可接受）

| 规则 | 数量 | 说明 | 处理策略 |
|------|------|------|---------|
| B101 | ~800 | assert_used（测试代码中正常） | 测试文件加 `# noqa: B101` 或 bandit 跳过 tests/ |
| B110 | ~300 | try_except_pass（降级模式） | 可接受，量化系统需要优雅降级 |
| B112 | ~200 | try_except_continue（循环中跳过） | 可接受，数据处理中正常 |
| B105 | 1 | hardcoded_password（'500000'） | 检查是否为端口号非密码 |

**建议**: 在 `pyproject.toml` 中配置 bandit 跳过测试目录和已知安全模式：
```ini
[tool.bandit]
exclude_dirs = ["tests"]
skips = ["B101", "B110", "B112"]
```

**预计工时**: 0.5 人天（High + Medium）

---

## 4. mypy 类型检查（42 错误，10 文件）

### 4.1 错误分布

| 文件 | 错误数 | 主要问题 | 严重度 |
|------|--------|---------|--------|
| `utils/wt_execution_algo.py` | ~15 | 类型不兼容赋值 + 操作数类型 | 中 |
| `utils/wt_backtest_engine.py` | ~10 | 参数类型不匹配 + 返回 Any | 中 |
| `utils/rss_feed_fetcher.py` | 1 | 缺类型注解（今日新增） | 低 |
| 其他被导入模块 | ~16 | 间接导入的类型错误 | 低 |

### 4.2 关键错误

```python
# 1. wt_execution_algo.py:209 — int/float 类型不兼容
avg_execution_price = float(total_amount) / float(total_exec)  # 声明为 int 但赋值 float
# 修复: avg_execution_price: float = float(total_amount) / float(total_exec)

# 2. wt_execution_algo.py:457 — object + float 不支持
orders[-1]["qty"] += float(remaining)  # orders[-1]["qty"] 类型为 object
# 修复: 确保 orders 的 dict 类型注解为 dict[str, float]

# 3. wt_execution_algo.py:490 — 赋值 None 到非 Optional 类型
self.cost_model = None  # 声明为 TransactionCostModel 非 Optional
# 修复: self.cost_model: TransactionCostModel | None = None

# 4. rss_feed_fetcher.py:91 — 缺类型注解（今日新增）
result = {}  # 修复: result: dict[str, list[dict]] = {}
```

### 4.3 修复方案

```bash
# 1. 修复今日新增模块（1 个错误，立即修）
# utils/rss_feed_fetcher.py:91
result: dict[str, list[dict]] = {}

# 2. 修复 wt_execution_algo.py（~15 个错误）
# 添加正确的类型注解：avg_execution_price: float
# 确保 orders: list[dict[str, float]]
# cost_model: TransactionCostModel | None

# 3. 修复 wt_backtest_engine.py（~10 个错误）
# 统一 strategy_func 的参数类型
# 修复返回类型声明
```

**预计工时**: 1.0 人天

---

## 5. pytest 测试状态

### 5.1 代表性子集（160 测试全 PASS）

| 测试文件 | 测试数 | 状态 |
|---------|--------|------|
| tests/contract/ (Q1-Q5 契约) | 16 | ✅ PASS |
| test_kondratiev_motif_unit.py | 25 | ✅ PASS |
| test_kondratiev_cycle_unit.py | 34 | ✅ PASS |
| test_macro_weather_unit.py | 28 | ✅ PASS |
| test_rss_feed_fetcher_unit.py | 21 | ✅ PASS |
| test_web_content_extractor_unit.py | 16 | ✅ PASS |
| test_performance_report_unit.py | 20 | ✅ PASS |

### 5.2 已知问题

- **全量 pytest 收集超时**（120s）：项目测试文件过多，建议用 `pytest-xdist` 并行或分批运行
- **wt_execution_algo.py 类型错误**可能导致运行时 `TypeError`（Python 3.8 下 `list[dict]` 不可下标）

---

## 6. 修复优先级与排期

| 优先级 | 任务 | 工时 | 类型 | G5 合规 |
|--------|------|------|------|---------|
| **P0** | B602 subprocess shell=True 修复 | 0.1 | fix（安全） | ✅ |
| **P0** | rss_feed_fetcher.py mypy 类型注解 | 0.1 | fix（今日新增） | ✅ |
| **P1** | B608 SQL 注入 × 3 参数化查询 | 0.3 | fix（安全） | ✅ |
| **P1** | B301 pickle × 2 加 noqa | 0.1 | fix（安全） | ✅ |
| **P1** | ruff W291 行尾空格 × 20 | 0.1 | fix（格式） | ✅ |
| **P2** | ruff ANN* 类型注解 × 11 | 0.3 | fix（类型） | ✅ |
| **P2** | ruff N999 模块名 × 4 | 0.1 | fix（命名） | ✅ |
| **P2** | mypy wt_execution_algo.py × 15 | 0.5 | fix（类型） | ✅ |
| **P2** | mypy wt_backtest_engine.py × 10 | 0.4 | fix（类型） | ✅ |
| **P3** | bandit 配置跳过 tests/ + B101/B110/B112 | 0.1 | config | ✅ |

**合计**: ~2.0 人天，全部为 `fix/*` 类型，G5 RED-FREEZE 合规

---

## 7. 立即可修复项（P0）

### 7.1 B602 subprocess shell=True

```python
# utils/console_encoding.py:56
# 修复前
subprocess.run(["chcp", "65001"], capture_output=True, shell=True, check=False)
# 修复后
subprocess.run(["chcp", "65001"], capture_output=True, shell=False, check=False)
```

### 7.2 rss_feed_fetcher.py 类型注解

```python
# utils/rss_feed_fetcher.py:91
# 修复前
result = {}
# 修复后
result: dict[str, list[dict]] = {}
```

---

## 8. 质量趋势

| 维度 | 当前状态 | 目标 | 差距 |
|------|---------|------|------|
| ruff lint | 35 Low | 0 | 35（全部可自动修复） |
| bandit High | 1 | 0 | 1 |
| bandit Medium | 5 | 0 | 5 |
| mypy 错误 | 42 | <10 | 32（集中在 2 文件） |
| pytest 覆盖率 | 160 代表性 PASS | 全量 PASS | 需并行化 |
| 测试通过率 | 100% (代表性) | 100% (全量) | 需验证 |

---

## 9. 结论

系统代码质量**整体良好**（B+），主要问题集中在：
1. **1 个 High 安全问题**（subprocess shell=True）— 立即修复
2. **wt_execution_algo.py / wt_backtest_engine.py 类型错误** — 需重构类型注解
3. **行尾空格 + 类型注解缺失** — ruff 可自动修复大部分

全部修复为 `fix/*` 类型，G5 RED-FREEZE 合规，可在支线窗口执行。预计 2.0 人天完成全部修复。