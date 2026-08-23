# 细粒度任务分解工作流 (Fine-Grained Task Decomposition)

> **文献**: #15 Toward Expert Investment Teams (Fine-Grained) (2026.02, ★★★★★)
> **任务**: LIT-2.1 细粒度任务分解重构 AI Hedge Fund
> **状态**: ✅ 已完成 (2026-08-23)
> **LOG 指针**: `cairn/LOG.md` → "2026-08-23 · LIT-2.1"

## 核心思想

将 20 位分析师的**纯角色模拟**重构为**细粒度任务分解架构**。

| 维度 | 旧架构 | 新架构 |
|------|--------|--------|
| 单元 | 分析师 agent (单一函数) | 任务节点 (TaskNode) |
| 编排 | LangGraph 线性流程 | 任务图 DAG (拓扑排序) |
| 复用 | 每个分析师独立收集数据 | 共享数据收集任务 |
| 并行 | 分析师间并行 | 任务级并行 (按层级分组) |
| 接口 | `agent_func(state) → state` | 兼容旧接口 + 内部任务图 |

## 7 种任务类型

```python
class TaskType(str, Enum):
    DATA_COLLECTION = "data_collection"        # 数据收集
    FEATURE_EXTRACTION = "feature_extraction"  # 特征提取
    SIGNAL_GENERATION = "signal_generation"   # 信号生成
    RISK_ASSESSMENT = "risk_assessment"       # 风险评估
    PORTFOLIO_CONSTRUCTION = "portfolio_construction"  # 组合构建
    DEBATE = "debate"                         # 多空辩论
    REFLECTION = "reflection"                 # 自反思
```

## 任务图 DAG

- **TaskNode**: task_id + task_type + analyst + dependencies + executor + result + status
- **TaskGraph**: 节点字典 + 拓扑排序 (Kahn 算法) + 并行分组 (按层级)
- **循环检测**: 拓扑排序结果长度 ≠ 节点数时抛出 ValueError

## 单分析师任务分解

```
data_collection → feature_extraction → signal_generation → risk_assessment → reflection(可选)
```

- 默认 5 个任务 (含反思), 禁用反思时 4 个
- 每个任务依赖前一个, 形成线性链

## 团队任务分解

```
shared_data → {analyst_1_features, analyst_2_features, ...}
            → {analyst_1_signal, analyst_2_signal, ...}
            → team_debate(可选, 需≥2分析师)
            → portfolio_construction
```

- **共享数据收集**: 一次收集, 多分析师复用
- **并行特征提取**: 各分析师并行执行
- **多空辩论**: 可选, 依赖所有信号
- **组合构建**: 依赖辩论结果或所有信号

## 执行引擎

- `execute(graph, context)`: 按拓扑顺序执行, 依赖结果传入下游上下文
- `execute_parallel_groups(graph, context)`: 按并行分组执行, 同组任务可并行
- **异常处理**: 捕获 ValueError/KeyError/TypeError 等, 标记任务失败, 不中断流程

## 旧接口兼容

```python
agent = create_fine_grained_agent("warren_buffett")
new_state = agent(state)  # 兼容 agent_func(state) → state 接口
```

内部使用任务图, 对外保持与旧 LangGraph agent 相同的签名。

## 交付物

| 文件 | 行数 | 说明 |
|------|------|------|
| `quant_modules/ai_hedge_fund/fine_grained_workflow.py` | ~578 | 核心实现 |
| `tests/unit/test_fine_grained_workflow_unit.py` | ~603 | 60 单元测试全绿 |

## 测试覆盖

- TaskType 枚举完整性 (3 测试)
- TaskNode 数据结构 (4 测试)
- TaskGraph DAG (11 测试: 拓扑排序/依赖解析/并行分组/循环检测)
- 默认执行器 (9 测试)
- build_analyst_tasks (5 测试)
- build_team_tasks (7 测试)
- execute (7 测试: 含失败/空执行器/并行)
- get_task_summary (2 测试)
- 旧接口兼容层 (4 测试)
- ANALYST_NAMES 完整性 (4 测试)
- 端到端集成 (3 测试)

## ruff.toml 豁免

```toml
# T201: CLI main() 演示入口 print 是合理的
# UP042: str+Enum 继承保留 Py3.8 兼容 (StrEnum 需 Py3.11+)
"quant_modules/ai_hedge_fund/**/*.py" = ["E402", "ANN", "C901", "T201", "UP042"]
```

## 后续方向

- **LIT-2.2**: TradingGroup 自反思机制 — 增强 `utils/ai_coordinator.py`
- **实际集成**: 将 `create_fine_grained_agent` 接入 `orchestrator.py` 替换旧 agent_func
- **真实执行器**: 替换 7 个 mock 执行器为实际数据/因子/信号/风控模块