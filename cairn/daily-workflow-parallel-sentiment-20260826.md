# 盘前工作流并行化 + 情感信号注入 + 12任务定时注册

> **日期**: 2026-08-26
> **状态**: 当前真相
> **LOG指针**: `cairn/LOG.md` 2026-08-26 · 盘前工作流并行化 + 情感信号注入盘前LLM决策 + 12任务定时注册

## 一、问题背景

交易日闭环存在三个缺口：
1. **盘前准备慢** — 7项晨间信息采集串行执行，总耗时 = Σ(每项耗时)，盘前08:00开始可能来不及
2. **舆情信号未接入决策** — 舆情综合日报已生成但未参与LLM盘前决策，舆情信息孤岛
3. **盘中LLM决策缺失** — `v84_IntradayLLMDecision`任务引用的脚本`v8.3_institutional/llm_intraday_decision_engine.py`不存在，DryRun显示[MISSING]

## 二、解决方案

### 2.1 两阶段并行化 (morning_info_runner.py)

**文件**: `15_每日工作流/morning_info_runner.py:794-852`

7项任务的依赖关系分析：
- 任务1-6（行情摘要/康波/ETF/舆情/CNEMC/标的研判）彼此独立
- 任务7（大宗商品基本面扫描）依赖任务1的`morning_market_data_{date}.json`和任务4的舆情日报

因此分两阶段：
- **阶段1**: `ThreadPoolExecutor(max_workers=4)` 并行执行任务1-6
- **阶段2**: 串行执行任务7（读取阶段1产出）

线程安全性保证：
- 每个任务写不同文件，无文件竞争
- `archive`目录已创建，`target_date`和`force`不可变
- I/O密集型任务在GIL下仍可从线程并行受益

### 2.2 情感信号注入 (apply_llm_decisions_to_plan.py)

**文件**: `tools/apply_llm_decisions_to_plan.py:93-181`

注入点：`apply_llm_decisions()`函数中`ai_recs = report.get("ai_recommendations", [])`之后。

流程：
1. `_load_sentiment_recs(plan_date)` 读取 `每日报告归档/{plan_date}/舆情综合日报_{date}.md`
2. 解析负面命中数（"负面命中"表格行数）、正面命中数、情绪判断文本
3. 转为`ai_recommendations`格式建议文本：
   - 负面命中 ≥5 → "建议买入Put保护510050和510300, 增加防御板块权重"
   - 负面命中 ≥3 → "建议调整板块权重, 防御板块超配, 科技板块低配"
   - 正面命中 ≥5 且负面 <3 → "可适当加仓相关标的"
   - 情绪"谨慎乐观" → "维持现有仓位和对冲结构"
4. 合并到`ai_recs`列表，复用现有7类调整解析器

关键设计：情感信号文本格式与`daily_pnl_report.ai_recommendations`一致，无需修改下游解析器。

### 2.3 盘中LLM决策引擎 (新建)

**文件**: `v8.3_institutional/llm_intraday_decision_engine.py`（新建，~120行）

被`v84_IntradayLLMDecision`任务每30分钟调用（09:30-15:30）。

流程：
1. `_fetch_market_snapshot()` — Wind MCP获取指数实时行情
2. `_load_positions()` — 读取`configs/positions.json`持仓
3. `GLM5DecisionEngine.make_decisions(scene="intraday_decision")` — 生成决策
4. 归档到`每日报告归档/YYYY-MM-DD/盘中LLM决策_HHMMSS.md`

支持三种模式：`--mode live`（实盘）/`dry-run`（预览）/`shadow`（影子）。

### 2.4 12任务定时注册

**文件**: `scripts/register_all_tasks_unified.ps1`

修改：v84_PreMarket StartTime 从 `07:00` 改为 `08:00`。

实际运行注册成功 12/12 个Windows计划任务（SYSTEM身份，周一至周五触发）。

## 三、交易日全流程时间线

| 时间 | 任务名 | 功能 |
|------|--------|------|
| 08:00 | v84_PreMarket | 盘前工作流（信息采集→校准→计划→LLM决策→报告） |
| 08:30 | v84_UniverseScan | 标的池扫描 |
| 09:00 | v84_PreMarketInstructions | 生成交易指令 |
| 09:30-15:30 | v84_IntradayLLMDecision | 盘中LLM决策（每30分钟，持续6小时） |
| 17:00 | v84_PostMarket | 盘后EOD风控链（保证金熔断→回撤→波动率→对冲→认沽保护） |
| 17:05 | v84_PostMarketExecute | 盘后执行已确认指令 |
| 17:30 | v84_DailyPnlReport | 收盘PnL报告 |
| 17:35 | v84_EvolutionEval | 策略进化评估 |
| 17:40 | v84_ObservationBriefing | 观察简报 |
| 17:45 | v84_ShadowAdmissionDaily | 影子准入DSR报告 |
| 17:50 | v84_PhaseBAuto | Phase B自动调度 |
| 18:30 | v84_ShadowAdmissionWatchdog | Shadow准入自愈watchdog |

## 四、管理命令

```bash
# 查询所有任务
schtasks /Query /FO TABLE | findstr /R "v84_"

# 立即运行某任务
schtasks /Run /TN v84_PreMarket

# 禁用任务
schtasks /Change /TN v84_IntradayLLMDecision /DISABLE

# 卸载全部
powershell -File "scripts\register_all_tasks_unified.ps1" -Uninstall

# 图形界面
taskschd.msc
```

## 五、验证

- `morning_info_runner.py` — `py_compile` 通过
- `apply_llm_decisions_to_plan.py` — `py_compile` 通过
- `llm_intraday_decision_engine.py` — `py_compile` 通过
- `register_all_tasks_unified.ps1 -DryRun` — 12/12 [OK]，0 [MISSING]
- `register_all_tasks_unified.ps1` 实际注册 — 12/12 成功

## 六、后续待办

- [ ] `morning_info_runner.py` 并行化后需实测验证线程安全性（各任务模块的import副作用）
- [ ] 情感信号注入需端到端验证：生成舆情日报 → 运行LLM决策 → 检查`llm_overrides`是否包含情感驱动的调整
- [ ] 盘中LLM决策引擎需在交易时段实测（当前仅语法验证）
- [ ] 考虑将`max_workers`参数暴露到配置文件（当前硬编码=4）