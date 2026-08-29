---
title: TODO from ROADMAP v9.3
created: 2026-08-26
author: automation
---

# TODO 从 `cairn/ROADMAP.md` 拆解（简要版）

说明：此文件由自动化脚本生成，列出高优先级未完成项及简短执行说明。进度用 `cairn/TODO_state.json` 跟踪（由 agent 管理）。

1. 生成 TODO_from_ROADMAP.md — 状态: IN-PROGRESS
   - 说明: 将 ROADMAP 中未完成项拆成可执行任务并写入此文件。

2. 巩固 B2 预热并完成门禁验证 — 状态: NOT STARTED
   - 目标: 确保 B2 预热 3 天完成，检查 `_check_b_order_gate` 健康并验证无越级 B3 启用。
   - 操作: 运行 `scripts/b2_health_check.py`，收集日志到 `reports/b2_preheat/`。

3. 运行 P3.0 影子账户闭环门禁验证 — 状态: NOT STARTED
   - 目标: 验证 ShadowAccount.consume_fills_from_store 与 NAV 回算端到端（需 ≥5 个真实交易日样本）。
   - 操作: 运行 `scripts/verify_p3_0_gate.py`，输出 `reports/p3_0_gate/verification.json`。

4. 启动 MVSK 与 qlib 的 shadow 30 天验证 — 状态: NOT STARTED
   - 目标: 为 `MVSK P5-2` 与 `qlib` 模型启动 30 天 shadow 运行并记录每日差异。
   - 操作: 安排 09-13 cron 启动 `scripts/launch_shadow_30day.py`，并将结果写入 `reports/shadow/`。

5. 列出低覆盖模块并补测占位 — 状态: NOT STARTED
   - 目标: 生成低覆盖模块清单并为每个添加测试占位或 TODO 注释，支持覆盖率提升至 80% 目标。
   - 操作: 运行 `scripts/find_low_coverage.py --threshold 0.6`，并创建 `tests/placeholders/test_<module>.py`。

6. 准备 QMT 实盘 smoke 测试套件 — 状态: NOT STARTED
   - 目标: 编写并运行 QMT 连接的 smoke 测试，确保 `quant_modules/qmt_connector.py` 的下单/撤单/回调链路在 paper 模式无异常。
   - 操作: 新增 `tests/integration/test_qmt_smoke.py`，并在 CI smoke stage 执行。

7. 日志与进展自动写入 `cairn/LOG.md` — 状态: NOT STARTED
   - 目标: 每日将关键进展追加至 `cairn/LOG.md`，并在 `cairn/progress_YYYYMMDD.md` 生成简报。
   - 操作: 实现 `scripts/write_daily_progress.py` 调用点并在每项任务完成后调用。

---
更新者: GitHub Copilot 自动化
