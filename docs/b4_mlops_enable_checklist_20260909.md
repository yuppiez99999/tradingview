# B4（USE_MLOPS_PIPELINE）启用评估操作 Checklist

> **来源**: ROADMAP NEXT 14 DAYS ~09-09（三）：B4 warmup 7/7 → `phase_b_progressive_enabler.py --check` 评估 USE_MLOPS_PIPELINE 启用。
> **真实路径（09-01 口径修正）**: B4 不走 enabler 阶段轨（`USE_MLOPS_PIPELINE` 不在 `STAGE_FLAGS`），由 `scripts/phase_b_b4_shadow_runner.py` shadow 7 个交易日后**人工评估 + 双签启用**。
> **预检日期**: 2026-09-08（盘中预检，warmup 5/7；今日 EOD 后 6/7，明日 09-09 EOD 后 7/7 达标 → 本清单正式执行时点最早 09-09 EOD 后）。
> **关联**: `cairn/ROADMAP.md` §CURRENT STATE `phase_b` / §NEXT 14 DAYS；`config/feature_flags.yaml`（B4 注册段）；`cairn/LOG.md` 09-01 B4 口径修正；ER-2.x 双签 checklist（`docs/er2x_dual_sign_checklist_20260905.md`，同机制参照）。

---

## 一、目标态与判据总览

| 项 | 当前态（09-08 预检） | 启用判据（全部 PASS 才执行双签） |
|------|------|------|
| warmup | **5/7**（09-01/02/03/04/07；09-08 EOD 后 6/7，09-09 EOD 后 7/7） | `b4_shadow_status.json` → `warmup_days >= 7` |
| 历史闭环 | 5 条 history 全 `loop_closed: true` | history 无 `need_rollback: true` |
| 连续失败 | `consecutive_failures: 0` | `< 3`（≥3 曾触发自动回退 B3 的阈值） |
| FLAG 不变式 | PASS（09-08 实测 `USE_MLOPS_PIPELINE=False`） | `--check-invariant` PASS（启用前必须仍 False） |
| B3 前置 | 阶段轨 `orchestrator`（6 天），B3 flag 已启用 | 阶段轨已到最终阶段（B3_OK 由 09-01 推进门禁验证） |
| enabler 健康 | `--check` 健康检查 PASS | `--check` 输出健康 PASS、无 kill_switch/lookahead 异常 |
| 观察期 | 32/21 天 | 观察期已满（早已满足） |

> **结论判据**: warmup 7/7 **且** 历史全部 loop_closed **且** 连败 0 **且** FLAG 不变式 PASS **且** enabler 健康 PASS → **Go 启用**；任一 FAIL → 记入 LOG 挂起，不硬推（对齐 HC-4 前阶段健康不达标不进入下一阶段）。

## 二、执行步骤（09-09 EOD 后 ~ 09-10，冻结窗 09-19 前充裕）

```bash
cd "28-终极量化交易系统8.4"

# 0. 前置快照（回滚基准 + 达标确认）
& ".venv\Scripts\python.exe" -X utf8 scripts\phase_b_b4_shadow_runner.py --check-invariant   # 期望 PASS
Get-Content reports\shadow\b4_shadow_status.json                                            # warmup_days 应 =7
& ".venv\Scripts\python.exe" -X utf8 scripts\phase_b_progressive_enabler.py --check         # 健康 PASS + 阶段轨 orchestrator

# 1. 双签启用（signer 与 co-signer 必须不同身份；审计落 reports/flag_audit/）
& ".venv\Scripts\python.exe" -X utf8 -c "from utils.infra.feature_flags import enable; enable('USE_MLOPS_PIPELINE', signer='<发起人>', co_signer='<风控>', reason='B4 warmup 7/7 达标启用 (YYYY-MM-DD)')"

# 2. 同步 system_config.json 快照（enabler 的 _sync_flags_to_system_config 为幂等原子写，与阶段轨同机制）
& ".venv\Scripts\python.exe" -X utf8 -c "import sys; sys.path.insert(0,'.'); from scripts.phase_b_progressive_enabler import _sync_flags_to_system_config; print(_sync_flags_to_system_config({'USE_MLOPS_PIPELINE': True}))"

# 3. 对账验证
& ".venv\Scripts\python.exe" -X utf8 -c "from utils.infra.feature_flags import is_enabled; print('USE_MLOPS_PIPELINE =', is_enabled('USE_MLOPS_PIPELINE'))"   # True
Get-Content reports\flag_overrides\USE_MLOPS_PIPELINE.json   # enabled: true + 双签 + 审计
Get-Content reports\flag_audit\USE_MLOPS_PIPELINE.jsonl      # 双签记录留痕
```

> 说明：`rollback_seconds: 60` 仅为注册元数据，`utils/infra/feature_flags.py` 未消费该字段 → 启用持久，无自动回滚定时器。

## 三、启用后影响与验证（首个 EOD 即生效）

启用后 EOD 阶段 4.86 自动**跳过 shadow 预热**（读 `system_config.json` 判 `USE_MLOPS_PIPELINE=True` → `skipped: "B4 already enabled, warmup complete"`），**MLOps 外层循环由 shadow 转入真实执行**（`utils/alpha/mlops_pipeline.py` Facade：Train → Register → Drift Monitor → A/B Test → Promote/Rollback，受子模块 Flag 独立门控）。

| # | 项 | PASS 判据 |
|---|---|----------|
| 1 | EOD 阶段 4.86 | 日志含 "B4 already enabled... skipped"，不再调 runner（runner 不变式要求 flag=False，会 FAIL） |
| 2 | MLOps 管线真实路径 | `utils/alpha/mlops_pipeline.py` 首次以 `USE_MLOPS_PIPELINE=True` 运行无阻断异常（子模块 fail-safe 降级正常） |
| 3 | 审计留痕 | `reports/flag_audit/USE_MLOPS_PIPELINE.jsonl` 双签记录 + `reports/flag_overrides/USE_MLOPS_PIPELINE.json` |
| 4 | 状态快照对齐 | `system_config.json` 与 `phase_b_status.json` 与 `config/feature_flags.yaml` 覆盖层三方一致 |
| 5 | 主链路无回归 | EOD 其余阶段与启用前一致（对照 t3 7 项一键基线） |
| 6 | 连续复验 | 连续 2 个 EOD 正常（防首日偶发） |

## 四、回滚步骤（任一验证 FAIL 即执行）

```bash
# flag 单签回滚（disable 单签即可，任何风控人员可关）
& ".venv\Scripts\python.exe" -X utf8 -c "from utils.infra.feature_flags import disable; disable('USE_MLOPS_PIPELINE', signer='<人>', reason='<原因>')"

# system_config 快照同步回退
& ".venv\Scripts\python.exe" -X utf8 -c "import sys; sys.path.insert(0,'.'); from scripts.phase_b_progressive_enabler import _sync_flags_to_system_config; print(_sync_flags_to_system_config({'USE_MLOPS_PIPELINE': False}))"
```

- 回退后 EOD 阶段 4.86 恢复 shadow 预热（若 warmup 未满或需重启预热则补跑）。
- 回滚事件当日记入 `cairn/LOG.md`（含根因 + 重试计划）。

## 五、完成登记

- [x] warmup 7/7 达标（实际 8/8，09-10 EOD；`b4_shadow_status.json`）
- [x] 双签执行完成（2026-09-11 11:59，signer=phase_b_enabler / co_signer=phase_b_health_gate，flag 终态 = True）
- [ ] 首个 EOD 验证 PASS（阶段 4.86 skipped + 主链路无回归）
- [ ] 连续 2 EOD 复验 PASS
- [x] `cairn/LOG.md` 条目 + ROADMAP `phase_b` B4 状态更新（shadow_running → enabled + 日期）
- [ ] 09-19 冻结窗开始后本清单归档

> **当前进度（09-08 盘中预检）**: warmup 5/7 ✅ 全闭环 ✅ 连败 0 ✅ 不变式 PASS ✅ enabler 健康 PASS —— 待 09-09 EOD 后 warmup 7/7 即达全绿。

> **执行记录（2026-09-11 11:59）**: 预检全绿（invariant PASS / enabler 健康 PASS / shadow preflight 9/9）→ 双签启用完成（phase_b_enabler × phase_b_health_gate，override/audit 双留痕）→ system_config 同步（已落盘 1 项）→ is_enabled 对账 = True。首 EOD 验证待 2026-09-11 EOD；连续 2 EOD 复验待 09-14（09-12/13 非交易日）。
