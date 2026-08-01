# T03 覆盖率基线报告

> 生成时间: 2026-07-28 07:44:10
> 任务: T03 pytest 覆盖率基线测量
> 阶段: Phase 1 - 诚实回测 (M1-M3)

## 一、总体覆盖率

| 指标 | 数值 |
|------|------|
| **行覆盖率** | 21.95% |
| 语句覆盖率 | 23.74% |
| 分支覆盖率 | 15.85% |
| 总语句数 | 48,838 |
| 已覆盖语句 | 11,594 |
| 未覆盖语句 | 37,244 |
| 总分支数 | 14,282 |
| 已覆盖分支 | 2,263 |
| 部分覆盖分支 | 541 |

### 与阈值对比

| 项目 | 当前 | 阈值 | 差距 |
|------|------|------|------|
| 行覆盖率 | 21.95% | 60% (.coveragerc) | -38.05% |
| 目标 (T13) | 21.95% | 80% | -58.05% |

**结论**: 当前覆盖率 **21.95%**, 远低于 60% 阈值 (CI 会 fail), 距离 T13 的 80% 目标缺口 58.05 个百分点.

## 二、最需要补测试的文件 (Top 20, 按未覆盖行数排序)

| 文件 | 覆盖率 | 语句数 | 未覆盖 |
|------|--------|-------|-------|
| `automated_execution_system.py` | 9.8% | 982 | 866 |
| `hedge_engine_v59.py` | 10.5% | 910 | 777 |
| `enhanced_trainer.py` | 1.4% | 739 | 726 |
| `data_provider.py` | 13.7% | 777 | 646 |
| `risk_guard_integrator.py` | 22.4% | 811 | 631 |
| `ml_predictor_v59.py` | 4.9% | 653 | 612 |
| `hedge_rebalance_v59.py` | 14.8% | 690 | 556 |
| `futures_scan.py` | 0.0% | 548 | 548 |
| `daily_build_and_hedge.py` | 4.4% | 551 | 521 |
| `pre_deploy.py` | 0.0% | 519 | 519 |
| `signal_fusion_v59.py` | 0.0% | 456 | 456 |
| `ifind_client.py` | 8.4% | 499 | 439 |
| `smart_trigger.py` | 11.8% | 484 | 417 |
| `stat_sig.py` | 0.0% | 399 | 399 |
| `qlib_signal_adapter.py` | 0.0% | 370 | 370 |
| `psi_monitor.py` | 0.0% | 370 | 370 |
| `web_scraper.py` | 0.0% | 369 | 369 |
| `alpha_factor_library.py` | 0.0% | 354 | 354 |
| `enhanced_fusion.py` | 0.0% | 350 | 350 |
| `data_quality_monitor.py` | 0.0% | 340 | 340 |

## 三、覆盖最好的文件 (Top 20, >=80%)

| 文件 | 覆盖率 | 语句数 |
|------|--------|-------|
| `decision_theories.py` | 84.7% | 541 |
| `daily_panel.py` | 88.2% | 560 |
| `ab_testing.py` | 81.2% | 324 |
| `risk_bus.py` | 81.3% | 188 |
| `broker_adapters.py` | 91.3% | 332 |
| `core.py` | 86.1% | 255 |
| `mlops_pipeline.py` | 82.8% | 175 |
| `drift_monitor.py` | 87.7% | 204 |
| `finance_agent_orchestrator.py` | 83.9% | 176 |
| `macro_indicator.py` | 92.7% | 279 |
| `managers.py` | 91.6% | 241 |
| `sector_rotation.py` | 92.7% | 194 |
| `risk_module_adapters.py` | 91.6% | 155 |
| `logger.py` | 83.8% | 112 |
| `deflated_sharpe.py` | 82.9% | 101 |
| `multi_factor_signal.py` | 92.7% | 240 |
| `momentum_agent.py` | 80.1% | 95 |
| `feature_flags.py` | 90.2% | 153 |
| `report_sections.py` | 93.8% | 285 |
| `tca_post_trade_attribution.py` | 94.0% | 230 |

## 四、基线意义与后续策略

### 基线意义
- **当前起点**: 21.95% (11,594/48,838 行已覆盖)
- **目标终点**: 80% (T13 任务, Phase 2)
- **缺口**: 需新增覆盖约 **27,476 行** 才能达到 80%

### 修复策略 (T13 任务执行时参考)

**P0 优先 (Phase 2 M4-M5)**:
1. `utils/kill_switch.py` - 风控核心, 已有 15 个 unit test, 需补到 95%+
2. `utils/risk/` 全部模块 - 已有 50+ 个 unit test
3. `utils/execution/broker_adapters.py` - 实盘交易路径, 已有 70+ 个 test
4. `utils/config_manager.py` - 配置加载, 影响全局

**P1 重要 (Phase 2 M5-M6)**:
5. `utils/data_provider.py` - 数据层核心
6. `utils/portfolio_optimizer.py` - 组合优化
7. `utils/risk_metrics.py` - 风险指标
8. `utils/alpha/` 目录 - alpha 因子

**P2 普通 (Phase 2 持续)**:
9. `utils/attribution/` - 归因分析
10. `utils/reporting/` - 报告生成
11. `v8.3_institutional/` - 旧版本模块

### 工作量估算
- 假设每 50 行未覆盖代码需要 1 个测试用例
- 27,476 行 / 50 = **549 个测试用例**
- 按每天写 15-20 个高质量测试用例估算, 约需 **30 个工作日**
- 建议分配在 Phase 2 (M4-M6) 3 个月内完成

## 五、CI 集成建议

当前阶段 (T03) 仅测量基线, **不**将 60% 阈值启用为 CI 失败条件 (否则会阻塞所有 PR).
等 T13 任务补齐测试后再启用 `--cov-fail-under=80`.

## 六、数据文件

- 原始 JSON: [T03_COVERAGE_BASELINE.json](./T03_COVERAGE_BASELINE.json)
- 本报告: [T03_COVERAGE_BASELINE.md](./T03_COVERAGE_BASELINE.md)
