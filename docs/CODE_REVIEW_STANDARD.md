# 代码审查标准 v1.0

> 适用范围：本仓库自研 Python 代码（磁盘实存 942 个文件，含 158 个测试文件）
> 制定依据：2026-08-08 全量扫描 + 逐项人工验证，非通用模板
> 配套文档：`docs/CODE_REVIEW_PROCESS.md`（流程与门禁）
> 数据复现：`python scripts/quality_snapshot.py`

---

## 0. 诊断结论

先摆事实。以下数据全部来自实际扫描并逐条验证过，可用上述命令一键复现。

### 0.1 被推翻的假设（方法论说明）

制定标准前做了四次假设，**三次被数据推翻**。记录在此，因为这些弯路本身就是
审查方法论的一部分：

| 初始假设 | 验证结果 | 真相 |
|---|:---:|---|
| 7 处 F821 未定义变量 = 运行时崩溃 | ❌ | 均在 `from __future__ import annotations`（`daily_workflow.py:27`）或字符串注解保护下，不求值 |
| 盲目异常密度：根目录是 `utils/` 的 144 倍 | ❌ | 按"静默处理率"重算，各区接近：根目录 25%、`utils/` 22% |
| 差异来自 `# noqa` 抑制刷门禁 | ❌ | 全仓 `noqa: BLE001` 数量为 **0**，无人钻空子 |
| 全仓零 `Decimal` | ❌ | 有 1 处且用对了地方：`utils/price_limit_calculator.py`（涨跌停计算） |

**方法论**：静态工具告警是线索，不是结论。误报会消耗团队信任，
信任透支后真问题也会被当噪声忽略。**定级前必须验证。**

还有一个坑值得记下来：最初用 `git ls-files` 取数，得到 1109 个文件。
实际磁盘上只有 942 个——**git 索引里有 466 个已删除文件的幽灵条目**。
这直接引出了下面这个最严重的问题。

### 0.2 🔴 最严重问题：工作区与版本控制脱节

```
已删除未提交: 710    已修改未提交: 392    未跟踪: 340
未提交变更合计: 1442
```

**这是所有问题里最要命的一个，因为它让代码审查在物理上无法进行。**

代码审查的前提是"存在一个可审查的变更单元"。当 1442 个变更游离在版本控制之外：

- PR 的 diff 无法反映真实改动
- 出问题时无法定位是哪次变更引入的
- 回滚没有可靠的锚点——实盘系统出事时这是致命的
- 任何门禁都可以被"不提交"轻易绕过

**在把这 1442 个变更收敛进版本控制之前，其余所有流程设计都是纸上谈兵。**
这也是 `docs/CODE_REVIEW_PROCESS.md` 落地清单的第一项。

### 0.3 🔴 核心质量问题：可观测性断层

按磁盘实存文件统计（`python scripts/quality_snapshot.py`）：

| 分区 | 文件 | `print()` | **密度** | `.exception()` | `.error()` | 静默异常 |
|---|---:|---:|---:|---:|---:|---:|
| **`utils/`**（有门禁） | 323 | 55 | **0.2** | 51 | 298 | 84 |
| `ui/` | 29 | 0 | **0.0** | 2 | 14 | 27 |
| `ai_decision/` | 15 | 2 | 0.1 | 0 | 10 | 0 |
| **根目录**（P0 所在） | **53** | **461** | **8.7** | 15 | 88 | 50 |
| `scripts/` | 108 | 1288 | 11.9 | 25 | 34 | 2 |
| `research/` | 30 | 343 | 11.4 | 0 | 17 | 0 |
| `tools/` | 16 | 251 | 15.7 | 0 | 5 | 24 |
| **`v8.3_institutional/`** | 5 | 65 | 13.0 | **0** | 63 | 28 |

**① `print()` 密度差 43 倍** —— `utils/` 0.2/文件 vs 根目录 8.7/文件。

根目录是生产交易入口所在（`daily_trade_executor.py`、`live_scheduler.py`、
`stop_loss_monitor.py`…），且 `live_scheduler.py` 由定时任务驱动。
**定时任务里 `print()` 的输出走 stdout，不进日志文件。**
交易日出故障，排查时翻不到任何记录——这不是风格问题，是**事故现场没有监控录像**。

P0 路径实测：14 个文件、**90 处 `print()`**、14 处静默异常、仅 9 处 `.exception()`。

**② `v8.3_institutional/` 的 `.exception()` 为 0** —— 63 处 `.error()`、
28 处静默异常，**没有任何一处记录堆栈**。`logger.error(f"失败: {e}")` 只留一行消息，
丢掉整个 traceback。出问题时你知道"失败了"，不知道"在哪一层失败的"。
该区含 6226 行的 `daily_workflow.py`，正是最需要堆栈定位的地方。

**③ 差异根因是门禁覆盖，不是人的水平** —— `utils/` 是唯一被 CI 覆盖的区：

```yaml
# .github/workflows/ci.yml:98
python -m mypy --config-file mypy.ini utils/
# .github/workflows/ci.yml:104
python -m pylint --rcfile=.pylintrc utils/infra/ utils/risk/ utils/execution/ utils/data/
```

同一个仓库、同一批人，有门禁的区 `print()` 密度 0.2，没门禁的区 8.7。
**问题在流程，不在人。** 这个结论是乐观的：团队已经证明了自己能写出 `utils/`
那种水准的代码，把门禁铺过去，质量会自然向标杆看齐。

### 0.4 已验证为真的问题清单

| # | 问题 | 证据 | 定级 |
|:--:|---|---|:---:|
| 1 | **1442 个变更游离在版本控制外** | 710 删除 + 392 修改 + 340 未跟踪未提交 | 🔴 |
| 2 | P0 交易入口零静态门禁 | 根目录 53 个文件不在任何 mypy/pylint 范围 | 🔴 |
| 3 | P0 区 90 处 `print()` | 定时任务场景输出丢失，故障无法追溯 | 🔴 |
| 4 | `v8.3_institutional/` 零堆栈记录 | `.exception()` = 0，`.error()` = 63 | 🔴 |
| 5 | py38 语法错误，文件无法运行 | `apply_ocr_fixes.py:150`、`scripts/_pip_noproxy.py:39` 用了 py3.12 f-string 语法，而 `ruff.toml:5` 声明 `target-version = "py38"` | 🔴 |
| 6 | 虚拟环境二进制进 git | `git ls-files qlib_env` = 13 个，含 `pip.exe`、`dotenv.exe` | 🟡 |
| 7 | 6226 行上帝文件 | `v8.3_institutional/daily_workflow.py` | 🟡 |
| 8 | 金额计算基本未用 `Decimal` | 全仓仅 `utils/price_limit_calculator.py` 一处 | 🟡 |
| 9 | 无可信覆盖率基线 | `coverage.xml`（08-06 本地快照）25 个包仅 3 个有数据，总行覆盖 1.2%，与 CI `--cov-fail-under=60` 矛盾 | 🟡 |

### 0.5 做得好的地方（明确保持）

审查不只是挑毛病，好东西要说出来才不会在重构中被弄丢：

- ✅ **CI 是真阻断**：mypy/pylint 已移除 `continue-on-error`（`ci.yml:95,102`），不是摆设
- ✅ **`utils/` 是标杆**：323 个文件，`print()` 密度 0.2，51 处 `.exception()` 规范记录堆栈；
  `ui/` 更是 29 个文件零 `print()`
- ✅ **异常标注有纪律**：根目录 95 处 `# noqa` 带原因说明，如
  `daily_trade_executor.py:684` 的 `# notify fail-open, 不阻断交易`——有意为之且写清楚了
- ✅ **`Decimal` 用在了刀刃上**：`utils/price_limit_calculator.py` 处理涨跌停价，
  正是最不能有浮点误差的地方
- ✅ **对冲取整方向保守**：`hedge_quantity_calculator.py:99,160` 用 `int()` 截断，
  宁可欠对冲不超对冲——判断是对的
- ✅ **无密钥泄露**：`.env` 未被 git 跟踪
- ✅ **已有未来函数防护**：`ci_lookahead_guard.py` + `tests/test_audit_lookahead_minunit.py`
- ✅ **已建知识图谱工具**：`code-review-graph`（tree-sitter + SQLite），支持影响半径分析
- ✅ **测试基建有雏形**：158 个测试文件

---

## 1. 缺陷分级定义

审查意见必须带优先级，避免"所有问题看起来一样重要"。

| 标记 | 含义 | 处置 |
|:---:|---|---|
| 🔴 **Blocker** | 会导致资金损失、错误交易、数据损坏、静默失败 | **禁止合并** |
| 🟡 **Suggestion** | 影响可维护性、可测试性、性能，或缺少测试 | 应在本 PR 修复；确有理由可开 issue 跟踪 |
| 💭 **Nit** | 命名、注释、风格偏好 | 可选，不阻塞 |
| ✅ **Praise** | 好的设计、巧妙的解法 | 明确说出来 |

**规则**：一次审查给完整意见，不分轮挤牙膏。

---

## 2. 通用审查清单

### 2.1 🔴 Blocker

- [ ] **`print()` 出现在生产代码**：交易/调度/风控路径一律用 `logger`，`print()` 只允许出现在 CLI 工具的用户交互输出
- [ ] **异常不记堆栈**：`except Exception as e: logger.error(f"{e}")` 丢失 traceback，改用 `logger.exception("上下文说明")`
- [ ] **异常吞噬**：`except` 后只有 `pass`/`return None` 且无注释说明
- [ ] **裸 except**：`except:` 会吞掉 `KeyboardInterrupt`/`SystemExit`，一律禁止
- [ ] **py38 兼容性**：禁止 3.9+ 语法（f-string 内反斜杠、引号复用、`dict | dict`、裸 `list[int]`）
- [ ] **硬编码密钥**：API key / token / 密码 / 资金账号必须走 `os.environ`
- [ ] **可变默认参数**：`def f(x=[])`
- [ ] **资源泄漏**：文件/连接/锁未用 `with`

### 2.2 🟡 Suggestion

- [ ] 单文件 > 800 行 → 拆分
- [ ] 单函数 > 80 行 或圈复杂度 > 15 → 拆分
- [ ] 新增公开函数缺类型注解
- [ ] 新增分支逻辑无测试
- [ ] 重复代码 ≥3 次 → 提取
- [ ] 循环内 I/O 或重复计算可外提

---

## 3. 量化交易专项清单（本项目核心）

通用清单管不到的地方，才是这个系统真正会亏钱的地方。

### 3.1 金额与价格精度 🟡→🔴

**现状**：全仓 `Decimal` 仅 1 处（`utils/price_limit_calculator.py` 涨跌停计算，用对了地方），金额、市值、成交额均为 float。

客观讲，float64 有 15-16 位有效数字，单次计算 A 股 2 位小数价格不会出错。
**真实风险在两处**：

1. **累加漂移** —— 组合市值、累计盈亏、分批建仓成本反复累加后与券商对账不平
2. **取整方向** —— `int()` 截断 vs `round()` 四舍五入，结果不同

已定位的取整点：

```python
# hedge_quantity_calculator.py:99
n_contracts = int(value_to_hedge / beta_adjusted_notional) if beta_adjusted_notional > 0 else 0
# hedge_quantity_calculator.py:160
n_options = int(vol_budget / (option_price_est * contract_size))
```

用 `int()` 截断在对冲场景是**安全的**——宁可欠对冲不可超对冲。
但**没有注释说明这是有意为之**，下一个人很可能"顺手优化成 `round()`"，
导致超额对冲、放大反向敞口。

**规则**：

- [ ] 🔴 下单股数/手数取整必须显式注明方向与理由
- [ ] 🔴 A 股买入必须 100 股整数倍：`qty = int(cash / price / 100) * 100`
- [ ] 🔴 现金/可用资金比较禁用 `==`，用 `abs(a-b) < 0.01`
- [ ] 🟡 **累加型**金额（组合市值、累计盈亏）建议迁 `Decimal`（路线图 P2）
- [ ] 🟡 金额落盘前统一 `round(x, 2)`，避免 `13.999999999` 入库

### 3.2 订单正确性 🔴

**重要前提（已核实）**：本系统**不直接对接券商 API**。
搜索 `easytrader|xtquant|miniqmt|trade_api|broker` 无匹配，无 `place_order`/`submit_order`
定义。系统产出订单指令（`hedge_execution_orders.py:merge_orders`），由人工执行。

这**显著改变风险定级**：

- 订单幂等 / 重复下单 → 🟡 **降级**（人工执行是天然闸门，重启不会自动重复报单）
- 计算结果正确性 → 🔴 **升级**（算错了人会照着下单，无程序侧兜底）

**规则**：

- [ ] 🔴 订单**方向**（buy/sell）、**标的代码**、**数量**必须有单元测试
- [ ] 🔴 `merge_orders` 必须测试：同标的反向单能否正确净额化
- [ ] 🔴 订单总金额不得超过可用资金——需断言校验
- [ ] 🟡 订单文件带生成时间戳与策略版本号，便于事后归因
- [ ] ⚠️ **若未来接入自动下单 API，本节全部升 🔴，并必须补幂等键**

### 3.3 回测未来函数 🔴

已有 `ci_lookahead_guard.py` 和 `tests/test_audit_lookahead_minunit.py`，基础不错。

- [ ] 🔴 新增因子/信号必须纳入 `ci_lookahead_guard` 覆盖
- [ ] 🔴 `shift()` 方向：T 日数据算的信号只能 **T+1** 成交
- [ ] 🔴 禁止用当日 `close` 生成信号又用当日 `close` 成交
- [ ] 🔴 滚动统计禁止用全样本计算后回填
- [ ] 🟡 标的池是否有幸存者偏差（用今天的成分股回测历史）
- [ ] 🟡 涨跌停、停牌、ST 状态下的成交假设

### 3.4 风控与实盘可观测性 🔴

- [ ] 🔴 止损逻辑（`stop_loss_monitor.py`、`vol_adjusted_stop_loss.py`）变更必须有测试
- [ ] 🔴 单笔/单日最大下单金额需有硬上限
- [ ] 🔴 数据源返回空/异常时，策略是否会误判为"清仓信号"
- [ ] 🔴 **定时任务（`live_scheduler.py`）的错误输出必须进日志文件，不能是 `print()`**
- [ ] 🔴 因子计算失败必须记录，不得静默返回 `None`（见 §6 实例）
- [ ] 🟡 交易日历是否正确处理节假日与临时休市

### 3.5 数据契约 🟡

- [ ] 🟡 外部数据源（akshare/tushare）字段变更是否有 schema 校验
- [ ] 🟡 复权方式（前/后/不复权）全链路一致
- [ ] 🟡 时区与交易时段边界

---

## 4. 分区差异化标准

一刀切会让治理停滞。按代码性质分区。

| 分区 | 目录 | 强度 | 门禁 |
|---|---|:---:|---|
| **P0 生产交易** | 根目录交易文件、`utils/execution/`、`utils/risk/` | 最严 | mypy strict + pylint + 覆盖率 ≥70% + 双人审查 |
| **P1 核心业务** | `utils/` 其余、`v8.3_institutional/src/` | 严 | mypy + pylint + 覆盖率 ≥60% + 单人审查 |
| **P2 支撑工具** | `scripts/`、`tools/`、`ui/` | 中 | ruff + 单人审查 |
| **P3 研究探索** | `research/`、`lgb_trainer/`、notebook | 宽 | ruff（仅 F/E 类），不阻塞 |
| **P4 归档** | `_archive/`、`backups/` | 豁免 | 排除，只读 |
| **P5 第三方** | `.venv/`、`qlib/`、`qlib_env/`、`external/` | 豁免 | 必须从扫描与 git 排除 |

**P0 清单（需纳入门禁的根目录文件）**：

```
alpha_hedge_engine.py          build_plan_executor.py
daily_trade_executor.py        hedge_execution_orders.py
hedge_quantity_calculator.py   live_scheduler.py
rebalance_execution_orders.py  signal_monitor.py
signal_post_processing.py      stop_loss_monitor.py
today_hedge_decision.py        vol_adjusted_stop_loss.py
daily_build_and_hedge.py       run_daily_eod.py
institutional_pipeline_runner.py
```

---

## 5. 量化门禁指标

门禁必须是**数字**，"代码质量要好"没法执行。

| 指标 | 当前实测 | 阶段1（2周） | 阶段2（1月） | 阶段3（1季） |
|---|:---:|:---:|:---:|:---:|
| **未提交变更总数** | **1442** | **≤100** | ≤30 | ≤30 |
| py38 语法错误 | **2** | **0** | 0 | 0 |
| git 中 venv/二进制 | **13** | **0** | 0 | 0 |
| P0 区 `print()` | 90 | ≤60 | ≤20 | 0 |
| P0 区静默异常 | 14 | ≤6 | ≤2 | 0 |
| 根目录 `print()` 密度 | 8.7/文件 | ≤6 | ≤2 | ≤0.5（向 `utils/` 看齐） |
| `v8.3/` `.exception()` | **0** | ≥10 | ≥25 | 覆盖全部 except |
| P0 区纳入 mypy | 否 | 是（允许 warning） | 零 error | strict |
| P0 区覆盖率 | 无基线 | 建立基线 | ≥50% | ≥70% |
| 全仓覆盖率 | 无可信数据 | 建立可信基线 | ≥40% | ≥60% |
| 单文件最大行数 | 6226 | ≤3000 | ≤1500 | ≤800 |

> **阶段1 有三项是硬要求**：收敛未提交变更、修 2 个语法错误、清 13 个二进制文件。
> 后两项合计半天工作量，第一项是所有后续流程的前提，没有理由拖延。

一键查看达成情况：

```bash
python scripts/quality_snapshot.py
```

---

## 6. 审查意见书写规范

### 标准格式（真实案例）

```
🔴 **静默失败：因子计算异常被吞，信号静默失效**

`build_plan_executor.py:381`

```python
    score = max(-1.0, min(1.0, 1.0 - float(value) * 1e8))
    return round(float(score), 4)
except Exception:
    return None
```

**为什么**：`alpha144` 计算失败时静默返回 `None`，没有任何日志。
调用方拿到 `None` 后，该标的可能被静默剔除出候选池，或按缺省值打分。
结果是：**策略行为改变了，但没有任何记录**。
在量化系统里，信号静默失效比程序报错危险得多——报错你会立刻知道，
静默失效可能持续数周才在归因分析时被发现。

**建议**：
```python
except Exception:
    logger.exception("alpha144 计算失败 symbol=%s，该标的本次跳过", symbol)
    return None
```
`logger.exception` 会自动带上 traceback，定位成本从"翻代码猜"降到"看日志"。
```

### 三条硬规则

1. **必须给位置**：`文件:行号`，不接受"某个地方有问题"
2. **必须讲后果**：说清会导致什么，不是"这样不好"
3. **用建议句式**：「建议 X，因为 Y」而非「改成 X」——🔴 除外

### 反面示例

| ❌ 不要这样 | ✅ 应该这样 |
|---|---|
| "异常处理有问题" | "🔴 `build_plan_executor.py:381` 因子异常静默返回 None，标的会被无声剔除，建议加 `logger.exception`" |
| "这函数太长" | "🟡 `daily_workflow.py` 6226 行，建议延续已有的 `phases/` 拆分模式，优先拆出 `_generate_mock_ohlcv`（:4525）等独立职责" |
| "加点测试" | "🟡 `merge_orders` 反向单净额化无测试，建议补：同标的 buy 100 + sell 300 应合并为 sell 200" |
| "别用 print" | "🔴 `live_scheduler.py` 由定时任务驱动，`print()` 输出不进日志文件，交易日故障将无迹可查，建议全量替换为 `logger`" |

---

## 7. 与现有工具集成

项目已有工具，直接用，不要重复造：

| 工具 | 用途 | 调用 |
|---|---|---|
| `code-review-graph` | 变更影响半径、调用链、死代码 | `detect_changes_tool` / `get_impact_radius_tool` |
| `review-changes` skill | 结构化审查流程 | `.claude/skills/review-changes/SKILL.md` |
| `ci_lookahead_guard.py` | 未来函数检测 | CI 自动 |
| ruff | 快速静态扫描 | `.venv/Scripts/ruff.exe check <path>` |

**推荐动线**：
`detect_changes_tool`（风险评分）→ `get_impact_radius_tool`（影响面）
→ 高风险函数查 `tests_for` → 按本文清单核对 → 出具分级意见

---

## 附录：结论可复现

```bash
cd "E:/各种PY程序/28-终极量化交易系统8.4"

# 日志规范三区对比（本标准的核心证据）
python -c "
import subprocess,os,re
def gl(p): return [f for f in subprocess.run(['git','ls-files',p],capture_output=True,text=True).stdout.split('\n') if f]
for z,fs in {'根目录':[f for f in gl('*.py') if '/' not in f],'utils/':gl('utils/*.py'),'v8.3/':gl('v8.3_institutional/*.py')}.items():
    ex=er=pr=0
    for f in fs:
        if not os.path.exists(f): continue
        t=open(f,encoding='utf-8',errors='ignore').read()
        ex+=len(re.findall(r'\.exception\(',t)); er+=len(re.findall(r'\.error\(',t)); pr+=len(re.findall(r'^\s*print\(',t,re.M))
    print(f'{z:<12} exception={ex:<5} error={er:<5} print={pr}')
"

# py38 语法错误
.venv/Scripts/ruff.exe check . --no-cache --exclude .venv --exclude qlib_env \
  --exclude qlib --exclude external --exclude _archive --select F821 --output-format concise

# git 污染
git ls-files qlib_env

# Decimal 使用
git ls-files "*.py" | grep -v "_archive\|qlib" | tr '\n' '\0' | xargs -0 grep -lE "from decimal import" | wc -l
```

---

**版本** v1.0 ｜ **制定** 2026-08-08 ｜ **下次复审** 阶段1 结束后
