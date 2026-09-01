# P3.1 启动检查清单 (2026-09-01 预准备)

> 创建: 2026-09-01 (周二) | 目标启动日: 09-04 (周一)
> 关联: `scripts/verify_p3_0_gate.py` (P3.0 终端校验), `docs/升级路线优化与排期_20260829.md` §2-4
> LOG 指针: `cairn/LOG.md` 2026-09-01 条目
> 前置链: P3.0 数据 5/5 @09-03 → 终端校验 → 解锁 P3.1 @09-04 → P3.2 30 天窗口 09-05~10-06 → P3.3 评估 10-06

## 1. P3.1 启动硬门禁 (全部 PASS 方可启动)

| # | 门禁 | 检查方式 | 09-01 当前状态 | 09-04 预期 |
|---|------|---------|---------------|-----------|
| 1 | **P3.0 数据积累 ≥5 交易日** (v3) | `verify_p3_0_gate.py` v3 | 2/5 (08-31 LOG) | **5/5 ✓** (09-01/02/03 三交易日 +1) |
| 2 | **shadow 消费 fills 链路** (v1) | `verify_p3_0_gate.py` v1 | 待 09-03 验 | PASS (build fills → shadow NAV 回算) |
| 3 | **每日 PnL 过滤** (v2) | `verify_p3_0_gate.py` v2 | 待 09-03 验 | PASS (无异常 PnL) |
| 4 | **B1+B2 稳定 ≥7 天** | `phase_b_status.json` consecutive_stable_days | **7/7 ✓ 已达标** | 保持 7/7 (需 09-01~09-04 全 healthy) |
| 5 | **D11 状态** (非阻塞, 仅记录) | `engineering_debt_gate.py` D11 | stable 7/7 + samples 7/20 | stable 7/7 + samples 9/20 (FAIL 预期内, 达标日 09-17/09-18) |

**判定**: 门禁 1-4 全 PASS → P3.1 可启动; 门禁 5 FAIL 不阻塞 (D11 达标日 09-17/09-18, 与 P3.1 并行).

## 2. 09-03 终端校验执行步骤 (P3.0 解锁日)

```bash
cd "28-终极量化交易系统8.4"

# 1. 运行 P3.0 三项门禁
python scripts/verify_p3_0_gate.py
#   期望: v1 PASS / v2 PASS / v3 PASS (5/5 交易日)
#   若 v3 FAIL (数据 <5): P3.1 顺延 (每缺 1 交易日顺延 1 天, P4 灰度同步顺延)

# 2. 核对 fills 文件
python -c "
from pathlib import Path
import json, glob
fills = sorted(glob.glob('reports/fills/fills_*.jsonl'))
build_count = sum(1 for f in fills[-7:] for line in open(f, encoding='utf-8') if '\"strategy\": \"build\"' in line or '\"strategy\":\"build\"' in line)
print(f'近 7 天 build fills: {build_count}')
"
#   期望: build_count >= 5

# 3. 记录校验结果到 cairn/LOG.md
#    - 全 PASS: "P3.0 终端校验 PASS (5/5), P3.1 解锁, 09-04 启动"
#    - 任一 FAIL: "P3.0 校验 FAIL (v? 原因), P3.1 顺延"
```

## 3. 09-04 P3.1 启动动作 (待 09-03 解锁后细化)

> **注**: 启动动作具体清单待 09-03 P3.0 解锁后, 根据 `shadow_account_system.py` 与 P3.2 30 天窗口需求细化. 当前预登记:

- [ ] **账户配置启动**: shadow 账户从"数据积累模式"切换至"30 天验证模式" (P3.2 窗口 09-05~10-06)
- [ ] **B1+B2 稳定确认**: 09-04 早间复核 `phase_b_status.json` stable 仍 7/7 (09-01~09-03 三交易日全 healthy)
- [ ] **D11 状态记录**: 09-04 时 samples 9/20, FAIL 预期内, 不阻塞 P3.1
- [ ] **P3.2 cron 注册**: 30 天窗口每日 EOD 自动跑 P3.2 shadow 比对 (待 09-04 启动后配置)
- [ ] **ROADMAP/LOG 同步**: P3.1 启动后更新排期状态

## 4. 风险与降级

- **数据积累延迟**: 若 09-01/02/03 任一交易日无真实成交 → P3.0 数据 <5/5 → P3.1 顺延 (每缺 1 天 +1 天), P4 灰度同步顺延
- **B1+B2 断链**: 若 09-01~09-03 任一交易日 shadow 不健康 → consecutive_stable_days 归零 → P3.1 门禁 4 FAIL → 需重新累计 7# 7 天 (大幅顺延)
- **D11 与 P3.1 关系**: D11 达标日 09-17/09-18, P3.2 窗口 09-05~10-06, 两者并行不冲突; 但 09-12 Sprint 1 收尾判定依赖 D11 绿 → 届时 D11 必然未绿 (16/20), 收尾判定材料需按 09-17/09-18 口径预修正 (见 `docs/d11_reverify_checklist_20260830.md` §6)

## 5. 后续排期锚点

| 日期 | 事件 | 依赖 |
|------|------|------|
| 09-03 (五) | P3.0 终端校验 | 数据 5/5 |
| **09-04 (一)** | **P3.1 启动** | P3.0 PASS + B1+B2 稳定 7/7 |
| 09-05~10-06 | P3.2 30 天窗口 | P3.1 启动 |
| 10-06 (二) | P3.3 评估 | P3.2 窗口结束 |
| 10-07~ | P4 灰度发布 | P3.3 评估 PASS |

---
更新者: CodeArts (2026-09-01 预准备)
下一步: 09-01~09-03 每日 EOD 后核对 P3.0 数据计数; 09-03 执行 §2 终端校验; 09-04 执行 §3 启动动作
