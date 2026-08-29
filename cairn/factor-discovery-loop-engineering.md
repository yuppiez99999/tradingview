# 因子发现 Loop Engineering 升级方案

> **状态**: 设计阶段（未实施）
> **创建**: 2026-08-05
> **LOG 指针**: `cairn/LOG.md` → "因子发现 Loop Engineering 升级方案设计"
> **参考**: 中金研究《大模型系列（7）：基于 Loop Engineering 的自动化因子发现引擎》(2026-08-03)

---

## 一、背景与动机

### 1.1 问题

当前系统的因子是**人工设计**的：VT_MICRO_VOL_SKEW、VT_QUALTREND_MARGIN_EXP 等因子在 [vibe_trading_factor_adapter.py](file:///e:/各种PY程序/28-终极量化交易系统8.4/research/vibe_trading_factor_analysis/adapters/vibe_trading_factor_adapter.py) 中以硬编码函数形式定义。PipelineOrchestrator 做的是因子**计算→评估→组合**，不涉及因子**生成**。

这意味着：
- 因子空间受限于人工想象力
- 无法系统性地探索表达式组合空间
- 没有"失败因子学习"机制（测试过的无效因子不会被记住）

### 1.2 中金方案的启发

中金研究的 Loop Engineering 框架用 LLM + 五维演化策略自动生成因子表达式，581 轮迭代测试 16,939 个候选，保留 69 个因子，Top 5 复合夏普 3.14。核心优势：
1. LLM 评判经济学含义，避免无意义统计因子
2. 五维演化（变异/交叉/扰动/随机/LLM）系统探索表达式空间
3. FSA 频繁子树规避防止同质化
4. 检查点持久化支持断点续跑

### 1.3 目标

构建一套与现有架构集成的自动化因子发现引擎，从"人工设计因子"升级为"LLM 引导 + 演化搜索 + 机器验证"的闭环。

---

## 二、现状分析

### 2.1 我们有什么

| 模块 | 位置 | 可复用性 |
|---|---|---|
| 因子流水线 | `research/vibe_trading_factor_analysis/pipeline/pipeline_orchestrator.py` | ✅ 因子计算+IC加权组合 |
| 因子定义 | `research/vibe_trading_factor_analysis/adapters/vibe_trading_factor_adapter.py` | ✅ VT_ 因子注册机制 |
| DSR 验证 | `research/vibe_trading_factor_analysis/validators/dsr_validator.py` | ✅ Bailey & Lopez de Prado 实现 |
| 四道关卡 | adapter.py 中定义 | ✅ 正交性/IC稳定性/DSR/经济逻辑 |
| 进化框架 | `research/vibe_trading_factor_analysis/committee/factor_committee.py` | ✅ EvolutionOrchestrator 状态机 |
| Feature Flag | `utils/infra/feature_flags.py` + `configs/feature_flags.yaml` | ✅ 双签控制 |
| LLM 集成 | `ai_decision/decision_gate.py` | ✅ Ollama 封装 |
| 快速回测 | `utils/alpha/fast_backtest.py` | ✅ 单因子回测 |
| VolRegimeWeighter | `utils/alpha/vol_regime_weighter.py` | ✅ Phase 0 实盘验证完成 |

### 2.2 我们缺什么

| 缺失能力 | 影响 |
|---|---|
| **因子表达式树表示** | 无法自动变异/交叉，因子是黑盒函数 |
| **演化引擎** | 没有变异/交叉/参数扰动，无法系统探索 |
| **LLM 因子生成** | LLM 仅用于决策，未用于因子假设生成 |
| **FSA 频繁子树规避** | 无法防止因子同质化 |
| **检查点持久化（因子级）** | progress.json 是进化级的，不是因子发现级的 |
| **11 项联合过滤** | 评估维度不够细致（只有 DSR+IC_IR，没有分年度/分时段） |

### 2.3 约束条件

| 约束 | 来源 | 影响 |
|---|---|---|
| LLM 资源隔离 | project_memory HC | 因子发现只能在盘后运行（15:30 后） |
| V9 基线保护 | project_memory HC | 新因子不能影响 V9 生产基线 |
| Phase 0 边界 | VolRegimeWeighter 观察 | 08-20 前不引入新代码 |
| 标的池差异 | project_memory | 全市场因子不能直接用到 23/105 标的池 |
| 双签要求 | Feature Flag 规范 | 新功能需 USE_FACTOR_DISCOVERY_LOOP 双签 |

---

## 三、目标架构

```
┌─────────────────────────────────────────────────────────┐
│              LoopEngineeringFactorDiscovery              │
│                                                          │
│  ┌─────────────┐  ┌─────────────┐  ┌─────────────┐     │
│  │  生成层      │→ │  审查层      │→ │  验证层      │     │
│  │ Generation   │  │ Review       │  │ Validation   │     │
│  │              │  │              │  │              │     │
│  │ • ExprTree   │  │ • RuleFilter │  │ • FastBack   │     │
│  │ • Evolver    │  │ • LLMReview  │  │ • ElevenFlt  │     │
│  │ • FSA        │  │   (Sub-agent)│  │ • FactorRepo │     │
│  │ • LLMGen     │  │              │  │              │     │
│  └─────────────┘  └─────────────┘  └─────────────┘     │
│          ↑                                    │          │
│          │     ┌─────────────────────┐        │          │
│          └─────│  编排层              │←───────┘          │
│                │  Orchestration       │                  │
│                │                      │                  │
│                │ • LoopEngine         │                  │
│                │ • CheckpointManager  │                  │
│                │ • HookManager        │                  │
│                └─────────────────────┘                  │
│                            │                             │
│  ┌─────────────────────────────────────────────────┐    │
│  │              基础设施 Infrastructure              │    │
│  │  • OllamaClient (14B生成 + 72B审查)              │    │
│  │  • FeatureFlag (USE_FACTOR_DISCOVERY_LOOP)       │    │
│  │  • ResourceManager (盘后运行, 资源隔离)           │    │
│  └─────────────────────────────────────────────────┘    │
└─────────────────────────────────────────────────────────┘
          │
          ↓ (因子入库)
┌─────────────────────────────────────────────────────────┐
│              现有系统 (只读集成)                          │
│  • vibe_trading_factor_adapter.py (四道关卡)             │
│  • dsr_validator.py (DSR 验证)                          │
│  • PipelineOrchestrator (因子计算+IC加权组合)            │
│  • EvolutionOrchestrator (自我进化框架)                  │
└─────────────────────────────────────────────────────────┘
```

---

## 四、核心模块设计

### 4.1 因子表达式树 (ExpressionTree)

**目标**：将因子从硬编码函数升级为可变异/交叉的表达式树。

**数据结构**：

```python
@dataclass
class ExprNode:
    """表达式树节点"""
    op: str           # 算子名: sub, ma, delta, mul, div, rank_cs, ...
    children: list    # 子节点列表 (叶子节点为空)
    field: str = None # 叶子节点字段名: close, open, volume, overnight, ...
    window: int = None # 时间窗口: 5, 10, 20, 60, ...
    params: dict = None # 额外参数

@dataclass
class FactorExpression:
    """因子表达式 (一棵表达式树)"""
    root: ExprNode
    name: str = ""           # 因子名 (自动生成或 LLM 命名)
    origin: str = "loop"     # 来源: loop/llm/mutate/crossover/random
    mechanism: str = ""      # 机制族: overnight/volume/reversal/...
    hash: str = ""           # 结构哈希 (用于去重和 FSA)
    depth: int = 0           # 树深度
```

**算子集（14 种，对齐中金方案）**：

| 算子 | 含义 | 参数 |
|---|---|---|
| `sub` | 减法 A - B | 2 个子节点 |
| `ma` | 移动平均 | window |
| `delta` | 差分 X_t - X_{t-n} | window |
| `mul` | 乘法 A × B | 2 个子节点 |
| `div` | 除法 A / B | 2 个子节点 |
| `rank_cs` | 截面排名 | 1 个子节点 |
| `skew` | 偏度 | window |
| `roc` | 变化率 | window |
| `max` | 滚动最大值 | window |
| `min` | 滚动最小值 | window |
| `std` | 滚动标准差 | window |
| `corr` | 滚动相关系数 | window, 2 子节点 |
| `slope` | 线性回归斜率 | window |
| `ema` | 指数移动平均 | span |

**字段集（对齐现有 VT_ 因子库）**：

```python
# 基础价量字段 (来自日线数据)
BASE_FIELDS = ["open", "close", "high", "low", "volume", "amount"]

# 派生字段 (来自 vibe_trading_factor_analysis)
DERIVED_FIELDS = [
    "overnight",      # 隔夜跳空: open / prev_close - 1
    "amplitude",      # 振幅: (high - low) / prev_close
    "down_shadow",    # 下影线: (min(open,close) - low) / prev_close
    "up_shadow",      # 上影线: (high - max(open,close)) / prev_close
    "hl_ratio",       # 高低比: high / low
    "co_ratio",       # 收开比: close / open
    "vwap",           # 成交量加权均价
    "turnover",       # 换手率
    "free_mv",        # 自由流通市值
    "log_return",     # 对数收益率
    "volume_ma_ratio", # 成交量均线比
    "return_skew",    # 收益偏度
    "return_kurt",    # 收益峰度
]
```

### 4.2 五维演化引擎 (FiveStrategyEvolver)

**预算分配**（对齐中金方案，可配置）：

| 策略 | 默认比例 | 触发条件 |
|---|---|---|
| 变异 (mutate) | 25% | 因子库有高分因子时 |
| 交叉 (crossover) | 25% | 因子库有 ≥2 个高分因子时 |
| 参数扰动 (perturb) | 15% | 因子库有已验证结构时 |
| 随机探索 (random) | 15% | 始终保留 |
| LLM 生成 (llm_generate) | 20% | 始终保留 |

**动态预算调整规则**：

```
if 高分候选大量触发 IC 相关性去重:
    提高 random + llm_generate 比例 (扩大结构多样性)
elif 已验证因子附近仍持续产出高分候选:
    提高 mutate + perturb 比例 (深入开采)
elif 某机制族长期缺少候选:
    llm_generate 优先补充该方向
elif 多机制已覆盖但入库率持续下降:
    提高 crossover 比例 (尝试组合不同信号源)
```

**核心方法签名**：

```python
class FiveStrategyEvolver:
    def evolve(self, factor_pool: list[FactorExpression],
               budget: int = 100) -> list[FactorExpression]:
        """根据因子库状态生成一批候选因子"""
        # 1. 评估因子库状态, 决定预算分配
        allocation = self._compute_budget(factor_pool)
        # 2. 按预算调用五种策略
        candidates = []
        candidates += self._mutate(factor_pool, allocation.mutate)
        candidates += self._crossover(factor_pool, allocation.crossover)
        candidates += self._perturb(factor_pool, allocation.perturb)
        candidates += self._random_generate(allocation.random)
        candidates += self._llm_generate(factor_pool, allocation.llm)
        # 3. FSA 过滤
        candidates = self._fsa_filter(candidates)
        return candidates
```

### 4.3 FSA 频繁子树规避 (FSAManager)

**目标**：防止搜索陷入同质化，超过 15% 阈值的骨架被禁止复用。

```python
class FSAManager:
    THRESHOLD = 0.15  # 15% 频次阈值

    def scan_and_freeze(self, tested_factors: list[FactorExpression]) -> set[str]:
        """扫描已测试因子, 提取频繁子树, 冻结超过阈值的骨架"""
        subtree_counts = self._count_subtrees(tested_factors)
        total = len(tested_factors)
        frozen = set()
        for subtree_hash, count in subtree_counts.items():
            if count / total > self.THRESHOLD:
                frozen.add(subtree_hash)
                logger.info(f"FSA 冻结子树 {subtree_hash}: 频次 {count}/{total} = {count/total:.1%}")
        return frozen

    def is_frozen(self, expr: FactorExpression, frozen_set: set[str]) -> bool:
        """检查因子表达式是否包含被冻结的子树"""
        for subtree_hash in self._extract_subtree_hashes(expr):
            if subtree_hash in frozen_set:
                return True
        return False
```

### 4.4 三步循环流水线

```python
class LoopEngine:
    """因子发现循环引擎 - 每 5 分钟一轮 (盘后运行)"""

    def run_iteration(self) -> IterationResult:
        """单轮迭代: 生成 → 审查 → 验证"""
        # 1. 加载检查点
        state = self.checkpoint.load()

        # 2. 生成阶段
        candidates = self.evolver.evolve(
            factor_pool=state.approved_factors,
            budget=self.config.batch_size  # 默认 100
        )

        # 3. 审查阶段
        # 3a. 规则过滤 (硬编码, 快速)
        candidates = self.rule_filter.filter(candidates)
        # 3b. LLM 精判 (Sub-agent, 抽样 5 个)
        candidates = self.llm_reviewer.review(candidates, sample_size=5)

        # 4. 验证阶段
        for expr in candidates:
            # 4a. 计算因子值
            factor_values = self.compute_factor(expr)
            # 4b. 快速回测
            backtest_result = self.fast_backtest(factor_values)
            # 4c. 11 项联合过滤
            if self.eleven_filter.pass_all(backtest_result, state.approved_factors):
                # 4d. 入库
                state.approved_factors.append(expr)
                logger.info(f"因子入库: {expr.name} (IC={backtest_result.ic:.4f})")

        # 5. 更新检查点 (原子写入)
        state.iteration_count += 1
        state.tested_hashes.update(c.hash for c in candidates)
        self.checkpoint.save(state)

        # 6. Hook: 输出因子库状态摘要
        self.hook_manager.emit_summary(state)

        return IterationResult(...)
```

### 4.5 11 项联合过滤 (ElevenFilter)

**本地化设计**（对齐现有 DSR + IC_IR 标准）：

| # | 过滤条件 | 阈值 | 复用现有模块 |
|---|---|---|---|
| 1 | IC 绝对值 | \|IC\| > 0.03 | 新建 |
| 2 | 2025 年超额 | > 0 | 新建 |
| 3 | 2026 年超额 | > 0 | 新建 |
| 4 | 2025 年夏普 | > 0.5 | 新建 |
| 5 | 2026 年夏普 | > 0.5 | 新建 |
| 6 | Calmar 比率 | > 1.0 | 新建 |
| 7 | 近 9 月超额 | > 0 | 新建 |
| 8 | 近 12 月超额 | > 0 | 新建 |
| 9 | IC 相关性 | < 0.70 (与已入库因子) | 新建 |
| 10 | DSR | > 0 (非过拟合) | ✅ 复用 DSRValidator |
| 11 | IC_IR | > 0.3 | ✅ 复用现有标准 |

### 4.6 检查点持久化 (CheckpointManager)

**检查点文件结构**（复用 progress.json 模式）：

```json
{
  "iteration_count": 581,
  "tested_hashes": ["a1b2c3...", "d4e5f6...", "..."],
  "approved_factors": [
    {
      "name": "loop_factor_001",
      "expression": {"op": "sub", "children": [...]},
      "hash": "a1b2c3...",
      "mechanism": "overnight",
      "origin": "mutate",
      "ic": 0.052,
      "ic_ir": 0.45,
      "sharpe": 1.68,
      "dsr": 0.82,
      "approved_at": "2026-08-20T16:10:00"
    }
  ],
  "fsa_frozen_subtrees": ["frozen_hash_1", "frozen_hash_2"],
  "momentum_tracker": {
    "overnight": {"direction": 1.0, "step": 0.1},
    "volume": {"direction": -0.5, "step": 0.05}
  },
  "mechanism_coverage": {
    "overnight": 45,
    "volume": 12,
    "reversal": 3,
    "momentum": 0
  }
}
```

**原子写入**（避免中断导致状态损坏）：

```python
class CheckpointManager:
    def save(self, state: LoopState) -> None:
        """原子写入: 先写临时文件, 再 rename"""
        tmp_path = self.path.with_suffix(".tmp")
        with tmp_path.open("w", encoding="utf-8") as f:
            json.dump(state.to_dict(), f, ensure_ascii=False, indent=2)
        tmp_path.replace(self.path)  # 原子 rename
```

### 4.7 Sub-agents 生成-审查分离

**目标**：降低"单一模型自我说服"风险。

```
生成 Agent (Ollama 14B):
  - 加载 GenerationSkill (算子定义 + 字段集 + 机制族)
  - 根据因子库覆盖情况, 生成未覆盖机制的因子表达式
  - 输出: FactorExpression + 自然语言假设

审查 Agent (Ollama 72B, 盘后可用):
  - 加载 ReviewSkill (边界条件 + 经济逻辑检查)
  - 独立审查候选因子
  - 输出: approve/reject + 理由
```

**关键设计**：生成和审查加载**不同的 Skill**，在**不同的 LLM 调用**中执行，避免生成模型自己审查自己的输出。

---

## 五、与现有系统集成点

### 5.1 因子入库集成（通过四道关卡）

新发现的因子不直接进入生产，而是通过现有 [vibe_trading_factor_adapter.py](file:///e:/各种PY程序/28-终极量化交易系统8.4/research/vibe_trading_factor_analysis/adapters/vibe_trading_factor_adapter.py) 的四道关卡：

```
LoopEngine 发现因子
    ↓ (FactorExpression → 函数包装)
Gate 1: 正交性检查 |r| < 0.7 (与现有 50+ 因子)
    ↓
Gate 2: IC 稳定性 Walk-Forward IC_IR > 0.5
    ↓
Gate 3: DSR 检验 n_trials > 5 (复用 DSRValidator)
    ↓
Gate 4: 经济逻辑 (LLM 标注 + 人工确认)
    ↓
入库 alpha_factor_library (factor_origin = "loop_discovery")
```

### 5.2 Feature Flag 控制

```yaml
# configs/feature_flags.yaml 新增
USE_FACTOR_DISCOVERY_LOOP:
  default: false
  description: "启用 Loop Engineering 因子发现引擎 (盘后运行, 不影响盘中)"
  requires: "dual_signature"
  rollback_seconds: 30
```

### 5.3 资源隔离策略

| 时段 | LLM 使用 | 因子发现 |
|---|---|---|
| 盘中 (9:25-15:10) | 14B 决策 | ❌ 禁止 |
| 盘后 (15:30-23:00) | 14B 生成 + 72B 审查 | ✅ 运行 |
| 深夜 (23:00-07:00) | 72B 审查 | ✅ 运行 (低优先级) |

**调度方式**：复用 v8.4 定时任务架构，新增 `v84_FactorDiscovery` 任务，每日 15:30 启动，23:00 停止。

### 5.4 与 EvolutionOrchestrator 的关系

```
EvolutionOrchestrator (进化层)
    ↓ 提供因子池状态
LoopEngine (发现层)
    ↓ 发现新因子 → 四道关卡 → 入库
EvolutionOrchestrator 评估新因子 → 决策是否进入 Shadow
```

EvolutionOrchestrator 是"管理者"，LoopEngine 是"工人"。LoopEngine 发现因子，EvolutionOrchestrator 决定哪些因子进入 Shadow 测试。

---

## 六、分阶段实施计划

### Phase A：MVP（2-3 周，08-20 后启动）

**目标**：跑通"生成→审查→验证"三步循环，验证表达式树可行性。

**范围**：
- ✅ ExpressionTree 数据结构 + 14 算子 + 13 字段
- ✅ 三步循环流水线 (简化版)
- ✅ 简化演化策略 (3 维: mutate + random + llm)
- ✅ CheckpointManager (原子写入)
- ✅ 基础 5 项过滤 (IC + DSR + IC_IR + 夏普 + 回撤)
- ✅ Feature Flag USE_FACTOR_DISCOVERY_LOOP

**不做**：
- ❌ 交叉 (crossover) 和参数扰动 (perturb)
- ❌ FSA 频繁子树规避
- ❌ Sub-agents 分离 (先用单一 14B)
- ❌ 11 项完整过滤
- ❌ 动态预算调整

**验收标准**：
- 能从零冷启动，生成 100 个候选因子
- 至少 1 个因子通过四道关卡入库
- 检查点断点续跑验证通过

### Phase B：完整版（2-3 周，Phase A 验收后）

**目标**：五维演化 + FSA + Sub-agents 完整版。

**范围**：
- ✅ 五维演化完整版 (mutate + crossover + perturb + random + llm)
- ✅ FSA 频繁子树规避
- ✅ Sub-agents 生成-审查分离 (14B 生成 + 72B 审查)
- ✅ 11 项联合过滤完整版
- ✅ 动量追踪器 (参数扰动方向引导)
- ✅ Hook 机制 (每轮输出因子库状态)

**验收标准**：
- 500 轮迭代，测试 ≥ 5000 个候选
- 至少 10 个因子通过四道关卡入库
- FSA 至少触发 1 次冻结
- 入库因子平均夏普 > 1.0

### Phase C：优化（持续）

**目标**：数据驱动 + 动态预算 + Graph 演进。

**范围**：
- 数据驱动特征分布 (自动提升有效字段权重)
- 动态预算调整 (exploration vs exploitation 平衡)
- 从 Loop 演进到 Graph (多 Agent 并行)
- 引入另类数据 (龙虎榜、大宗交易)
- 引入分钟级高频因子

---

## 七、风险与缓解

| 风险 | 等级 | 缓解措施 |
|---|---|---|
| **LLM 资源竞争** | 🔴 高 | 盘后运行 + 72B 仅审查不生成 + ResourceManager 监控 |
| **过拟合** | 🔴 高 | DSR 验证 + 11 项联合过滤 + 分年度超额检查 |
| **因子同质化** | 🟡 中 | FSA 频繁子树规避 + IC 相关性 < 0.70 |
| **标的池适配** | 🟡 中 | 因子在 23/105 标的池上重新验证，不直接用全市场因子 |
| **V9 基线干扰** | 🔴 高 | 新因子 factor_origin="loop_discovery"，不直接进生产 |
| **LLM 幻觉** | 🟡 中 | Sub-agents 分离 + 规则过滤前置 + 硬编码回测验证 |
| **检查点损坏** | 🟡 中 | 原子写入 (tmp → rename) + 断点续跑验证 |

---

## 八、资源需求

### 8.1 计算资源

| 资源 | Phase A | Phase B | 说明 |
|---|---|---|---|
| Ollama 14B | 1 次/轮 | 1 次/轮 (生成) | 盘后 15:30-23:00 |
| Ollama 72B | 不用 | 1 次/轮 (审查) | 盘后 + 深夜 |
| CPU | 低 | 中 | 因子计算 + 回测 |
| 内存 | 2GB | 4GB | 因子值缓存 |
| 磁盘 | 1GB | 5GB | 检查点 + 因子库 |

### 8.2 数据资源

| 数据 | 来源 | 频率 |
|---|---|---|
| 日线 OHLCV | 现有数据源 | 每日 |
| 派生字段 (overnight 等) | vibe_trading_factor_analysis | 每日 |
| 标的池 | 23/105 标的 | 每日 |

### 8.3 时间预估

| 阶段 | 工时 | 日历周 |
|---|---|---|
| Phase A (MVP) | 40h | 2-3 周 |
| Phase B (完整版) | 60h | 2-3 周 |
| Phase C (优化) | 持续 | 持续 |

---

## 九、成功指标

### 9.1 Phase A 验收

| 指标 | 目标 |
|---|---|
| 候选因子生成数 | ≥ 100 |
| 入库因子数 | ≥ 1 |
| 检查点断点续跑 | ✅ 通过 |
| 单轮迭代耗时 | < 5 分钟 |

### 9.2 Phase B 验收

| 指标 | 目标 |
|---|---|
| 迭代轮数 | ≥ 500 |
| 测试候选数 | ≥ 5000 |
| 入库因子数 | ≥ 10 |
| 入库因子平均夏普 | > 1.0 |
| FSA 冻结触发 | ≥ 1 次 |
| 机制族覆盖 | ≥ 3 个 |

### 9.3 长期目标（对标中金）

| 指标 | 中金方案 | 我们的目标 |
|---|---|---|
| 迭代轮数 | 581 | 500+ |
| 测试候选数 | 16,939 | 5000+ |
| 入库因子数 | 69 | 20+ |
| Top 5 复合夏普 | 3.14 | > 2.0 |
| 年化超额 | 18.3% | > 15% (对齐 V9 基线) |

---

## 十、文件规划

### 10.1 新增文件

```
utils/alpha/factor_discovery/
├── __init__.py
├── expression_tree.py          # ExpressionTree + ExprNode
├── evolver.py                  # FiveStrategyEvolver
├── fsa_manager.py              # FSAManager
├── loop_engine.py              # LoopEngine
├── checkpoint.py               # CheckpointManager
├── eleven_filter.py            # ElevenFilter
├── llm_generator.py            # LLM 因子生成 (Sub-agent)
├── llm_reviewer.py             # LLM 因子审查 (Sub-agent)
└── hooks.py                    # HookManager

configs/
└── factor_discovery.yaml       # 配置 (算子集 + 字段集 + 预算 + 过滤阈值)

tests/unit/
├── test_expression_tree.py
├── test_evolver.py
├── test_fsa_manager.py
├── test_loop_engine.py
└── test_eleven_filter.py
```

### 10.2 修改文件

```
configs/feature_flags.yaml      # 新增 USE_FACTOR_DISCOVERY_LOOP
scripts/run_factor_discovery.py # 新增定时任务启动脚本
```

### 10.3 不修改的文件（只读集成）

```
research/vibe_trading_factor_analysis/adapters/vibe_trading_factor_adapter.py  # 四道关卡只读
research/vibe_trading_factor_analysis/validators/dsr_validator.py              # DSR 只读
utils/alpha_factor_library.py                                                  # 因子库只读
```

---

## 十一、参考资料

1. **中金研究**：《大模型系列（7）：基于 Loop Engineering 的自动化因子发现引擎》(2026-08-03, 郑文才/周萧潇/刘均伟)
2. **Bailey & Lopez de Prado** (2014): Deflated Sharpe Ratio — 现有 DSRValidator 的理论基础
3. **Anthropic Claude Code 团队**: Loop Engineering 范式
4. **HKUDS/Vibe-Trading**: https://github.com/HKUDS/Vibe-Trading — 现有因子库的来源
5. **项目内参考**:
   - [vibe_trading_factor_adapter.py](file:///e:/各种PY程序/28-终极量化交易系统8.4/research/vibe_trading_factor_analysis/adapters/vibe_trading_factor_adapter.py) — 四道关卡
   - [dsr_validator.py](file:///e:/各种PY程序/28-终极量化交易系统8.4/research/vibe_trading_factor_analysis/validators/dsr_validator.py) — DSR 实现
   - [vol_regime_weighter.py](file:///e:/各种PY程序/28-终极量化交易系统8.4/utils/alpha/vol_regime_weighter.py) — Phase 0 实盘集成参考
   - `cairn/self-evolution-framework.md` — 自我进化框架设计

---

## 修订记录

| 日期 | 版本 | 变更 | LOG 指针 |
|---|---|---|---|
| 2026-08-05 | v1.0 | 初版设计（基于中金 Loop Engineering 报告） | `cairn/LOG.md` → "因子发现 Loop Engineering 升级方案设计" |

---

> **下一步**: 等待 VolRegimeWeighter 观察期（08-20）结束后，启动 Phase A MVP 开发。期间可继续细化表达式树算子集和字段集的定义。

<!-- AUTO-GENERATED: 相关文档 -->
## 相关文档

- [AlphaCFG 语法引导因子发现 (LIT-1.5)](alpha-cfg-discovery.md) (相似度 16%)
- [R&D-Agent-Quant 多智能体因子挖掘引擎 (LIT-1.1)](rd-agent-quant.md) (相似度 13%)
- [异常处理规约 (Exception Handling Standards)](exception-handling-standards.md) (相似度 11%)
- [Alpha 因子体系](alpha-factor-system.md) (相似度 10%)
- [W6.3.3 预研 · QS-Trader 风格 secid 合约解析难点清单](w633_secid_contract_parsing_challenges.md) (相似度 9%)

<!-- 由 scripts/cairn_cross_ref.py 自动生成，请勿手动编辑此区块 -->
