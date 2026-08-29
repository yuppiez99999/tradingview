# G1 QMT 真实下单接线 (2026-08-24)

> 工作线 A: 执行链完整化 — G1 QMT 真实下单接线
> 安全核查: security-and-hardening skill (密钥管理/门控)
> 状态: 配置层完成 (装配契约就绪), 环境层待用户操作 (装 xtquant + 配账号)

## 0. 结论

**代码装配层已完整, 当前保持安全默认 (enabled=false) 降级 SimulatedBroker, 未裸实盘。**
真实下单仍物理阻塞于 xtquant 未安装 (memory 23032726 确认), 需 QMT 终端环境安装。

## 1. 修复内容

### system_config.json broker 段补齐 (config/system_config.json)
原文件极简 `{"environment":"ci","trading":{"dry_run":true}}` **无 broker 段**。补齐安全默认:
```json
"broker": {
  "type": "qmt", "enabled": false, "dry_run": true,
  "account_id": "", "session_id": 0, "account_type": "STOCK",
  "qmt_path": "", "connect_timeout": 10,
  "comment": "真实下单需 enabled=true+dry_run=false+TRADING_ENV=production+xtquant; 账号走环境变量"
}
```

## 2. 安全核查 (security-and-hardening)

| 检查项 | 结果 |
|---|---|
| 密钥管理 | ✅ RPC token 走 `QMT_RPC_TOKEN` 环境变量; QMT 账号建议 `QMT_ACCOUNT/QMT_PASSWORD` 环境变量, 不落盘明文 |
| 裸实盘防护 | ✅ 四重门控 (enabled + dry_run + TRADING_ENV=production + xtquant) |
| 失败降级 | ✅ connect 失败/xtquant 缺失 → SimulatedBroker + send_alert |
| 明文敏感配置 | ⚠ 未来配账号时 account_id 若放 json 会明文, 建议走环境变量 (已写入 comment 指引) |

## 3. 装配验证

```
xtquant installed: False
broker cfg: {'type':'qmt','enabled':False,'dry_run':True}
装配结果: SimulatedBroker (sim 模式)   ← enabled=false 安全默认
是否裸实盘: NO (安全)
```

## 4. 剩余工作 (环境层, 需用户操作)

1. **装 xtquant**: 从 QMT (miniQMT) 终端安装目录 `bin.x64/lib/site-packages/xtquant`, 非 pip 可装。装后 `find_spec('xtquant')` 应为 True。
2. **配账号**: 环境变量 `QMT_ACCOUNT`/`QMT_PASSWORD` (不落盘明文)。如用云端桥接, 设 `QMT_RPC_URL`/`QMT_RPC_TOKEN`。
3. **dry_run 影子期**: 装好后先 `enabled=true + dry_run=true` 验证连接与下单语法, 确认无异常再逐步放开 (memory 23032726 Phase 4 口径)。
4. **TRADING_ENV**: 实盘时设 `TRADING_ENV=production`, 否则 get_broker 强制降级模拟。

## 5. 关联

- 真实下单执行路径: automated_execution_system.OrderRouter → get_broker() (本轮审查确认已注入 broker=SimulatedBroker)。
- EX-5 已修 QmtBrokerAPI.orderStock 参数错位, 待 xtquant 装好后需验证真实下单签名。
- 本轮审查 DTE-1 记录: daily_trade_executor 建仓执行未接撮合链, 需在 QMT 影子期前补齐 (工作线 DTE-1)。

<!-- AUTO-GENERATED: 相关文档 -->
## 相关文档

- [UE-1 统一实盘门控 (2026-08-24)](ue1-live-gate-20260824.md) (相似度 18%)
- [DTE-1 建仓接入 FillsStore 事实源 (2026-08-24)](dte1-build-fills-store-20260824.md) (相似度 16%)
- [EX-1 broker 契约统一 (2026-08-24)](ex1-broker-contract-20260824.md) (相似度 13%)
- [风控 fail-close 完整化 (2026-08-24)](risk-failclose-complete-20260824.md) (相似度 13%)
- [运维部署 + 灰度发布 (2026-08-24)](ops-gray-release-20260824.md) (相似度 12%)

<!-- 由 scripts/cairn_cross_ref.py 自动生成，请勿手动编辑此区块 -->
