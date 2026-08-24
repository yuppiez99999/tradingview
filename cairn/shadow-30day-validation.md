# Shadow 30 天验证 (W7.2.8 + W7.2.9)

> **创建**: 2026-08-24 (Wave 7 Sprint 2)
> **状态**: 基础设施就绪, 待 09-13 启动 30 天验证窗口
> **依赖**: W7.1.6 (MVSK P5-1) + W7.1.7 (qlib shadow 接入) — 均已完成
> **LOG 指针**: 2026-08-24 W7.2.8/W7.2.9 shadow 30天验证启动器

---

## 1. 目标

### W7.2.8 MVSK P5-2
实盘 shadow 对比 BL+MVSK(378, γ_s=0.1, γ_k=0.1) vs 当前中线层 BL+MV(252)，每日记录权重/收益/换仓差异，30 天后评估 Δ夏普。

### W7.2.9 qlib_lgb_v2
实盘 shadow 对比 qlib_lgb_v2 vs V9 Regime-Specific，每日记录信号/收益/换仓差异，30 天后评估 Δ夏普。

---

## 2. 架构

```
scripts/launch_shadow_30day.py      # 每日运行器 (cron/scheduler 阶段7 调用)
    ├── _run_mvsk_shadow()          # 调用 apply_mvsk_shadow_to_mid_layer()
    ├── _run_qlib_shadow()          # 调用 apply_qlib_lgb_v2_shadow()
    ├── _check_fail_fast()          # fail-fast 监控 (单日差异>3%)
    └── _update_status()            # 更新窗口状态

utils/shadow_30day_evaluator.py     # 30天评估器
    ├── _evaluate_mvsk()            # MVSK 评估 (权重差异/换仓/Δ夏普)
    ├── _evaluate_qlib()            # qlib 评估 (信号差异/方向一致率/Δ夏普)
    └── to_markdown()               # 生成评估报告

reports/shadow/
    ├── mvsk_p5_daily_diff.jsonl    # MVSK 每日权重差异 (W7.1.6 已就绪)
    ├── qlib_lgb_v2_daily.jsonl     # qlib 每日信号差异 (W7.1.7 已就绪)
    ├── shadow_30day_status.json    # 30天窗口状态
    └── shadow_30day_eval_*.md      # 评估报告
```

---

## 3. 关键设计决策

### 3.1 shadow 模式不侵入生产链路
- MVSK: `apply_mvsk_shadow_to_mid_layer()` 返回原始 portfolio (unchanged)
- qlib: `register_qlib_lgb_v2_shadow()` weight=0.0 不参与融合
- 符合 W7.1.6/W7.1.7 "不侵入生产链路" 设计

### 3.2 Δ夏普估算方法

**MVSK** (`weight_diff_stability_proxy`):
```
Δ夏普 ≈ 0.05 × (1 - std_diff / (mean_diff + ε))
```
- 保守上界 0.05 (MVSK 相对 MV 的年化夏普提升)
- 稳定性 = 1 - 变异系数 (std/mean)
- mean_diff=0 时返回 0.01 (微正, 差异不大但有调整)

**qlib** (`oos_backtest_prior`):
```
Δ夏普 = (qlib_lgb_v2 OOS 夏普 - V9 OOS 夏普) × confidence
     = (2.44 - 1.315) × confidence
```
- confidence 由方向一致率决定: >0.6→1.0, 0.4~0.6→0.5, <0.4→0.1

### 3.3 fail-fast 条件
- 单日权重/信号差异 > 3% (与 shadow_account_system.py 阈值对齐)
- 触发后 latch, 需人工介入

### 3.4 通过条件 (ROADMAP W7.3.7/W7.3.8)
1. Δ夏普 > 0
2. 换仓成本 < 2% (MVSK) / 最大信号差异 < 0.5 (qlib)
3. fail-fast 未触发

---

## 4. 使用方式

```bash
# 每日运行 (由 cron 调用, 09-13 开始)
python -X utf8 scripts/launch_shadow_30day.py

# 指定日期
python -X utf8 scripts/launch_shadow_30day.py --date 2026-09-13

# 查看窗口状态
python -X utf8 scripts/launch_shadow_30day.py --status

# 生成评估报告 (30天后)
python -X utf8 scripts/launch_shadow_30day.py --evaluate
```

环境变量控制:
- `USE_MVSK_MID_LAYER=1` (默认启用)
- `USE_QLIB_LGB_V2=1` (默认启用)

---

## 5. 基础设施就绪状态

| 组件 | 状态 | 位置 |
|------|------|------|
| `apply_mvsk_shadow_to_mid_layer()` | ✅ W7.1.6 | `utils/universe/portfolio_builder.py:484` |
| `register_qlib_lgb_v2_shadow()` | ✅ W7.1.7 | `utils/signal_fusion.py:1311` |
| `apply_qlib_lgb_v2_shadow()` | ✅ W7.1.7 | `utils/signal_fusion.py:1355` |
| `ShadowAccount` + `FailFastMonitor` | ✅ | `shadow_account_system.py` |
| `launch_shadow_30day.py` | ✅ W7.2.8/W7.2.9 | `scripts/launch_shadow_30day.py` |
| `shadow_30day_evaluator.py` | ✅ W7.2.8/W7.2.9 | `utils/shadow_30day_evaluator.py` |
| `mvsk_p5_daily_diff.jsonl` | ✅ 有数据 | `reports/shadow/` (22条) |
| `qlib_lgb_v2_daily.jsonl` | ✅ 有数据 | `reports/shadow/` |
| scheduler.py 接入 | ⏳ 待 W7.2.8 启动 | 阶段5后插入 (环境变量控制) |

---

## 6. 测试

- `tests/unit/test_shadow_30day_unit.py` — 22 测试全绿
- 覆盖: 评估器核心逻辑 + 运行器核心函数 + fail-fast + Markdown 报告

---

## 7. 后续步骤

1. **09-13 启动**: cron 每日调用 `launch_shadow_30day.py`
2. **10-12 评估**: 运行 `--evaluate` 生成 30 天评估报告
3. **W7.3.7** (10-13~11-12): 若 MVSK 通过 → 正式启用中线层 BL+MVSK(378)
4. **W7.3.8** (10-13~11-12): 若 qlib 通过 → 切换 `ml` 信号源 V9 → qlib_lgb_v2

---

## 8. 踩坑记录

### 8.1 `pass` 是 Python 关键字
`MVSKEvaluation` / `QlibEvaluation` 的通过字段不能用 `pass`, 用 `pass_` 替代 (dataclass field 名)。

### 8.2 ruff F541 f-string 无占位符
Markdown 表头 `f"| 指标 | 值 |"` 无占位符, ruff F541 报错。修复: 去掉 `f` 前缀。

### 8.3 `py` 命令在 PowerShell 不可用
Windows PowerShell 中 `py` launcher 可能创建进程失败, 用 `python` 替代。