# EX-1 broker 契约统一 (2026-08-24)

> 工作线 A: 执行链完整化 — EX-1 broker 契约统一
> 审查工具: code-review-and-quality 五轴 + 运行时防御
> 状态: 运行时契约校验已实现 (架构债已文档化)

## 0. 结论

**broker 契约已通过继承统一 (SimulatedBroker/QmtBrokerAPI 继承 broker_api.BrokerAPI 抽象基类),
并为 SmartOrderRouter 加运行时契约校验 (fail-fast), 防止"传入对象缺方法导致运行中 AttributeError"
(EX-7 同类问题的系统性防御)。**

## 1. 架构发现 (契约分裂)

存在**同名 BrokerAPI 双重定义**, 签名不兼容:

| 文件 | 类型 | place 返回 | wait_fill/cancel 接收 |
|---|---|---|---|
| broker_api.py L55 | 抽象基类 | Order 对象 | Order 对象 |
| smart_order_router.py L30 | Protocol | order_id (str) | order_id (str) |

实际运行正常 (鸭子类型): SmartOrderRouter 用 MockBroker (自洽), get_broker() 返回 SimulatedBroker
(继承抽象基类, 同时满足两者)。但**静态类型与语义错位**: SOR 的 Protocol 声明 place 返回 str,
而 SimulatedBroker 返回 Order 对象。

## 2. 决策

**不强行合并两个接口** (改动大、破坏现有运行、非实盘阻断)。采取:
1. **运行时契约校验** (SmartOrderRouter.__init__): 传入 broker 必须具
   备 `get_order_book/place/wait_fill/cancel/get_account_info` 五方法, 缺失 fail-fast 抛 TypeError。
2. **架构债文档化**: 同名 BrokerAPI 双重定义记录为 P2 架构债 (后续可统一到 broker_api 单一契约)。

## 3. 五轴审查

| 轴 | 结论 |
|---|---|
| Correctness | ✅ 合法 broker (MockBroker/SimulatedBroker) 通过; 缺方法/部分方法 fail-fast |
| Readability | ✅ REQUIRED_BROKER_METHODS 模块级常量, 清晰 |
| Architecture | ✅ 不改继承结构, 加防御层, 符合"不做无关重构"边界 |
| Security | ✅ 无密钥/注入 |
| Performance | ✅ 构造时一次校验, 无热路径开销 |

## 4. 验证快照

| 验证 | 结果 |
|---|---|
| MockBroker | ✅ 通过契约校验 |
| SimulatedBroker | ✅ 通过契约校验 |
| BadBroker (缺方法) | ✅ fail-fast 抛 TypeError |
| PartialBroker | ✅ 拒绝 |
| 回归测试 | 4 passed (test_ex1_broker_contract_20260824.py) |
| ruff_incremental_gate | ✅ 3 文件无新增违规 |

## 5. 关联

- 本轮审查 EX-7 (algo_engine 调不存在 SOR 方法) 已修; EX-1 补齐运行时防御, 系统性防同类问题。
- 真实 OrderRouter (automated_execution_system.py L149) 对接 SmartOrderRouter, 其 broker=get_broker()
  (G1 已接线); EX-1 校验确保装配的 broker 满足 SOR 契约。
- 架构债: 同名 BrokerAPI 双重定义 (broker_api 抽象基类 vs smart_order_router Protocol), P2 后续统一。

<!-- AUTO-GENERATED: 相关文档 -->
## 相关文档

- [代码审查明细 — execution 模块 (2026-08-24)](code-review-execution-20260824.md) (相似度 14%)
- [G1 QMT 真实下单接线 (2026-08-24)](g1-qmt-wiring-20260824.md) (相似度 13%)
- [DTE-1 建仓接入 FillsStore 事实源 (2026-08-24)](dte1-build-fills-store-20260824.md) (相似度 12%)
- [UE-1 统一实盘门控 (2026-08-24)](ue1-live-gate-20260824.md) (相似度 8%)
- [代码审查明细 — daily_trade_executor (2026-08-24)](code-review-daily-executor-20260824.md) (相似度 6%)

<!-- 由 scripts/cairn_cross_ref.py 自动生成，请勿手动编辑此区块 -->
