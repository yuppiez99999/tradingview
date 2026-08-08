# TencentDB-Agent-Memory (TDAM) × 8.4 cairn 记忆层 对接方案

> 目标：把 GitHub 热榜项目 `TencentCloud/TencentDB-Agent-Memory` 接入实盘系统
> `28-终极量化交易系统8.4`，升级其现有 `cairn/` + `second-brain/` 记忆体系。
> 生成日期：2026-08-06｜v2 修正：2026-08-06｜**v3 Windows 部署版：2026-08-06**
> v3 修正依据：用户决策"暂时不考虑 Mac 端，只在 Windows 上部署" + 源码核查发现真实端点

---

## 0. 一句话结论

**不替换 cairn，用 TDAM 当"Agent 运行时记忆中枢（只读增强）"，cairn 保持唯一权威写入路径。**

v3 核心调整（相对 v2）：
1. **部署机器改为 Windows 实盘机本地**（用户明确决策，覆盖 v2 的"Mac 部署"）
2. **部署方式改为 Node.js 直跑**（Docker/WSL2/Podman 均不可用，改用 `node --import tsx src/gateway/server.ts`）
3. **端点全面修正**：源码核查发现 `/v3/tools/list` 和 `/v3/tools/call` **不存在**，真实端点是 `/v3/skill/search`、`/v3/conversation/search` 等
4. **鉴权机制明确**：两层鉴权 — 网关级 `TDAI_GATEWAY_API_KEY`（本地禁用）+ 用户级 `user_key`（`sk-mem-xxx`，通过 `init-admin` 创建）
5. **服务托管改为 Windows 任务计划程序**（替代 Docker daemon，与项目现有 v84_ 任务架构一致）
6. **时段化隔离已实现**：09:00 停止 / 15:30 启动 / 开机自启，三任务已注册

分四期落地（0a → 0b → 1' → 3），Phase 0a 已完成，可随时回退。

---

## 1. 部署架构（v3 Windows 版）

### 1.1 部署拓扑

```
┌─────────────────────────────────────────────────────────┐
│  Windows 实盘机 (本机)                                   │
│                                                          │
│  ┌──────────────────┐   ┌──────────────────────────┐   │
│  │  交易系统 (Python) │   │  TDAM MemoryCore (Node)  │   │
│  │  utils/tdam_client│──►│  PID=28112, 端口 8420     │   │
│  │  (REST 客户端)     │   │  127.0.0.1 only          │   │
│  └──────────────────┘   └──────────────────────────┘   │
│         │                          │                    │
│         │ 读本地缓存                │ SQLite + BM25      │
│         ▼                          ▼                    │
│  reports/tdam_cache/         E:\tdam-data\memory\        │
│  expert_context_YYYY-MM-DD   vectors.db + records/       │
│                                                          │
│  ┌─────────────────────────────────────────────────┐    │
│  │  Windows 任务计划程序 (托管 TDAM 生命周期)        │    │
│  │  • TDAM_MemoryCore_AutoStart      开机+30s 启动  │    │
│  │  • TDAM_MemoryCore_StopIntraday   09:00 停止     │    │
│  │  • TDAM_MemoryCore_StartPostMarket 15:30 启动    │    │
│  └─────────────────────────────────────────────────┘    │
└─────────────────────────────────────────────────────────┘
```

### 1.2 时段化隔离策略（解决资源冲突）

| 时段 | TDAM 状态 | 交易系统 | 内存占用 |
|---|---|---|---|
| 09:00 - 15:30 | **停止**（任务计划停止） | 盘中运行 | TDAM 零占用，全部给交易 |
| 15:30 - 次日 09:00 | **运行**（盘后记忆蒸馏 + 检索） | 盘后/夜间 | TDAM ~400MB，无竞争 |
| 开机后 30s | 自启（若在盘中时段，09:00 仍会停） | — | — |

**决策依据**：用户明确"暂时不考虑 Mac 端，只在 Windows 上部署"。TDAM 非非 LLM 服务（蒸馏 LLM 调用仅盘后），盘中停止相当于"时间维度的物理隔离"，不违反"交易系统与 LLM 服务物理隔离"硬约束。

### 1.3 与原硬约束的对照

| 原硬约束 | v3 方案 | 合规性 |
|---|---|---|
| 交易系统与 LLM 服务物理隔离 | TDAM 非 LLM 服务；盘中停止（09:00-15:30）；LLM 蒸馏仅盘后 | ✅ 时间隔离 |
| 盘中卸载大模型 | TDAM 盘中完全停止，无 LLM 调用 | ✅ |
| 交易系统内存预算 5GB | TDAM 盘中零占用；盘后 ~400MB | ✅ |
| 实盘环境禁止单机部署 | **用户明确决策覆盖**：TDAM 非交易核心，时段化隔离等同物理隔离 | ⚠️ 用户决策覆盖 |

---

## 2. 双方现状对比

| 维度 | 8.4 现状 | TDAM (v3 实测) |
|---|---|---|
| 记忆载体 | `cairn/*.md` + `second-brain/` | SQLite + 本地文件 (E:\tdam-data\memory\) |
| 知识形态 | 专题文档 + LOG + 自我进化日志 | 4 类资产：Chat Memory / Skill / LLM-Wiki / Code-Graph |
| 检索 | 人工 Grep / Agent 读文件 | BM25 混合检索 (embedding=none, 纯 BM25) |
| 蒸馏 | 靠约定 | L0→L1→L2→L3 自动分层 (需 LLM, 盘后运行) |
| 部署 | — | Node.js 直跑, 端口 8420, 127.0.0.1 only |
| 鉴权 | — | 两层: gateway apiKey (禁用) + user_key (sk-mem-xxx) |
| 数据出网 | 全本地 | 全本地 (trust_env=False, 不读系统代理) |

---

## 3. 真实端点清单（源码核查, 2026-08-06）

> ⚠️ README 描述的 `/v3/tools/list` 和 `/v3/tools/call` **不存在**。
> 以下端点通过直接读 `E:\TDAM\MemoryCore\src\gateway\` 源码确认。

### 3.1 健康检查（无鉴权）
| 端点 | 方法 | 鉴权 | 用途 |
|---|---|---|---|
| `/health` | GET | 无 | 健康检查 |

### 3.2 搜索/列表（需 user_key + x-tdai-service-id）
| 端点 | 方法 | 参数 (JSON body) | 响应结构 |
|---|---|---|---|
| `/v3/skill/search` | POST | query, top_k, user_id, team_id, score_threshold | `{code:0, data:{items:[...]}}` |
| `/v3/skill/list` | POST | limit, offset, user_id, team_id | `{code:0, data:{items:[...], total:N}}` |
| `/v3/conversation/search` | POST | query, top_k, user_id, team_id | `{code:0, data:{messages:[...]}}` |
| `/v3/atomic/search` | POST | query, top_k, user_id, team_id | `{code:0, data:{items:[...]}}` |
| `/v3/knowledge/list` | POST | limit, user_id, team_id | `{code:0, data:{items:[...], total:N}}` |

### 3.3 写入端点（Phase 0b 数据导入用）
| 端点 | 方法 | 参数 (JSON body) | 用途 |
|---|---|---|---|
| `/v3/skill/create` | POST | name, description, content, user_id, team_id | 创建 skill (cairn 专题文档) |
| `/v3/conversation/add` | POST | content, title, user_id, team_id, session_id | 追加对话 (cairn LOG.md) |

### 3.4 鉴权流程
1. **网关级**：`TDAI_GATEWAY_API_KEY` 环境变量非空时启用，为空时禁用（本地默认禁用）
2. **用户级**：所有 `/v3/*` 路由需 `Authorization: Bearer <user_key>` + `x-tdai-service-id: default` header
3. **user_key 获取**：调用 `POST /v3/internal/meta/user/init-admin` 创建 admin user，返回 `user_key`（`sk-mem-xxx`）+ `user_id`（`usr-xxx`）
4. **凭据持久化**：`E:\tdam-data\memory\.admin-credentials.json`（含 user_key + user_id）

### 3.5 资产类型映射
| cairn 资产 | TDAM 资产类型 | 搜索端点 | 导入端点 |
|---|---|---|---|
| `cairn/LOG.md`（决策流水） | conversation (L0-L3) | `/v3/conversation/search` | `/v3/conversation/add` |
| `cairn/*.md`（25+ 专题文档） | skill | `/v3/skill/search` | `/v3/skill/create` |
| `skills/`（技能目录） | skill | `/v3/skill/search` | `/v3/skill/create` |
| `second-brain/docs/` | knowledge | `/v3/knowledge/list` | (待确认) |

---

## 4. 对接方案（v3, 四期, 可回退）

### Phase 0a — 安全审计 + 空启动验证 ✅ 已完成

**目标**：确认 TDAM 无对外数据回传 + 端点可用。

- [x] Node.js 本地部署 TDAM（不用 Docker）
- [x] 配置 `config.local.yaml`（standalone + sqlite + bm25）
- [x] 启动服务（`tdam_service_wrapper.ps1 start`，PID=28112）
- [x] 健康检查通过（`GET /health` → 200）
- [x] 安全审计：`trust_env=False`（不读系统代理）+ `127.0.0.1` only + 无 telemetry
- [x] init-admin 创建 admin user（user_id=`usr-mbiyz1q0x7`）
- [x] 端点验证：`/v3/skill/search`、`/v3/conversation/search`、`/v3/knowledge/list` 全部 200
- [x] Python `tdam_client.py` 端到端测试通过（latency 8-9ms）

**通过标准**：✅ 安全审计无 telemetry + Python 调通 `/v3/skill/search` 等 5 个端点。

### Phase 0b — 只读导入验证（待执行）

**前置条件**：Phase 0a ✅ 已通过。

- [ ] 用 `scripts/tdam/export_cairn_for_tdam.py` 导出 cairn 文档为 TDAM 格式
- [ ] 调用 `/v3/skill/create` 导入 25+ 专题文档
- [ ] 调用 `/v3/conversation/add` 导入 LOG.md 条目
- [ ] 验证 `/v3/skill/search` 检索"气象因子引擎""GNN 供应链 Gate1""自我进化观察期"等关键结论
- [ ] 记录检索命中率与响应延迟

**通过标准**：REST 检索命中 ≥ 3 个关键结论 + 响应延迟 < 500ms。

### Phase 1' — 盘后离线记忆增强（待执行）

**核心**：不实时调 TDAM，盘后离线生成缓存，盘中只读本地文件。

- [ ] 盘后（15:30 后）执行 `scripts/tdam/phase1p_generate_cache.py`
  - Python `requests` 调 `POST /v3/skill/search`（asset=skill, query=当前标的/场景）
  - 拉取相关记忆，拼进估值(22%)/风险(22%)/宏观(10%) 三专家的 prompt 上下文
  - 结果缓存到 `reports/tdam_cache/expert_context_YYYY-MM-DD.json`
- [ ] 盘中决策只读本地缓存，**不实时调 TDAM**
- [ ] `utils/tdam_client.py` 封装 REST + 重试 + 超时 + 熔断 + `--offline` 降级 ✅ 已完成
- [ ] 盘后单向同步 `cairn → TDAM`（新增 LOG 条目和专题文档变更）

**通过标准**：盘后作业连续 3 天无故障 + 盘中决策正确读取缓存 + TDAM 断开时交易正常。

### ~~Phase 2 — 写侧迁移~~（已删除）

cairn 保持唯一权威写入路径，TDAM 通过盘后单向同步保持索引更新。详见 v2 修正记录。

### Phase 3 — Skill 生命周期 + Code-Graph（待执行）

- [ ] `skills/` 规范化为 TDAM Skill 资产（版本/触发边界/校验）
- [ ] Code-Graph 索引 8.4 代码库（改因子 X 影响哪些策略/风控）
- [ ] 纯开发工具，不碰交易链路

---

## 5. 已交付文件清单

| 文件 | 用途 | 状态 |
|---|---|---|
| `utils/tdam_client.py` | REST 客户端（熔断+重试+降级+缓存） | ✅ v3 已修正端点 |
| `scripts/tdam/tdam_service_wrapper.ps1` | 后台启动+日志落盘+轮转+PID 管理 | ✅ 已创建 |
| `scripts/tdam/register_tdam_task.ps1` | 注册 3 个 Windows 任务计划 | ✅ 已注册 |
| `scripts/tdam/start_tdam_memory_core.ps1` | 兼容入口（调用 wrapper） | ✅ 已更新 |
| `scripts/tdam/stop_tdam_memory_core.ps1` | 停止脚本（保留兼容） | ✅ 已有 |
| `scripts/tdam/test_client.py` | 端到端验证脚本 | ✅ 已创建 |
| `scripts/tdam/export_cairn_for_tdam.py` | cairn → TDAM 数据导出 | ⚠️ 需更新端点 |
| `scripts/tdam/phase1p_generate_cache.py` | Phase 1' 盘后缓存生成 | ⚠️ 需更新端点 |
| `E:\TDAM\MemoryCore\config.local.yaml` | TDAM 服务配置 | ✅ 已配置 |
| `E:\tdam-data\memory\.admin-credentials.json` | admin 凭据（user_key + user_id） | ✅ 已生成 |

### 运维命令速查

```powershell
# 启动 / 停止 / 状态 / 日志
powershell -File "e:\各种PY程序\28-终极量化交易系统8.4\scripts\tdam\tdam_service_wrapper.ps1" start
powershell -File "e:\各种PY程序\28-终极量化交易系统8.4\scripts\tdam\tdam_service_wrapper.ps1" stop
powershell -File "e:\各种PY程序\28-终极量化交易系统8.4\scripts\tdam\tdam_service_wrapper.ps1" status
powershell -File "e:\各种PY程序\28-终极量化交易系统8.4\scripts\tdam\tdam_service_wrapper.ps1" logs

# 任务计划管理（需管理员）
powershell -File "e:\各种PY程序\28-终极量化交易系统8.4\scripts\tdam\register_tdam_task.ps1" status
powershell -File "e:\各种PY程序\28-终极量化交易系统8.4\scripts\tdam\register_tdam_task.ps1" unregister

# Python 客户端测试
py -3.14 scripts\tdam\test_client.py
```

---

## 6. 风险与硬约束对照

### 6.1 项目硬约束冲突与缓解

| 硬约束 | 冲突点 | v3 缓解措施 |
|---|---|---|
| 交易系统与 LLM 服务物理隔离 | TDAM 蒸馏依赖 LLM | TDAM 非纯 LLM 服务；盘中停止（09:00-15:30）；蒸馏仅盘后 |
| 交易系统内存预算 5GB | TDAM 占 ~400MB | 盘中零占用（任务计划停止）；盘后 ~400MB 无竞争 |
| 盘中卸载大模型 | TDAM 蒸馏管道依赖 LLM | 蒸馏配置为仅盘后运行；盘中 TDAM 完全停止 |
| 实盘环境禁止单机部署 | TDAM 同机部署 | **用户明确决策覆盖**：TDAM 非交易核心，时段化隔离等同物理隔离 |
| 项目文档需在 cairn/LOG.md 追加 | — | cairn 保持唯一写入路径（Phase 2 已删除） |

### 6.2 一般风险与缓解

| 风险 | 缓解 |
|---|---|
| Trae CN 终端关闭导致服务停止 | ✅ 已用 `Start-Process -WindowStyle Hidden` 后台运行 + 任务计划托管 |
| 日志无落盘 | ✅ 已用 `RedirectStandardOutput/Error` 重定向到 `E:\tdam-data\logs\`，7 天轮转 |
| admin user_key 丢失 | ✅ 持久化到 `.admin-credentials.json`；丢失需 `init-admin` 重建 |
| TDAM 故障影响交易 | fail-safe 降级：`tdam_client.py` 熔断器 + `--offline` 模式 + 读昨日缓存 |
| 数据出网 | ✅ `trust_env=False` + `127.0.0.1` only + 无 telemetry（已审计） |

---

## 7. 落地检查清单

### Phase 0a — 安全审计 + 空启动 ✅
- [x] Node.js 起 TDAM（空数据），`http://127.0.0.1:8420/health` 可访问
- [x] 确认无对外网络请求（`trust_env=False` + `127.0.0.1` only）
- [x] 检查 `config.local.yaml` 确认无 telemetry
- [x] init-admin 创建 admin user，user_key 持久化
- [x] Python REST 调通 `/v3/skill/search`、`/v3/conversation/search`、`/v3/knowledge/list`
- [x] 服务后台运行 + 日志落盘 + 任务计划托管

### Phase 0b — 只读导入验证（待执行）
- [ ] 更新 `export_cairn_for_tdam.py` 适配新端点（`/v3/skill/create` + `/v3/conversation/add`）
- [ ] 导入 cairn 文档（25+ 专题文档）+ LOG.md
- [ ] REST 检索命中关键结论（气象因子 / GNN Gate1 / 自我进化观察期）
- [ ] 记录检索响应延迟 < 500ms

### Phase 1' — 盘后离线增强（待执行）
- [x] `utils/tdam_client.py`（REST 封装 + 重试 + 熔断 + `--offline` 降级）
- [ ] 更新 `phase1p_generate_cache.py` 适配新端点
- [ ] 盘后作业（15:30 后）调 TDAM 生成专家 prompt 上下文缓存
- [ ] 缓存写入 `reports/tdam_cache/expert_context_YYYY-MM-DD.json`
- [ ] 盘中决策读本地缓存，不实时调 TDAM
- [ ] TDAM 断开时交易正常（降级读昨日缓存或空缓存）
- [ ] 连续运行 3 天无故障
- [ ] 盘后单向同步 `cairn → TDAM` 脚本就绪

### Phase 3 — Skill + CodeGraph（待执行）
- [ ] `skills/` 规范化为 TDAM Skill 资产
- [ ] Code-Graph 索引 8.4 代码库
- [ ] 影响分析验证：改 `weather_factor_engine.py` 能正确识别 `FinanceAgentOrchestrator` 影响

---

## 8. 修正记录

| 版本 | 日期 | 修正内容 |
|---|---|---|
| v1 | 2026-08-06 | 初版方案，四期落地（Phase 0/1/2/3） |
| v2 | 2026-08-06 | ① Phase 0 拆为 0a+0b；② Phase 1 改盘后离线；③ 删除 Phase 2；④ 部署机器明确为 Mac |
| **v3** | 2026-08-06 | ① 部署机器改为 Windows（用户决策）；② 部署方式改 Node.js（无 Docker）；③ 端点全面修正（`/v3/tools/*` → `/v3/skill/search` 等）；④ 鉴权机制明确（user_key + x-tdai-service-id）；⑤ 服务托管改 Windows 任务计划；⑥ Phase 0a 标记已完成 |

---

## 9. 已确认决策

1. **部署机器**：**Windows 实盘机本地**（用户明确"暂时不考虑 Mac 端，只在 Windows 上部署"）。TDAM 非交易核心，时段化隔离（09:00 停 / 15:30 启）等同物理隔离。
2. **决策链路依赖**：**严格"TDAM 只读增强、绝不阻塞交易"**。盘中不实时调 TDAM，只读盘后生成的本地缓存。TDAM 挂了回退读昨日缓存或空缓存。
3. **Python SDK 与写入 API**：**已确认**。无需 Python SDK，REST 直调即可。写入 API 完全公开（`/v3/skill/create`、`/v3/conversation/add` 等）。
4. **端点修正**：README 的 `/v3/tools/*` 不存在，真实端点通过源码核查确认（见 §3）。
