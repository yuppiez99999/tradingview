---
type: project_topic
status: active
authoring_mode: ai_generated
created: 2026-08-02
updated: 2026-08-02
---

# 外部引用索引

> 记录项目中引用的外部来源（研报、API、论文、数据源等），仅保留指针不复制原文。按引用类型分组。

## 研报 / 因子体系

- **GTJA191 因子**：国泰君安 191 个技术类 Alpha 因子。`ms_strategy/factors/gtja191_factors.py` 实现 21 个纯 Python 版。来源：国泰君安研究所量化专题报告。
- **Almgren-Chriss 最优执行**：Almgren, R., & Chriss, N. (2000). "Optimal execution of portfolio transactions." *Journal of Risk*. 实现于 `utils/almgren_chriss/`。

## API / 数据源

- **Open-Meteo**（`api.open-meteo.com`）：免费气象数据 API，用于天气因子引擎降级链。`utils/weather_data_adapter.py`。
- **apizero.cn**（商业 API）：气象数据商业源，已废弃为主源、仅作备选。`utils/weather_data_adapter.py`。
- **AkShare / JQData / RQData**：A 股数据源三选一。`utils/data_provider/`。

## 量化框架

- **Qlib**（Microsoft）：AI 驱动的量化投资框架，本项目集成用于 LightGBM 模型训练和信号生成。`qlib/` 子模块。
- **LightGBM**（Microsoft）：梯度提升决策树框架，用于增强训练器和多因子选股。`lgb_trainer/`、`utils/alpha/`。

## 定价与风控模型

- **Black-Scholes 期权定价**：`fineng/pricing/black_scholes.py` 为本项目 BS 定价的权威实现，14 处调用全部委托至此。
- **GARCH 波动率建模**：`fineng/garch/`，Engle (1982) ARCH + Bollerslev (1986) GARCH。
- **Kalman Beta 估计**：`fineng/kalman/`，状态空间模型动态 Beta。
- **EVT 极值理论**：`fineng/evt/`，GPD 尾部拟合。Embrechts, Kluppelberg & Mikosch (1997)。
- **AQR/Man Group 波动率目标**：`utils/vol_target_controller.py` 参考 AQR 和 Man Group 的波动率缩放方法论。

## 交易算法

- **TWAP/VWAP**：时间加权/成交量加权平均价格算法。`utils/execution/`。
- **Implementation Shortfall**：Perold (1988) + Almgren & Chriss (2000)。`utils/almgren_chriss/`。

## 方法论 / 交叉验证

- **Purged K-Fold**：Marcos Lopez de Prado (2018). *Advances in Financial Machine Learning*. `utils/purged_kfold.py`。
- **MAD 法去极值**：在因子预处理中替代 Z-score 去极值，对异常值更稳健。
- **Shrinkage Estimator**（协方差矩阵）：Ledoit & Wolf (2004)，用于风控归因的协方差矩阵周度更新。

## 数据格式

- **Parquet**（Apache）：列式存储格式，用于缓存行情和因子数据。`data_cache/`、`data/`。

## 第三方工具

- **Scrapling**：反爬虫 Python 库。`utils/scrapling_adapter.py`。
- **TradingAgents-CN**：多 Agent 协作框架的中文版，通过 HTTP 桥接集成。`utils/tradingagents_bridge.py`。
- **Ollama**（本地 LLM）：qwen2.5:7b 模型用于 LLM 盘中决策引擎。AI 决策降级链的第一级。
