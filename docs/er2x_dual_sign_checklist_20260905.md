# ER-2.x Flag 双签操作 Checklist（D-3 拍板：09-13 ~ 09-18 执行）

> **来源**: 09-03 D-3 拍板（ER-2.x 双签提前至 09-13~09-18，冻结窗前唯一空档，双签动作 <0.5 人天）。
> **目的**: institutional pipeline 的 Step 4.6（phase_evolution）与 Step 6.6（phase_rebalance）生产启用。
> **前置（已就绪 2026-08-27）**: ER-2.1/2.2/2.3 代码 + 12/12 测试全绿（`tests/unit/test_er23_pipeline_orchestration.py`）；编排顺序已验证（evolution 在 Step 5 前，rebalance 在 Step 6 后）。
> **窗口纪律**: 09-18 前必须完成（09-19 冻结窗开始后属生产写入，禁止）；失败不阻塞管道（fail-safe 已内置），但双签本身不可跳过。

---

## 一、双签对象与目标态

| Flag | 当前态 | 目标态 | 影响面 |
|------|--------|--------|--------|
| `USE_EVOLUTION_ORCHESTRATOR` | enabled（rollout 50%，STAGE_2_50PCT） | rollout 推进 100%（STAGE_3_100PCT）或按 enabler 阶段轨记录确认 | pipeline Step 4.6 调用 EvolutionOrchestratorV2.run_cycle() |
| `USE_EOD_REBALANCE` | false（未启用） | **enabled**（`requires_dual_sign: true`） | pipeline Step 6.6 调用 ETFOptionHedgeRebalancer.run_daily_rebalance() |

> 执行前先用 `scripts/phase_b_progressive_enabler.py --check` 记录当前阶段轨状态——若阶段轨与目标态冲突，以 enabler 为准并停下核对（不硬推）。

## 二、执行步骤（窗口内任一交易日盘后 17:00 后）

```bash
cd "28-终极量化交易系统8.4"

# 0. 前置快照（回滚基准）
python scripts/phase_b_progressive_enabler.py --check
cp config/feature_flags.yaml reports/capital_gates/feature_flags_pre_er2x_$(date +%Y%m%d).yaml  # 或手动复制

# 1. dry-run 验证（flag 关闭态跑一遍管道, 确认主链路无回归）
python institutional_pipeline_runner.py --phase all --dry-run   # 以脚本实际 CLI 为准

# 2. 双签启用（现成入口, 双签角色必须为两个不同人/身份）
python run_eod_evolution_rebalance.py \
    --enable-rebalance \
    --signer "<发起人>" \
    --co-signer "<风控负责人>"

# 3. USE_EVOLUTION_ORCHESTRATOR rollout 推进（若 enabler 阶段轨确认应推进）
#    走 flag_manager enable/stage API（同上入口 --enable-evolution 或 phase_b enabler --advance）
```

**双签要求**: signer 与 co-signer 不得为同一人；`requires_dual_sign: true` 的 flag 由 `utils/infra/feature_flags.enable()` 强制校验，审计落 `reports/flag_audit/`。

## 三、验证步骤（启用后首个 EOD）

| # | 项 | PASS 判据 |
|---|---|----------|
| 1 | pipeline steps 输出 | `result["steps"]` 含 `eod_evolution` / `eod_rebalance` 段且 status 非 disabled |
| 2 | 主链路无回归 | Step 1-7 其余阶段结果与启用前一致（对照快照） |
| 3 | 审计留痕 | `reports/evolution/train_rebalance_bridge.jsonl`（若有训练事件）+ `reports/flag_audit/` 双签记录 |
| 4 | 次日复验 | 连续 2 个 EOD 正常（防首日偶发） |

## 四、回滚步骤（任一验证 FAIL 即执行）

```bash
# flag 回退（rollback_seconds 见注册表; USE_EOD_REBALANCE 需记录回滚双签）
python -c "from utils.infra.feature_flags import disable; disable('USE_EOD_REBALANCE', signer='<人>', reason='<原因>')"
# 或恢复前置快照 config/feature_flags.yaml 并重启 EOD 任务
```

- 回退后 Step 4.6/6.6 自动降级为 `disabled`（管道 fail-safe 设计，主链路不受影响）
- 回滚事件当日记入 `cairn/LOG.md`（含根因 + 重试计划）

## 五、完成登记

- [ ] 双签执行完成（日期 + signer/co-signer + flag 终态）
- [ ] 首个 EOD 验证 PASS（steps 输出留档 `reports/capital_gates/`）
- [ ] 连续 2 EOD 复验 PASS
- [ ] `cairn/LOG.md` 条目 + ROADMAP `er_2x_flag_dual_sign` 状态更新（enabled → 日期）
- [ ] 09-19 冻结窗开始后：本清单归档，后续 ER-3.x 按冻结窗纪律只观察不推进

> **关联**: ROADMAP §CURRENT STATE `erl_evolution_rebalance`；`cairn/evolution-rebalance-loop.md` §十三；`run_eod_evolution_rebalance.py`（双签入口）。
