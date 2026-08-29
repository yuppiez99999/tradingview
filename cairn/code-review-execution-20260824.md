# 代码审查明细 — execution 模块 (2026-08-24)

> 审查对象: `ms_strategy/src/execution/` (7 文件)
> 审查方法: 既有 SOP 四步法 + Q1-Q7 硬门禁 + code-review-and-quality 五轴 + 量化 DoD
> 审查批次: 全量核心模块逐模块审查计划 (plan: quant-system-module-audit-20260824) 第 1/10 模块

## 0. 门禁基线 (修复前)

| 门禁 | 结果 |
|---|---|
| ruff 静态扫描 (F/E9/BLE001/T201) | ✅ All checks passed |
| bandit 安全扫描 | ✅ 0 issue |
| execution 相关单测 | ✅ 202 passed |

## 1. 缺陷清单与修复状态

| ID | 严重度 | 文件 | 缺陷 | 状态 |
|---|---|---|---|---|
| EX-5 | **P0** | qmt_broker.py | `orderStock` 参数语义错位：第3参 orderType 传价格类型、第5参 priceType 传买卖方向（官方要求反之）；account 传字符串非 `StockAccount` 对象；`price=-1` 无对应报价类型。一旦接实盘会下错方向。受 `XTQUANT_AVAILABLE` 门控当前不可达 | ✅ 已修复(参数纠正, 不改门控) |
| EX-7 | HIGH | algo_engine.py | `execute_order` 调用 `sor.execute_twap/vwap/pov`，但 `SmartOrderRouter` 只有 `execute()` → 必 AttributeError（测试盲区，原测试未覆盖 execute_order） | ✅ 已修复(补 3 个算法方法) |
| EX-12 | HIGH | smart_order_router.py | `MockBroker` 未实现 `get_account_info`，`execute()` 资金校验/期权保证金调用时 AttributeError | ✅ 已修复(补方法) |
| EX-1 | HIGH | smart_order_router.py | `BrokerAPI` Protocol(返回str) 与 `broker_api.py` 抽象类(返回Order) 两套不兼容契约 | ⏸ 记录(P1 门禁加固, 涉及架构改动延后) |
| EX-4 | MEDIUM | smart_order_router.py | Protocol 未声明 `get_account_info` 但 execute/期权风控依赖它 | ✅ 已修复(Protocol 补声明) |
| EX-2 | MEDIUM | broker_api.py | `SimulatedBroker.wait_fill` 未设 volume 时静默只成交 `qty//10`（部分成交占比异常） | ✅ 已修复(无 volume 时全额成交) |
| EX-8 | MEDIUM | post_execution_review.py | IS/impact 用 `abs()` 丢失买卖方向，无法反映有利滑点 | ✅ 已修复(IS 带方向) |
| EX-11 | MEDIUM | algo_engine.py | `execute_order` 未检查 `self.sor is None`，会 AttributeError | ✅ 已修复(fail-open 返回空) |
| EX-10 | LOW | algo_engine.py | `logger.warning(f"...")` f-string 惰性求值 (惯例) | ⏸ 记录(P2 存量) |

**汇总**: P0×1, HIGH×3, MEDIUM×4, LOW×1；已修复 7 项, 记录 2 项 (EX-1 架构级 P1 延后, EX-10 P2 存量)。

## 2. 修复详情

### EX-5 (P0) — QmtBrokerAPI.orderStock 参数纠正
```python
# 官方签名: orderStock(account, stock_code, order_type, order_volume, price_type, price, strategy_name, order_remark)
# 修复前: orderType=价格类型(错), priceType=买卖方向(错), account=字符串, price=-1
qt_direction = xtconstant.STOCK_BUY if side == "BUY" else xtconstant.STOCK_SELL   # order_type(第3参)
qt_price_type = ... FIX_PRICE / LATEST_PRICE / MARKET_*                            # price_type(第5参)
qt_account = StockAccount(self.account_id)  # account 用对象, 构造失败回退字符串
```
依据: 迅投官方 xttrader.md 文档。xtquant 未安装时本方法不可达, 修复不改变门控。

### EX-7 + EX-12 — 算法执行断链修复
- `SmartOrderRouter` 补 `execute_twap/execute_vwap/execute_pov` 三个方法, 统一委托 `execute()`。
- `MockBroker` 补 `get_account_info()`。
- `AlgoEngine.execute_order` 补 `sor is None` 守卫 (fail-open 返回空 + warning)。

### EX-2 — SimulatedBroker 部分成交修复
未设 volume 时 `avail_vol=0 → 全额成交`; 设 volume 时 `min(qty, avail_vol//10)` 保留流动性约束。

### EX-8 — IS 方向性修复
买入不利 `(avg-decision)*qty>0`, 买入有利 `<0`; 卖出对称。市场冲击/时机成本仍用 abs 仅供占比分析。

## 3. 验证快照 (打真实接入点)

| 验证 | 结果 |
|---|---|
| EX-7 VWAP 执行 | 6 笔 fills 全部 FILLED (修复前 AttributeError) |
| EX-11 sor=None | 返回空列表 + warning, 不崩 |
| EX-2 无 volume | 成交 1000 (修复前 100) |
| EX-2 有 volume=5000 | 成交 500 (流动性约束保留) |
| EX-8 买入不利/有利 | IS = +10 / -10 (带方向) |
| 回归测试 | 9 passed (test_execution_audit_regression_20260824.py) |
| execution 相关单测 | 211 passed (含新增) |
| ruff_incremental_gate | ✅ 7 文件无新增违规 (含修复存量 B905 zip strict) |
| assert_data_validity | 12 PASS, 0 FAIL |

## 4. 防复发门禁 / 契约

- 新增回归测试 `tests/unit/test_execution_audit_regression_20260824.py` 覆盖 EX-2/7/8/11/12。
- **契约建议 (待后续批次落地)**: execution 模块应统一 broker 契约 (Protocol vs 抽象类二选一), 消除 EX-1 双套接口。
- **测试盲区教训**: 原单测只测 AlgoEngine 初始化/时段判断, 未测 `execute_order` → EX-7 长期潜伏。审查必须看"高价值函数的实际调用路径是否被测试覆盖"。

## 5. 关联记忆/经验

- EX-5 印证 memory ID 23032726: QmtBrokerAPI 受 XTQUANT_AVAILABLE 门控, 真实下单待装 xtquant。
- EX-1 呼应 memory ID 97089364/25810107: 执行系统契约一致性是实盘正确性的前提。
- 审查纪律: 每条结论由门禁脚本 + 真实接入点验证佐证 (SOP 四步法 + code-review DoD)。

<!-- AUTO-GENERATED: 相关文档 -->
## 相关文档

- [代码审查明细 — 轻量子模块 (alpha/governance/macro/ml/monitoring) (2026-08-24)](code-review-lightweight-20260824.md) (相似度 20%)
- [代码审查明细 — risk 模块 (2026-08-24)](code-review-risk-20260824.md) (相似度 18%)
- [代码审查明细 — hedging 模块 (2026-08-24)](code-review-hedging-20260824.md) (相似度 17%)
- [代码审查明细 — 主链路 institutional_pipeline_runner (2026-08-24)](code-review-pipeline-20260824.md) (相似度 16%)
- [代码审查明细 — data 模块 (2026-08-24)](code-review-data-20260824.md) (相似度 16%)

<!-- 由 scripts/cairn_cross_ref.py 自动生成，请勿手动编辑此区块 -->
