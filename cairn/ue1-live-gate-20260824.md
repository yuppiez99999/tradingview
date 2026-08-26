# UE-1 统一实盘门控 (2026-08-24)

> 工作线 B: 风控 fail-close 完整化 — UE-1 统一实盘门控
> 审查工具: doubt-driven-development (对抗复核) + security-and-hardening
> 状态: 已实现并验证

## 0. 结论

**为三个实盘撮合/下单入口 (--hedge-execute / --rebalance-execute) 加统一实盘门控,
防止未来接 QMT (broker.enabled=true) 后裸实盘。当前模拟盘 (enabled=false) 完全不受影响。**

## 1. CLAIM (doubt-driven Step 1)

> 三个实盘入口默认即真实执行 (dry_run 默认 False), 接 QMT 后 (enabled=true +
> TRADING_ENV=production) 将真实下单, 无显式确认 = 裸实盘风险。
> 需统一门控: 真实 broker 就绪时非 dry_run 撮合必须 --yes 双签。

## 2. 实现 (量化策略系统_统一入口_v8.6.py)

### 新增 `_enforce_live_gate(dry_run, confirm_only, action, confirm)`
四态门控:
1. `dry_run` 或 `confirm_only` → 放行 (模拟/演练)
2. `broker.enabled=false` → 放行 (当前模拟盘)
3. `enabled=true 但 TRADING_ENV≠production` → **阻断 + send_alert** (防裸实盘)
4. `enabled=true + production 但无 --yes` → **阻断** (需双签确认)
5. `enabled=true + production + --yes` → 放行 (双签完成)

### 接入点
- `--hedge-execute`: 调用 `execute_hedge_orders` 前检查
- `--rebalance-execute`: 调用 `execute_rebalance_orders` 前检查
- `--live`: AutoTradingSystem 内部已有 get_broker 门控 (_use_live=False), 不改

### `--yes` 参数
argparse 补充 `--yes` (UE-1 实盘门控双签确认)。未传时 getattr 返回 False (安全默认阻断)。

## 3. 对抗复核 (doubt-driven DOUBT+RECONCILE)

| 质疑 | 结论 |
|---|---|
| 门控会影响现有模拟流程吗? | 否: enabled=false → 放行, 验证 `当前模拟 real: (True,'sim')` |
| --yes 未定义时安全吗? | 是: getattr 返回 False → 真实场景阻断 (fail-closed) |
| 绕过门控直接调 execute_hedge_orders? | 是, 但 execute_hedge_orders 内部走 SimulatedBroker (非真钱), 且门控是 CLI 层防裸实盘第一道防线 |
| _load_broker_config 导入可用? | 是: broker_factory 在 utils.execution, 统一入口可 import |
| TRADING_ENV 读取正确? | 是: os.environ.get("TRADING_ENV","sim"), 与 broker_factory 一致 |

## 4. 门控状态验证 (5 场景)

| 场景 | 结果 |
|---|---|
| 模拟盘 dry-run | 放行 ✅ |
| 模拟盘 real | 放行 ✅ |
| 真实 broker 无 production | 阻断 ✅ |
| 真实 broker + production 无 --yes | 阻断 ✅ |
| 真实 broker + production + --yes | 放行 ✅ |

## 5. 验证快照

| 门禁 | 结果 |
|---|---|
| ruff 统一入口 | ✅ All checks passed |
| lints | ✅ 0 errors |
| 门控 5 场景 | ✅ 全部符合预期 |

## 6. 关联

- 当前 broker.enabled=false, 门控不激活, 不影响现有模拟撮合/报告流程。
- 接 QMT 时 (G1 完成后) 置 enabled=true, 门控自动激活, 防止裸实盘。
- memory 23032726 双签模式落地。
