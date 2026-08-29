---
type: project_topic
status: resolved
authoring_mode: ai_generated
created: 2026-08-08
updated: 2026-08-08
contains: fills-store, g2-rebalance-execution, g4-pnl-from-fills, fail-open-bridge, single-source-of-truth, windows-encoding, deliverable-generation
related:
  - cairn/LOG.md
  - cairn/data-integrity-fix-lessons-20260806.md
  - cairn/eod-operations-lessons-20260807.md
  - cairn/industrial-grade-anti-regression-framework.md
  - docs/phase1_wrapup/真实状态快照_20260808.md
  - docs/phase1_wrapup/08-20决策材料_索引.md
---

# 成交回报驱动 PnL：G2/G4 执行闭环补齐经验沉淀（2026-08-08）

> 本文沉淀 08-08 补齐 **G2（再平衡撮合后 fills 落盘）** 与 **G4（PnL 改读真实成交价）** 两项执行闭环缺口的架构判断、实现要点、踩坑与铁律。
> **状态**：代码层已补齐并端到端验证通过；G1（QMT 真实下单接线）仍按高门槛后置保留 Phase 4。

---

## 一、最重要的教训（Top 4）

### 1. "有撮合能力" ≠ "有成交回报"——断链藏在返回值被丢弃的那一行

排查前的直觉是"撮合缺失"，但实际读代码发现 `OrderRouter.process_execution_queue` **已经调用 `smart_router.execute_route` 完成了撮合**，`execution_result` 里 `filled_size` / `average_price` 都有值——**只是被塞进内存 return dict 后就丢弃了，没有任何持久化位置**。

这导致两个下游同时失血：
- G4 的 PnL 无从消费真实成交价，只能退回行情 `close` 估算
- 订单生命周期追溯在"成交"这一环彻底断链，TCA 无原始事实源

**铁律**：
- 判断执行闭环是否完整，**不能只看"有没有撮合函数"，要看"撮合结果有没有落盘"**——内存里的成交等于没有成交
- 审计断链的正确姿势：从**下游消费方倒推**（PnL 读什么？TCA 读什么？），而不是从上游生成方顺推
- 与 08-06 期权对冲断链（只生成不撮合）互补：这次是**只撮合不落盘**，两类是执行链的两种典型断法，都要单列检查项

### 2. fills 必须成为单一事实源，但接入方式要"增强"而非"替换"

补齐时有两条路：
- ❌ 改写 `pnl_calculator.calculate_pnl` 内部逻辑，让它直接读 fills
- ✅ 在**调用前**用 `augment_market_prices()` 增强传入的 `market_prices`，把有成交标的的 `close` 替换成真实成交均价，并打上 `close_source="fill"` 标记

选后者的理由：
- **不修改既有契约**——`pnl_calculator` 的输入输出完全不变，零回归风险
- **有成交的走成交、无成交的走行情**，两种来源互补而非互斥，不会因为部分标的无成交而制造新的数据空洞
- `close_source` 标记让报告能区分"实际成交价"和"行情估值"，可追溯性不丢失

**铁律**：
- 把新事实源接入老链路时，**优先在边界做增强（adapter/bridge），而不是改老模块的内脏**
- 任何被覆盖的数据都要**留来源标记**，否则下游无法判断这个数字是真成交还是估算

### 3. fail-open 是执行/报告链路的默认姿势，fail-close 会把 EOD 拖死

`FillsStore.record_fill` 落盘失败 → 只记 `logger.warning`，内存仍保留，**不抛异常打断执行队列**。
`augment_market_prices` 文件缺失/为空/解析失败 → 原样返回行情价，**不阻断 EOD 报告生成**。
`_record_fill_for_order` 整体裹 `try/except`，失败只 warning。

理由：成交落盘是**观测性设施**，不是交易决策路径。让观测失败去打断真实资金操作，是典型的本末倒置。

**铁律**：
- 区分**决策路径**（风控/下单，失败必须 fail-close 阻断）与**观测路径**（落盘/报告/归因，失败必须 fail-open 降级）
- 但 fail-open **必须留日志**——静默 `except: pass` 是 08-06 教训里排第一的坑，会让断链再次隐形

### 4. 导入即副作用的模块，`import logging` 必须在文件最顶部

`fills_store.py` 首版把 `logger = logging.getLogger(__name__)` 写在了 `import logging` 之前（因为 docstring 后先写了业务 import），触发 `NameError`。因为该模块底部有 `_store = FillsStore()` 模块级单例，**import 时就会执行**，错误在 import 阶段立刻爆炸。

**铁律**：
- 模块级单例 = import 时执行 = 任何顶层顺序错误都会在 import 阶段炸，且会连带炸掉所有 importer
- 标准库 import 一律置顶，`logger` 定义紧随其后，业务 import 放最后

---

## 二、落地实现要点

### 2.1 新增两个模块（职责单一）

| 模块 | 职责 | 关键设计 |
|------|------|---------|
| `utils/execution/fills_store.py` | 成交回报统一落盘 | 进程内单例 + `threading.Lock` 写锁；按交易日分文件 JSONL（`reports/fills/fills_{date}.jsonl`）；内存 buffer + 文件双写，读取时合并 |
| `utils/execution/fills_pnl_bridge.py` | fills → PnL 桥接 | `augment_market_prices()` 覆盖 close 并标 `close_source`；`realized_pnl()` 汇总已实现 PnL；全 fail-open |

**JSONL 而非单个 JSON 数组的理由**：追加写 O(1)、并发安全、写崩不会毁掉整个文件（只丢最后一行）、天然适合流式成交。

**记录字段**（可追溯性最小集）：
`ts / date / symbol / side / filled_qty / avg_price / broker / is_live / strategy / source / meta`
其中 `is_live` + `source`（`live_route` / `sim_route`）让实盘与模拟成交在**同一文件**内可区分——避免以后两套文件产生口径分裂。

### 2.2 接入点只有一处

`automated_execution_system.OrderRouter.process_execution_queue` 的 `if execution_result.get("success"):` 分支内调用 `self._record_fill_for_order(order, execution_result)`。

**单点接入的价值**：live 与 sim 两条路由都收敛到这一个成功分支，天然保证"所有成交都被记录"，不会出现某条路径漏记。

导入用 fail-safe 三段式（与项目既有降级风格一致）：
```python
try:
    from utils.execution.fills_store import FillsStore
    _FILLS_STORE_AVAILABLE = True
except Exception:
    FillsStore = None
    _FILLS_STORE_AVAILABLE = False
```

### 2.3 有效性守门

`_record_fill_for_order` 内做前置校验：`symbol` 非空 且 `filled_qty > 0` 且 `avg_price > 0` 才记录。防止把"成功但零成交"的假成交污染事实源——这类记录会让 `realized_pnl` 和 TCA 产生错误分母。

---

## 三、踩坑清单（可复用）

| # | 坑 | 现象 | 解法 |
|---|----|------|------|
| 1 | `logger` 在 `import logging` 之前 | 模块单例导致 import 阶段 `NameError` | 标准库 import 置顶 |
| 2 | `self.report_date` 不存在 | `generate_daily_report.calculate_pnl` 中 `AttributeError` | 该文件用的是**全局 `REPORT_DATE`**，非实例属性 |
| 3 | PowerShell 中文路径 + `&` 解析失败 | 命令无法执行 | 用 `cmd /c "cd /d e:\... && ..."` 包装 |
| 4 | `qlib_env` 缺 openpyxl 且 pip 代理不通 | 无法生成 xlsx | 改用系统 `python3`（自带 openpyxl） |
| 5 | python-pptx 不可用 | 无法生成 pptx | 改用 `node` + `pptxgenjs`（npm 可联网） |
| 6 | pptx 输出路径写成 `docs/docs/...` | 文件落到错误位置 | fileName 用单层 `docs/` |
| 7 | LibreOffice/soffice 缺失，无法转图视检 | 无法视觉验收交付物 | 用 `python3 zipfile` 校验 slide 数量、无占位符残留、关键文本存在 |
| 8 | P0 根目录文件裸 `print()` | pre-commit T201 门禁阻断 | 统一改 `logging`；`generate_daily_report.py` 补 `import logging` + 模块 `logger` |

> 坑 2 与坑 8 同属一类元教训：**改老文件前必须先读该文件的局部约定**（用全局常量还是实例属性、有没有 logger、是不是 P0 门禁文件），照搬其他文件的写法必错。

---

## 四、验证方法（端到端而非单函数）

1. **模块自检**：`python -m utils.execution.fills_store` 写一条模拟成交并读回，确认文件生成与 `latest_avg_price_by_symbol` 命中。
2. **落盘验证**：构造 `OrderRouter` 走完整 `process_execution_queue`，确认 `reports/fills/fills_{date}.jsonl` 出现记录（验证的是**真实接入点**，不是直接调 `record_fill`）。
3. **覆盖验证**：`augment_market_prices({"600519": {"close": 1700.0}})` → close 变为 1680.5 且 `close_source == "fill"`。数值可见地变化，比"无报错"强得多。
4. **回退验证**：删掉 fills 文件后重跑，确认 PnL 正常走行情估算、不报错。
5. **清理**：删除验证产生的临时 fills 文件与 `node_modules` / `package.json`，避免污染仓库。

**铁律**：验证必须打到**真实接入点**并观察**可见的数值变化**；"跑通了没报错"是最弱的验证形式。参考 08-07 教训——手动验证 ≠ 端到端验证，理想情况还应观察一个完整 EOD 日确认无回归。

---

## 五、与既有知识的衔接

- **08-06 期权断链**（只生成不撮合）+ **08-08 fills 断链**（只撮合不落盘）→ 执行链有两个独立断点位置，`industrial_grade_check.py` 的 C1 判据应同时覆盖"有无撮合"与"有无成交落盘"。
- **08-06 dry-run 陷阱**（TCA 有记录但 JSON 不落盘）与本次同源：**日志有记录 ≠ 事实源已持久化**。判断闭环只认落盘产物。
- **fail-open + 必留日志** 与 08-06 的"静默失败是头号敌人"一致：降级可以，静默不行。

---

## 六、遗留与后续

| 项 | 状态 | 说明 |
|----|------|------|
| G2 fills 落盘 | ✅ 代码层已补齐（08-08 验证） | 待一个真实 EOD 日观察端到端产出 |
| G4 PnL 读 fills | ✅ 代码层已补齐（08-08 验证） | 有成交标的走成交价，无成交走行情，互补 |
| G1 QMT 真实下单接线 | ⏸ 后置 Phase 4 | 高门槛项，涉及真实资金，保持 `dry_run` |
| `realized_pnl` 配对逻辑 | ⚠️ 简化 FIFO 近似 | 仅供日常监控参考，**不作会计级成本基础**；若要用于正式归因需替换为严格 FIFO/移动加权成本 |
| fills 与 TCA 归因打通 | 📌 待办 | fills 已是可用事实源，TCA 可改为消费 fills 而非各自记录 |

---

## 七、一句话结论

**执行闭环的完整性判据不是"能不能撮合"，而是"成交有没有变成可追溯的落盘事实源，并被下游 PnL/TCA 真正消费"**；接入新事实源时用边界桥接 + 来源标记 + fail-open 降级，可以在零回归的前提下把断链补上。

<!-- AUTO-GENERATED: 相关文档 -->
## 相关文档

- [DTE-1 建仓接入 FillsStore 事实源 (2026-08-24)](dte1-build-fills-store-20260824.md) (相似度 22%)
- [Agent-Skills 集成与适配（2026-08-24）](agent-skills-integration.md) (相似度 20%)
- [数据修复经验沉淀：EOD 管道数据断链诊断与修复（2026-08-06）](data-integrity-fix-lessons-20260806.md) (相似度 19%)
- [P3.0 影子账户闭环门禁 (2026-08-26)](p3-0-gate.md) (相似度 15%)
- [代码审查质量门禁经验沉淀：为什么多次审查仍有 bug](code-review-quality-gate-lessons-20260811.md) (相似度 14%)

<!-- 由 scripts/cairn_cross_ref.py 自动生成，请勿手动编辑此区块 -->
