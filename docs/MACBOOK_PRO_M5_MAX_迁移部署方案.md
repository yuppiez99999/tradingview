# MacBook Pro M5 Max 量化系统迁移部署方案

> **生成日期**: 2026-08-06
> **目标硬件**: MacBook Pro M5 Max 顶配 (128GB 统一内存)
> **应用系统**: 终极量化交易系统 v8.6.3
> **当前硬件**: Dell i7-10870H + 16GB 内存 (已严重瓶颈)
> **实盘方案**: Mac 研究 + Windows 虚拟机 (QMT/iFinD)

---

## 一、硬件升级决策依据

### 1.1 当前硬件瓶颈

| 项目 | 当前配置 | 问题 |
|------|---------|------|
| CPU | Intel i7-10870H (第10代, 8核16线程) | 2020 年产品, 已严重落伍 |
| 内存 | 16GB | 严重不足, 运行回测+模型训练频繁 swap |
| 系统 | Windows 11 专业版 | 可用但非量化研究最优解 |
| 机型 | Dell 笔记本 | 服役约 5-6 年 |

### 1.2 为什么选 M5 Max 顶配 (128GB)

| 对比项 | M5 Max 顶配 | M7 (传闻) | 当前 Dell |
|--------|------------|-----------|----------|
| CPU | 18核 (6超级+12性能) | 未知 | 8核 |
| 内存上限 | **128GB 统一内存** | 预计 256GB | 16GB |
| 内存带宽 | 614GB/s | 未知 | ~45GB/s |
| 发布时间 | 已发布 (2026.03) | 最早 2027 年底 | — |
| 等待周期 | 0 | 17 个月 | — |

**核心结论**:
- M7 等待周期 17 个月, 机会成本远大于芯片升级收益
- M5 Max 128GB 对量化研究场景已严重过剩, 足以服役 5-6 年
- M7 升级重点是 AI/LLM 推理, 非量化研究核心需求

### 1.3 关于 Kimi K3 本地部署的说明

| 模型 | 权重体积 | 最低显存需求 | M5 Max 128GB 可行性 |
|------|---------|------------|-------------------|
| Kimi K3 (2.8T MoE) | ~1.56 TB | ~1.4 TB (FP4) | ❌ 差 11 倍 |

**Kimi K3 属于数据中心级模型, 任何个人电脑都无法本地部署。** 想用 K3 必须走官方 API。

---

## 二、LLM 模型选型对比

### 2.1 四大国产旗舰横向对比

| 维度 | DeepSeek V4 Pro | GLM 5.2 | Qwen 3.7 Max | Kimi K3 |
|------|----------------|---------|-------------|---------|
| 总参数 | 1.6T (MoE) | 744B (MoE) | ~1T+ (MoE) | **2.8T** (MoE) |
| 激活参数 | ~40B | ~40B | ~40B | ~104B |
| 上下文 | 1M tokens | 1M tokens | 1M tokens | **1M tokens** |
| 开源协议 | MIT | MIT | Apache 2.0 / MIT | Modified MIT |
| 多模态 | ❌ 纯文本 | ❌ 纯文本 | ✅ 原生支持 | ✅ 原生支持 |

### 2.2 量化金融场景能力评分

| 场景 | DeepSeek V4 | GLM 5.2 | Qwen 3.7 | Kimi K3 |
|------|------------|---------|----------|---------|
| 中文能力 (最重要) | ⭐⭐⭐⭐ | ⭐⭐⭐⭐ | ⭐⭐⭐⭐⭐ | ⭐⭐⭐⭐⭐ |
| 代码/编程 | ⭐⭐⭐⭐ | ⭐⭐⭐⭐⭐ | ⭐⭐⭐⭐ | ⭐⭐⭐⭐⭐ |
| 数学/逻辑推理 | ⭐⭐⭐⭐ | ⭐⭐⭐⭐ | ⭐⭐⭐⭐⭐ | ⭐⭐⭐⭐⭐ |
| 长文本/上下文 | ⭐⭐⭐⭐ | ⭐⭐⭐⭐⭐ | ⭐⭐⭐⭐ | ⭐⭐⭐⭐⭐ |
| API 性价比 | ⭐⭐⭐⭐⭐ | ⭐⭐⭐ | ⭐⭐⭐⭐ | ⭐⭐ |

### 2.3 API 成本对比 (百万 tokens)

| 模型 | 输入价格 | 输出价格 | 性价比评级 |
|------|---------|---------|-----------|
| DeepSeek V4 Flash | **$0.14** | **$0.28** | ⭐⭐⭐⭐⭐ 最便宜 |
| Qwen 3.7 | $0.3-1 | $1-3 | ⭐⭐⭐⭐ |
| GLM 5.2 | $1.4 | $4.4 | ⭐⭐⭐ |
| Kimi K3 | $3 (命中$0.3) | **$15** | ⭐⭐ |

### 2.4 本地部署可行性 (128GB M5 Max)

| 模型 | 可否本地跑 | 推荐量化 | 内存占用 | 预计速度 |
|------|-----------|---------|---------|---------|
| Qwen 2.5 72B | ✅ 舒服跑 | Q4_K_M | ~42GB | ~10-15 tok/s |
| Qwen 2.5 32B | ✅ 轻松 | Q5_K_M | ~22GB | ~15-25 tok/s |
| Qwen 2.5 14B | ✅ 非常轻松 | Q6_K | ~13GB | ~40-60 tok/s |
| DeepSeek R1 14B | ✅ 轻松 | Q5_K_M | ~10GB | ~15-25 tok/s |
| GLM 5.2 (744B MoE) | ⚠️ 勉强 | 极端量化 | ~100GB+ | ~2-5 tok/s |
| DeepSeek V4 (1.6T) | ❌ 不行 | — | 300GB+ | — |
| Kimi K3 (2.8T) | ❌ 完全不行 | — | 1.4TB+ | — |

---

## 三、量化系统模型配置方案

### 3.1 整体架构

```
┌─────────────────────────────────────────────────┐
│  云 API 层 (主力, 99% 请求)                      │
│  Fallback 链: deepseek → doubao → glm →          │
│              siliconflow → ollama(本地)          │
├─────────────────────────────────────────────────┤
│  本地 Ollama 层 (兜底, 1% 请求)                   │
│  L1 日常兜底: qwen2.5:14b-instruct-q6_K          │
│  L2 深度兜底: deepseek-r1:14b-q5_K_M             │
│  (可选) L3 大模型: qwen2.5:72b-instruct-q4_K_M   │
└─────────────────────────────────────────────────┘
```

### 3.2 云 API 层配置

| Fallback 顺序 | Provider | 推荐模型 | 用途 | 原因 |
|-------------|----------|---------|------|------|
| 1 (主力) | DeepSeek | `deepseek-chat` (V4) | 日常 AI 决策 | 最便宜, 质量够用 |
| 1 (深度) | DeepSeek | `deepseek-reasoner` (R1) | 复杂对冲决策 | 推理链成熟, 成本可控 |
| 2 (备1) | 豆包 | `doubao-1-5-pro-32k` | 速度优先场景 | 火山引擎稳定, 速度快 |
| 3 (备2) | 智谱 | `glm-5.2` | 代码/长文档 | GLM 5.2 代码能力登顶 |
| 4 (备3) | SiliconFlow | `Qwen/Qwen2.5-72B-Instruct` | 高质量中文 | 72B 中文最强, 价格便宜 |

### 3.3 本地 Ollama 层配置 (三层金字塔)

| 层级 | 模型 | 量化 | 内存占用 | 速度 | 占比 | 用途 |
|------|------|------|---------|------|------|------|
| L1 极速 | `qwen2.5:14b-instruct-q6_K` | Q6_K | ~13GB | ~50 tok/s | 70% | 晨报生成、情绪分类、格式转换 |
| L2 主力 | `qwen2.5:32b-instruct-q5_K_M` | Q5_K_M | ~22GB | ~20 tok/s | 20% | 持仓分析、研报解读、复杂决策 |
| L3 深度 | `qwen2.5:72b-instruct-q4_K_M` | Q4_K_M | ~42GB | ~10 tok/s | 8% | 多标的联动、宏观判断、策略评审 |
| (可选) L4 | `deepseek-r1:32b-q5_K_M` | Q5_K_M | ~22GB | ~8-15 tok/s | 2% | 极端行情深度推理 |

**总内存占用 (L1+L2+L3): ~77GB**, 剩余 ~51GB 给系统+Python 回测+浏览器, 从容。

### 3.4 极简替代方案 (如不想维护 4 个模型)

| 模型 | 量化 | 内存 | 说明 |
|------|------|------|------|
| `qwen2.5:14b-instruct-q6_K` | Q6_K | ~13GB | 一个模型覆盖 95% 场景 |
| `deepseek-r1:14b-q5_K_M` | Q5_K_M | ~10GB | 深度推理备用 |

总共 ~23GB, 剩下 100GB+ 随便用。简单、够用、省心。

---

## 四、128GB 内存分配总览

| 项目 | 内存占用 |
|------|---------|
| macOS 系统 | ~6GB |
| Python 量化回测 + IDE | ~10GB |
| 浏览器 + 其他 | ~6GB |
| Ollama qwen2.5:14b Q6 | ~13GB |
| Ollama deepseek-r1:14b Q5 | ~10GB |
| **已用合计** | **~45GB** |
| **剩余可用** | **~83GB** |
| (可选) 再加 qwen2.5:32b Q5 | ~22GB |
| (可选) 再加 qwen2.5:72b Q4 | ~42GB |
| (全加载后) 合计 | ~109GB |
| (全加载后) 剩余 | ~19GB |

---

## 五、配置文件修改清单

### 5.1 已完成的配置修改

#### ① `v8.3_institutional/config/llm_router.yaml`

| 配置项 | 修改前 | 修改后 |
|--------|--------|--------|
| SiliconFlow 默认模型 | `Qwen/Qwen2.5-7B-Instruct` | `Qwen/Qwen2.5-72B-Instruct` |
| Ollama 日常模型 | `qwen2.5:7b` | `qwen2.5:14b-instruct-q6_K` |
| Ollama 深度模型 | `deepseek-r1:14b` | `deepseek-r1:14b-q5_K_M` |

#### ② `system_config.json`

| 配置项 | 修改前 | 修改后 |
|--------|--------|--------|
| Ollama 模型 | `llama3` | `qwen2.5:14b-instruct-q6_K` |

### 5.2 待修改的 `.env` 文件 (换机后执行)

```bash
# ===== 云 API 层 (保持现有 key 即可) =====
DEEPSEEK_API_KEY=你的key
VOLCENGINE_API_KEY=你的key
GLM_API_KEY=你的key
SILICONFLOW_API_KEY=你的key

# ===== Ollama 本地模型 (M5 Max 128GB 配置) =====
OLLAMA_BASE_URL=http://localhost:11434
OLLAMA_MODEL=qwen2.5:14b-instruct-q6_K
OLLAMA_DEEP_MODEL=deepseek-r1:14b-q5_K_M

# 如果想上 72B 作为本地大模型 (可选):
# OLLAMA_MODEL=qwen2.5:72b-instruct-q4_K_M
# OLLAMA_DEEP_MODEL=deepseek-r1:14b-q5_K_M
```

---

## 六、M5 Max 拿到后的部署步骤

### 6.1 安装 Ollama

1. 访问 https://ollama.com/download/mac 下载 Mac 版安装包
2. 双击安装, 按提示完成
3. 终端验证: `ollama --version`

### 6.2 拉取模型 (按需执行)

```bash
# === 必装 (主力 + 深度) ===
ollama pull qwen2.5:14b-instruct-q6_K
ollama pull deepseek-r1:14b-q5_K_M

# === 可选 (升级到 32B 主力) ===
ollama pull qwen2.5:32b-instruct-q5_K_M

# === 可选 (上 72B 大模型, 128GB 甜点) ===
ollama pull qwen2.5:72b-instruct-q4_K_M

# === 可选 (深度推理链, 替代 14B) ===
ollama pull deepseek-r1:32b-q5_K_M
```

### 6.3 验证模型

```bash
# 基础验证
ollama run qwen2.5:14b-instruct-q6_K "你好, 请用一句话介绍自己"
ollama run deepseek-r1:14b-q5_K_M "请分析 1+1 为什么等于 2"

# 量化场景验证
ollama run qwen2.5:14b-instruct-q6_K "分析贵州茅台2024年三季报的关键财务指标变化"

# 性能验证 (测速度)
time ollama run qwen2.5:14b-instruct-q6_K "写一段200字的市场分析"
```

### 6.4 启动量化系统

```bash
# 1. 同步代码到 M5 Max (从 Git 拉取或 rsync)
cd ~/量化交易系统/28-终极量化交易系统8.4

# 2. 配置 Python 环境
python -m venv venv
source venv/bin/activate
pip install -r requirements.txt

# 3. 配置 .env (参考 5.2 节)

# 4. 启动 Ollama 服务
ollama serve &

# 5. 验证 LLM 路由
python -c "from utils.alpha.llm_router import LLMRouter; r=LLMRouter(); print(r.chat('你好'))"

# 6. 启动量化系统
python main.py
```

---

## 七、Mac + Windows 虚拟机方案（推荐）

### 7.1 整体架构

```
┌──────────────────────────────────────────────────────┐
│  MacBook Pro M5 Max (128GB)                           │
│  ├── 研究回测 (Python + 因子流水线 + LLM 本地部署)    │
│  ├── 代码开发 (IDE + Git + 文档)                      │
│  └── 数据存储 (行情数据 + 模型文件 + 研究报告)         │
├──────────────────────────────────────────────────────┤
│  Windows 11 虚拟机 (Parallel Desktop / VMware Fusion) │
│  ├── QMT 客户端 (实盘交易)                             │
│  ├── 同花顺 iFinD (行情数据)                           │
│  └── Windows 专属工具 (如有)                           │
└──────────────────────────────────────────────────────┘
        ↑ 共享文件夹 / 网络通信 ↑
```

**核心理念**: Mac 负责所有研发工作, Windows 虚拟机只负责跑需要 Windows 环境的客户端 (QMT/iFinD)。

### 7.2 虚拟机方案选型

| 方案 | 软件 | 优势 | 推荐度 |
|------|------|------|--------|
| **Parallel Desktop** | Parallels Desktop | Mac 原生体验最好, 性能损耗最小, 支持 M 系列芯片 | ⭐⭐⭐⭐⭐ 首选 |
| VMware Fusion | VMware Fusion | 免费 (个人版), 性能也不错 | ⭐⭐⭐⭐ 备选 |
| Boot Camp | 原生双启动 | 性能最好, 但无法同时用 Mac | ⭐⭐ 不推荐 |

**推荐 Parallel Desktop 的原因**:
- M5 Max 上运行 Windows 11 ARM 版, 性能损耗极小 (~5-10%)
- 支持 x86_64 程序通过转译运行, QMT 客户端可正常使用
- 与 Mac 共享文件夹方便, 拖拽文件即可
- 支持 Windows 11 许可证自带 (ARM 版)

### 7.3 虚拟机配置建议

| 配置项 | 推荐值 | 说明 |
|--------|--------|------|
| CPU | 8 核 | M5 Max 有 18 核, 分 8 核给 Windows 足够 |
| 内存 | **32GB** | 虚拟机内存, QMT + iFinD + Windows 本身够用 |
| 硬盘 | 200GB+ | 建议存放在 Mac 桌面, 方便共享文件 |
| 网络 | 共享模式 (NAT) | Mac 和虚拟机都能上网 |
| 系统 | Windows 11 ARM | 原生支持 M 系列芯片, 性能最好 |

**为什么给虚拟机 32GB 内存?**
- 128GB 总内存, Mac 系统 + 量化系统占 ~60GB
- 剩余 ~68GB, 分 32GB 给虚拟机, 36GB 留作余量
- QMT 本身不占多少内存, 32GB 绰绰有余

### 7.4 Mac 与 Windows 虚拟机通信方案

#### 方案 A: Parallels 共享文件夹 (推荐)

**优势**: 最简单, 直接在访达中访问 Windows 文件

**设置方法**:
1. Parallels 控制中心 → 配置 → 选项 → 共享文件夹
2. 添加 Mac 上的项目文件夹, 如 `~/量化交易系统`
3. Windows 中会映射为网络驱动器, 如 `\\Mac\Home\量化交易系统`

**文件交换**:
- Mac → Windows: 直接拖拽文件到共享文件夹
- Windows → Mac: QMT 导出数据保存到共享文件夹

#### 方案 B: SSH / SCP (进阶)

**优势**: 命令行直接操作, 适合自动化脚本

```bash
# Mac 终端中直接访问 Windows 虚拟机
ssh user@192.168.1.100

# 或直接用 scp 传文件
scp data.csv user@192.168.1.100:C:\Users\user\Desktop\
```

**查找 Windows 虚拟机 IP**:
- Windows 中运行 `ipconfig`, 找到 IPv4 地址
- 确保 Parallels 网络模式为 "共享网络" 或 "桥接"

### 7.5 QMT 在 Windows 虚拟机中的运行注意事项

| 注意事项 | 说明 |
|----------|------|
| 性能损耗 | Windows 11 ARM 通过转译运行 x86 程序, QMT 性能损耗约 5-10%, 实盘交易完全无影响 |
| 网络稳定性 | Parallels 虚拟网卡稳定, 不会断网, 可放心运行实盘策略 |
| 定时任务 | Windows 任务计划程序在虚拟机中正常运行, 建议设置"当 Mac 开机时自动启动虚拟机" |
| 数据备份 | QMT 数据建议同时备份到 Mac 共享文件夹, 避免虚拟机故障丢失 |

### 7.6 路径与编码统一

| 项目 | Mac 端 | Windows 虚拟机 | 处理方案 |
|------|--------|---------------|---------|
| 项目代码 | `~/量化交易系统/` | `\\Mac\Home\量化交易系统\` | 共享文件夹同步, 两边修改同一份代码 |
| 行情数据 | Mac 本地存储 | 通过共享文件夹访问 | 数据只存在 Mac, Windows 只读取 |
| QMT 数据 | 不存储 | `C:\Users\user\Documents\QMT` | QMT 数据留在 Windows, 需要时复制到 Mac |
| 环境变量 | `.env` (Mac) | `.env` (Windows) | 两边独立配置, API Key 保持一致 |
| 中文路径 | 支持 | 支持 | 无问题 |

### 7.7 开发效率提升

- Python/Unix 工具链在 Mac 上体验更好
- Homebrew 包管理比 Windows 方便很多
- 整体开发效率预计提升 20-30%
- 实盘交易通过 Windows 虚拟机, 无任何兼容性问题

---

## 八、模型选型核心原则总结

| 原则 | 说明 |
|------|------|
| 中文优先 | 量化系统文本全是中文 (财报/研报/公告), Qwen 系列首选 |
| 分层调用 | 不求一个模型打天下, 按场景分层: 日常走小模型, 深度走大模型 |
| 本地兜底 | 本地模型是兜底, 不是主力, 主力还是云 API (DeepSeek 最便宜) |
| 速度优先 | 盘前决策走小模型 (快), 盘后研究走大模型 (深) |
| 不求最大 | 72B 已是 128GB 甜点, 再往上性价比急剧下降 |

---

## 九、后续优化方向

1. **IC 加权组合模型适配** - 探索用本地 72B 模型辅助因子权重分析
2. **RAG 知识库** - 利用 128GB 内存优势, 本地部署向量数据库 + 72B 模型做研报 RAG
3. **多模型并行** - 同时加载 14B + 32B + 72B, 按请求复杂度自动路由
4. **微调探索** - 用 LLaMA-Factory 对 Qwen 14B 做金融领域微调 (M5 Max GPU 加速)

---

## 附录: 相关文件引用

- LLM 路由配置: [v8.3_institutional/config/llm_router.yaml](file:///e:/各种PY程序/28-终极量化交易系统8.4/v8.3_institutional/config/llm_router.yaml)
- LLM 路由实现: [utils/alpha/llm_router.py](file:///e:/各种PY程序/28-终极量化交易系统8.4/utils/alpha/llm_router.py)
- 系统配置: [system_config.json](file:///e:/各种PY程序/28-终极量化交易系统8.4/system_config.json)
- QMT 交易接口: [ms_strategy/src/execution/qmt_broker.py](file:///e:/各种PY程序/28-终极量化交易系统8.4/ms_strategy/src/execution/qmt_broker.py)
- 日常工作流: [v8.3_institutional/daily_workflow.py](file:///e:/各种PY程序/28-终极量化交易系统8.4/v8.3_institutional/daily_workflow.py)

---

**文档版本**: v1.1
**最后更新**: 2026-08-06
**状态**: 配置已就绪, 待 M5 Max 到货后执行部署
