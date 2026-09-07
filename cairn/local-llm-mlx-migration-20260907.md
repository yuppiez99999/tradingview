# 本地 LLM 决策方案 — MLX + Qwen3 (剔除 doubao/火山引擎)

> **创建**: 2026-09-07
> **背景**: 用户设备 MacBook Pro M5 Max 顶配 (16 核 / 128GB 统一内存 / MPS 加速)
> **决策**: 剔除 doubao/火山引擎 (VOLCENGINE_API_KEY), 改为 MLX (Apple 官方) + Qwen3 系列本地优先
> **目标**: 本文档设备无关, 后期更换 Apple Silicon 设备直接照此落地

---

## 1. 为什么剔除 doubao

| 维度 | doubao (火山引擎) | MLX + Qwen3 本地 |
|------|------------------|-----------------|
| API 成本 | 0.0008-0.003 元/1K tok | **0 元** (仅电费) |
| 延迟 | 网络 RTT 50-200ms | **本机内存直访** <10ms |
| 隐私 | 金融决策 prompt 出云 | **全本地**, 零数据外泄 |
| 网络依赖 | 必须联网 | **离线可用** |
| 密钥管理 | VOLCENGINE_API_KEY 托管 | **无需密钥** |
| 中文金融理解 | 强 (豆包 Seed) | **强** (Qwen3 通义千问, 阿里中文金融语料充分) |
| 推理质量 (32B) | doubao-pro-32k | **Qwen3-32B / DeepSeek-R1-Distill-Qwen-32B** (R1 蒸馏 CoT 更强) |

**结论**: M5 Max 128GB 统一内存足以常驻 32B 4bit 模型, 本地推理在成本/延迟/隐私/质量全维优于云端 doubao。

---

## 2. 硬件前提 — Apple Silicon 统一内存架构

本方案适用于任何 **Apple Silicon (M 系列芯片)** 设备, 关键约束是**统一内存容量**:

| 芯片 | 典型统一内存 | 可常驻最大模型 (4bit) | 适用场景 |
|------|-------------|---------------------|---------|
| M3 / M4 (Pro/Max) | 36-64GB | Qwen3-14B (8GB) | 轻量/报告/盘中 |
| M5 Max (顶配) | **128GB** | **Qwen3-32B (18GB) + 14B + 8B 同时常驻** | **全场景** |
| M5 Ultra | 256-512GB | 70B 级别 | 全场景 + 多模型并行 |

**Apple Silicon 优势**:
- 统一内存: CPU/GPU 共享同一池, 无 CPU↔VRAM 拷贝
- MLX 框架原生利用全部统一内存做模型推理
- MPS (Metal Performance Shaders) 加速矩阵运算
- 4bit/8bit 量化原生支持, 量化效率高于 llama.cpp

> **换设备时**: 只需确认统一内存 ≥ 模型 4bit 显存 × 1.3 (留余量), 见 §7。

---

## 3. 为什么选 MLX + Qwen3

### 3.1 推理框架: MLX > Ollama > llama.cpp

| 框架 | Apple Silicon 效率 | 易用性 | 本项目集成 |
|------|-------------------|--------|-----------|
| **MLX** (Apple 官方) | **最高** (统一内存直用, MPS) | 中 (Python API) | 新增 (本方案) |
| Ollama | 中 (封装层开销) | **高** (CLI/REST) | 已有 (`glm5_client.py` ollama 模式) |
| llama.cpp | 低 (CPU 偏优) | 低 (C++ 编译) | 已有 (`local_llm.py` llama-cpp-python) |

MLX 在 Apple Silicon 上比 llama.cpp 效率高 20-40%, 比 Ollama 高 10-20%。

### 3.2 模型族: Qwen3 系列 (通义千问)

**选型理由**:
1. **中文金融语料最充分** — 阿里通义千问, A 股/期货/宏观术语理解强
2. **2025+ 最新代际** — Qwen3 > Qwen2.5 (项目原默认 Qwen2.5-1.5B 太小, 仅兜底)
3. **全尺寸覆盖** — 4B/8B/14B/32B, 按场景选, 同族 prompt 兼容
4. **MLX 原生支持** — HuggingFace 有官方 MLX 4bit 量化版

**DeepSeek-R1-Distill-Qwen-32B** (再平衡场景):
- R1 蒸馏到 Qwen32B, 保留 R1 的推理链 (CoT) 能力
- 再平衡/宏观需要深度推理, R1 蒸馏版质量最接近云端 deepseek-v4-pro
- MLX 4bit 约 18GB, M5 Max 128GB 轻松承载

---

## 4. 场景路由表 (对应 `config/model_routing.yaml` 5 场景)

| 决策场景 | 原 doubao 方案 | **新 MLX 本地方案** | 4bit 显存 | 估计吞吐 | 云端 fallback |
|---------|--------------|-------------------|----------|---------|--------------|
| **盘中决策** (<12s) | doubao-seed-1-6-251015 | **Qwen3-8B-Instruct (MLX 4bit)** | ~5GB | ~50 tok/s | **deepseek-v4-pro** (并行) / deepseek-v4-chat (fallback) |
| **再平衡分析** | doubao-pro-32k (交叉验证) | **DeepSeek-R1-Distill-Qwen-32B (MLX 4bit)** | ~18GB | ~18 tok/s | deepseek-v4-pro |
| **宏观综合分析** | doubao-seed-1-6-251015 | **Qwen3-32B-Instruct (MLX 4bit)** | ~18GB | ~18 tok/s | deepseek-v4-pro |
| **报告生成** | doubao-seed-1-6-251015 | **Qwen3-14B-Instruct (MLX 4bit)** | ~8GB | ~30 tok/s | deepseek-v4-pro |
| **轻量分析** (情感/分类) | doubao-seed-1-6-251015 | **Qwen3-4B-Instruct (MLX 4bit)** | ~2.5GB | ~80 tok/s | — |

**常驻策略** (M5 Max 128GB):
- 32B (18GB) + 14B (8GB) + 8B (5GB) = 31GB, 同时常驻, 场景切换零加载延迟
- 4B (2.5GB) 按需加载 (轻量任务偶发)
- 剩余 ~90GB 供 LightGBM 训练 / TimesFM / 系统

---

## 5. 配置落地 — 已完成变更清单 (2026-09-07)

### 5.1 `config/model_routing.yaml` (场景路由核心)
- ✅ 所有 `volcengine`/`doubao` 场景 → `mlx`/`Qwen3`
- ✅ `providers.volcengine` 段移除, 新增 `providers.mlx` (api_base: local, runtime: mlx-lm, device: mps)
- ✅ fallback 链改: mlx → **deepseek-v4-pro (云端并行对冲)** → deepseek-v4-chat (云端 fallback) → ollama (本地兜底)
- ✅ 盘中云端首选 deepseek-v4-pro (不限价格: MoE 快 + 量化推理顶级 + 与再平衡/宏观模型统一); Qwen-Max 降为备选 (providers.qwen_max 保留, 切 provider 即用)

### 5.2 `config/settings_mac.yaml` (Mac 研究模式)
- ✅ 移除 `env.VOLCENGINE_API_KEY`
- ✅ 新增 `local_llm_mlx` 段 (framework/runtime/device/scenes + est_mem_gb/est_tps)

### 5.3 `config/llm_pricing.yaml` (成本表)
- ✅ 移除 `doubao_speed` + `doubao` 段
- ✅ 新增 `mlx_qwen3_4b/8b/14b/32b` + `mlx_deepseek_r1_32b` (本地零 API 成本)

### 5.4 `system_config.json`
- ✅ `llm_config.doubao` 段 → `llm_config.mlx` 段 (model: Qwen3-8B, runtime: mlx-lm, device: mps)

### 5.5 代码层默认模型
- ✅ `utils/ai_coordinator.py` — `MODEL_CONFIG.doubao_speed` → `mlx_qwen3_8b` (cost 0.0); route() 返回值全改
- ✅ `utils/glm5_client.py` — `api_model` 默认 `doubao-seed-1-6-251015` → `Qwen/Qwen3-8B-Instruct-4bit`; api_key 环境变量移除 `VOLCENGINE_API_KEY` 优先, 仅读 `ZHIPUAI_API_KEY`
- ✅ `utils/glm5_decision_engine.py` — 默认 `api_model` → `Qwen/Qwen3-8B-Instruct-4bit`

### 5.6 保留未改 (降为禁用 fallback, 避免破坏测试)
- `utils/multi_model_router.py` 的 `_call_volcengine` 方法保留 (配置层已不路由到 volcengine)
- `utils/alpha/llm/providers/` 的 volcengine/doubao provider 保留 (代码死路径, 配置不触发)
- `tests/unit/test_llm_router.py` 的 doubao 测试保留 (测试 provider 注册机制, 非生产路由)
- UI 文案 `ui/pages/03_🔄_再平衡执行.py` 的"豆包Seed"字样保留 (后续 UI 改造单独处理)

---

## 6. 安装与启动步骤 (新设备落地)

### 6.1 安装 MLX

```bash
# Apple Silicon 设备 (M 系列)
pip install mlx-lm           # MLX 推理后端
# 可选: pip install mlx-vlm   # 多模态 (若未来接图表)
```

### 6.2 模型下载 (首次自动从 HuggingFace 拉)

```python
# 首次加载自动下载到 ~/.cache/huggingface/hub/
from mlx_lm import load

# 盘中决策
load("Qwen/Qwen3-8B-Instruct-4bit")
# 再平衡 (R1 蒸馏, CoT 强)
load("deepseek-ai/DeepSeek-R1-Distill-Qwen-32B-MLX-4bit")
# 宏观分析
load("Qwen/Qwen3-32B-Instruct-4bit")
# 报告生成
load("Qwen/Qwen3-14B-Instruct-4bit")
# 轻量分析
load("Qwen/Qwen3-4B-Instruct-4bit")
```

> 模型名以 HuggingFace MLX 社区最新 4bit 量化版为准; 若官方名带 `-mlx-4bit` 后缀则用全名。

### 6.3 调用示例

```python
from mlx_lm import load, generate

model, tokenizer = load("Qwen/Qwen3-8B-Instruct-4bit")

prompt = tokenizer.apply_chat_template(
    [{"role": "system", "content": "你是量化交易分析师"},
     {"role": "user", "content": "分析今日持仓风险"}],
    tokenize=False, add_generation_prompt=True,
)
response = generate(model, tokenizer, prompt=prompt, max_tokens=1500, temp=0.15)
print(response)
```

### 6.4 验证

```bash
cd "28-终极量化交易系统8.4"
python "量化策略系统_统一入口_v8.6.py" --check    # 自检 (确认无 VOLCENGINE 依赖)
python -c "from utils.ai_coordinator import AICoordinator, TaskType; \
c=AICoordinator(); print(c.route(TaskType.INTRADAY_DECISION))"  # 应输出 mlx_qwen3_8b
```

---

## 7. 设备更换指南 (后期换设备直接照此)

### 7.1 判断新设备能否跑 32B

```python
# Apple Silicon 查统一内存
import subprocess
out = subprocess.check_output(["sysctl", "hw.memsize"]).decode()
mem_gb = int(out.split(":")[1].strip()) / 1024**3
print(f"统一内存: {mem_gb:.0f}GB")
# 32B 4bit 需 18GB × 1.3 余量 ≈ 24GB; 128GB 充裕, 36GB 可跑 14B 主力
```

### 7.2 按内存选场景配置

| 统一内存 | 推荐配置 |
|---------|---------|
| <24GB | 全场景用 Qwen3-8B (5GB), 再平衡降级到 deepseek 云端 |
| 24-64GB | 盘中/报告/轻量本地 (8B/14B/4B), 再平衡/宏观走 deepseek 云端 |
| 64-128GB | 全场景本地, 32B 按需加载 (场景切换有加载延迟) |
| **≥128GB** | **全场景本地 + 32B/14B/8B 同时常驻** (零切换延迟, 本方案默认) |

### 7.3 非 Apple Silicon 设备 (Linux/Windows + NVIDIA)

本方案 MLX 不适用, 改用:
- 推理框架: **vLLM** (NVIDIA) 或 **Ollama** (跨平台)
- 模型: 同 Qwen3 系列, 4bit 量化 (vLLM 用 AWQ/GPTQ, Ollama 用 GGUF)
- 配置: `config/model_routing.yaml` 的 `providers.mlx` → `providers.vllm`/`providers.ollama`, `device: mps` → `device: cuda`

### 7.4 配置文件改动清单 (换设备只需改这些)

1. `config/settings_mac.yaml` 的 `local_llm_mlx.scenes` — 按内存选模型尺寸
2. `config/model_routing.yaml` 的 `providers.mlx.device` — mps (Apple) / cuda (NVIDIA) / cpu
3. `config/llm_pricing.yaml` — 本地模型保持 0 成本; 若改云端正价更新

---

## 8. 验证清单

- [ ] `pip install mlx-lm` 成功
- [ ] `mlx_lm.load("Qwen/Qwen3-8B-Instruct-4bit")` 首次下载成功
- [ ] `python "量化策略系统_统一入口_v8.6.py" --check` 无 VOLCENGINE 依赖告警
- [ ] `AICoordinator().route(TaskType.INTRADAY_DECISION)` 返回 `mlx_qwen3_8b`
- [ ] `config/model_routing.yaml` 无 `volcengine`/`doubao` 残留
- [ ] `config/llm_pricing.yaml` 无 `doubao` 段
- [ ] 盘中决策端到端: MLX 推理 <12s (M5 Max 8B 4bit 预期 <5s)
- [ ] 再平衡端到端: MLX R1-Distill-32B 推理 <60s

---

## 9. 风险与回退

| 风险 | 缓解 |
|------|------|
| MLX 模型名在 HuggingFace 变更 | §6.2 注释, 以 HF 最新 4bit 量化版为准 |
| 32B 推理质量不及云端 deepseek-v4-pro | 保留 deepseek 云端 cross_validation (model_routing.yaml 已配) |
| MLX 未安装 (非 Apple Silicon) | `glm5_client.py` 已有 ollama 兜底; 配置层 fallback 链 mlx→deepseek→ollama |
| 首次模型下载慢 (32B 4bit ~18GB) | 预下载到 `~/.cache/huggingface/`; 或用 syncthing 从已下载设备同步 |

**回退方案** (若 MLX 不可用):
1. `config/model_routing.yaml` 的 `providers.mlx` → `providers.ollama` (device: cpu)
2. 模型名改 Ollama 格式: `qwen3:8b-instruct-q4_K_M`
3. `ollama pull qwen3:8b-instruct-q4_K_M`

---

## 10. 关联文件指针

- 场景路由: `config/model_routing.yaml`
- Mac 配置: `config/settings_mac.yaml` (`local_llm_mlx` 段)
- 成本表: `config/llm_pricing.yaml`
- 系统配置: `system_config.json` (`llm_config.mlx`)
- AI 协调器: `utils/ai_coordinator.py` (`MODEL_CONFIG` + `route()`)
- GLM5 客户端: `utils/glm5_client.py` (默认 api_model)
- 决策引擎: `utils/glm5_decision_engine.py` (默认 api_model)
- 本地选型器: `utils/local_model_selector.py` (llmfit 集成, 可选)
- 本地 LLM (llama.cpp): `utils/local_llm.py` (回退方案)

---

**版本**: v8.6 本地 LLM 决策方案 v1.0
**适用**: 任何 Apple Silicon (M 系列) 设备, 统一内存 ≥24GB 起步, ≥128GB 全场景
**维护**: 换设备时改 §7.4 清单; 模型升级时改 §4 路由表 + §6.2 下载命令

---

## 11. 2026-09-07 晚间更新: 不限预算·云端旗舰优先 (政策反转)

用户明确"不限预算, 用最好模型做最好结果"后, `model_routing.yaml` 由本方案的 MLX 本地优先切换为**云端旗舰优先**, MLX 降级为离线兜底。本方案保留作为: ① 成本敏感期/断网降级方案 ② `glm5_client.py`/`ai_coordinator.py` 本地链路 (未改动)。

### 11.1 关键发现: MLX 在 ModelRouter 链路从未可用

`utils/multi_model_router.py` 的 `_call_model()` 无 mlx 分支, 且原配置 `api_key_env: ""` → `os.environ.get("")` 返回 None → 每次"缺少 API Key"直接失败。即 §5.1 的 MLX-primary 配置在该路由器上**每次都静默降级到 deepseek fallback**。修复: mlx provider 改为 `mlx_lm.server` 的 OpenAI 兼容端点 (`http://localhost:8080/v1/chat/completions`) + `MLX_API_KEY=local` 占位。

### 11.2 云端优先终版路由表

| 场景 | 主模型 | 第二路 (并行/交叉验证) | 兜底 |
|------|--------|----------------------|------|
| 盘中决策 | deepseek-v4-chat | gemini-3.5-flash (并行先回先得) | qwen3-max (国内直连) |
| 再平衡 | deepseek-v4-pro | gpt-5.5 (异源交叉验证) | qwen3-max |
| 宏观分析 | kimi-k3 (1M ctx) | gpt-5.5 | deepseek-v4-pro |
| 报告生成 | claude-opus-4.8 | — | glm-5.2 |
| 轻量分析 | gemini-3.5-flash | — | mlx Qwen3-4B (离线) |

选型依据与坑见 cairn/LOG.md 2026-09-07 对应条目; 海外 provider 需 HTTPS_PROXY。