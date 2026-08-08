# 审查体系有效性复核 (CODE_REVIEW_GAP_AUDIT)

> **审查日期**：2026-08-08 14:40–15:10
> **审查性质**：独立第三方复核（不预设既有结论，全部结论附可复现证据）
> **被审对象**：`docs/CODE_REVIEW_STANDARD.md` / `PROCESS.md` / `PLAN.md` / `BACKLOG.md` / `RETRO.md`（2026-08-08 09:23–09:52 建立）+ 全仓当前代码
> **配套文档**：本文是对既有审查体系的**缺口补丁**，不替代任何既有文档

---

## 0. 一句话结论

**标准和流程不缺，缺的是"门禁覆盖面"与"存量清算"。**
既有五份文档质量良好、四道门禁确已落地；但门禁存在 **3 处结构性盲区**，导致 **6 类真实缺陷至今在库**，其中 2 项直接关联资金安全。`RETRO.md` 中"门禁全部 `[x]` 生效"的结论**成立但不充分**——门禁生效 ≠ 覆盖到位。

---

## 1. 既有体系复核：哪些是真的

先确认既有工作的有效部分，避免重复建设。

| 既有结论（来自 RETRO/STANDARD） | 复核判定 | 证据 |
|---|---|---|
| pre-commit / CI incremental-static / quality-gate / mypy P0 四道门禁已落地 | ✅ **属实** | `.github/workflows/ci.yml` L67–141、`.pre-commit-config.yaml` |
| `quant_review_lint.py` 已升 `--strict` 接入 CI | ✅ **属实** | `scripts/quant_review_lint.py`（5,419 B，08-08 09:50） |
| 全仓 `noqa: BLE001` 数量为 0，无人钻空子 | ✅ **属实** | 独立 grep 复验通过 |
| `alpha_hedge_engine.py` 防御设计到位 | ✅ **属实且优秀** | KillSwitch 熔断、NaN 守卫、fail-closed、胖手指限额、sim 默认安全模式 |
| P0 关键路径无 High/Medium 安全漏洞 | ✅ **属实** | bandit 定向扫描 P0 集合：0 High / 0 Medium / 6 Low |
| 豁免登记机制（BACKLOG §4）已建立 | ✅ **属实** | 3 条豁免均附理由与审批人，规范 |

**这套体系的设计水平在同类项目中属于上游。** 下面只谈它没盖住的部分。

---

## 2. 结构性盲区（根因层）

### 🔴 G-1　增量门禁 = 存量债务永久豁免

`ci.yml:104`：
```powershell
$files = git diff --name-only "$base" "$head" -- '*.py' | Where-Object { $_ -notmatch '^(tests?/|docs/)' }
```
pyflakes 零容忍、ruff `T201`、P0 print 阻断**全部只作用于 PR 改动文件**。

**后果**：全仓 **7,342 个 ruff 问题**、**8 个 `F821` 未定义名**永远不进审查视野。只要文件不被改动，里面的 `NameError` 就能无限期存活。

> **实测**：`ruff check . --statistics`（排除 .venv/qlib/_archive 等）
> `BLE001` 宽异常 404 ｜ `T201` print 779 ｜ `F401` 未用导入 448 ｜ `C901` 复杂度超标 100 ｜ `F821` 未定义名 8 ｜ 语法错误 3

**建议**：增量门禁保留（防新增），另加 **nightly 全量扫描 + 基线文件**。基线只允许下降，不允许上升。

---

### 🔴 G-2　P0 名单未随重构同步 —— 真正的下单引擎不在保护范围

`scripts/check_no_print_p0.py:33` 的 `P0_FILES` 共 15 个文件，**不含** `automated_execution_system.py`。

而代码已重构：

| 文件 | 行数 | 角色 | P0 门禁 | pylint job |
|---|---|---|---|---|
| `automated_execution_system.py`（根） | **70** | 仅 re-export 空壳 | ❌ 不在名单 | ❌ |
| `utils/execution/automated_execution_system.py` | **2,500** | **真正的下单引擎** | ❌ **不在名单** | ✅ 覆盖 |
| `utils/wt_backtest_engine.py` | 611 | 回测引擎 | ❌ **不在名单** | ❌ **完全盲区** |

`P0_FILES` 按 **basename** 匹配（`check_no_print_p0.py:93`），所以只要把 `automated_execution_system.py` 加进名单即可自动覆盖 `utils/execution/` 下的实现——**一行修改**。

`utils/wt_backtest_engine.py` 则同时落在 P0 名单外和 pylint 四目录（`utils/infra|risk|execution|data`）外，是**双重盲区**——G-3 的 bug 正好长在这里，不是巧合。

---

### 🟡 G-3　修复只写注释，不写回归测试

全仓大量 `# BUG-X 修复` 注释，但缺少对应回归测试锁定。

**实证代价**：2026-08-06 的《代码质量审查报告》宣称修复 7 个致命 bug，两天后因文件重构，**报告中的行号全部失效**（`automated_execution_system.py` 从 1,970 行变成 70 行；`backtest_engine.py` 从数百行变成 11 行）。结论无法验证、无法复用。

**规则建议**：修 bug 必须同时提交一个"未修复时会失败"的测试。`# BUG-X 修复` 注释后应附测试路径，例如 `# BUG-C2 修复，回归见 tests/test_hedge_orders.py::test_build_orders_arity`。

---

## 3. 在库真实缺陷（证据级）

以下均为**当前代码**实测，非引用旧报告。

### 🔴 D-1　`utils/wt_backtest_engine.py:543` 使用未导入的 `pd`
```
ruff: F821 Undefined name `pd`
grep -c "import pandas" utils/wt_backtest_engine.py  →  0
```
**风险**：回测路径抛 `NameError`。若被上层宽 `except` 吞掉，将产出**看似正常实则错误的回测业绩**——虚假 alpha 是量化系统最昂贵的 bug。
**位置**：双重门禁盲区（见 G-2），无人能发现。

### 🔴 D-2　`institutional_pipeline_runner.py:105` 兜底日志二次崩溃
`except` 分支调用 `logger.warning(...)`，但 `logger` 在 **第 162 行**才定义。
**风险**：LightGBM 导入失败时，兜底日志自身抛 `NameError`，**把真实的导入错误彻底吞掉**，排障时只看到一个无关的 `NameError`。
**讽刺点**：该文件**在** `P0_FILES` 名单内，但 P0 门禁只查 `print()`，不查未定义名。

### 🔴 D-3　`hedge_quantity_calculator.py:82-86` 硬编码期货价格
```python
IF = 3800    IC = 5500    IM = 5800     # L82-86
vix = 25                                 # L130
drawdown = 0.03                          # L212
```
**风险**：对冲手数基于**虚构行情**计算 → 对冲比例系统性错误 → **实打实的资金敞口**。
**盲区成因**：该文件在 P0 名单内，`STANDARD.md:119` 还表扬了它 `int()` 取整方向保守——但**没有任何检查项针对"硬编码行情"**。量化系统特有风险，通用 lint 抓不到。
**建议**：`quant_review_lint.py` 增加规则——P0 文件中，形如 `<合约代码/价格/vix/drawdown> = <字面量数值>` 一律阻断，须走配置或实时行情接口。

### 🔴 D-4　3 个文件在生产 Python 3.8.9 下无法编译

`ruff.toml:5` 声明 `target-version = "py38"`，系统 Python 为 3.8.9，实测：

| 文件 | 行 | 根因 |
|---|---|---|
| `apply_ocr_fixes.py` | 150 | f-string 表达式内含反斜杠 `'\\n'`（PEP 701，需 ≥3.12） |
| `scripts/_pip_noproxy.py` | 39 | f-string 内嵌同类引号 `f'...{'OK'...}'`（需 ≥3.12） |
| `cache/patch_history_growth_data.py` | 17 | **docstring 闭合后内容被重复粘贴到代码区**，中文被当标识符解析 |

`PROCESS.md:335` 已登记前两个，但 **checkbox 至今未打勾**；**第三个从未被登记**。

> `cache/patch_history_growth_data.py` 的成因值得单独警惕：docstring 在 L17 用 `"""` 闭合后，**同一段文字又被原样粘贴了一遍**。这是一次自动化/AI 批量改写留下的残骸，且**提交前连一次 `py_compile` 都没跑**。建议 pre-commit 增加最廉价的一道防线：对改动文件跑 `python -m py_compile`。

### 🔴 D-5　`realtime_monitor` 关闭 SSL 校验抓取行情
```python
# watch_my_positions.py:141  /  watch_my_universe.py:205
resp = requests.get(url, timeout=10, headers=headers, verify=False, proxies={...})
```
bandit `B501` **High / High confidence**（CWE-295）。
**风险**：中间人可**伪造行情数据**喂给持仓监控与股票池监控。对会依据行情做决策的系统，这不是普通的证书告警，而是**数据可信度问题**。
**为何漏检**：既有 bandit 扫描只覆盖 P0 集合，`realtime_monitor/` 不在其中。

### 🟡 D-6　`hedge_execution_orders.py` 双份并存、签名分歧

| 路径 | `build_orders` 签名 |
|---|---|
| `hedge_execution_orders.py:609`（根） | **5 参** |
| `ms_strategy/scripts/hedge_execution_orders.py` | **3 参** |

`P0_FILES` 按 basename 匹配，两份都会被 print 门禁扫到，但**签名分歧无人检查**。调用方 import 到哪一份取决于 `sys.path` 顺序——典型的隐性 `TypeError` 来源。

### 🟡 D-7　执行链路外层仍静默吞错

`utils/execution/automated_execution_system.py` 内层 `except` 已按 C2 修复**显式 re-raise**（复核属实，是正确修法）；但**外层仍有 `except Exception` 兜底，仅打 warning**。

这是有意的 fail-safe，设计上可接受，但当前风险是：**"对冲实际没生效"与"普通告警"在日志里长得一样**。建议此路径的兜底降级必须走独立告警通道（而非 `logger.warning`），并计入 RETRO 指标。

---

## 4. 安全扫描结论（全业务代码）

排除 `qlib_env/`（509 MB 虚拟环境，已 gitignore，其内 25 High / 263 Medium 均为第三方依赖噪音）后：

| 范围 | High | Medium | 判定 |
|---|---|---|---|
| P0 交易路径集合 | **0** | **0** | ✅ 合格 |
| 全业务代码 | **4** | ~34 | 🟡 需处置 |

4 个 High 明细：

| 位置 | 类型 | 判定 |
|---|---|---|
| `realtime_monitor/watch_my_positions.py:141` | B501 SSL 校验关闭 | 🔴 见 D-5 |
| `realtime_monitor/watch_my_universe.py:205` | B501 SSL 校验关闭 | 🔴 见 D-5 |
| `utils/console_encoding.py:51` | B602 `shell=True` | 💭 静态命令，低危，建议改 `shell=False` |
| `quant_modules/ai_hedge_fund/utils/display.py:260` | B605 `os.system("cls")` | 💭 vendored 第三方，静态字符串，可豁免登记 |

> 扫描口径务必排除 `qlib_env/`，否则 29 High 的数字会掩盖真正的 4 个。

---

## 5. 建议补入 BACKLOG 的条目

> BACKLOG 维护人为流程 owner，故此处仅提供**可直接粘贴的行**，未擅自改动 `docs/CODE_REVIEW_BACKLOG.md`。
> 当前 BACKLOG 仅 3 条（B1 巨型文件 / B2 覆盖率 / B3 QMT），与 7,342 条实测债务量级不匹配。

```markdown
| B4 | utils/wt_backtest_engine.py:543 | F821 未定义 pd，回测可能静默出错 | 高 | 模块owner | 2026-08-11 | 待修复 |
| B5 | institutional_pipeline_runner.py:105 | logger 先用后定义，兜底日志二次 NameError | 高 | 模块owner | 2026-08-11 | 待修复 |
| B6 | hedge_quantity_calculator.py:82-86,130,212 | 硬编码期货价/VIX/回撤，对冲手数失真（资金风险） | 高 | 风控owner | 2026-08-15 | 待修复 |
| B7 | realtime_monitor/watch_my_{positions,universe}.py | verify=False，行情可被 MITM 伪造 (B501 High) | 高 | 数据owner | 2026-08-15 | 待修复 |
| B8 | cache/patch_history_growth_data.py:17 | docstring 重复粘贴致语法错误（自动化改写残骸） | 中 | 模块owner | 2026-08-11 | 待修复 |
| B9 | hedge_execution_orders.py 双份 | 根目录5参 vs ms_strategy/scripts 3参，签名分歧 | 中 | 模块owner | 2026-08-20 | 待修复 |
| B10 | 全仓 ruff 7342 项（BLE001 404 / T201 779 / F401 448 / C901 100） | 存量债务无基线、增量门禁扫不到 | 中 | 流程owner | 2026-08-22 | 待修复 |
```

---

## 6. 门禁补丁清单（按性价比排序）

| # | 动作 | 成本 | 收益 | 落点 |
|---|---|---|---|---|
| 1 | `P0_FILES` 增加 `automated_execution_system.py`、`wt_backtest_engine.py`、`backtest_engine.py` | **1 行** | 关闭 G-2，覆盖 2,500 行下单引擎 | `scripts/check_no_print_p0.py:33` |
| 2 | pre-commit 增加 `python -m py_compile` 检查改动文件 | ~5 行 | 杜绝 D-4 类语法错误入库 | `.pre-commit-config.yaml` |
| 3 | CI 增量检查的 ruff 规则集加入 `F821` | 1 处 | 未定义名零新增 | `ci.yml` |
| 4 | 新增 nightly 全量 ruff + 基线文件，只降不升 | ~20 行 | 关闭 G-1，存量开始收敛 | `.github/workflows/` |
| 5 | `quant_review_lint.py` 增加"P0 文件硬编码行情"规则 | ~30 行 | 关闭 D-3 类量化专属风险 | `scripts/quant_review_lint.py` |
| 6 | bandit 扫描口径固化：排除 `qlib_env/`，纳入 `realtime_monitor/` | 1 行 | 4 个 High 不再被噪音淹没 | CI / `bandit.yaml` |
| 7 | 修 bug 必带回归测试，注释需引用测试路径 | 流程约定 | 关闭 G-3 | `STANDARD.md` |

**第 1、2 项建议今天就做**——合计不到 10 行改动，直接关闭两个最大盲区。

---

## 7. 对 RETRO 指标的补充建议

`RETRO.md §1` 现有 6 项指标全部围绕 print / 文件行数 / 覆盖率，建议增补 3 项**直接反映资金安全**的指标：

| 指标 | 口径 | 目标 |
|---|---|---|
| P0 路径 `F821` 数 | 全量扫描，非增量 | 恒为 0 |
| P0 路径硬编码行情项数 | `quant_review_lint` 新规则输出 | 恒为 0 |
| 全仓 ruff 基线值 | nightly 全量，与上期对比 | 单调递减 |

---

**版本** v1.0 ｜ **复核人** 独立代码审查 ｜ **日期** 2026-08-08
**建议下次复核** 门禁补丁 1–4 落地后
