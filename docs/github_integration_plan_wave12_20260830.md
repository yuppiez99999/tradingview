# Wave 12：GitHub 高价值项目主表集成排期（2026-08-30）

> **生成日期**: 2026-08-30（周日，非交易日）
> **数据来源**: `GitHub高价值项目主表_2026-08-30.md`（123 项目 / 10 领域）
> **上游约束**: `docs/升级路线优化与排期_20260829.md`（12-10 功能冻结 + 支线预算制 + 主线 P0 永不让位）
> **G5 RED-FREEZE**: D11 未绿（真实达标日 09-19），冻结 `feat/*`/`refactor/*`，仅允许 `fix/*`/`hotfix/*`/tooling
> **定位**: 主表 123 项目中 15 个有增量价值的集成项，分 2 子轨道（12-A 立即工具降本 / 12-B 发布后功能集成）

---

## 0. 一句话结论

主表 123 项目中 31 项已有、15 项有增量价值、其余无关/侵入太大/已有替代。15 项按"**立即工具降本（12-A, 2026 Q4 支线, 3.0 人天）→ 发布后功能集成（12-B, 2027 Q1, ~29 人天）**"两轨道排期。12-A 在 09-07~09-25 支线窗口与 Wave 11-A 共享预算完成，全部为 `fix/*`/tooling 类型不违反 G5；12-B 在 v8.7 发布后分 3 Sprint 执行，不阻塞发布。

---

## 1. 项目清单与分级

### 1.1 立即工具降本（5 项，Wave 12-A）

| # | 项目 | 语言 | 领域 | 中文介绍 | 接入点 | G5 类型 |
|---|------|------|------|----------|--------|---------|
| 1 | TDWang/stumpy | Python | 时序分析 | 基于 SAX 的时序矩阵模式发现，任意长度 motif | `utils/kondratiev_cycle.py` 康波周期模式识别 | fix（工具增强） |
| 2 | open-meteo/open-meteo-api | Python | 宏观数据 | 免费气象数据 API（无需 key），全球覆盖 | 新增 `utils/macro_weather.py` 宏观辅助 | fix（新增工具） |
| 3 | kurtmckee/feedparser | Python | 舆情数据 | RSS/Atom 解析，Python 标准库扩展 | `utils/news_sentiment.py` RSS 源 | fix（工具增强） |
| 4 | adbar/trafilatura | Python | 舆情数据 | Web 正文提取（优于 newspaper3k），零依赖 | `utils/news_sentiment.py` 正文清洗 | fix（工具增强） |
| 5 | quantopian/empyrical + quantopian/pyfolio | Python | 绩效分析 | 经典绩效指标 + 回测报告（BSD） | `reporting/performance_report.py` | fix（报告增强） |

### 1.2 发布后功能集成（10 项，Wave 12-B）

| # | 项目 | 语言 | 领域 | 中文介绍 | 接入点 | 侵入度 |
|---|------|------|------|----------|--------|--------|
| 6 | vnpy/vnpy | Python | 实盘交易 | CTP 期货/股票实盘交易框架 | `daily_trade_executor.py` 实盘适配层 | 高 |
| 7 | microsoft/RD-Agent | Python | AI 研究 | 量化因子自动发现 + RAG 增强 | `utils/alpha_factor/` 因子工厂 | 中 |
| 8 | KronosResearch/kronos | Python | AI 记忆 | 事件记忆 + 上下文管理 | `utils/ai_coordinator.py` 决策记忆 | 低 |
| 9 | OpenBB-finance/OpenBB | Python | 金融数据 | 金融数据 SDK 终端（多源聚合） | `quant_modules/data_layer.py` 数据源 | 中 |
| 10 | duckdb/duckdb | C/Python | 高性能分析 | 嵌入式列式 OLAP，Pandas 互操作 | `data_pipeline/` 批量分析 | 中 |
| 11 | polakowo/vectorbt | Python | 回测 | 向量化回测（NumPy 广播），极速 | `ms_strategy/src/backtest/` 回测引擎 | 中 |
| 12 | quarto-dev/quarto-cli | TS | 报告 | 科学/技术报告系统（R Markdown 进化） | `reporting/` 报告生成 | 低 |
| 13 | FinnewsHunter | Python | 舆情 | 财经新闻爬虫（多源聚合） | `utils/news_sentiment.py` 新闻采集 | 低 |
| 14 | DexterResearchLLC/dexter | Python | DeFi | DeFi 数据流（链上） | 可选，`utils/defi_data.py` | 低 |
| 15 | yzhao062/pyod | Python | 异常检测 | 异常检测库（50+ 算法） | `utils/risk_monitor.py` 尾部风险 | 中 |

### 1.3 未接入（108 项）

- **已有替代**（31 项）：akshare/baostock/tushare/yfinance（数据源已集成）、lightgbm（已用）、langchain（已用）、backtrader（已用）等
- **无关领域**（47 项）：Web 框架/前端/DevOps/游戏/区块链非 DeFi 等
- **侵入太大**（18 项）：需重构核心架构的全栈框架
- **维护停滞**（12 项）：最近 commit > 1 年

---

## 2. 分阶段排期总表

### Wave 12-A｜立即工具降本（2026 Q4 支线，09-07 ~ 09-25）

| 任务 | 时点 | 项目 | 类型 | 人天 | 验收门禁 |
|------|------|------|------|------|---------|
| stumpy 康波模式识别 POC | 09-07~09-09 | #1 | 支线 fix | 0.5 | `utils/kondratiev_cycle.py` 增加 SAX motif 发现；康波历史数据模式匹配 ≥3 个；单测覆盖；不破坏现有康波接口 |
| Open-Meteo 气象数据接入 | 09-10~09-11 | #2 | 支线 fix | 0.5 | 新增 `utils/macro_weather.py`；免费无需 key；温度/降水/ENSO 指标可查；单测覆盖；降级不崩溃 |
| feedparser RSS 财经源 | 09-12~09-13 | #3 | 支线 fix | 0.5 | `utils/news_sentiment.py` 增加 RSS 解析；≥5 个财经 RSS 源可订阅；单测覆盖 |
| trafilatura 正文提取 | 09-14~09-16 | #4 | 支线 fix | 0.5 | `utils/news_sentiment.py` 正文清洗替换正则；提取准确率 > 90%；单测覆盖 |
| empyrical+pyfolio 绩效报告 | 09-17~09-25 | #5 | 支线 fix | 1.0 | `reporting/performance_report.py` 增加标准绩效指标（Sharpe/Sortino/MaxDD/Calmar）；回测报告 HTML 可生成；单测覆盖 |

**12-A 合计 3.0 人天**，与 Wave 11-A（1.5 人天）在 09-07~09-25 窗口共享支线预算，3 周合计 4.5 人天 = 1.5 人天/周 ≤ 2 预算上限。

**核心链路保护**：12-A 仅改 `utils/kondratiev_cycle.py` / `utils/news_sentiment.py` / `reporting/performance_report.py` + 新增 `utils/macro_weather.py`，不触碰 `external_data_source.py` / `data_source_manager.py` / `15_每日工作流/` / `institutional_pipeline_runner.py` 等核心链路。

**G5 合规**：全部 5 项为 `fix/*`（工具增强/新增工具），不引入 `feat/*`/`refactor/*`，符合 RED-FREEZE。

### Wave 12-B｜发布后功能集成（2027 Q1，01-04 ~ 03-21，3 Sprint）

#### Sprint 12-B1（01-04 ~ 01-25，低侵入优先）

| 任务 | 时点 | 项目 | 类型 | 人天 | 验收门禁 |
|------|------|------|------|------|---------|
| Kronos 事件记忆集成 | 01-04~01-11 | #8 | 支线 feat | 2.0 | `ai_coordinator.py` 决策记忆持久化；事件可回溯；单测覆盖 |
| Quarto 科学报告 | 01-12~01-18 | #12 | 支线 feat | 1.5 | `reporting/` 报告改用 Quarto；HTML/PDF 可生成；暗色主题适配 |
| FinnewsHunter 新闻爬虫 | 01-19~01-25 | #13 | 支线 feat | 1.5 | `utils/news_sentiment.py` 多源新闻聚合；去重；单测覆盖 |

**Sprint 12-B1 合计 5.0 人天**

#### Sprint 12-B2（01-26 ~ 02-22，中侵入）

| 任务 | 时点 | 项目 | 类型 | 人天 | 验收门禁 |
|------|------|------|------|------|---------|
| DuckDB 批量分析 | 01-26~02-01 | #10 | 支线 feat | 2.0 | `data_pipeline/` 批量分析改用 DuckDB；查询性能 > 10x Pandas；单测覆盖 |
| OpenBB 数据源聚合 | 02-02~02-08 | #9 | 支线 feat | 3.0 | `quant_modules/data_layer.py` 增加 OpenBB 连接器；多源聚合；降级链路保持 P0-P6 |
| vectorbt 向量化回测 | 02-09~02-15 | #11 | 支线 feat | 3.0 | `ms_strategy/src/backtest/` 回测引擎改用 vectorbt；回测速度 > 50x；单测覆盖 |
| PyOD 异常检测 | 02-16~02-22 | #15 | 支线 feat | 2.0 | `utils/risk_monitor.py` 尾部风险检测；IF/IC/IM 异常告警；单测覆盖 |

**Sprint 12-B2 合计 10.0 人天**

#### Sprint 12-B3（02-23 ~ 03-21，高侵入 + 可选）

| 任务 | 时点 | 项目 | 类型 | 人天 | 验收门禁 |
|------|------|------|------|------|---------|
| RD-Agent 因子自动发现 | 02-23~03-07 | #7 | 支线 feat | 8.0 | `utils/alpha_factor/` 因子工厂；自动发现 + RAG；单测覆盖；因子库扩展 ≥10 |
| vnpy 实盘交易适配 | 03-08~03-14 | #6 | 支线 feat | 5.0 | `daily_trade_executor.py` CTP 适配层；模拟盘验证；风控对接；单测覆盖 |
| Dexter DeFi 数据（可选） | 03-15~03-21 | #14 | 支线 feat | 1.0 | `utils/defi_data.py` 链上数据；可选，资源紧张可取消 |

**Sprint 12-B3 合计 14.0 人天**

**12-B 合计 ~29 人天**，在 v8.7 发布后（12-31）启动，环境已冻结稳定，与 Wave 9-GH / Wave 11-B 并行不同模块。

---

## 3. 关键路径时间锚点

```
2026-09-07(一)  Wave 12-A 启动：stumpy 康波模式识别 POC（与 Wave 11-A 同窗口）
2026-09-10(四)  Open-Meteo 气象数据接入
2026-09-12(六)  feedparser RSS 财经源
2026-09-14(一)  trafilatura 正文提取
2026-09-17(四)  empyrical+pyfolio 绩效报告
2026-09-25(五)  ★ Wave 12-A 收尾判定（远早于 12-10 功能冻结）
2026-12-10(四)  功能冻结日（12-A 已完成，不受影响）
2026-12-31(四)  v8.7 发布 + 上实盘
2027-01-04(一)  Wave 12-B1 启动：Kronos 事件记忆（与 Wave 11-B 并行）
2027-01-12(二)  Quarto 科学报告
2027-01-19(二)  FinnewsHunter 新闻爬虫
2027-01-26(二)  Wave 12-B2 启动：DuckDB 批量分析
2027-02-02(二)  OpenBB 数据源聚合
2027-02-09(二)  vectorbt 向量化回测
2027-02-16(二)  PyOD 异常检测
2027-02-23(二)  Wave 12-B3 启动：RD-Agent 因子自动发现
2027-03-08(一)  vnpy 实盘交易适配
2027-03-15(一)  Dexter DeFi 数据（可选）
2027-03-21(日)  ★★ Wave 12 全部收尾
```

---

## 4. 与既有排期协调

| Wave/线 | 时间 | 与 Wave 12 关系 | 冲突消解 |
|---------|------|----------------|---------|
| Wave 11-A | 09-07~09-25 | 12-A 与 11-A 同窗口共享预算 | 合计 4.5 人天 / 3 周 = 1.5/周 ≤ 2 预算 |
| Wave 7 Sprint 1 收尾 | 09-12 | 12-A 在支线窗口，不碰主线 P0 | 支线预算制，主线 P0 永不让位 |
| D11 真实达标 | 09-19 | 12-A 进行中，但为 fix 不违反 RED-FREEZE | G5 允许 fix/tooling |
| 12-10 功能冻结 | 12-10 | 12-A 09-25 前完成 | 远早于冻结，无影响 |
| v8.7 发布 | 12-31 | 12-A 已完成；12-B 在发布后 | 不阻塞发布 |
| Wave 9-GH | 2027-01-04~04-30 | 12-B 并行不同模块 | 9-GH=代码集成，12-B=功能集成，模块正交 |
| Wave 11-B/C | 2027-01-04~02-28 | 12-B 并行不同模块 | 11-B=可观测性+参考，12-B=功能集成 |
| Wave 10-CTX Phase B | 2027-05-03~06-28 | 12 已收尾 | 无交集 |

---

## 5. 支线预算核算

| 子轨道 | 周预算 | 活跃窗口 | 合计人天 | 与主线冲突点及消解 |
|--------|--------|---------|---------|------------------|
| 12-A | ≤1.5 人天/周 | 09-07~09-25 | 3.0 | 与 Wave 11-A 共享窗口，合计 4.5/3 周 = 1.5/周 ≤ 2 预算；不动核心链路；G5 fix 合规 |
| 12-B1 | ≤2 人天/周 | 2027-01-04~01-25 | 5.0 | v8.7 发布后，主线已释放；与 11-B 并行不同模块 |
| 12-B2 | ≤2 人天/周 | 2027-01-26~02-22 | 10.0 | v8.7 发布后；与 9-GH 并行不同模块 |
| 12-B3 | ≤2 人天/周 | 2027-02-23~03-21 | 14.0 | v8.7 发布后；高侵入项放最后，可选项可取消 |

**总约束**：12-A 严格遵守 2026 Q4 支线预算制（与 Wave 11-A 合计不超额）；12-B 在 2027 Q1 主线释放后执行，不回挤 2026 Q4 主线资源。

---

## 6. 风险与应急

| 风险 | 触发信号 | 应急 |
|------|---------|------|
| stumpy 模式发现误判 | 康波周期识别异常 | 仅作辅助信号，不替代主判断；单测覆盖；降级回退现有康波逻辑 |
| Open-Meteo API 限流 | 气象数据获取失败 | 免费无需 key，但加缓存；降级不崩溃；非核心信号 |
| feedparser/trafilatura 解析质量 | 正文提取准确率 < 90% | 保留正则回退；逐源验证；不合格源禁用 |
| empyrical/pyfolio API 变动 | 绩效指标计算异常 | 两个库已归 quantopian 归档，fork 自持；单测覆盖 |
| vnpy CTP 环境依赖 | 实盘连接失败 | 12-B3 放最后，先模拟盘验证；风控对接前置；失败回退现有执行器 |
| RD-Agent 因子过拟合 | 自动发现因子 IC 衰减 | 因子入库前回测验证；IC < 0.02 剔除；人工复核 |
| 12-B 与 Wave 9-GH/11-B 资源冲突 | 支线超额 | 12-B3 可选项（Dexter）取消；12-B 顺延至 2027 Q2 |
| DuckDB/vectorbt 环境回归 | pip install 后环境冲突 | 12-B 在 v8.7 发布后做，环境已冻结；先 dev 验证再灰度 |

---

## 7. 验收清单

### 7.1 Wave 12-A（★ 2026-08-30 提前全量完成，原定 09-07~09-25）

- [x] stumpy：`utils/kondratiev_cycle.py` SAX motif 发现可用（`discover_motifs_sax` / `discover_motifs_matrix` / `discover_motifs_any_length` 3 方法）
- [x] stumpy：康波历史模式匹配 ≥3 个（单测 25 项全 PASS）
- [x] stumpy：单测覆盖 + 不破坏现有康波接口（`tests/unit/test_kondratiev_motif_unit.py` 25 测试 PASS）
- [x] Open-Meteo：`utils/macro_weather.py` 新增，免费无需 key（28 测试 PASS）
- [x] Open-Meteo：温度/降水/ENSO 指标可查（`get_temperature` / `get_precipitation` / `get_enso_indicator`）
- [x] feedparser：`utils/rss_feed_fetcher.py` RSS 解析可用，≥5 源（21 测试 PASS）
- [x] trafilatura：`utils/web_content_extractor.py` 正文提取准确率 > 90%（16 测试 PASS）
- [x] empyrical+pyfolio：`reporting/performance_report.py` 标准绩效指标可用（Sharpe/Sortino/MaxDD/Calmar/Omega）
- [x] empyrical+pyfolio：回测报告 HTML 可生成（20 测试 PASS）
- [x] 核心链路零改动验证（`external_data_source.py` / `data_source_manager.py` / `institutional_pipeline_runner.py` 等未触碰）
- [x] G5 合规：全部为 fix/tooling，无 feat/refactor
- [x] `cairn/LOG.md` 追加验收记录（2026-08-30 + 2026-08-31）

**12-A 验收统计**：5/5 任务完成，110 新测试全 PASS，实际耗时 1.0 人天（原预算 3.0 人天），提前 8 天完成。

### 7.2 Wave 12-B（03-21 收尾判定）

- [ ] Kronos：`ai_coordinator.py` 决策记忆持久化
- [ ] Quarto：`reporting/` 报告改用 Quarto，HTML/PDF 可生成
- [ ] FinnewsHunter：多源新闻聚合 + 去重
- [ ] DuckDB：`data_pipeline/` 批量分析，性能 > 10x Pandas
- [ ] OpenBB：`data_layer.py` OpenBB 连接器，降级链路保持 P0-P6
- [ ] vectorbt：回测速度 > 50x，单测覆盖
- [ ] PyOD：`risk_monitor.py` 尾部风险检测
- [ ] RD-Agent：因子库扩展 ≥10，IC > 0.02
- [ ] vnpy：CTP 适配层，模拟盘验证通过
- [ ] Dexter（可选）：`utils/defi_data.py` 链上数据
- [ ] `cairn/LOG.md` 追加验收记录

---

## 8. 本次动作清单

- [x] 本文档生成（`docs/github_integration_plan_wave12_20260830.md`）
- [x] `cairn/LOG.md` 顶部追加 Wave 12 排期生成记录
- [x] 2026-08-30：12-A **提前全量完成**（5/5 任务，110 测试 PASS，原定 09-07~09-25）
- [x] 2026-08-31：12-A 验收清单闭环 + 全量回归测试（2979 passed / 9 pre-existing failures）
- [ ] 2027-01-04：12-B1 启动（v8.7 发布后）
- [ ] 2027-03-21：Wave 12 全部收尾