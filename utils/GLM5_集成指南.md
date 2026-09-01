# GLM-5 本地集成与决策训练指南
# 适用于量化交易系统 v5.10

## 适配说明

本指南已按 `量化策略系统 v5.10.py` 的实际架构重写，重点覆盖两类用法：
- **本地训练与预测**：`--train-model`、`--train-enhanced`、`--ml-signal`、`--ml-enhanced`
- **本地决策与报告**：`GLM5DecisionEngine`、`ModelRouter`、`GLM5Client`

v5.10 中 GLM-5 不再只是“报告生成器”，而是多模型决策架构的一部分：
- 场景路由：`intraday_decision` / `rebalancing_analysis` / `macro_analysis` / `report_generation` / `light_analysis`
- 并行对冲：主备模型同时请求，取先返回
- 交叉验证：双模型并行分析，交集采纳、分歧标记
- 熔断器：连续失败自动切换，冷却后恢复

---

## 本地训练管线（v5.10 可直接运行）

### 1. 时序预测训练

```bash
cd e:\各种PY程序\11_量化策略
python "量化策略系统 v5.10.py" --train-model
```

用途：训练基础时序预测模型，输出可用于 `--ml-signal` 的信号。

### 2. ML 增强训练 v2.0（推荐）

```bash
# T+5 中期预测
python "量化策略系统 v5.10.py" --train-enhanced --horizon 5

# T+10 长期预测 + 贝叶斯调参
python "量化策略系统 v5.10.py" --train-enhanced --horizon 10 --optuna
```

用途：四维优化训练（标签、窗口、特征、权重），输出增强模型供 `--ml-enhanced` 使用。

### 3. 使用训练好的模型生成信号

```bash
# 基础 ML 信号
python "量化策略系统 v5.10.py" --ml-signal

# 增强 ML 信号（优先加载增强模型）
python "量化策略系统 v5.10.py" --ml-enhanced
```

说明：
- `--ml-enhanced` 会优先自动发现并加载增强模型
- 结果可直接进入 `GLM5DecisionEngine` 的 `market_data` / `portfolio_data`
- 训练结果通常落盘在模型缓存目录，由 `EnhancedPredictor.auto_discover_and_load()` 自动发现

---

## 本地决策集成（v5.10 实际接入方式）

### 1. GLM-5 客户端

文件：`utils/glm5_client.py`

```python
from utils.glm5_client import GLM5Client

# API 模式（默认豆包，也可回退到智谱）
client = GLM5Client(mode="api")

# 本地模式（ModelScope）
client = GLM5Client(mode="local")

# Ollama 模式
client = GLM5Client(mode="ollama")

# 本地 GGUF 模式（llama-cpp-python）
client = GLM5Client(mode="local_gguf")

resp = client.chat("分析今天的市场行情")
print(resp["content"])
```

注意：
- v5.10 的 `GLM5Client` 默认 API 模型已改为豆包 Speed：`doubao-seed-1-6-251015`
- 智谱模型作为 fallback：`glm-4-plus`、`glm-4-flash`
- 环境变量优先读 `VOLCENGINE_API_KEY`，其次 `ZHIPUAI_API_KEY`

### 2. AI 决策引擎

文件：`utils/glm5_decision_engine.py`

```python
from utils.glm5_decision_engine import GLM5DecisionEngine

engine = GLM5DecisionEngine()

decisions = engine.make_decisions(
    market_data={
        "日期": "2026-06-29",
        "指数行情": {"上证指数": {"收盘": 3950, "涨跌幅": "+0.85%"}},
        "板块表现": {"高端制造": "+2.1%"},
        "资金流向": {"北向资金": "净流入 +85亿"},
    },
    portfolio_data={
        "positions": [...],
        "cash": 500000,
        "total_value": 1000000,
    },
    scene="intraday_decision",
)
```

支持场景：
- `intraday_decision`：盘中实时决策，低延迟优先
- `rebalancing_analysis`：再平衡深度分析，推理质量优先
- `macro_analysis`：宏观综合分析
- `report_generation`：报告生成
- `light_analysis`：轻量分析

### 3. 多模型路由器

文件：`utils/multi_model_router.py`
配置：`config/model_routing.yaml`

```python
from utils.multi_model_router import ModelRouter

router = ModelRouter()
result = router.route(
    scene="intraday_decision",
    prompt="基于当前市场数据给出交易建议",
    system_prompt="你是一位量化交易决策官...",
)
```

v5.10 场景路由默认配置：
- 盘中决策：`glm-4.7-flash` + `doubao-seed-1-6-251015` 并行对冲
- 再平衡：`deepseek-v4-pro` + `doubao-pro-32k` 交叉验证
- 宏观分析：`deepseek-v4-pro` + `glm-5.2` 交叉验证
- 报告生成：`doubao-seed-1-6-251015`
- 轻量分析：`doubao-seed-1-6-251015`

---

## 三种部署模式（按 v5.10 实际支持）

| 模式 | 适用场景 | 硬件要求 | 响应速度 | v5.10 角色 |
|------|---------|---------|---------|-----------|
| **API** (推荐) | 快速训练/无GPU | 无需GPU | ~1-2秒 | 主力推理；训练也可走 API 标注 |
| **Ollama** | 零配置本地 | 8GB+ 显存 | ~2-5秒 | 本地回退推理；适合离线训练验证 |
| **Local** | 完全离线/私有化 | 24GB+ 显存 | ~3-8秒 | 本地训练/推理；适合长期私有部署 |

---

## 方式一：API 模式（最快，推荐训练/推理混合使用）

### 步骤1: 获取 API Key
- 豆包（火山引擎）：https://console.volcengine.com/ark
- 智谱 AI：https://open.bigmodel.cn/

### 步骤2: 安装依赖
```bash
pip install requests pyyaml
```

### 步骤3: 设置环境变量
```powershell
# 豆包（v5.10 默认主模型）
$env:VOLCENGINE_API_KEY = "你的API密钥"
$env:VOLCENGINE_API_BASE = "https://ark.cn-beijing.volces.com/api/v3/responses"
$env:VOLCENGINE_MODEL = "doubao-seed-1-6-251015"

# 智谱（fallback）
$env:ZHIPUAI_API_KEY = "你的API密钥"
```

### 步骤4: 运行测试
```bash
cd e:\各种PY程序\11_量化策略
python utils/glm5_client.py --mode api --message "你好"
```

### 在项目中使用
```python
from utils.glm5_client import GLM5Client

client = GLM5Client(mode="api")
result = client.chat("分析今天A股市场走势")
print(result["content"])
```

---

## 方式二：Ollama 模式（零配置本地推理）

### 步骤1: 安装 Ollama
- 下载：https://ollama.com/download/windows
- 安装后启动 Ollama

### 步骤2: 拉取模型
```bash
ollama pull glm-5
# 若不存在，可尝试
ollama pull glm4
```

### 步骤3: 运行测试
```bash
python utils/glm5_client.py --mode ollama --message "分析市场"
```

### 在项目中使用
```python
from utils.glm5_client import GLM5Client

client = GLM5Client(mode="ollama")
result = client.chat("生成每日交易报告", context_data=market_data)
```

---

## 方式三：本地模型部署（训练 + 完全离线推理）

### 硬件要求
- **最低**：NVIDIA GPU 8GB VRAM（INT8 量化）
- **推荐**：NVIDIA GPU 24GB+ VRAM（FP16）
- **显存不足**：自动使用 CPU（很慢）

### 步骤1: 安装 CUDA 和 PyTorch
```bash
nvidia-smi
pip install torch torchvision --index-url https://download.pytorch.org/whl/cu121
```

### 步骤2: 安装依赖
```bash
pip install modelscope transformers accelerate sentencepiece protobuf
```

### 步骤3: 运行测试（首次运行会自动下载模型）
```bash
python utils/glm5_client.py --mode local --message "你好"
```

首次下载约需 **10-20 分钟**（模型约 30-50 GB）。

### 在项目中使用
```python
from utils.glm5_client import GLM5Client, get_glm5_client

client = GLM5Client(
    mode="local",
    device="auto",
    dtype="float16"
)

result = client.chat("分析持仓风险")

for chunk in client.chat_stream("生成报告"):
    print(chunk, end="", flush=True)

analysis = client.analyze_market(
    market_data={
        "指数": {"上证": 3050, "深证": 9800},
        "持仓": {"中际旭创": {"仓位": "5%", "盈亏": "+12%"}},
    },
    focus_areas=["大盘", "持仓标的"]
)
print(analysis)
```

---

## 本地训练决策工作流（v5.10 推荐）

```bash
# 1. 先训练/更新模型
python "量化策略系统 v5.10.py" --train-enhanced --horizon 5

# 2. 用训练好的模型生成信号
python "量化策略系统 v5.10.py" --ml-enhanced

# 3. 启动 AI 盘中决策（自动调用训练信号 + 多模型路由）
python "量化策略系统 v5.10.py" --ai-decision

# 4. 或启动统一监控（多模块并行，含 AI Hedge Fund + ML 信号）
python "量化策略系统 v5.10.py" --unified-monitor
```

---

## 在 v5.10 各模块中的实际接入

### 盘前计划
- 调用 `GLM5DecisionEngine(scene="report_generation")`
- 结合 `--train-enhanced` 输出的 ML 信号
- 输出：盘前计划 + 调仓建议

### 盘中决策
- 调用 `ModelRouter.route("intraday_decision", ...)`
- 并行对冲：`glm-4.7-flash` + `doubao-seed-1-6-251015`
- 熔断失败时自动降级到 `deepseek-v3.2`
- 输出：交易信号 + 风险预警

### 盘后报告
- 调用 `ModelRouter.route("report_generation", ...)`
- 默认模型：`doubao-seed-1-6-251015`
- 可追加 LLM 深度解读
- 输出：`每日报告归档/YYYY-MM-DD/综合日报_YYYYMMDD.txt`

### 宏观综合分析
- 调用 `ModelRouter.route("macro_analysis", ...)`
- 交叉验证：`deepseek-v4-pro` + `glm-5.2`
- 启用 RAG：基本面 + 财报数据
- 输出：康波周期 + 十五五规划 + 社保基金 ETF 综合分析

---

## 性能优化建议

### 1. 训练阶段
- 优先使用 `--train-enhanced --optuna` 做超参搜索
- 大数据集启用 `--mlflow` 追踪实验
- GPU 不足时降低 batch size，不要直接切 CPU 全量训练

### 2. 推理阶段
- 盘中使用 `ModelRouter` 场景路由，避免全局高精度模型
- 重复查询启用内存缓存
- 非关键分析关闭 RAG，减少上下文长度

### 3. 成本控制
- 轻量任务固定走 `doubao-seed-1-6-251015`
- 深度分析走 `deepseek-v4-pro` + `glm-5.2` 交叉验证
- 设置 `confidence_threshold: 0.6`，低于阈值标记“需人工确认”

---

## 常见问题

**Q: v5.10 里 GLM-5 是不是主力模型？**
A: 不是唯一主力。v5.10 是多模型架构，GLM 系列主要用于再平衡 fallback、宏观交叉验证和本地部署；主力推理更多使用豆包 Speed 和 DeepSeek。

**Q: 本地训练好的模型如何被决策系统调用？**
A: 通过 `--ml-enhanced` / `--ml-signal` 生成信号后，`GLM5DecisionEngine` 和 `ModelRouter` 可以把这些信号作为 `market_data` 或 `extra_context` 传入，不依赖单一模型内部记忆。

**Q: 如何只跑本地、完全不上云？**
A: 使用 `mode="local"` 或 `mode="ollama"`，并配置 `config/model_routing.yaml` 将 provider 指向本地可用的模型服务；训练侧使用 `--train-enhanced` 本地产出模型即可。

**Q: 模型更新后怎么办？**
A: API / Ollama 模式自动使用最新版本；Local 模式重新加载模型权重；训练模型重新执行 `--train-enhanced`。
