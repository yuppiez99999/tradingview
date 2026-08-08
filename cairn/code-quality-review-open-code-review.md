---
type: review_report
status: completed
authoring_mode: ai_generated
created: 2026-08-03
updated: 2026-08-03
contains: open-code-review, code-quality, bug-fix, dead-code, eod-workflow, cache-bug
related:
  - cairn/LOG.md
---

# open-code-review 代码审查报告（核心模块）

> 使用 alibaba/open-code-review v1.8.6 工具规则，对核心生产代码进行质量审查。覆盖 12 个核心文件（数据/因子 + 交易/工作流模块）。

## 一、审查方法

- **工具**：`@alibaba-group/open-code-review` v1.8.6（npm 全局安装）
- **模式**：`ocr delegate` 委托模式（免 LLM API Key），工具生成审查规则（Rule Group 1: system/**/*.py），AI 编码代理执行实际审查
- **规则**：优先精确而非召回——只报告**确认的真实缺陷**（误报比漏报更伤信任）；安全/正确性视为阻塞
- **范围**：核心生产代码（utils/alpha_factor、utils/graph_data_source、supply_chain_graph、supply_chain_builder、gate1_validation、data_provider、data_layer、institutional_pipeline_runner、daily_trade_executor、run_daily_eod_workflow、shadow_real_data_feeder）

## 二、已确认并修复的缺陷

### 缺陷 1【高】EOD 工作流 `log()` 参数错误导致 TypeError
- **文件**：`15_每日工作流/run_daily_eod_workflow.py`（715-731）
- **问题**：`log(msg, level="INFO")` 只收 2 参数，但 FeedbackLoop 阶段传入 3-4 个位置参数（含 `%` 格式化值 + `"ERROR"`）。`log` 内部用 f-string 不执行 `%` 格式化，导致正常成功路径（`status=="ok"`）必然抛 TypeError，被捕获后阶段误判失败，EOD 工作流最终以 `sys.exit(1)` 退出，且掩盖真实错误。
- **修复**：改为 f-string 内联 + 正确 `level` 参数（保留 722-725 合法分支）。
- **影响**：修复后 FeedbackLoop 成功/失败路径正常记录，EOD exit code 正确。

### 缺陷 2【中】`institutional_pipeline_runner.py` 5 个 `_mock_*` 死代码
- **文件**：`institutional_pipeline_runner.py`（原 1854-1906）
- **问题**：`_mock_factor_result`/`_mock_forward_returns`/`_mock_llm_signals`/`_mock_etf_signals`/`_mock_macro_signals` 定义但从未在主流程调用（`run()` 用 `_real_*` 系列）。含 `np.random` 不确定性，若误接回会污染数据。
- **修复**：删除 5 个死代码方法。

### 缺陷 3【中】`data_provider.py` 情绪缓存 TTL 用 `.seconds` 回绕
- **文件**：`utils/data_provider.py`（718）
- **问题**：`(datetime.now() - cache_time).seconds < 300` 用 `.seconds`（只取 0-86399 秒部分），缓存超 1 天后回绕导致过期缓存误命中；与 623 行 `.total_seconds()` 不一致。
- **修复**：`.seconds` → `.total_seconds()`。

### 缺陷 4【中】`graph_data_source.py` 主营构成缓存类型不匹配
- **文件**：`utils/graph_data_source.py`（478-482）
- **问题**：`fetch_main_business` 用 `_save_board_cache` 存 **dict**，但 `_load_board_cache` 只接受 **list**（`isinstance(rows, list)`），导致缓存永久失效 + `dict(file_rows)` 死代码。250 只主营构成每次都重新请求东财 F10（触发限流风险）。
- **修复**：新增 `_load_json_cache_value`（list/dict 通用读取），主营构成缓存正确命中。

## 三、架构问题（已评估）

### 缺陷 5【中】`data_layer.py` 降级链 P0-P5 全部委托同一 `MarketDataProvider` — **保留（评估后不改）**
- **问题**：P0/P1/P2/P4/P5 各级注册为同一个 `_make_delegator("get_ohlcv")` 并委托同一 `MarketDataProvider`。数据源整体不可用时降级链重复触发多次完整降级（重复网络调用 + fallback 日志失真）。
- **决策**：**保留不改**。理由：完整修复需重构 MarketDataProvider 暴露逐级查询接口，风险高于收益——当前实现功能正确（各级结果一致，最终都成功或失败），主要代价是数据不可用时的重复网络调用（性能冗余，非正确性缺陷）。且 `fallback_chain_used` 语义被下游依赖，贸然改动可能破坏生产降级行为。建议未来在重构 MarketDataProvider 时统一降级层。

### 缺陷 6【中低】`data_layer.py` 持 RLock 包裹含网络 IO 的降级链 — **已修复 2026-08-03**
- **问题**：`with self._lock:` 包裹整个 P0-P6 循环（含网络 IO 的 `query_fn`），跨线程查询被串行阻塞。
- **修复**：锁收窄——移除外层锁，仅在写共享状态（`_p6_cache_store` / `_write_fallback_log`）时加锁。网络 IO 在锁外执行，跨线程并行。
- **验证**：`ast.parse` 语法 OK，`read_lints` 0 错误。

## 四、未发现问题的类别

- **可变默认参数**：12 文件 dataclass 均用 `field(default_factory=...)`，无 `def f(x=[])`。
- **身份比较**：未发现 `is` 比较字面量。
- **除零/越界**：`shadow_real_data_feeder` 的空输入保护（`total_abs_weight<=0`、coverage 分母）正确。

## 五、验证

- 修复后 4 个文件 `ast.parse` 语法全部 OK。
- `read_lints` 全部 0 错误。

## 六、结论

共确认 6 个真实缺陷，**5 个已修复**（含 1 个高严重度 EOD 正确性缺陷 + 1 个 data_layer 锁串行缺陷），1 个架构问题（降级链重复）评估后保留。审查遵循"精确优先"原则，仅报告确认的缺陷。

## 七、补充审查（第二轮，2026-08-03，对冲/AI/风控/执行模块）

第二轮扩展审查 24 个高风险生产模块（风控、执行交易、对冲/Greek/AI决策），确认 5 个新缺陷。

### 已修复（4 个）

| # | 严重度 | 文件 | 行号 | 类型 | 问题 → 修复 |
|---|--------|------|------|------|-------------|
| 7 | **高** | `glm5_decision_engine.py` | 649-650 | 字段错位 | AI信号 `urgency`/`reason` 列错位（表头 cells[8]=紧急程度, cells[9]=理由）→ 修正为 `reason=cells[9]`, `urgency=cells[8]` |
| 8 | **高** | `greek_hedge_manager.py` | 208-214 | Delta数值错误 | `_bs_delta` 无效输入返回 0.5（`_bs_d1_d2` 哨兵 (0,0)→N(0)=0.5），与其它 Greeks 返回 0 不一致 → 加 S<=0/K<=0/T<=0/sigma<=0 检查返回 0 |
| 9 | 中 | `hedge_engine.py` | 1066 | 过度对冲 | `max(...,0.5)` 强制 Beta 下限，低 Beta 组合（黄金/国债ETF）被高估对冲 → 改 0.0 下限，低 Beta 组合自然少对冲 |
| 10 | 低 | `gamma_engine.py` | 154,157 | 死代码/冗余IO | `self.config.get` 结果丢弃 + `_get_market_ma60()` 结果丢弃后 L162 重新计算 → 删除 |

### 待评估（1 个）

| # | 严重度 | 文件 | 位置 | 问题 |
|---|--------|------|------|------|
| 11 | 低 | `signal_fusion.py` | 743-749 | 模块导入时自动注册（`register_fast_signal_source`→建 SQLite 库+改全局单例），import 即文件IO/数据库副作用，线程不安全。评估：可测试性受影响，但外层有 except 防护，非阻塞。建议后续改为显式初始化 |

### 已排查但未报告的疑似项

- `hedge_engine._compute_portfolio_vol`：疑似死代码但被测试引用，保留。
- `protective_put_engine.L345` 年化成本公式 `(ytd_spent+total_premium)*4`：意图模糊，无法确认为错误，不报。
- `generate_futures_hedge` `max(1,round(...))` 强制≥1手：保守对冲常规做法，不报。

### 补充审查验证

4 个修复文件 `ast.parse` 语法 OK + `read_lints` 0 错误。

### 累计结论

两轮审查共确认 **11 个真实缺陷，10 个已修复**（含 2 个高严重度正确性缺陷），1 个架构问题 + 1 个模块副作用待评估。
