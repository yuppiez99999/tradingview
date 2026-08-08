---
type: project_topic
status: active
authoring_mode: ai_generated
created: 2026-08-08
updated: 2026-08-08
related:
  - cairn/refactoring-standards.md
  - cairn/ROADMAP.md
---

# daily_workflow.py 拆分计划（门禁 ≤3000 行 · 长期架构重构）

> 本文档沉淀自 2026-08-08 P0 治理收尾。6226 行的 `v8.3_institutional/daily_workflow.py` 超出架构门禁（单文件 ≤3000 行），属**长期架构重构**，单独排期，**不纳入紧急 P0 治理**。
> 核心约束：**零行为变更**（见 `cairn/refactoring-standards.md` §1），且**必须在周末/非交易时段**执行（§1.2）。

## 1. 现状基线

| 项 | 值 |
|----|----|
| 文件 | `v8.3_institutional/daily_workflow.py` |
| 规模 | **6226 行**（门禁 ≤3000，超约 2.1×） |
| 顶层结构 | `WorkflowConfig`(L315) + `DailyWorkflow`(L416) + `main()`(L6118) |
| 方法数 | 65+ 个 `def`（其中 `DailyWorkflow` 类约 63 个方法/属性） |
| 入口 | `main()` → `DailyWorkflow(...).run(only_phase=...)` |
| CLI phases | 14 个可独立执行阶段（见 §3 phase 注册表） |
| 调用方 | 统一入口 `量化策略系统_统一入口_v8.6.py`、EOD 定时任务 `run_v84_postmarket.ps1` |

**注意**：`cli/modes/daily_workflow.py`（3.2KB）是**另一个文件**——它只是 `daily_trading_workflow.py` 三阶段（盘前/盘中/盘后）的薄封装，与 6226 行文件**无关**，不属本次拆分范围。

## 2. 拆分原则（源自 refactoring-standards.md）

1. **零行为变更底线**：拆分前后 `run()` 各 phase 的输入/输出/副作用（写文件、推送告警、退出码）必须 bit-for-bit 一致。
2. **按 phase 切分，而非按行数硬切**：`DailyWorkflow` 的每个 `phase_*` 方法是一个天然边界，对应 CLI 阶段。
3. **共享状态通过构造器/上下文传递**：`WorkflowConfig`、`self.state`、`self.phase_manager`、`logger`、`BASE_DIR` 等是跨 phase 共享的"上下文对象"，拆出后须以**显式参数或共享上下文对象**传递，禁止全局可变单例。
4. **保留降级语义**：现有 `except Exception` 多为 fail-safe 降级（如 `phase_signal` 的 iFinD 研判失败返回 `{}`），拆分时不得改变异常流向。
5. **不在交易时间做**：所有结构性改动在周末执行，留足回归测试窗口。

## 3. phase 注册表（run() L6067-L6082）

```
PHASE_SEQUENCE = [
    ("check",            self.phase_check),             # L1011 系统自检
    ("calibrate",        self.phase_calibrate),         # L1095 收益预测校准
    ("market",           self.phase_market),            # L1178 市场状态/熔断
    ("risk",             self.phase_risk),               # L1310 风险预算(Kelly/风险平价)
    ("hedge",            self.phase_hedge),              # L1713 Beta/Vol 对冲
    ("hedge_fund",       self.phase_hedge_fund),         # L2196 对冲基金视角融合
    ("v10_risk",         self.phase_v10_risk),           # L2335 回撤+VaR+压测
    ("quant_neutral",    self.phase_quant_neutral),      # L2597 量化中性+IC对冲
    ("cash_management",  self.phase_cash_management),    # L2808 现金管理+逆回购
    ("directional_futures", self.phase_directional_futures), # L2968 方向性期货
    ("signal",           self.phase_signal),             # L3242 信号生成(神华建仓+Alpha)
    ("execute",          lambda: self.phase_execute(...)),# 执行
    ("report",           self.phase_report),             # 盘后报告
    ("autolearn",        self.phase_autolearn),          # 自主学习训练
]
```

每个 phase 方法行数估算（峰值）：
- `phase_hedge` L1713-L2195 ≈ **482 行**（最大单 phase，含 `_compute_beta_hedge_order` L1643）
- `phase_signal` L3242-L3890 ≈ **648 行**（含 qlib/ifind/lgb 信号融合子方法群）
- `phase_hedge_fund` L2196-L2334 ≈ 138 行
- 其余 phase 多在 80-300 行区间

## 4. 建议目标结构（拆后 ≤3000 行/文件）

保持 `DailyWorkflow` 作为**编排门面（Facade）**，将 phase 实现下沉到子模块：

```
v8.3_institutional/
├── daily_workflow.py              # 门面: WorkflowConfig + DailyWorkflow.run() + main()  (~800行, 仅编排)
├── workflow/
│   ├── __init__.py
│   ├── context.py                 # WorkflowContext: 共享状态/配置/phase_manager 容器
│   ├── phases/
│   │   ├── check.py               # phase_check + _load_fusion_config/_load_external_reports
│   │   ├── calibrate.py           # phase_calibrate
│   │   ├── market.py              # phase_market + _scan_anysearch_news
│   │   ├── risk.py                # phase_risk + _infer_style_from_code/_style_beta_proxy/_get_if_realtime
│   │   ├── hedge.py               # phase_hedge + _compute_beta_hedge_order
│   │   ├── hedge_fund.py          # phase_hedge_fund
│   │   ├── v10_risk.py            # phase_v10_risk + 压测持仓加载
│   │   ├── quant_neutral.py       # phase_quant_neutral + IC 对冲
│   │   ├── cash_management.py     # phase_cash_management + 逆回购
│   │   ├── directional_futures.py # phase_directional_futures + 6 个 _load/_get/_save 子方法
│   │   ├── signal.py              # phase_signal + qlib/ifind/lgb 融合子方法群
│   │   ├── execute.py             # phase_execute
│   │   ├── report.py              # phase_report
│   │   └── autolearn.py           # phase_autolearn
│   └── registry.py                # PHASE_SEQUENCE 注册表(原 L6067-L6082)
```

**关键设计**：`DailyWorkflow` 拆为门面后，phase 方法改为 `phase_xxx(ctx: WorkflowContext)` 自由函数或 `Phase` 类方法，`ctx` 承载原 `self.state` / `self.config` / `self.phase_manager` / `logger`。门面 `run()` 仅负责：构造 `ctx` → 按 `PHASE_SEQUENCE` 顺序调用 → 收集 `state["phases"]` → 算退出码（原 L6211-L6222 逻辑）。

## 5. 执行步骤（建议分多轮，每轮一个 phase 群）

每轮遵循 `refactoring-standards.md` §6 流程：

1. **扫描定位**：`python scripts/_scan_func_quality.py` 确认目标 phase 行数/CC。
2. **提取测试基线**：搜索该 phase 的所有调用点与现有测试，记录基线（无测试则先写行为对比脚本）。
3. **建立 `WorkflowContext`**：先从 `DailyWorkflow.__init__` 抽取共享状态到 `context.py`，这是所有 phase 拆出的前置依赖。
4. **逐个 phase 搬移**：将 phase 方法 + 其私有 `_` 子方法一并移至 `phases/<name>.py`，改为接收 `ctx`。
5. **门面接线**：`DailyWorkflow` 改为 `from workflow.phases.<name> import phase_xxx`，保持 `PHASE_SEQUENCE` 不变。
6. **验证零回归**：`python -m pytest` 跑全量 + `ast.parse` 语法检查 + `read_lints`。
7. **回归 EOD**：选一个周末跑 `run_v84_postmarket.ps1` 干跑，对比 `每日报告归档/` 产物与拆分前一致。
8. **更新 LOG.md**：追加拆分条目（含降幅/测试/模式）。

**推荐顺序**（按依赖与风险）：
- 第 1 轮：低风险 leaf phase（`check` / `calibrate` / `market` / `autolearn`）→ 验证门面+ctx 模式可行
- 第 2 轮：中等 `risk` / `v10_risk` / `cash_management` / `directional_futures`
- 第 3 轮：高复杂 `hedge`（482行）+ `hedge_fund` / `quant_neutral`
- 第 4 轮：最大 `signal`（648行，含信号融合子方法群，建议内部再拆 `signal_qlib`/`signal_ifind`/`signal_lgb`）
- 第 5 轮：清理 `DailyWorkflow` 门面至 ≤3000 行，跑全量质量门禁确认达标

## 6. 风险与缓解

| 风险 | 缓解 |
|------|------|
| 共享状态遗漏（某 `_` 私有方法依赖 `self.xxx` 未进 ctx） | 搬移前用 grep 列出 phase 方法内全部 `self.` 引用，逐一确认在 ctx 中 |
| `main()` 的 AI 沙箱/复盘等旁路分支（`--ai-sandbox` 等 L6160-L6200）依赖其他模块 | 这些分支**不进** `DailyWorkflow`，保持 `main()` 内 import，不受影响 |
| 路径常量 `BASE_DIR`/`SRC_DIR`/`sys.path` 注入（L45-L49） | 拆出的 `phases/*.py` 需统一从 `context.py` 或 `core.context` 取 `BASE_DIR`，禁止各自重新 insert sys.path |
| 退出码语义（L6211-L6222：check 阶段单独允许降级通过） | 门面 `run()` 末尾逻辑原样保留，不得简化 |
| 隐性跨 phase 状态（phase 间通过 `self.state["phases"][x]` 传递） | `execute` 读 `signal` 的 state（L6079）是已知耦合点，ctx 必须保留 `state` 字典且 phase 顺序不变 |

## 7. 验收标准

- [ ] 拆分后 `daily_workflow.py`（含门面+context+registry）≤3000 行
- [ ] 每个 `phases/*.py` ≤800 行（超出则内部再拆）
- [ ] `python -m pytest` 全量 0 失败（零行为变更）
- [ ] 周末 EOD 干跑产物与拆分前一致（报告/告警/退出码）
- [ ] `scripts/_scan_func_quality.py` Strong 函数 = 0（延续 refactoring-standards §8 目标）
- [ ] 质量门禁（`industrial_grade_check` / `assert_data_validity` / `check_dangling_refs`）全过，无新增悬挂引用

## 8. 排期状态

- **状态**：待排期（长期架构重构，不阻塞 P0 治理）
- **前置**：P0 静默异常日志补齐已 DONE（2026-08-08），daily_workflow.py 的 3 处 logger.exception 已就位，为拆分提供可观测基础
- **触发条件**：P0 治理收尾后，在下一轮 Wave（建议 Wave 4 工程化达标期 09-05~10-31 内启动）或独立周末窗口执行
- **禁止**：交易时段（周一至周五 9:30-15:00 + 夜盘）执行任何结构性改动

## 9. 相关文档

- [cairn/refactoring-standards.md](file:///e:/各种PY程序/28-终极量化交易系统8.4/cairn/refactoring-standards.md) — 重构规约（零行为变更/表驱动化/提取 helper/三轴阈值）
- [cairn/ROADMAP.md](file:///e:/各种PY程序/28-终极量化交易系统8.4/cairn/ROADMAP.md) — 系统路线图（Wave 4 工程化达标期）
- [cairn/LOG.md](file:///e:/各种PY程序/28-终极量化交易系统8.4/cairn/LOG.md) — 进展日志
