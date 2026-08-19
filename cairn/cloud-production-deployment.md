---
type: project_topic
status: active
authoring_mode: ai_generated
created: 2026-08-17
updated: 2026-08-17
contains: cloud-production, k8s, docker, rpc-bridge, cronjob, streamlit, runbook, rollback, migration
related:
  - cairn/cloud-modelarts-deployment.md
  - cairn/architecture-map.md
  - docs/云端部署小白教程_Windows版_从零到第一个训练作业_20260815.md
---

# 生产上云部署 Runbook (云 Linux + Win 实盘机 混合架构)

> 本文档是 28 终极量化交易系统**生产实盘闭环**上云的执行手册。
> 与 `cloud-modelarts-deployment.md`（仅训练作业）互补：那份管"周末重训"，本份管"每日实盘"。

## 一、架构总览

```
MacBook Pro (开发)  ──git/scp──▶  云 Linux (大脑)  ──HTTPS/RPC──▶  Win 实盘机 (手脚)
                                 live_scheduler        qmt_rpc_server
                                 K8s CronJob ×11       QMT 客户端
                                 Streamlit UI          xtquant
                                 LightGBM/因子/回测
```

| 组件 | 位置 | 形态 | 产物 |
|---|---|---|---|
| 策略/因子/回测/采集/UI | 云 Linux (华东区) | K8s Deployment + CronJob | `cloud/k8s/*.yaml` |
| 实盘下单 | Win 实盘机 (现有) | FastAPI 网关 + QMT 客户端 | `utils/execution/qmt_rpc_server.py` |
| 云→Win 桥接 | 云端 | RemoteQmtBroker (httpx) | `utils/execution/remote_qmt_broker.py` |
| 镜像 | SWR (qt1) | `qt-system:v1` | `cloud/docker/Dockerfile.system` |
| 数据 | 云盘 PVC + OBS 冷备 | parquet/SQLite | `data_cache/` `qlib_data/` |
| 训练 | ModelArts (已有) | 周末 CronJob | 见 `cloud-modelarts-deployment.md` |

## 二、产物清单 (本次新增)

| 文件 | 职责 |
|---|---|
| `requirements-core.txt` / `requirements-win.txt` / `requirements-cloud.txt` | 依赖三分 (跨平台/Win/云) |
| `cloud/docker/Dockerfile.system` | 完整系统镜像 (python:3.11-slim + lightgbm/streamlit/akshare) |
| `.dockerignore` | 构建上下文排除 |
| `utils/execution/qmt_rpc_server.py` | Win 侧 FastAPI 网关 (下单/撤单/持仓/账户) |
| `utils/execution/remote_qmt_broker.py` | 云侧 HTTP 客户端 (实现 BrokerAPI) |
| `utils/execution/broker_factory.py` | 改造: 加 RemoteQmtBroker 分支 (QMT_RPC_URL 驱动) |
| `tests/unit/test_remote_qmt_broker_unit.py` | 桥接单元测试 (mock HTTP, fail-open 覆盖) |
| `cloud/k8s/cronjobs.yaml` | 11 个 CronJob (替代 register_all_tasks_unified.ps1) |
| `cloud/k8s/deployments.yaml` | live_scheduler + streamlit Deployment + 5 PVC + Service |
| `cloud/k8s/config.yaml` | Secret 占位 + ConfigMap |
| `cloud/k8s/ingress.yaml` + `kustomization.yaml` | 域名/HTTPS + 统一 apply |
| `scripts/cloud/sync_data.sh` | rsync 数据/配置到云 |
| `scripts/cloud/env_to_secret.py` | .env → K8s Secret YAML |
| `scripts/cloud/install_qmt_rpc_service.ps1` | NSSM 注册 Win 网关服务 |
| `utils/cloud/health_exporter.py` | Prometheus 指标 (broker/持仓/资金/RPC延迟) |

## 三、迁移步骤 (分阶段, 零停机)

### 阶段 0: 云资源准备 (用户在云控制台)
1. 开华为云 ECS (华东-上海一, 4C16G, Ubuntu 22.04, 100G SSD)
2. 安装 k3s (轻量 K8s): `curl -sfL https://get.k3s.io | sh -`
3. 配置 VPN 到本地 Win 机 (Tailscale: `curl -fsSL https://tailscale.com/install.sh | sh`)
4. 复用已有 SWR 组织 `qt1` / OBS 桶 `qt-data` `qt-models`

### 阶段 1: 构建推送镜像
```bash
# 在 Mac 或 Win (Docker Desktop)
docker build --platform linux/amd64 -f cloud/docker/Dockerfile.system -t qt-system:v1 .
# 推 SWR (用 crane 绕代理 bug, 见 cloud-modelarts-deployment.md §三)
docker save -o system.tar qt-system:v1
crane push system.tar swr.cn-east-3.myhuaweicloud.com/qt1/qt-system:v1
```

### 阶段 2: 数据同步
```bash
# 在 Mac (rsync)
bash scripts/cloud/sync_data.sh ubuntu@<云机IP> /opt/quant
# 云机: 把 /opt/quant/{data_cache,qlib_data} 绑定到 PV hostPath
```

### 阶段 3: 部署非实盘
```bash
# 生成真实 Secret (从 .env)
python scripts/cloud/env_to_secret.py .env > cloud/k8s/secret-real.yaml
# 应用 (云机)
kubectl apply -f cloud/k8s/secret-real.yaml
kubectl apply -f cloud/k8s/config.yaml
kubectl apply -f cloud/k8s/deployments.yaml
kubectl apply -f cloud/k8s/cronjobs.yaml
kubectl apply -f cloud/k8s/ingress.yaml
# 或一键: kubectl apply -k cloud/k8s/
```
验证: MacBook 浏览器访问 `https://<域名>` 看到 Streamlit UI 16 页面。

### 阶段 4: Win 实盘机改造
```powershell
# Win 机 (管理员 PowerShell)
pip install fastapi uvicorn httpx
# 设环境变量 (QMT 账户 + RPC token)
[Environment]::SetEnvironmentVariable("QMT_ACCOUNT_ID", "800123456", "Machine")
[Environment]::SetEnvironmentVariable("QMT_PATH", "D:\QMT\userdata_mini", "Machine")
[Environment]::SetEnvironmentVariable("QMT_RPC_TOKEN", "<强随机token>", "Machine")
# 注册服务
.\scripts\cloud\install_qmt_rpc_service.ps1 -Install
# 验证
curl http://localhost:8765/health -H "X-Token: $env:QMT_RPC_TOKEN"
```

### 阶段 5: 云端接通桥接
```bash
# 云机: 在 secret-real.yaml 填 QMT_RPC_URL (Win 机 Tailscale IP) + QMT_RPC_TOKEN
kubectl edit secret quant-secret -n quant
#   QMT_RPC_URL: http://<win-tailscale-ip>:8765
#   QMT_RPC_TOKEN: <同 Win 侧>
# 重启 live-scheduler 使 Secret 生效
kubectl rollout restart deployment/live-scheduler -n quant
```
验证小单: 1 手 ETF 走完整链路 (云决策→Win 网关→QMT→回传→云 UI 显示持仓)。

### 阶段 6: 切换 + 回收
- 旧 Win 机停掉 `register_all_tasks_unified.ps1` 注册的非下单任务 (保留 QMT + 网关)
- 观察 1 周稳定后, 旧机只做下单手脚

## 四、回滚预案 (零数据损失)

| 失败环节 | 回滚动作 | 耗时 |
|---|---|---|
| 云机宕机 | K8s 自动重启; 极端时旧 Win 机 `TRADING_ENV=production` 不配 `QMT_RPC_URL` → 切回本地 `QmtBrokerAPI` 直连 | <1min |
| Win 网关断 | `RemoteQmtBroker` fail-open 降级 `SimulatedBroker`, 不下单; 钉钉告警; 指令排队 | 即时 |
| 桥接下单异常 | `broker_factory` 环境变量 `QMT_RPC_URL=""` → 本地直连 | 改 env 重启 |
| 镜像坏 | `kubectl rollout undo deployment/live-scheduler` | <30s |
| 数据损坏 | OBS 冷备恢复 (每日 dump) | <10min |

**关键**: 切换期保留旧 Win 机完整环境 2 周, 任一环节失败立即 `QMT_RPC_URL` 置空切回本地直连。

## 五、验证清单

- [ ] `pytest tests/unit/test_remote_qmt_broker_unit.py` 全绿
- [ ] `docker build -f cloud/docker/Dockerfile.system .` 成功
- [ ] 云机 `kubectl get pods -n quant` 全 Running
- [ ] Streamlit UI 16 页面可访问
- [ ] Win 机 `curl /health` 返回 `{"connected":true}`
- [ ] 云端 `RemoteQmtBroker.connect()` 返回 True
- [ ] 小单 1 手 ETF 完整链路成交
- [ ] CronJob 首日 11 个任务全成功 (`kubectl get jobs -n quant`)
- [ ] 钉钉/飞书告警通道可达

## 六、与 modelarts-deployment 的关系

| 维度 | 本文档 (生产实盘) | cloud-modelarts (训练) |
|---|---|---|
| 镜像 | `qt-system:v1` (完整系统) | `qt-qlib-trainer:v7` (仅训练) |
| 调度 | K8s CronJob ×11 (每日) | ModelArts 定时 (每周六) |
| 数据 | PVC 热数据 + OBS 冷备 | OBS 桶 qt-data/qt-models |
| 下单 | RemoteQmtBroker → Win | 无 |
| 依赖 | requirements-core + cloud | ms_strategy/cloud_train/requirements.txt |

两者共享: SWR 组织 `qt1` / 华为云 AK/SK / OBS 桶 / `auto_train.py`。

## 七、成本 (华为云, 2026-08 估价)

| 项 | 规格 | 月费 |
|---|---|---|
| ECS | 4C16G + 100G SSD (华东-上海一) | ~350 |
| 公网 | 5Mbps 按流量 | ~30 |
| OBS | 50G + 请求 | ~10 |
| 域名+证书 | 已有/免费 | ~0 |
| **合计** | | **~390 元/月** |

## 八、contains 标签索引

- `cloud-production` — 生产实盘上云总览
- `k8s` — K8s CronJob/Deployment/PVC/Ingress
- `docker` — Dockerfile.system 完整镜像
- `rpc-bridge` — 云→Win QMT RPC 桥接 (qmt_rpc_server + remote_qmt_broker)
- `cronjob` — 11 个定时任务替代 Windows 任务计划
- `streamlit` — Web UI 容器化 + Ingress
- `runbook` — 迁移步骤 + 验证清单
- `rollback` — 回滚预案 (fail-open + 环境变量切回)
- `migration` — 分阶段零停机迁移