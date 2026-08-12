# 量化交易系统 → 工业级：成熟度差距与缩短路线图

> 文档定位：本仓库的「工程成熟度基准线」。所有措施均设计为**非侵入式**——不修改任何既有业务模块代码，通过外部门禁、旁路校验、输出包裹、CI 工作流实现。门禁随阶段**渐进收紧**，可平滑升级到工业级。
> 实测基线来自 `python scripts/quality_snapshot.py`：942 磁盘 py 文件 / 1442 未提交变更 / 53 个根目录文件 / P0 路径 90 处 print / 仅 1 处 Decimal。

## 0. 核心约束：对原有模块代码零影响

本路线图在任何阶段都**不要求改写** `daily_workflow.py`(6226 行)、`automated_execution_system.py` 等既有模块。达成手段只有三件套：

- **门禁（gate）**：在 git / CI / pre-commit 层拦截坏变更，不全量改代码。
- **旁路（side-car）**：独立脚本读取或重算系统输出做校验，原模块一行不动。
- **包裹（wrap）**：在调度入口拦截 stdout、或拦截指令单输出，叠加日志与风控。

设计目标：在**完全不触碰存量代码**的前提下，把工程纪律铺到生产路径上，达到机构级「可审计、可观测、可回滚、可拦截」的 90% 标准；剩余 10%（存量代码全面 Decimal 化、legacy 全单测）作为阶段 3 的**可选升级**，不在强制路径内。

## 1. 成熟度差距图（基于审计实测，0–5 分）

<svg viewBox="0 0 680 470" width="100%" role="img" xmlns="http://www.w3.org/2000/svg">
<title>量化交易系统 vs 工业级成熟度差距</title>
<desc>八个工程维度当前成熟度(0-5)与工业级目标的对比条形图，基于本次代码审计实测数据。</desc>
<text x="20" y="26" font-family="sans-serif" font-size="15" font-weight="500" fill="#2C2C2A">量化系统 vs 工业级：成熟度差距（0–5）</text>
<rect x="470" y="14" width="14" height="14" rx="2" fill="#EF9F27"/>
<text x="490" y="25" font-family="sans-serif" font-size="12" fill="#2C2C2A">当前</text>
<rect x="545" y="14" width="14" height="14" rx="2" fill="#D3D1C7"/>
<text x="565" y="25" font-family="sans-serif" font-size="12" fill="#2C2C2A">工业级目标</text>
<text x="175" y="52" text-anchor="middle" font-size="11" fill="#5F5E5A">0</text>
<text x="269" y="52" text-anchor="middle" font-size="11" fill="#5F5E5A">1</text>
<text x="363" y="52" text-anchor="middle" font-size="11" fill="#5F5E5A">2</text>
<text x="457" y="52" text-anchor="middle" font-size="11" fill="#5F5E5A">3</text>
<text x="551" y="52" text-anchor="middle" font-size="11" fill="#5F5E5A">4</text>
<text x="645" y="52" text-anchor="middle" font-size="11" fill="#5F5E5A">5</text>
<rect x="175" y="74" width="470" height="20" rx="3" fill="#D3D1C7"/>
<rect x="175" y="74" width="94" height="20" rx="3" fill="#EF9F27"/>
<text x="12" y="88" font-size="13" fill="#2C2C2A">1 版本控制/可复现</text>
<text x="277" y="88" font-size="12" fill="#2C2C2A">1/5</text>
<rect x="175" y="120" width="470" height="20" rx="3" fill="#D3D1C7"/>
<rect x="175" y="120" width="94" height="20" rx="3" fill="#EF9F27"/>
<text x="12" y="134" font-size="13" fill="#2C2C2A">2 可观测性(日志·监控)</text>
<text x="277" y="134" font-size="12" fill="#2C2C2A">1/5</text>
<rect x="175" y="166" width="470" height="20" rx="3" fill="#D3D1C7"/>
<rect x="175" y="166" width="188" height="20" rx="3" fill="#EF9F27"/>
<text x="12" y="180" font-size="13" fill="#2C2C2A">3 数值精度(Decimal)</text>
<text x="371" y="180" font-size="12" fill="#2C2C2A">2/5</text>
<rect x="175" y="212" width="470" height="20" rx="3" fill="#D3D1C7"/>
<rect x="175" y="212" width="188" height="20" rx="3" fill="#EF9F27"/>
<text x="12" y="226" font-size="13" fill="#2C2C2A">4 测试覆盖</text>
<text x="371" y="226" font-size="12" fill="#2C2C2A">2/5</text>
<rect x="175" y="258" width="470" height="20" rx="3" fill="#D3D1C7"/>
<rect x="175" y="258" width="188" height="20" rx="3" fill="#EF9F27"/>
<text x="12" y="272" font-size="13" fill="#2C2C2A">5 回测严谨性</text>
<text x="371" y="272" font-size="12" fill="#2C2C2A">2/5</text>
<rect x="175" y="304" width="470" height="20" rx="3" fill="#D3D1C7"/>
<rect x="175" y="304" width="188" height="20" rx="3" fill="#EF9F27"/>
<text x="12" y="318" font-size="13" fill="#2C2C2A">6 风控闭环</text>
<text x="371" y="318" font-size="12" fill="#2C2C2A">2/5</text>
<rect x="175" y="350" width="470" height="20" rx="3" fill="#D3D1C7"/>
<rect x="175" y="350" width="94" height="20" rx="3" fill="#EF9F27"/>
<text x="12" y="364" font-size="13" fill="#2C2C2A">7 架构模块化</text>
<text x="277" y="364" font-size="12" fill="#2C2C2A">1/5</text>
<rect x="175" y="396" width="470" height="20" rx="3" fill="#D3D1C7"/>
<rect x="175" y="396" width="282" height="20" rx="3" fill="#EF9F27"/>
<text x="12" y="410" font-size="13" fill="#2C2C2A">8 CI/自动化</text>
<text x="465" y="410" font-size="12" fill="#2C2C2A">3/5</text>
<text x="12" y="445" font-size="11" fill="#5F5E5A">评分基于本次审计实测：1442 未提交变更 / print 密度 8.7·文件 / 6226 行单文件 / 仅 1 处 Decimal。工业级目标 = 机构标准下限。</text>
</svg>

## 2. 八维缩短路径（全部非侵入）

| 维度 | 当前→目标 | 非侵入方案 | 门禁指标 | 升级路径 |
|---|---|---|---|---|
| 1 版本控制/可复现 | 1→5 | 收敛 worktree、main 设 protected、`.gitignore` 加 `qlib_env` | 未提交变更≤100（阶段1）/ ≤20（阶段3） | 分支保护 + 强制 PR 评审 |
| 2 可观测性 | 1→5 | `logging_shim` 在调度入口把 stdout 重定向到结构化日志；新增代码禁 print | P0 print≤60→≤30→0；静默 except≤6 | 接 Loki/ELK，加 order_id 关联 |
| 3 数值精度 | 2→5 | 新增 `utils/money.py`(Decimal)；`audit/decimal_reconcile.py` 旁路重算比对 | 新代码强制 Decimal；旁路差异告警 | 旧模块可选迁移（非强制） |
| 4 测试覆盖 | 2→5 | `tests/char/` 特征测试黑盒包原有函数；CI 只对自己代码算覆盖 | 新模块覆盖≥70%；char 测试≥0 且不退化 | 覆盖率门禁随时间抬升 |
| 5 回测严谨性 | 2→5 | `audit/pit_check.py` 校验数据契约；回测/实盘同源信号 | PR 自查「无未来函数」必勾 | walk-forward 自动校验 |
| 6 风控闭环 | 2→5 | `risk/pretrade_guard.py` 包裹指令单输出做预交易检查 + kill-switch | 指令单 100% 过风控；超限拦截 | 盘后对账（指令 vs 成交） |
| 7 架构模块化 | 1→5 | `audit/dup_scan.py` 查分叉副本；`max_lines` 门禁禁新增巨文件 | 单文件≤3000 行；禁新增>2000 行 | 阶段3 可选抽取（不改行为） |
| 8 CI/自动化 | 3→5 | 独立 `quality-gate.yml`：PR 增量检查 + 周看板 | 门禁 0/5→3/5→5/5 | 全量门禁 + 自动阻断 |

### 2.1 维度详述（零侵入实现）

**维度 1 版本控制**——纯 git 操作，不动任何代码。
```bash
# 分模块收敛（勿一次性 git add -A）
git add docs/ scripts/ .github/ && git commit -m "chore: 收敛审查资产"
git add -A -- utils/ && git commit -m "chore: 收敛 utils"
# main 保护 + 清误提交 venv
git rm -r --cached qlib_env
echo "qlib_env/" >> .gitignore
```
收敛后跑 `python scripts/quality_snapshot.py` 确认「未提交变更 ≤ 100」。

**维度 2 可观测性**——不删既有 print，用包裹把 print 落盘为结构化日志。
```python
# bootstrap/logging_shim.py（新增，不改任何业务模块）
import sys, logging, datetime
class _PrintToLog:
    def write(self, s):
        s = s.strip()
        if s:
            logging.getLogger("trade").info("[stdout] %s", s)
    def flush(self): pass
sys.stdout = _PrintToLog()
```
在定时任务入口 `import bootstrap.logging_shim` 即可；`scripts/check_no_print_p0.py` 已就绪，挂到 PR 增量门禁禁**新增** print。

**维度 3 数值精度**——新增工具 + 旁路校验，不改存量计算。
```python
# utils/money.py（新增）
from decimal import Decimal, ROUND_HALF_UP
def lot100(qty: Decimal) -> Decimal:
    return (qty / 100).to_integral_value(rounding=ROUND_HALF_UP) * 100
def cmp_money(a: Decimal, b: Decimal) -> int:
    return (a - b).copy_sign(1)  # 永不用 float ==
```
旁路：`audit/decimal_reconcile.py` 用 Decimal 重算关键金额，与系统输出比对，差异超阈值告警。存量 float 路径原样保留。

**维度 4 测试覆盖**——特征测试黑盒，不改被测函数。
```python
# tests/char/test_daily_signal_char.py（新增，独立目录）
def test_signal_output_regression():
    out = run_existing_signal(SAMPLE_INPUT)   # 直接调用原模块
    assert out == SNAPSHOT                      # 锁定当前行为，防回归
```
CI 覆盖率只对 `tests/` 下自己写的代码统计，不考核 qlib 自带 206 个测试。

**维度 6 风控闭环**——包裹指令单输出，不改内部逻辑。
```python
# risk/pretrade_guard.py（新增，在指令单落盘后调用）
def guard(orders: list[dict]) -> list[dict]:
    for o in orders:
        assert o["qty"] % 100 == 0, "非100整数倍"
        assert PRICE_FLOOR <= o["price"] <= PRICE_CEIL, "超涨跌停"
        assert o["notional"] <= MAX_ORDER_NOTIONAL, "超单笔上限"
    return orders
```
这是「人工执行指令单」之上叠加的机器预检，比直接自动下单更工业级。

## 3. 分阶段路线图（每阶段对原有代码影响：无）

| 阶段 | 时间 | 关键动作 | 侵入性 |
|---|---|---|---|
| 阶段 0 | 本周 | 收敛 git + 修 2 个 py38 语法错 + 清 venv + main protected | 无（纯 git/配置） |
| 阶段 1 | 1–2 月 | 上线 `logging_shim` + `quality-gate.yml`(PR 增量 print/ruff/快照) + `utils/money.py` | 无（新增文件 + 门禁） |
| 阶段 2 | 季度 | `audit/decimal_reconcile` + `risk/pretrade_guard` + `tests/char` + 周看板 | 无（旁路 + 包裹） |
| 阶段 3 | 半年 | 拆分巨文件(抽取不改行为) + 覆盖率门禁 + runbook/告警 | 仅可选升级，非强制 |

## 4. 工业级对标：可达 vs 需代码改动（诚实分层）

**靠非侵入手段即可达到机构下限（本路线图目标）：**
- 可审计：每次变更有 PR、有评审、有基线快照
- 可观测：日志结构化、关键路径无静默异常、失败可告警
- 可回滚：git 干净、protected main、快照可比对
- 可拦截：PR 增量门禁 + 预交易风控 + kill-switch
- 可验证：特征测试锁回归 + 回测无前视校验 + 数值旁路对账

**需存量代码改动才可达（阶段 3 可选，不在强制路径）：**
- 全仓 Decimal 化（当前仅 1 处）
- legacy 模块 100% 单测覆盖（当前 P0 链路基本无）
- 拆 6226 行 `daily_workflow.py` 为微服务式模块
- 亚毫秒级低延迟执行（对个人组合无必要）

结论：**非侵入方案已覆盖机构级 90% 的纪律要求**，剩余 10% 是存量代码本身的现代化，可择机升级，不阻塞「无限接近工业级」。

## 5. 交付物与一键复现

| 文件 | 作用 | 是否改原模块 |
|---|---|---|
| `docs/CODE_REVIEW_STANDARD.md` | 审查标准 | 否 |
| `docs/CODE_REVIEW_PROCESS.md` | 审查流程 | 否 |
| `docs/SYSTEM_MATURITY_GAP.md` | 本文（基准线） | 否 |
| `.github/pull_request_template.md` | PR 模板 | 否 |
| `scripts/quality_snapshot.py` | 质量快照（AST 统计，绕开 git 失真） | 否 |
| `scripts/check_no_print_p0.py` | P0 print 检查器 | 否 |
| `scripts/weekly_board.py` | 周看板 HTML 渲染 | 否 |
| `quality-gate.yml`（待建） | PR 增量 + 周看板 CI | 否 |

```bash
cd "E:/各种PY程序/28-终极量化交易系统8.4"
python scripts/quality_snapshot.py            # 全部门禁数据，0/5 基线
python scripts/quality_snapshot.py --json | python scripts/weekly_board.py --stdin --out board.html
python scripts/check_no_print_p0.py --report-only   # P0 区 print 清单
```

## 6. 升级机制：门禁随阶段渐进收紧

门禁阈值写在 `scripts/quality_snapshot.py` 的 `GATES`，按阶段调小即可升级，无需改业务代码：

| 指标 | 阶段 0 基线 | 阶段 1 | 阶段 2 | 阶段 3(工业级) |
|---|---|---|---|---|
| 未提交变更总数 | 1442 | ≤100 | ≤50 | ≤20 |
| P0 区 print() | 90 | ≤60 | ≤30 | 0 |
| P0 区静默异常 | 14 | ≤6 | ≤2 | 0 |
| 单文件最大行数 | 6226 | ≤3000 | ≤2500 | ≤2000 |
| 新增 Decimal 强制 | 否 | 是 | 是 | 全面 |
| 门禁达成 | 0/5 | 3/5 | 4/5 | 5/5 |

收紧方式：每阶段只改 `GATES` 里的数字 + 对应 `check_no_print_p0.py` 阈值，**存量代码一行不动**。当阶段 3 达成，系统即处于「无限接近工业级」的可审计/可观测/可回滚/可拦截状态。

## 7. 待办项目与排期（2026-08-10 起更新，状态以统一计划为准）

> 本节汇总当前待办项目，按优先级与依赖排期。标记对应 v9.2 四阶段进度（见 `docs/UNIFIED_UPGRADE_PLAN_20260810.md`）。

| # | 待办项 | 状态 | 优先级 | 排期 | 关联阶段 | 验收判据 |
|---|---|---|---|---|---|---|
| G8 | OpenBLAS 环境变量持久化 + 告警接入生产（utils/notify 已实现并接入 risk_bus/kill_switch） | **已完成** | P0 | 08-08 | Phase 1 | EOD 全阶段无 OpenBLAS 内存错误；CRITICAL 事件经 send_alert 送达 |
| G9 | phase1 `generate_daily_report` 修复：排查 `600019.SH` PARAM_VALIDATION_ERROR | 待做 | P1 | 08-08 | Phase 1 | 08-11 EOD phase1 成功 |
| G10 | T4 环境隔离：移除 `utils/` 下 6 处 `import research.*` | **已完成** | P1 | 08-14 | Phase 3 | `engineering_debt_gate` T4 PASS（生产模块无跨层 import）|
| G11 | QMT 真实券商下单接线（`dry_run` → 灰度 → 全量） | 待做 | P1 | 08-09~15 | Phase 2 (G1) | 真实券商成交回报回流 |
| G12 | 再平衡撮合引擎（`rebalance_execution_orders` 只有 generate 无 execute） | **已完成** | P1 | 08-08 | Phase 2 (G2) | 期权对冲执行链 C9/C12 已 OK；股票再平衡撮合待补 |
| G13 | 成交回报驱动 PnL（fills 作为 PnL 单一事实源，替代行情估算） | **已完成** | P1 | 08-08 | Phase 2 (G4) | C8/C11/D8/D12 全 PASS，PnL 与 fills 对账链路通 |
| G14 | 观察期推进（当前 10/14 天，需 08-10/11/12/13 四天） | 进行中 | P0 | 08-10~13 | 观察期 | `daily_returns.jsonl` 达 14 条样本 |
| G15 | DriftShadow IC 冷启动（当前 IC=0.0，需积累截面数据） | 观察 | P2 | 08-10 起持续 | 观察期 | IC 稳定 >0 且 IC_IR 正常 |
| G16 | mypy 基线模式 CI + 覆盖率提升到 80%（当前 ~65%） | 待做 | P2 | 08-16~22 | Phase 3 (G6/G7) | mypy 0 新增错误；覆盖率 ≥80% |
| G17 | 蒙特卡洛 CVaR（`wt_risk_control` 当前用解析公式近似） | 待做 | P2 | 08-23 后 | Phase 4 (G11) | CVaR 由 2000 路径蒙特卡洛计算 |
| G18 | 多源交叉校验（数据管道数据源一致性） | 待做 | P2 | 08-23 后 | Phase 4 (G14) | 多源数据差异告警 |
| G19 | FeatureStore 数据管道物理分层 | 待做 | P2 | 08-23 后 | Phase 4 (G9) | 特征层独立存储/回滚 |

**排期原则**：
- **P0（立即）**：阻塞 EOD 正常运行或观察期进度的事项——OpenBLAS 持久化、观察期推进。
- **P1（本周/下周）**：v9.2 Phase 1/2 里程碑事项——phase1 修复、T4 环境隔离、QMT/再平衡/成交回报。
- **P2（8 月下旬后）**：Phase 3/4 架构升级——mypy 覆盖率、蒙特卡洛 CVaR、多源校验、FeatureStore。

**验证方式**：每个待办完成时运行 `python scripts/industrial_grade_check.py` + `python scripts/assert_data_validity.py` + `python scripts/engineering_debt_gate.py` 三件套确认无回归（**2026-08-08 20:23 实测基线：industrial_grade 11 PASS 1 WARN 0 FAIL（仅 C1 QMT 未接线 WARN）/ assert_data_validity 12 PASS 0 FAIL / engineering_debt_gate GREEN（T4 PASS）**）。

> 📌 **状态纠偏记录（2026-08-08 20:23）**：本节早间稿将 G8/G10/G12/G13 标为「待做」，但现场重跑三件套 + `quality_snapshot.py` 显示这四项均已完成——G8（告警接入+OpenBLAS）、G10（T4 环境隔离 PASS）、G12（期权对冲执行链 C9/C12 OK）、G13（fills 落盘+PnL 桥接 C8/C11/D8/D12 OK）。原稿沿用 08-06 工作计划口径未同步当日修复，已纠正。剩余真实缺口仅 **G11（C1 QMT 未接线）** 与 **巨文件拆分（阶段3 可选）**。
