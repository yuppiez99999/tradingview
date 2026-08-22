# ds4 集成指南 (DwarfStar 推理引擎)

> 集成日期: 2026-08-21 (W34, B 轨 ds4 POC)
> 来源: [antirez/ds4](https://github.com/antirez/ds4) — Redis 作者 Salvatore Sanfilippo 推出的自包含原生推理引擎
> 状态: **代码集成完成, feature flag 默认关闭, 待 shadow 验证**

---

## 一、项目背景

ds4 (DwarfStar) 是 antirez 推出的原生推理引擎:
- 首发针对 **DeepSeek V4 Flash** 优化, 同时支持 **GLM 5.2** (系统当前模型)
- 自包含、刻意收窄 (非通用 GGUF runner)
- 内置 HTTP server (OpenAI 兼容端点 `/v1/chat/completions`)
- 2-bit 非对称量化 + DSpark 推测解码
- Metal / CUDA / ROCm 多后端
- 实测: MacBook Pro M5 Max 128GB 生成 39.35 t/s (2048 上下文)

**与 v8.6 契合点**: 系统已用 `utils/glm5_client.py` + `utils/alpha/llm/router.py` LLMRouter (5-provider fallback), ds4 可作为新 provider 加入, 替换 Ollama 本地后端, 加速盘前决策。

---

## 二、集成点

```
utils/alpha/llm/router.py LLMRouter
  └─ _register_default_providers()
      └─ self.register_provider("ds4", self._call_ds4)  # 新增
  └─ _fallback_chain = [..., "siliconflow", "ds4", "ollama"]  # ds4 在 ollama 前
  └─ _call_ds4() 薄代理 → call_ds4()
      └─ utils/alpha/llm/providers/ds4.py  # 新增
          └─ openai_compatible_chat()  # 复用 OpenAI 兼容逻辑
```

**fallback 链**: `omniroute → deepseek → doubao → glm → siliconflow → ds4 → ollama`

ds4 在 ollama 前 (ds4 性能更好), 但 feature flag 默认关闭, 实际不生效。

---

## 三、代码改动清单 (W34 已完成)

| 文件 | 改动 | 行数 |
|---|---|---|
| `utils/alpha/llm/providers/ds4.py` | **新建** — call_ds4() 复用 openai_compatible_chat | ~80 |
| `utils/alpha/llm/providers/__init__.py` | 导出 call_ds4 + 文档字符串 | +2 |
| `utils/alpha/llm/router.py` | import + 默认 chain 2处 + register + _call_ds4 方法 | +20 |
| `tests/unit/test_llm_router.py` | 断言更新 (6→7 provider, chain 加 ds4) | ~8 |

**验证**: `pytest tests/unit/test_llm_router.py tests/unit/test_d6_litellm_router.py` → **91 passed**

---

## 四、Feature Flag

| 环境变量 | 默认 | 说明 |
|---|---|---|
| `GLM5_DS4_ENABLED` | `0` (关闭) | `1` 开启 ds4 provider |
| `DS4_BASE_URL` | `http://localhost:8080` | ds4 HTTP server 地址 |
| `DS4_MODEL` | `glm-antirez-q4` | GLM 5.2 Q4_K 单文件 GGUF |

**关闭时行为**: `call_ds4()` 直接返回 None, 不影响 fallback 链 (优雅降级到 ollama)。

---

## 五、启动与验证

### 5.1 启动 ds4 服务 (需 Mac ≥96GB 或 CUDA GPU)

```bash
# 下载模型
./download_model.sh glm-antirez-q4   # GLM 5.2 Q4_K

# 启动 HTTP server (端口 8080)
./ds4 --model glm-antirez-q4 --port 8080
```

### 5.2 shadow 验证 (W35)

```bash
# 1. ds4 与 Ollama 并行推理, 对比响应一致性
export GLM5_DS4_ENABLED=1
export DS4_BASE_URL=http://localhost:8080

# 2. 跑盘前决策, 对比 ds4 vs ollama 输出
python "量化策略系统_统一入口_v8.6.py" --ai-decision

# 3. 连续 7 天 shadow 运行, 不影响实盘
#    验收: ds4 与 Ollama 决策一致率 ≥ 95%
#    验收: ds4 延迟 < Ollama 50%
```

### 5.3 定版 (W36)

shadow 验证通过后, 在 `config/llm_router.yaml` (若创建) 或环境变量永久设置 `GLM5_DS4_ENABLED=1`。

---

## 六、验收标准

- [x] 代码集成完成 (91 passed)
- [x] feature flag 默认关闭, 不影响现有行为
- [~] ds4 本地启动成功 — **❌ 本机不可行**（见 §九 硬件评估）
- [ ] shadow 运行 7 天, 决策一致率 ≥ 95% — 阻塞于本地硬件
- [ ] ds4 延迟 < Ollama 50% — 阻塞于本地硬件
- [ ] 定版后盘前决策延迟显著改善 — 阻塞于本地硬件

---

## 七、回滚

`GLM5_DS4_ENABLED=0` 即切回 Ollama, 零影响。

---

## 八、风险

| 风险 | 缓解 |
|---|---|
| ds4 是 Beta (热榜文档明确) | feature flag + shadow 验证 |
| 需 Mac ≥96GB 或 CUDA GPU | 无合格硬件时保持 Ollama |
| ds4 HTTP server 端口冲突 | DS4_BASE_URL 可配置 |
| GLM 5.2 vs GLM-5 模型差异 | shadow 对比验证一致性 |

---

## 九、本机硬件评估结论（2026-08-21）

> **结论: ds4 本地 POC 不可行，保持 Ollama fallback，feature flag 永久关闭（除非获得远程 GPU 服务器）。**

### 9.1 本机硬件

| 项 | 值 |
|---|---|
| OS | Windows 11 (win32) |
| GPU | NVIDIA GeForce RTX 3060 Laptop GPU |
| 显存 | 6144 MiB (6 GB)，空闲 5877 MiB |
| 驱动 | 596.21 |
| 架构 | Ampere (compute 8.6) |

### 9.2 不可行原因（双重阻断）

**阻断 1 — 无 Windows 原生编译支持**:
- ds4 Makefile 仅支持 macOS Metal / Linux CUDA / Linux ROCm / CPU-only
- 无 `make windows` 或 MSVC 项目文件
- 需 WSL2 + CUDA on WSL 间接编译，但 ds4 未验证此路径，Beta 状态风险高

**阻断 2 — 6 GB 显存远不够**:
- ds4 2-bit 量化仅压缩 routed MoE experts（IQ2_XXS / Q2_K），dense parts（attention / shared experts / projections / routing）保持 Q8/F32 原精度
- GLM 5.2 dense parts 驻留内存需求估计 10-20 GB+（远超 6 GB）
- DeepSeek V4 Flash Q2 最低需 96/128 GB RAM
- SSD streaming 也无法救：streaming 仅对 routed experts 流式加载，dense parts 必须驻留，6 GB 连 dense parts 都放不下
- 已是最激进量化（IQ2_XXS），无法进一步压缩

### 9.3 降级路径评估（v86 方案 §4.1.1 四级降级）

| 级别 | 方案 | 可行性 | 说明 |
|---|---|---|---|
| 1 | ds4 shadow（本机 CUDA） | ❌ | 6 GB 显存不够 + 无 Windows 编译 |
| 2 | 更小量化 | ❌ | 已是 IQ2_XXS（最激进），无更小选项 |
| 3 | CPU 推理（`make cpu`） | ❌ | 需 WSL2 编译 + 系统内存可能不够 + 速度极慢（<1 t/s）无实用价值 |
| 4 | 远程 ds4 服务器 | ⚪ | 需远程 Mac ≥96GB 或 CUDA ≥24GB 服务器，当前无此资源 |
| 5 | Ollama 本地（现状） | ✅ | 当前 fallback 链终端，保持不变 |

### 9.4 决策

- **ds4 代码集成保留**（`utils/alpha/llm/providers/ds4.py` + router 注册），不回滚
- **`GLM5_DS4_ENABLED=0` 永久保持**，除非获得远程 GPU 服务器
- **fallback 链不变**: `omniroute → deepseek → doubao → glm → siliconflow → ds4(关闭) → ollama`
- **v86 方案 §4.1 B 轨 ds4 部分标记为"代码集成完成，本地 POC 不可行，待远程硬件"**
- **不影响 C 轨**：C 轨 ai_decision 集成使用 `glm5_client.py` / `router.py`，provider 可用性由 fallback 链保证

### 9.5 后续触发条件

若以下任一条件满足，重新评估 ds4 本地 POC：
1. 获得远程 CUDA GPU 服务器（≥24 GB 显存）
2. 获得 Mac（≥96 GB 统一内存）
3. ds4 发布 Windows 原生支持 + 更小量化变体

---

*集成完成时间: 2026-08-21*
*硬件评估完成: 2026-08-21 — 本机不可行*
*下一步: 保持 Ollama fallback；ds4 待远程硬件触发*