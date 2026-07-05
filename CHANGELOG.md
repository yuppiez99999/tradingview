# 更新日志 (Changelog)

所有重要的项目变更都会记录在此文件中。

格式基于 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.0.0/)，
并且本项目遵循 [语义化版本](https://semver.org/spec/v2.0.0.html)。

## [7.5.1] - 2026-07-06

### 新增 (Added) — 7/6 自动交易建仓与全自动运行

- ✅ 7/6 建仓计划文件
  - `v7.5_institutional/trade_plans/trade_plan_20260706.json`
  - `v7.5_institutional/trade_plans/trade_plan_20260706.md`
- ✅ 交易计划格式对齐 `daily_workflow.py`
  - 支持 `phase` + `execution_plan.morning_orders/afternoon_orders`
  - 首日 10 笔订单，金额 1,091,964 元
- ✅ 交易日自动运行
  - 盘前 07:00：`v75_PreMarket`，执行 `run_all_modules.bat pre`
  - 盘后 15:30：`v75_PostMarket`，执行 `run_all_modules.bat post`
- ✅ 干跑验证通过
  - `daily_workflow.py --date 2026-07-06 --dry-run`
  - Phase 1-7 全部通过，报告生成成功

### 变更 (Changed)

- 🔄 `README.md`
  - 新增“7/6 自动建仓已就绪”状态说明
  - 更新资金配置：股票 47% + 对冲 40% + 现金 13%
  - 更新首日建仓标的与执行摘要

### 验证 (Verified)

- ✅ `daily_workflow.py` 读取 `trade_plan_20260706.json` 正常
- ✅ Windows 任务计划已注册并查询成功
- ✅ README 已更新并推送至 GitHub

## [7.5.0] - 2026-07-05

### 新增 (Added) — 机构级实盘交易系统

- ✅ 风险预算：Risk Parity + Improved Kelly Criterion + 三级回撤防御
- ✅ 三联对冲：Beta/Vol/Correlation 三类对冲实时联动
- ✅ 智能执行：Iceberg + TWAP/VWAP/POV + 滑点熔断 + NTP 时间同步
- ✅ 回测严谨性：Walk-Forward + 三段极端行情压力测试
- ✅ 全模块自动调度：盘前 Wind 校准 + v5 优化 + 每日工作流；盘后 黑天鹅测试 + 汇总报告
- ✅ Qlib 信号集成：本地 LightGBM 深度学习信号生成，36 个技术指标特征，自动注入 SignalFusion 并映射为订单调整（加仓 30% / 减仓 50% / 跳过）
- ✅ 订单智能调整：Qlib 信号直接参与 Phase 5/6，强看多自动加仓、中性维持、看空减仓或跳过，实现信号到执行的闭环

## [Unreleased]

### 规划中
- 实现投资组合优化（增加国际资产配置）
- 实现风险平价优化（动态风险调整）
- 增强实时监控功能（5分钟频率）
- 添加更多压力测试场景
- 优化机器学习预测模型

## [7.4.0] - 2026-07-04

### 新增 (Added) — v7.4 个股分档建仓执行系统

- ✅ 建仓计划生成器 (`generate_shenhua_build_plan.py`)
  - 基于外部 PDF 研报《中国神华股价分析与建仓策略》(2026-07-03) 数字化建仓策略
  - 三档建仓配置：底仓(40-42元,35%)/加仓(36-39元,35%)/重仓(32-35元,30%)
  - 股息率锚定：4.64% → 5.20% → 5.80% 三档递增
  - 输出 JSON + Markdown 双格式计划文件
  - 100万资金测试：26,700股，平均成本37.28元

- ✅ 建仓执行器 (`shenhua_build_executor.py`)
  - `ShenhuaBuildExecutor` 类：492行，含建仓计划加载/进度查询/订单生成/报告输出
  - 智能价格档位判断：4种场景（折价买入/正常拆分/溢价跳过/异常阻断）
    - 折价买入：价格 < 中枢 × (1-2%)，当日全额买入
    - 正常拆分：中枢 ±合理区间，拆分上下午两批
    - 溢价跳过：价格 > 中枢 × (1+3%)，当日跳过
    - 异常阻断：偏离中枢 > 8%，触发风控审查
  - Phase 4 风险联动：emergency_level 0=正常 / 1=资金减半 / ≥2=阻断
  - 数据结构：`Order` 和 `DailyTradeSheet` dataclass
  - 报告输出：reports/YYYY-MM-DD/shenhua_orders_YYYYMMDD.md + .json

- ✅ 工作流集成（条件导入，优雅降级）
  - `trading_workflow.py` — HAS_V74_MODULES 标志，集成 Phase 11 个股建仓阶段
  - WorkflowPhase 枚举新增 SHENHUA_BUILD = "11_shenhua_build"
  - `phase_shenhua_build()` 方法实现：含 Phase 4 风险联动 + 智能档位判断
  - CLI 新增 `--mode shenhua_build` 选项
  - 工作流版本号升级：v7.3 → v7.4
  - 工作流摘要新增 `v74_modules` 字段

### 变更 (Changed)

- 🔄 `trading_workflow.py` — 升级为 v7.4
  - L73-81: 新增 v7.4 条件导入块（ShenhuaBuildExecutor）
  - L150-160: WorkflowPhase 枚举新增 SHENHUA_BUILD
  - L1785-1873: 新增 `phase_shenhua_build()` 方法
  - L1890: 工作流版本 "v7.3" → "v7.4"
  - L1911-1913: `_build_workflow_summary()` 新增 v74_modules 字段
  - L1921: 控制台打印 "v7.2" → "v7.4"
  - L2142-2153: CLI epilog 和 choices 新增 shenhua_build
- 🔄 `README.md` — 更新为 v7.4 版本
  - 标题升级 v7.3 → v7.4，副标题新增"个股分档建仓"
  - 系统概述新增 v7.4 核心升级描述
  - 新增 v7.4 详细章节（设计理念/三档策略/价格判断/风险联动/工作流集成/使用示例/测试验证）
  - 版本历史表新增 v7.4 行
  - 版本信息更新为 v7.4

### 验证 (Verified)

- ✅ 5种场景测试全部通过：
  - 正常区间：38.5元 → 拆分上下午两批
  - 折价买入：35.5元（中枢下方2%+）→ 当日全额买入
  - 溢价跳过：41.5元（中枢上方3%+）→ 当日跳过
  - 紧急半仓：emergency_level=1 → 资金倍率 50%
  - 紧急阻断：emergency_level=2 → 阻断建仓
- ✅ 工作流端到端测试：`phase_shenhua_build()` 返回 `ok=True`
- ✅ 100万资金建仓计划验证：26,700股，平均成本37.28元
- ✅ 条件导入测试：HAS_V74_MODULES=True 时正常加载，=False 时优雅降级

### 设计说明

**研报→JSON→订单全自动转化**：
传统建仓依赖人工阅读研报、手动计算档位和股数，效率低且易出错。v7.4 将研报建仓策略结构化为 JSON 计划文件，由执行器根据实时价格自动判断当前应执行的档位和订单数量，实现"研报→JSON→订单"的全自动转化。

**非阻塞设计**：
建仓阶段作为盘后第 11 阶段自动执行，无计划文件或模块不可用时自动跳过，不影响主交易流程。通过 `HAS_V74_MODULES` 标志实现条件导入，与 v7.1.2/v7.3 的条件导入机制保持一致。

## [7.1.2] - 2026-07-04

### 新增 (Added) — v7.1.2 模块反向同步（来源: ZCodeProject）

- ✅ 黑天鹅极端行情优化器 (`black_swan_optimizer.py` v7.2)
  - 6大组件：日内动态熔断/风控执行引擎/相关性崩溃模型/期权流动性调整/涨跌停板处理/对手方风险监控
  - 解决原系统 `_stop_trading` / `_emergency_hedging` 空实现问题
  - 完成"预警工具"→"自动风控系统"执行闭环

- ✅ 决策护栏层（4个模块，整合自 daily_stock_analysis 项目）
  - `utils/market_context_guardrail.py` — 大盘保守环境软化激进买入建议
  - `utils/phase_decision_guardrail.py` — 盘前/盘中/盘后时段行为约束
  - `utils/alert_service.py` — 6类预警评估（价格/技术/组合/信号灯等）
  - `utils/semantic_backtest.py` — 中英文操作建议语义解析+止损止盈模拟

- ✅ 工作流集成（条件导入，优雅降级）
  - `trading_workflow.py` — HAS_V712_MODULES 标志，集成 alert_service + guardrail + SemanticBacktest
  - `live_trading_workflow.py` — HAS_V712_MODULES 标志，集成市场上下文构建 + 预警评估 + 护栏校验

- ✅ 研究文档
  - `research_report_black_swan_resilience.md` — 2000年互联网泡沫+2008年次贷危机深度韧性评估
  - `README_and_workflow_update_summary.md` — v7.1.2 工作流集成完成总结

### 变更 (Changed)

- 🔄 `trading_workflow.py` — 覆盖为 v7.1.2 版本（原版备份: .bak.20260704_0615）
  - 丢失 safe_float 防御代码(3处)，由 try-except 兜底替代
- 🔄 `live_trading_workflow.py` — 覆盖为 v7.1.2 版本（原版备份: .bak.20260704_0615）
  - 丢失 safe_float 防御代码(15处)，由 30 个 try-except 块兜底替代
- 🔄 `README.md` — 更新为 v7.1.2 版本（原版备份: .bak.20260704_0615）
  - 新增 v7.1.2 模块描述、工作流集成点、第三方模块测试命令

### 验证 (Verified)

- ✅ 语法检查通过：7个文件 py_compile 全部 OK
- ✅ 导入测试通过：5个核心模块全部 OK
- ✅ 实例化测试通过：BlackSwanOptimizer 6大组件全部 ON
- ✅ v7.1.2 集成代码验证：HAS_V712_MODULES=True，5个模块全部可访问
- ✅ 基础工作流测试：`--check-today` 正常运行

### 已知限制 (Known Limitations)

- ⚠️ 丢失 safe_float 防御代码（28系统原版有，ZCodeProject版本无）
  - 风险等级：中（ZCodeProject 有 30 个 try-except 块兜底）
  - 影响：实时交易场景异常数据可能跳过订单而非优雅降级
  - 恢复方案：可从 .bak.20260704_0615 备份手动合并 safe_float 代码

## [5.10.0] - 2026-07-03

### 新增 (Added)
- ✅ 多层次对冲策略实现
  - Delta对冲策略 (60%资金)
  - 波动率对冲策略 (30%资金)  
  - 尾部风险对冲策略 (10%资金)
  
- ✅ 智能对冲触发机制
  - 市场情绪监控
  - 技术指标分析
  - 机器学习预测
  - 多重验证机制
  
- ✅ 动态资金管理器
  - 风险预算分配
  - 动态调整机制
  - 市场自适应调整
  
- ✅ 增强风险管理器
  - 实时风险监控
  - 多级风险阈值
  - 压力测试引擎
  - 自动风险控制
  
- ✅ 自动化执行系统
  - 7:00 AM定时执行
  - 市场状态评估
  - 智能订单路由
  - 异常处理机制
  
- ✅ 策略优化器
  - 多策略整合
  - 回测验证
  - 实时优化
  - 性能评估
  
- ✅ 配置管理系统
  - 动态参数配置
  - 风险参数设置
  - 执行配置管理
  - 配置备份和恢复
  
- ✅ 监控仪表板
  - Web界面监控
  - 命令行监控
  - 实时状态显示
  - 风险警报系统
  
- ✅ 系统部署脚本
  - 自动化部署
  - 环境配置
  - 依赖检查
  - 一键启动

### 改进 (Improved)
- 🔄 系统架构优化
  - 模块化设计
  - 清晰的接口定义
  - 错误处理机制
  
- 🔄 数据处理优化
  - 数据缓存机制
  - 数据验证
  - 性能优化
  
- 🔄 日志系统
  - 多级日志
  - 文件和终端输出
  - 日志轮转
  
- 🔄 用户界面
  - 简化的启动方式
  - 直观的监控界面
  - 详细的错误信息

### 修复 (Fixed)
- 🐛 修复了模块导入问题
- 🐛 修复了数据获取超时问题
- 🐛 修复了内存泄漏问题
- 🐛 修复了并发访问问题

### 文档 (Documentation)
- 📝 完善的用户指南
- 📝 详细的项目文档
- 📝 目录结构说明
- 📝 部署指南

## [5.9.0] - 2026-06-15

### 新增
- 基本框架搭建
- 核心策略模块
- 数据提供器
- 风险指标计算

### 改进
- 代码结构优化
- 错误处理机制
- 日志系统

### 修复
- 内存使用优化
- 并发处理问题

## [5.8.0] - 2026-06-01

### 新增
- 项目初始化
- 基础架构设计

---

## 版本说明

### 版本号格式
- 主版本号：重大功能变更
- 次版本号：新功能添加
- 修订号：问题修复

### 发布周期
- 重大版本：每季度发布
- 功能版本：每月发布
- 修复版本：按需发布

### 兼容性说明
- 重大版本可能引入不兼容的变更
- 功能版本保持向后兼容
- 修复版本完全向后兼容

### 贡献指南
如需贡献代码，请遵循：
1. Fork 项目
2. 创建功能分支
3. 提交变更
4. 推送到分支
5. 创建 Pull Request

---

**注意：** 此项目仍在积极开发中，可能会有重大变更。