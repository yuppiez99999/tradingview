# Shadow 30 天验证启动手册 (Shadow 30-Day Launch Runbook)

> **W7.2.8 (MVSK P5-2) + W7.2.9 (qlib_lgb_v2)** | 启动日 **09-13** | 30 天窗口
>
> 目标: 实盘 shadow 对比 BL+MVSK(378) vs BL+MV(252)、qlib_lgb_v2 vs V9，
> 每日记录差异，30 天后由 `utils/shadow_30day_evaluator.py` 评估 Δ夏普。
> 前置资产详见 `cairn/shadow-30day-validation.md`。

---

## 1. 启动前自检（09-13 cron 首日之前必做）

> 新增 `--preflight` 自检模式（fail-closed），逐项校验全部前置依赖。
> 任一**阻塞项**失败则整体 NOT READY（exit code 1），**不可**启动窗口。

```bash
python -X utf8 scripts/launch_shadow_30day.py --preflight
```

自检覆盖的判定项：

| 判定项 | 级别 | 通过标准 | 失败影响 |
|--------|------|----------|----------|
| 窗口状态 | block | 未启动或进行中 | 已结束则禁止重复启动 |
| feature flag `USE_MVSK_MID_LAYER` | warn | =1/true | 该影子维度不产出 |
| feature flag `USE_QLIB_LGB_V2` | warn | =1/true | 该影子维度不产出 |
| shadow 基础设施可导入 | block | 3 个符号均导入成功 | 每日影子无法运行 |
| qlib_lgb_v2 生产模型 | block | `reports/qlib_model_*.pkl` + `predictions_*.csv` 存在 | qlib 影子降级无真实信号 |
| 输出目录 `reports/shadow` | block | 目录可写/可自建 | 无法落盘记录 |
| 评估器 (`--evaluate`) | block | `Shadow30DayEvaluator` 可导入 | 30 天后无法出评估 |

> 说明：`--preflight` 仅做只读探测 + 一次性写入探针（写入后即删除），
> 不触发真实下单、不修改生产组合/信号，安全可重复执行。

---

## 2. cron 启动步骤（09-13）

### 2.1 在每日调度阶段 5 之后插入（环境变量控制）

```bash
# 首日（09-13 收盘后 EOD 阶段）
python -X utf8 scripts/launch_shadow_30day.py --date 2026-09-13

# 后续每日（由 scheduler 阶段7 调用, 无参数=当天）
python -X utf8 scripts/launch_shadow_30day.py
```

### 2.2 确认窗口状态

```bash
python -X utf8 scripts/launch_shadow_30day.py --status
```

预期输出首日：

```
  起始日期:     2026-09-13
  已运行天数:   1 / 30
  MVSK 记录数:  4
  qlib 记录数:  4
  fail-fast:    ✅ 正常
```

---

## 3. 每日值守要点

- **fail-fast 触发即 latch**：单日差异 >3%（`mvsk_weight_diff_l2` / `|qlib_signal_diff|`）
  → 脚本 exit code 1，需人工介入排查后再决定是否继续。
- 记录落盘：`reports/shadow/mvsk_p5_daily_diff.jsonl` + `qlib_lgb_v2_daily.jsonl`。
- 影子为**旁路记录**，不修改生产组合（portfolio unchanged / weight=0.0），不会产生真实订单。

---

## 4. 30 天评估（10-12 之后）

```bash
python -X utf8 scripts/launch_shadow_30day.py --evaluate
```

输出 `reports/shadow/shadow_30day_eval_*.md`。通过判定（对照 ROADMAP W7.3.7/W7.3.8）：

1. Δ夏普 > 0
2. 换仓成本 < 2% (MVSK) / 最大信号差异 < 0.5 (qlib)
3. fail-fast 未触发

通过后进入 W7.3.7（MVSK 正式启用中线层）/ W7.3.8（qlib 切换 ml 信号源 V9→qlib_lgb_v2）。

---

## 5. 踩坑提示

- `py` launcher 在 PowerShell 不可用时改用 `python`。
- shadow 期 MVSK 依赖真实 378 日历史收益率矩阵；若拉取失败会 fallback 合成随机，
  评估时需留意 `data_sufficient` 标记。
- 测试隔离：跑 `test_shadow_30day_unit.py` 中 `test_run_daily_shadow_mvsk_disabled`
  会在真实 `reports/shadow/` 留下状态文件（gitignored，不影响版本库），
  巡检如需清空运行残留可 `rm -rf reports/shadow`。
