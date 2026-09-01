# 实证研究 Skill 索引 (Empirical Research Skills)

> 源自 [Auto-Empirical-Research-Skills (AERS)](https://github.com/...) — Stanford 实证研究 1150+ skill 库
> 整理日期: 2026-08-22

本目录收录与**量化策略实证验证**最相关的研究方法 Skill prompt，供 AI 协调器
（`utils/ai_coordinator.py`）在策略验证、因子检验、回测分析时引用。

## 收录 Skill（8 个）

| Skill | 用途 | 量化场景 |
|-------|------|----------|
| [empirical_analysis_full.md](empirical_analysis_full.md) | 完整实证分析工作流（StatsPAI） | 策略全流程验证：描述统计→推断→诊断→稳健性 |
| [causal_inference_mixtape.md](causal_inference_mixtape.md) | 因果推断 10 法（Cunningham Mixtape） | 验证策略收益因果性，排除混杂偏误 |
| [pyfixest_econometrics.md](pyfixest_econometrics.md) | Python 高性能固定效应回归 | 面板因子回归，毫秒级估计 |
| [did_analysis.md](did_analysis.md) | 双重差分 (DiD) | 政策事件研究：十五五规划对板块冲击 |
| [iv_estimation.md](iv_estimation.md) | 工具变量 (IV/2SLS) | 因子内生性处理 |
| [rdd_analysis.md](rdd_analysis.md) | 断点回归 (RDD) | 退市/纳入指数的断点效应 |
| [ml_causal.md](ml_causal.md) | ML + 因果推断 | Double ML/TARN/T-learner 因子因果效应 |
| [panel_data.md](panel_data.md) | 面板数据分析 | 横截面+时间序列因子面板回归 |

## 使用方式

这些 Skill 是 **AI prompt 模板**（非 Python 代码），用法：

1. **AI 协调器引用**: `utils/ai_coordinator.py` 在策略验证任务中加载对应 prompt
2. **CLI 模式调用**: `python 量化策略系统_统一入口_v8.6.py --empirical-validate`
3. **人工参考**: 研究员可直接阅读 prompt 中的方法说明与代码模板

## 与主系统的协同

| 主系统模块 | 推荐 Skill |
|-----------|-----------|
| `utils/alpha_factor/` (11大类因子) | `ml_causal` + `panel_data` — 因子因果效应 + 面板回归 |
| `utils/hedge_rebalance_backtest.py` | `did_analysis` — 对冲政策事件研究 |
| `utils/kondratiev_cycle.py` | `empirical_analysis_full` — 周期假设完整检验 |
| `utils/five_year_plan.py` | `did_analysis` + `rdd_analysis` — 政策冲击因果识别 |
| `ms_strategy/src/backtest/` | `causal_inference_mixtape` — 排除策略收益的混杂偏误 |

## AERS 全库

AERS 完整库含 70 个顶级目录 / 1756 子目录 / 3657 文件 / 1150+ skill，覆盖：
学术写作、贝叶斯统计、复现研究、文献综述、LaTeX、Stata/R/Python 计量等。
本目录仅收录与量化交易最相关的 8 个。如需更多，访问 AERS 原仓库。
