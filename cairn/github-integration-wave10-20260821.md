# GitHub 周热门项目集成 Wave10 (2026-08-21)

> **状态**: 已完成 ✅ | **版本**: v8.6 | **日期**: 2026-08-21
> **范围**: unsloth + Switchyard + OpenViking 三件套下载→适配→集成→安装→自检
> **指针**: `utils/github_integration_registry.py` · `docs/GitHub周热门项目集成_20260821.md`

## 1. 背景与筛选

GitHub 2026-08-21 本周 trending 18 个项目中，按与 28 量化系统 AI 决策核心链路的适配度筛选：

| 项目 | 适配点 | 适配度 |
|------|--------|--------|
| unslothai/unsloth | 本地 LLM 训练，替代/补充 GLM5DecisionEngine | ★★★ |
| NVIDIA-NeMo/Switchyard | LLM 多模型路由，对接 GLM5Client/LiteLLMRouter | ★★★ |
| volcengine/OpenViking | Agent 上下文数据库+RAG，增强 AI Hedge Fund 20 分析师 | ★★★ |
| semantica-agi/semantica | 图原生 AI 上下文，重构 ai_coordinator | ★★ (未选) |
| akitaonrails/ai-memory | Agent 长期记忆 | ★★ (未选, OpenViking 更全) |

## 2. 架构设计

### 2.1 设计原则

- **不可变性**: 所有适配器创建新对象，不修改第三方库内部状态
- **优雅降级**: 第三方不可用时 `is_ready()=False`，主系统回退到原有行为
- **薄包装**: 不重写第三方 API，仅做场景适配和类型转换
- **KISS**: 每个适配器 200-280 行，单一职责
- **零侵入核心**: 仅添加 try/except 钩子，不修改核心逻辑

### 2.2 集成架构图

```
GitHub Trending (18 projects)
    ↓ 筛选 3 个
10_第三方项目/{unsloth, Switchyard, OpenViking}/
    ↓ 适配器层
utils/unsloth_adapter.py ──→ utils/glm5_decision_engine.py (本地推理)
utils/switchyard_adapter.py ──→ utils/glm5_client.py (多模型路由)
quant_modules/ai_hedge_fund/openviking_memory.py ──→ agents/ (20分析师记忆)
    ↓ 注册器
utils/github_integration_registry.py (统一自检入口)
    ↓ 核心模块钩子
15_每日工作流/run_daily_morning.py (启动自检)
lgb_enhanced_trainer.py (unsloth GPU 钩子)
signal_monitor.py (OpenViking 记忆钩子)
```

## 3. 适配器 API 详解

### 3.1 unsloth_adapter — 本地 LLM 训练

**文件**: `utils/unsloth_adapter.py` (280行)
**集成点**: `GLM5DecisionEngine.make_decisions()`

```python
from utils.unsloth_adapter import get_unsloth_adapter, is_unsloth_available

adapter = get_unsloth_adapter()
adapter.is_ready()                     # True (GPU 已激活)
adapter.get_gpu_info()                 # {available, device_name, memory_gb}
model, tokenizer = adapter.load_finetuned("glm5-quant-v1")
text = adapter.infer(model, tokenizer, "分析市场", system_prompt="...")
adapter.list_finetuned()               # 已微调模型列表
```

**配置**: `UnslothConfig(model_name, max_seq_length, load_in_4bit, finetuned_dir)`
**模型输出**: `models/unsloth_finetuned/<model_tag>/`

### 3.2 switchyard_adapter — LLM 多模型路由

**文件**: `utils/switchyard_adapter.py` (269行)
**集成点**: `GLM5Client.chat()` / `LiteLLMRouter`

```python
from utils.switchyard_adapter import get_switchyard_adapter

adapter = get_switchyard_adapter()
adapter.is_ready()                     # True (跨版本桥接)
resp = adapter.route(
    messages=[{"role": "user", "content": "分析"}],
    model="auto",
    scene="intraday_decision",         # → qwen3-flash
    strategy="cost_aware",
)
adapter.benchmark_models(test_prompts)  # 模型对比
```

**场景模型映射**:
| 场景 | 模型 | 用途 |
|------|------|------|
| intraday_decision | qwen3-flash | 盘中低延迟 |
| rebalancing_analysis | deepseek-v4-pro | 深度推理 |
| macro_analysis | glm-5.2 | 宏观分析 |
| report_generation | qwen-plus | 结构化输出 |
| light_analysis | doubao-speed | 情感/分类 |

### 3.3 openviking_memory — Agent 长期记忆

**文件**: `quant_modules/ai_hedge_fund/openviking_memory.py` (280行)
**集成点**: `ai_hedge_fund/agents/` 20 位分析师

```python
from quant_modules.ai_hedge_fund.openviking_memory import get_openviking_memory

memory = get_openviking_memory()
memory.add_context(
    agent_id="warren_buffett",
    content="2024Q3 茅台毛利率 91.5%",
    metadata={"ticker": "600519", "type": "fundamental"},
)
results = memory.retrieve(agent_id="warren_buffett", query="茅台基本面", top_k=5)
memory.add_decision_audit(agent_id, decision, rationale)
memory.get_agent_history(agent_id, days=30)
```

**Agent 命名空间映射** (10 位分析师):
| Namespace | Agents |
|-----------|--------|
| value_investing | warren_buffett, ben_graham, charlie_munger |
| growth_investing | phil_fisher, cathie_wood, peter_lynch |
| contrarian | michael_burry |
| macro | stanley_druckenmiller, ray_dalio |
| activist | bill_ackman |

### 3.4 github_integration_registry — 统一注册器

**文件**: `utils/github_integration_registry.py` (220行)

```python
from utils.github_integration_registry import run_startup_selfcheck

report = run_startup_selfcheck()
# report.available, report.total, report.overall_ok, report.adapters
```

## 4. 安装与激活

### 4.1 unsloth (GPU 直连)

```bash
pip install unsloth_zoo unsloth
pip install torch==2.11.0 torchvision==0.26.0 \
    --index-url https://download.pytorch.org/whl/cu126 \
    --force-reinstall --no-deps
```

**环境**: torch 2.11.0+cu126, RTX 3060 6GB, CUDA 13.2 驱动

### 4.2 Switchyard (跨版本桥接)

```bash
# nemo-switchyard 需要 Python >=3.12, 主系统 3.11
python3.12 -m pip install nemo-switchyard --break-system-packages
# 适配器自动检测 python3.12 并通过 subprocess 桥接
```

**桥接**: `utils/switchyard_bridge.py` 在 Python 3.12 中执行路由，返回 JSON

### 4.3 OpenViking (SDK 直连)

```bash
pip install -e 10_第三方项目/OpenViking/sdk/python
# 服务端需单独启动 (见 OpenViking/README.md)
```

## 5. 踩坑记录

`contains: 踩坑`

### 5.1 torch cu121 版本不足

- **问题**: `pip install torch --index-url cu121` 仅到 2.5.1，unsloth 需 2.11+
- **解决**: 改用 cu126 索引 (`https://download.pytorch.org/whl/cu126`)
- **教训**: unsloth 安装前先查 `pip index versions torch --index-url <url>` 确认版本

### 5.2 torchvision 算子不兼容

- **问题**: torchvision 0.28.0+cu126 的 `torchvision::nms` 算子不加载
- **解决**: 降级到 `torchvision==0.26.0+cu126` (匹配 torch 2.11.0)
- **教训**: torch + torchvision 版本需严格配对，按 unsloth 错误提示安装

### 5.3 磁盘空间不足

- **问题**: torch 2.11.0+cu126 安装时报 `No space left on device`
- **解决**: `pip cache purge` 释放 5GB (pip 缓存 4.8GB)
- **教训**: 大模型安装前先检查磁盘 + 清理 pip 缓存

### 5.4 Switchyard Python 版本限制

- **问题**: `nemo-switchyard` 要求 Python >=3.12，主系统 3.11
- **解决**: 创建 `switchyard_bridge.py` 桥接脚本，适配器通过 subprocess 调用 python3.12
- **教训**: 跨版本依赖用 subprocess + JSON 通信，避免复杂 IPC

### 5.5 OpenViking SDK 类名/方法名不匹配

- **问题**: 适配器初版用 `OpenVikingClient` / `ingest` / `retrieve`，实际是 `SyncHTTPClient` / `add_message` / `find`
- **解决**: 查 `dir(SyncHTTPClient)` 确认方法名，参数 `server_url` → `url`
- **教训**: 第三方 SDK 集成前先 `inspect.signature` 确认 API 签名

### 5.6 dataclass field 多括号

- **问题**: `field(default_factory=lambda: {...}))` 多一个右括号导致语法错误
- **解决**: 修复为 `field(default_factory=lambda: {...})`
- **教训**: dataclass field 语法易错，写完用 `python -c "import ast; ast.parse(open(f).read())"` 校验

## 6. 核心模块接入点

| 文件 | 位置 | 钩子内容 |
|------|------|----------|
| `15_每日工作流/run_daily_morning.py` | `main()` 开头 | `run_startup_selfcheck()` 启动自检 |
| `lgb_enhanced_trainer.py` | `main()` 训练前 | unsloth GPU 信息记录 |
| `signal_monitor.py` | `analyze_signal_effectiveness()` | OpenViking 信号评估记忆 |
| `system_config.json` | 顶层 | `github_integration` 配置段 |

## 7. 验证结果

```
run_startup_selfcheck() → available=3/3, overall_ok=true

  ✓ unsloth     (torch 2.11.0+cu126, RTX 3060 GPU)
  ✓ switchyard  (Python 3.12 跨版本桥接, nemo-switchyard 0.2.0)
  ✓ openviking  (SDK 0.1.dev1, 10 agents)
```

## 8. 后续扩展

- [ ] UI 页面 `13_🤖_AI决策与ML信号.py` 添加三个适配器状态卡片
- [ ] `utils/system_check.py` 启动自检纳入三个适配器
- [ ] CI 工作流添加适配器导入测试
- [ ] unsloth 微调 GLM-5 模型用于盘中决策 (低延迟场景)
- [ ] Switchyard benchmark 后优化场景模型映射
- [ ] OpenViking 服务端部署 + 20 分析师记忆初始化

## 9. 相关文档

- `docs/GitHub周热门项目集成_20260821.md` — 集成清单 + 激活步骤
- `cairn/github-integration-wave6.md` — Wave6 集成经验
- `cairn/github-trending-wave9-20260819.md` — Wave9 trending 统计
- `cairn/third-party-integration-20260821.md` — 第三方项目集成总览

<!-- AUTO-GENERATED: 相关文档 -->
## 相关文档

- [docs/1 八项目集成 — Sprint A 知识专题](docs1-integration-sprint-a-20260817.md) (相似度 15%)
- [第三方项目集成专题 — 2026-08-21](third-party-integration-20260821.md) (相似度 11%)
- [第三方项目批量集成专题 — 2026-08-22](third-party-integration-batch-20260822.md) (相似度 11%)
- [华为云 ModelArts 云端训练部署](cloud-modelarts-deployment.md) (相似度 11%)
- [Wave 9：GitHub 热榜项目集成决策沉淀](github-trending-wave9-20260819.md) (相似度 10%)

<!-- 由 scripts/cairn_cross_ref.py 自动生成，请勿手动编辑此区块 -->
