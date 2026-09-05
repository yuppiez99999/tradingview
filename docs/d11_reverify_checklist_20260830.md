# D11 复验检查清单 (2026-08-30 预准备, 2026-09-01 状态刷新)

> 创建: 2026-08-30 (周日, A股休市) | 刷新: 2026-09-01 (周二)
> 目的: 为 09-02 (周三) D11 Phase B shadow 复验做预准备, 并记录排期口径矛盾
> 关联: `scripts/engineering_debt_gate.py:1062` `_check_d11_phase_b_shadow_stable()`
> 状态文件: `reports/evolution/phase_b_status.json`
> LOG 指针: `cairn/LOG.md` 2026-09-01 条目

## 1. 当前状态快照 (2026-09-01 复核, 08-30 旧快照见文末附录)

| 字段 | 值 (08-30 快照 → 09-01 实测) | 说明 |
|---|---|---|
| stage | `auto_retrain` (不变) | Stage 3, B1+B2+B3 已于 08-27 全启用 |
| consecutive_stable_days | 6 → **7/7 ✓ 已达标** | 08-31 EOD 修复后 healthy 记录推进 |
| stable_days_target | 7 (不变) | dataclass 默认值 |
| min_shadow_samples | **20 (不变)** | dataclass 默认值 (硬性, 见 §3) |
| len(daily_health_log) | 6 → **7/20** | +08-31 (healthy=true); **唯一剩余瓶颈** |
| flags_enabled | 6 个全 true (不变) | DRIFT_DETECTOR/FEEDBACK_LOOP/ABTEST/AUTO_RETRAIN/FINENG_GARCH/FINENG_KALMAN_BETA |

daily_health_log 最近日期序列: ..., 08-25 / 08-26 / 08-27 / 08-28 / (08-29·30 周末跳过) / **08-31** ✓
→ 09-01 EOD 收盘后 samples 预期为 **8**。

## 2. D11 PASS 双条件 (engineering_debt_gate.py:1094)

```python
if stable_days >= target and total_samples >= min_samples:
    return (True, "达标 ✓")
```

- 条件 A: `consecutive_stable_days >= 7` (连续稳定天数)
- 条件 B: `len(daily_health_log) >= 20` (shadow 样本数)
- **两者必须同时满足**, 缺一不可
- 周末 (周六=5, 周日=6) 降级不阻断; CI 环境结构性缺失跳过

## 3. 排期口径矛盾 (重要发现)

ROADMAP/LOG 08-29 口径: "D11 shadow 6/7 天, 09-02 满 7/7 后复验"
—— 只关注条件 A (stable_days), **忽略条件 B (min_samples=20)**

实测推演 (每交易日 +1 样本, 自 09-01 EOD 后 8 样本起算; 08-31 样本已记录使达标日较 08-30 推演前移 1 天):

| 日期 | stable_days | total_samples | D11 判定 |
|---|---|---|---|
| 08-31 (一, 已记录) | 7 | 7 | FAIL (7<20; 08-31 早间 EOD 数据未生成曾报 no_daily_return, 修复后 healthy) |
| 09-01 (二, EOD 后) | 7 | 8 | FAIL (8<20) |
| 09-02 (三, ROADMAP 复验日) | 7 | 8~9 | **FAIL (预期内, 样本不足, 非回归)** |
| 09-12 (五, Sprint 1 收尾) | 7 | 16 | **FAIL (16<20)** |
| 09-17 (四, EOD 后) | 7 | 20 | **双条件满足** |
| 09-18 (五, 复验) | 7 | 20 | **PASS ✓** |

**结论**: D11 真实达标日 = **09-17 EOD 后 (20/20), 09-18 复验可 PASS**。
09-02 复验将 FAIL (样本不足), 属**预期结果**, 不应被当作 "D11 转绿日", 也不构成排期回归。

## 4. 待确认事项 (需决策)

1. **min_shadow_samples=20 是否为硬性要求?**
   - 若硬性 → ROADMAP 排期需修正: D11 达标日 09-02 → 09-19
   - 这影响: 09-12 Sprint 1 收尾判定 (依赖 D11 绿) / 09-13 shadow 30 天 cron / 12-10 冻结窗
   - 若可调 (如降至 7) → 需评估降低样本数的统计风险, 并修改 `phase_b_progressive_enabler.py:111` 默认值

2. **ROADMAP "09-02 复验" 的真实含义?**
   - 选项 a: 期望 D11 转绿 → 与 §3 推演矛盾, 需修正排期
   - 选项 b: 仅 "到 7/7 时间点验证一次" (允许 FAIL) → 需在 ROADMAP 明确, 避免误读为达标日

3. **daily_health_log 是否有批量回填机制?**
   - 当前 `phase_b_progressive_enabler.py:293` 是每日 append 1 条
   - 若存在历史回填, 样本数可能快进 → 需确认

## 5. 09-02 复验执行步骤 (检查清单)

```bash
cd "28-终极量化交易系统8.4"

# 1. 运行 D11 门禁 (周三不降级, 硬判定)
python scripts/engineering_debt_gate.py
#   期望输出: D11 PhaseB shadow 未达标 (7/7 天, 8/20 或 9/20 样本) → FAIL
#   ★ FAIL = 预期结果 (样本瓶颈), 不是回归; 唯一异常情形见 §6 stable_days 归零

# 2. 核对状态文件
python -c "import json; d=json.load(open('reports/evolution/phase_b_status.json')); print('stable_days=', d['consecutive_stable_days']); print('samples=', len(d['daily_health_log'])); print('last=', d['daily_health_log'][-1])"
#   期望: stable_days=7, samples>=8, last.date=2026-09-01 或 09-02, last.healthy=true

# 3. 确认 09-01/09-02 交易日 healthy=true
#    若任一日 shadow 不健康 → consecutive_stable_days 归零, D11 排期从归零日 +20 交易日起重估

# 4. 记录复验结果到 cairn/LOG.md
#    - 预期情形: "D11 09-02 复验 FAIL (8~9/20 样本, stable 7/7 达标), 预期内, 达标日 09-17/09-18"
#    - 异常情形: "D11 断链 (stable 归零), 排期重估" + 根因分析
```

## 6. 风险提示

- **stable 已达标 (7/7) 后仍可能归零**: 若 09-01~09-17 期间任一交易日 shadow 不健康, `consecutive_stable_days` 归零 (phase_b_progressive_enabler.py:291), 需重新累计 7 天 + 样本缺口 → D11 大幅顺延
- 当前 7 天全 healthy; 样本数瓶颈 (7/20) 是唯一约束, 不应只盯 stable_days
- G5 RED-FREEZE 期间, 不得修改 `min_shadow_samples` 默认值以 "加速达标" (属 feat 范畴, 且降低样本数有统计风险)
- 09-12 Sprint 1 收尾判定 ~~依赖 D11 绿~~ → **口径已修正 (2026-09-01)**：Sprint 1 主体收尾判定材料不含 D11（仅 B1+B2 稳定 + daily_workflow + R10），09-12 按 Sprint 1 范围收尾；D11 复验作为独立里程碑 09-18 补章，Sprint 1 收尾判定至此完整闭环。排期文档已同步修正。

## 7. 08-30 旧快照 (存档)

- 08-29 17:46:59: stage=auto_retrain, stable 6/7, samples 6/20, 6 flags 全 true
- 当日收益序列: -0.107% / -0.488% / +0.064% / +0.415% / +0.353% / -0.192% (全 healthy)

---

# 2026-09-18 复验版清单 (R-5 预刷新 2026-09-05)

> **来源**: ROADMAP 结构审查 #3（D11 从"运行了 20 天"升级为"连续 20 个样本证明整个生产闭环可信"）→ 拍板 R-5。
> **判据不变铁律**: `engineering_debt_gate.py` D11 代码判据保持双条件不变（stable≥7 AND samples≥20）；本节完整性子项为**人工核对附加项**，避免复验口径第三次漂移。

## A. 代码判据（自动，不变）

```bash
python scripts/engineering_debt_gate.py
# 期望 09-18: D11 [OK] (7+/7 天, 20/20 样本)
```

## B. 数据完整性（人工核对）

| 项 | 核对方法 | PASS 判据 |
|---|---------|----------|
| no missing EOD | `daily_health_log` 日期序列与交易日历逐日比对（09-19~09-17 窗口内） | 每交易日恰好 1 条，无缺口（节假日除外） |
| no duplicated sample | 同上序列按日期去重计数 | 去重前后条数一致 |
| timestamp monotonic | `daily_health_log` 各条时间戳 | 单调不减 |

## C. 风险完整性（人工核对）

| 项 | 核对方法 | PASS 判据 |
|---|---------|----------|
| NAV reconciliation | S12 shadow NAV（`reports/shadow/s12_*`）与内部逐日收益回算 NAV 复核 | 偏差 < 0.1%（浮点容差） |
| position reconciliation | S12 持仓权重和 = 1，无负权重 | 纯 shadow 无真实持仓，权重一致性即可 |

## D. 执行完整性（人工核对）

| 项 | 核对方法 | PASS 判据 |
|---|---------|----------|
| build fills consumed | `reports/fills/` strategy=build fills 被 shadow 消费（P3.0 已验 194 笔通道） | 消费链路无断档日志 |
| no unexplained fills | fills 逐笔有 source 标记（live_route/sim_route） | 无无来源成交 |

## E. 运营完整性（人工核对）

| 项 | 核对方法 | PASS 判据 |
|---|---------|----------|
| backup fresh | T4 备份链每日 17:30 任务（D 盘 + manifest SHA256） | 复验日备份为当日/前一日 |
| alert functioning | `utils/notify` 三通道（复验当日 EOD degradation 有告警记录即证） | 通道可达 |
| recovery test | T4 恢复演练（09-02 已通过，之后无备份链变更即延续有效） | 演练记录在案且备份链未变更 |

## 判定规则

- 代码判据（A）FAIL → D11 FAIL，顺延重估（既有逻辑）
- 代码判据 PASS + 完整性子项（B~E）全 PASS → **D11 复验 PASS，Sprint3-1 解锁**
- 代码判据 PASS + 任一完整性子项 FAIL → **不自动 PASS**：记录问题 + 修复后人工复验（B~E 项可当日重核，A 判据不受影响）；连续 3 个交易日无法闭环 → 上报用户拍板

---
更新者: CodeArts (2026-08-30 周日预准备; 2026-09-01 状态刷新 + 达标日前移至 09-17/09-18; **2026-09-05 R-5 预刷新: 追加 09-18 复验版完整性子项 B~E, 代码判据不变**)
下一步: 09-01 EOD 后核对 samples=8; 09-02 执行 §5 复验步骤 (FAIL=预期); ~~09-12 前修正 Sprint 1 收尾判定材料口径~~ ✅ 已于 2026-09-01 修正; **09-18 按"2026-09-18 复验版清单"执行（A~E 全流程）**
