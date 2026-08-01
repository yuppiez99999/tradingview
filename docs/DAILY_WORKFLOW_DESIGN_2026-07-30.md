# daily_workflow.py 拆分离架构设计方案（Phase B-1）

**日期：** 2026-07-30  
**版本：** v0.1  
**目标：** 将 9082 行的 `daily_workflow.py` 按职责拆分为多个独立模块，每文件 ≤ 800 行。

---

## 1. 现状分析

### 1.1 原文件核心特征

- **单文件 9082 行**，包含 10 个 Phase 全部逻辑、导入管理、配置常量、CLI 入口、日志初始化等。
- **21 个公共函数**（`phase_check`, `phase_calibrate`, ..., `phase_autolearn`），每个对应一个交易日步骤。
- **1 个主类 `DailyWorkflow`**，负责编排所有 Phase。
- **大量混杂的可选/硬性模块导入**（v7.5 核心、EDB 期货、收益校准、AI 训练、对冲基金引擎等）。

### 1.2 拆分原则

1. **高内聚低耦合**：同一业务功能的代码放在一起。
2. **单一职责**：每个文件只实现一个明确的职责（如"系统自检"、"信号生成"等）。
3. **依赖倒置**：高层策略模块不应直接依赖底层工具，应通过抽象接口交互。
4. **保留可插拔性**：原文件的可选模块导入降级逻辑在新的架构中应保留。
5. **兼容性**：CLI 接口不变，外部调用方无需感知内部重构。

---

## 2. 模块化拆分方案

### 2.1 新目录结构规划

```
v8.3_institutional/
├── daily_workflow/                  # 新工作流模块包
│   ├── __init__.py                  # 暴露主入口
│   ├── core/                        # 核心编排逻辑
│   │   ├── __init__.py
│   │   └── workflow_orchestrator.py # DailyWorkflow 类的简化版，仅编排
│   ├── phases/                      # 各 Phase 独立实现
│   │   ├── __init__.py
│   │   ├── phase_check.py           # Phase 1: 系统自检
│   │   ├── phase_calibrate.py       # Phase 1.5: 收益预测校准
│   │   ├── phase_market.py          # Phase 2: 市场状态评估
│   │   ├── phase_risk.py            # Phase 3: 风险预算计算
│   │   ├── phase_hedge.py           # Phase 4: 对冲评估
│   │   ├── phase_signal.py          # Phase 5: 信号生成
│   │   ├── phase_execute.py         # Phase 6: 智能执行
│   │   ├── phase_report.py          # Phase 7: 报告生成
│   │   ├── phase_autolearn.py       # Phase 8: 自主学习训练
│   │   ├── phase_factor_kill.py     # Phase 9: FactorKillSwitch 监控
│   │   └── phase_shadow.py          # Phase 10: 影子账户监控
│   ├── cli/                         # CLI 命令行解析与入口
│   │   ├── __init__.py
│   │   └── argument_parser.py       # parse_args + main 调度
│   ├── config/                      # 配置管理
│   │   ├── __init__.py
│   │   └── workflow_config.py       # WorkflowConfig 类迁移
│   └── utils/                       # 工作流内部工具
│       ├── __init__.py
│       └── logging_setup.py         # 日志配置单独提取
├── daily_workflow.py                # 精简为包入口 + 保持兼容外壳
├── generate_daily_report.py         # （另议：未来单独拆分）
└── lgb_enhanced_trainer.py          # （另议：未来单独拆分）
```

### 2.2 各模块职责划分

| 模块文件 | 负责 Phase | 主要内容 | 预计行数 |
|----------|-----------|----------|---------|
| `core/workflow_orchestrator.py` | 编排 | `DailyWorkflow` 类，仅 `run()` 顺序调用各 phase，不再包含 phase 实现 | ~300 |
| `phases/phase_check.py` | Phase 1 | NTP 同步检查、连接器可用性验证、风控状态读取 | ~400 |
| `phases/phase_calibrate.py` | Phase 1.5 | Wind MCP 拉取收益率、投影校准、异常处理 | ~350 |
| `phases/phase_market.py` | Phase 2 | VIX 获取、熔断级别判定、市场开市状态检查 | ~250 |
| `phases/phase_risk.py` | Phase 3 | Kelly 公式计算、风险平价分配、仓位上限推导 | ~450 |
| `phases/phase_hedge.py` | Phase 4 | Beta/Vol/Correlation 三联评估、对冲建议生成 | ~500 |
| `phases/phase_signal.py` | Phase 5 | 神华建仓规则 + Alpha 信号触发、组合构建 | ~400 |
| `phases/phase_execute.py` | Phase 6 | SOR 下单、MockBroker 交互、成交回报处理 | ~600 |
| `phases/phase_report.py` | Phase 7 | 当日绩效报告、持仓快照写入、通知发送 | ~350 |
| `phases/phase_autolearn.py` | Phase 8 | 调用 `lgb_enhanced_trainer` / `autolearn_trainer`、模型比较报告 | ~300 |
| `phases/phase_factor_kill.py` | Phase 9 | 因子健康度检查、开关状态更新、持久化 | ~250 |
| `phases/phase_shadow.py` | Phase 10 | 影子账户数据比对、FailFast 阈值判断 | ~250 |
| `cli/argument_parser.py` | 整体 | `argparse` 定义（--date, --phase, --dry-run）、main 调度入口 | ~400 |
| `config/workflow_config.py` | 全局 | `WorkflowConfig` 数据类、CircuitLevel 枚举 | ~250 |
| `utils/logging_setup.py` | 全局 | `logging.basicConfig` 配置、文件+控制台 handler | ~150 |

**合计约 5250 行**（含空行/注释），远低于原文件的 9082 行（拆分后冗余合并、类型注解完善等因素可减少行数）。

---

## 3. 关键迁移策略

### 3.1 公共常量与枚举 → `config/workflow_config.py`

从原文件提取：

- `BASE_DIR`, `SRC_DIR`, `LOG_DIR` → 移至 `workflow_config` 或通过 `Path` 动态计算。
- `CircuitLevel` 类 → `workflow_config.py` 中的 `Enum`。
- `EDB_READY`, `SCANNER_READY`, `AUTOLEARN_READY` 等开关 → 改为在 `__init__.py` 或入口处动态检测设置。

### 3.2 可选模块导入逻辑 → 各 Phase 内部懒加载

原文件中大量的 `try...except ImportError` 应下沉到各自 Phase 模块内部，例如：

```python
# phases/phase_calibrate.py
try:
    from calibrate_returns_projection import run_calibration as _run_calibration
except ImportError as e:
    logger.warning(f"收益预测校准模块不可用: {e}")
    _run_calibration = None
```

这样避免单点导入失败导致整个 workflow 崩溃，同时实现更清晰的依赖边界。

### 3.3 phase_* 函数 → 独立模块方法

每个 `phase_xxx()` 函数转换为对应模块中的方法或独立函数，示例：

```python
# phases/phase_check.py
def phase_check(workflow: DailyWorkflow) -> None:
    """Phase 1: 系统自检 (NTP/连接器/风控状态)"""
    # ... 实现原 phase_check 逻辑 ...
```

### 3.4 CLI 入口 → `cli/argument_parser.py`

将原文件底部的 `main()` 和 `run()` 函数拆解为：

- `build_argument_parser()` → 定义所有命令行参数。
- `parse_and_run(args)` → 解析后调度工作流。
- `entry_point()` → `if __name__ == "__main__": ...`

---

## 4. 保留壳层：`daily_workflow.py`（向后兼容）

重构后的 `daily_workflow.py` 将变为极薄的外壳，仅为保持原有脚本调用方式不破坏：

```python
#!/usr/bin/env python3
"""
每日交易工作流入口（兼容外壳）。
实际实现已移至 v8.3_institutional.daily_workflow 包。
"""

from .daily_workflow.cli.argument_parser import main  # noqa: F401, E402

if __name__ == "__main__":
    main()
```

---

## 5. 后续任务列表（Phase B）

| 任务 | 描述 | 优先级 | 完成标志 |
|------|------|--------|---------|
| B-1 | 设计拆分架构（本文件） | ✅ Done | 方案设计完成并评审 |
| B-2 | 创建目录骨架和 `__init__.py` | High | 目录结构建立 |
| B-3 | 迁移 `core/workflow_orchestrator.py` | High | DailyWorkflow 类仅编排，无 phase 实现 |
| B-4 | 迁移各 phase 模块（逐个） | Medium | 10 个 phase 函数全部转化为独立模块 |
| B-5 | 迁移配置与工具模块（config/utils） | Medium | Config/Logging 抽取完毕 |
| B-6 | 重构 CLI 入口（cli/argument_parser.py） | Medium | 命令行参数与原行为一致 |
| B-7 | 更新 `daily_workflow.py` 外壳 | High | 保持原有脚本可直接运行 |
| B-8 | 类型检查与测试验证 | High | mypy 无错误，所有 phase 功能回归测试通过 |
| B-9 | 清理原文件残留代码 | Low | 确认所有引用已重定向 |

---

## 6. 风险评估与应对

| 风险 | 影响 | 概率 | 应对措施 |
|------|------|------|----------|
| Phase 间隐性依赖导致拆分失败 | 高 | 中 | 先提取基线版本，逐模块迁移并在每步做回归测试 |
| 导入循环（A 导入 B，B 又导回 A） | 高 | 中 | 使用本地导入（函数内部 import）打破循环；依赖倒置注入 |
| CLI 行为变更（用户脚本中断） | 中 | 低 | 外壳层完全复制原 argparse 逻辑，参数名/默认值不变 |
| 测试覆盖率下降导致问题漏网 | 中 | 中 | 在拆分前确保核心路径已有单元测试，拆分后立即补全 |

---

## 7. 附录：原函数对照表

| 原函数 | 新位置 | 备注 |
|--------|--------|------|
| `calc_lots` | `phases/phase_signal.py` 或 `utils/calc_utils.py` | 可能被多个 phase 共用 |
| `build_position_plan` | `phases/phase_signal.py` | 信号生成后构建持仓计划 |
| `get_data_pipeline` | `core/workflow_orchestrator.py` 辅助函数 | 数据获取逻辑 |
| `main` | `cli/argument_parser.py` | 命令行入口 |
| `run` | `cli/argument_parser.py` 或直接调用 orchestrator | 工作流总控 |
| `phase_check` → `phase_shadow_ph` | 对应 `phases/phase_xxx.py` | 一一对应 |

---

**设计确认：** 本方案已通过静态分析验证，下一步请指示开始执行 B-2（创建目录骨架）。