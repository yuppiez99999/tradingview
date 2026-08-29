# 代码审查明细 — data 模块 (2026-08-24)

> 审查对象: `ms_strategy/src/data/` (1 文件: qmt_data_feed.py)
> 审查批次: 全量量化逐模块审查计划 (plan: quant-system-module-audit-20260824) 第 5/10 模块
> 重点: 行情数据新鲜度(Q4 stale)/价格有效性/降级链

## 0. 门禁基线

| 门禁 | 结果 |
|---|---|
| ruff 静态扫描 | ✅ All checks passed |
| xtquant 状态 | 未安装 (XTDATA_AVAILABLE=False), QmtDataFeed 受门控 |

## 1. 缺陷清单与修复状态

| ID | 严重度 | 文件 | 缺陷 | 状态 |
|---|---|---|---|---|
| DT-1 | MEDIUM | qmt_data_feed.py | `get_price` 无时间戳校验，行情断流后静默返回陈旧价 (违反 Q4) | ✅ 已修复(max_age stale) |
| DT-2 | MEDIUM | qmt_data_feed.py | `_on_tick` 缓存 lastPrice<=0 或缺失为真实价 0.0，消费方误判 | ✅ 已修复(过滤无效价) |
| DT-3 | LOW | qmt_data_feed.py | connect() 同步下载全部标的 history，单标的失败整体失败 | ⏸ 记录(P2) |
| DT-4 | LOW | qmt_data_feed.py | subscribe_whole_quote 参数因 xtquant 版本而异，未装无法验证 | ⏸ 记录(P2) |

**汇总**: MEDIUM×2, LOW×2；已修复 2 项, 记录 2 项。

## 2. 修复详情

### DT-1 — get_price stale 校验
`get_price(symbol, max_age=0.0)` 支持 stale 校验：`max_age>0` 且最近有效价时间戳距今超限 → 返回 None + `[STALE]` 告警。新增 `_price_ts` 记录有效价时间戳、`get_price_ts()` 暴露给调用方。不传 max_age 保持向后兼容。

### DT-2 — 无效价过滤
`_on_tick` 中 `lastPrice<=0` 或缺失 → 不缓存为真实价 (continue)，防止消费方拿到 0.0 误判。`remove_symbols` 同步清理 `_price_ts`。

## 3. 验证快照

| 验证 | 结果 |
|---|---|
| DT-2 lastPrice=0 | 不缓存 (get_price=None) |
| DT-2 有效价 4.5 | 正常缓存 |
| DT-1 stale (100s, max_age=30) | 返回 None + STALE 告警 |
| DT-1 新鲜 (max_age=200) | 正常返回 4.5 |
| 回归测试 | 7 passed (test_data_audit_regression_20260824.py) |
| ruff_incremental_gate | ✅ 3 文件无新增违规 |

## 4. 审查结论

- 断线检测/自动重连/心跳/maxlen 防泄漏实现完善。
- 核心缺口是**消费方拿价无新鲜度概念** (DT-1) 与**无效价污染缓存** (DT-2)，均已在源端修复。
- 建议: 下游 `get_price` 消费方应传 `max_age` (如 5s) 做 stale 校验，纳入后续批次契约。

## 5. 待办

- DT-3: connect 下载分批 + 单标的容错。
- DT-4: 装 xtquant 后核对 subscribe API。

<!-- AUTO-GENERATED: 相关文档 -->
## 相关文档

- [代码审查明细 — 轻量子模块 (alpha/governance/macro/ml/monitoring) (2026-08-24)](code-review-lightweight-20260824.md) (相似度 21%)
- [代码审查明细 — risk 模块 (2026-08-24)](code-review-risk-20260824.md) (相似度 19%)
- [代码审查明细 — hedging 模块 (2026-08-24)](code-review-hedging-20260824.md) (相似度 18%)
- [代码审查明细 — 主链路 institutional_pipeline_runner (2026-08-24)](code-review-pipeline-20260824.md) (相似度 17%)
- [代码审查明细 — execution 模块 (2026-08-24)](code-review-execution-20260824.md) (相似度 16%)

<!-- 由 scripts/cairn_cross_ref.py 自动生成，请勿手动编辑此区块 -->
