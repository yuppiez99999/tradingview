# IV Rank 自适应 ETF 期权 collar 组合改造

> 日期：2026-09-06  
> 类型：策略能力 / 回测增强  
> 状态：代码完成，默认关闭，待回测压测后评估启用

---

## 1. 背景与目标

v9.0 ETF+期权保护系统的核心矛盾：单一静态 collar 参数无法同时适配低波动（IV 便宜，应加深保护、保留上行）与高波动（IV 昂贵，应收窄 put/call 距离、降低权利金成本）环境。

本次改造引入 **IV Rank 自适应层**：用 510050 现货 K 线滚动 20 日已实现波动率（RV）的 252 日分位数作为 IV Rank proxy，按低/中/高三档动态覆盖 collar 的 put/call OTM 与 DTE 参数，实现"低波动多保护、高波动控成本"的自适应目标。

---

## 2. 设计要点

### 2.1 数据层：`utils/alpha/iv_rank.py`

- `IVRankProvider.fetch_iv_rank()` 返回 0-100 的整数分位数，或 `None`（全失败时回退静态参数）。
- RV proxy 标的：`510050.SH`，滚动窗口 20 交易日，年化因子 √252。
- 三级降级链（fail-open）：
  1. Wind MCP kline → 滚动 RV → percentile
  2. `output/shadow_account/shadow_state.json` daily_return（只读）→ 滚动 RV
  3. `reports/volatility/iv_rank_cache.json` history 段
- 缓存按 TTL 刷新，历史滚动上限 400 条。

> 真实期权 IV 数据源接入时，只需替换 `IVRankProvider` 内部实现；消费方 `iv_adaptive` / `combo_orchestrator` 不变。

### 2.2 解析层：`utils/etf_option_combo/iv_adaptive.py`

- `resolve_adaptive_params(iv_rank, iv_adaptive_cfg, static_params)`：纯函数，生产与回测共用。
- 分档规则：左闭右开，按 `max_rank` 升序匹配第一档。
  - `low`：IV Rank < 30
  - `mid`：30 ≤ IV Rank < 70
  - `high`：≥ 70
- 合法覆盖字段与区间硬约束：`put_otm_pct`/`call_otm_pct` (2%-8%)、`dte_min`/`dte_max` (10-180 天) 等。
- 任何异常（数据缺失、配置非法、未命中档）均回退静态参数，**绝不阻断建仓**。

### 2.3 引擎层

- `collar.py`：`generate()` 覆写，接收 `iv_adaptive` 配置，按当前 IV Rank 解析生效参数后构造 collar。
- `combo_base.py`：`ComboResult` 新增 `meta` 字段，记录实际生效 tier、iv_rank、生效参数，便于回测归因。
- `combo_orchestrator.py`：装载时 `validate_iv_adaptive_config()` fail-fast；运行时 fetch IV Rank 注入 `market_state["iv_rank"]`，异常旁路。
- `combo_risk_manager.py`：类型注解小修（`kwargs: Any`），与本次改动无行为影响。

### 2.4 回测层：`combo_backtest.py`

- 参数化 `run_backtest` / `_simulate_collar`，支持传入 `collar_resolved` 生效参数。
- 新增 `run_comparison(static_params, adaptive_params, ...)` 入口，输出静态 vs 自适应 collar 的对比结果。
- BS 定价保留默认 sigma=0.20；tier 参数差异通过 put/call 行权价与期限体现。

### 2.5 配置

`config/etf_option_combo.yaml` 新增 `iv_adaptive` 段：

```yaml
iv_adaptive:
  enabled: false          # 默认关闭，待 run_comparison 验证后评估启用
  underlying: "510050.SH"
  lookback_days: 252
  min_history_days: 60
  method: "percentile"
  rv_window: 20
  cache_ttl_seconds: 300
  tiers:
    - name: "low"
      max_rank: 30
      params:
        put_otm_pct: 0.03
        call_otm_pct: 0.07
        dte_min: 15
        dte_max: 55
    - name: "mid"
      max_rank: 70
      params: {}
    - name: "high"
      max_rank: 101
      params:
        put_otm_pct: 0.07
        call_otm_pct: 0.03
        put_otm_max: 0.12
        dte_min: 45
        dte_max: 90
```

---

## 3. 验证结果

- **新增测试**：
  - `tests/unit/test_iv_rank.py`：IVRankProvider 降级链、缓存、非法值处理。
  - `tests/test_etf_option_combo/test_iv_adaptive.py`：分档解析、配置校验、fail-open。
  - `tests/test_etf_option_combo/test_combo_backtest_iv_adaptive.py`：combo_backtest 参数化与对比入口。
- **测试结果**：`pytest tests/unit/test_iv_rank.py tests/test_etf_option_combo/test_iv_adaptive.py tests/test_etf_option_combo/test_combo_backtest_iv_adaptive.py -q` → **84 passed**。
- **回归测试**：旧 126 个 ETF option combo 相关测试零修改全绿。
- **Lint**：target 文件 `ruff check` 全通过。

---

## 4. 已知限制与下一步

1. **IV Rank 是 RV proxy，非真实期权隐含波动率**：`utils/option_data_fetcher.py` 的 Wind 期权链接口尚未实现，`option_cache` 为空。生产启用前应接入真实期权链或至少用历史 IV 数据校准 proxy 偏差。
2. **BS 定价仍为理论值**：默认 sigma=0.20，未反映 bid-ask、期限结构、IV skew。
3. **默认关闭**：`iv_adaptive.enabled=false`，需通过 `combo_backtest.run_comparison` 在长样本上验证"自适应优于静态"后才能启用。
4. **真实资金启用前**：应纳入影子账户运行，比较 adaptive vs static collar 的净成本与保护效果。

---

## 5. 指针文件

- `utils/alpha/iv_rank.py`
- `utils/etf_option_combo/iv_adaptive.py`
- `utils/etf_option_combo/collar.py`
- `utils/etf_option_combo/combo_base.py`
- `utils/etf_option_combo/combo_orchestrator.py`
- `utils/etf_option_combo/combo_backtest.py`
- `config/etf_option_combo.yaml`
- `tests/unit/test_iv_rank.py`
- `tests/test_etf_option_combo/test_iv_adaptive.py`
- `tests/test_etf_option_combo/test_combo_backtest_iv_adaptive.py`
