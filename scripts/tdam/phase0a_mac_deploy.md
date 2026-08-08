# Phase 0a — Mac 部署 + 安全审计指南

> **目标**: 在开发/LLM 机 (Mac) 上 Docker 部署 TDAM, 确认无 telemetry 后才导入项目数据。
> **执行机器**: Mac (开发/LLM 机), **不是 Windows 实盘机**。
> **风险等级**: 零 (空数据启动, 不导入任何项目文件)
> **预计耗时**: 0.5 天

---

## 前置条件

- [ ] Mac 上已安装 Docker Desktop
- [ ] Mac 上已安装 git
- [ ] 有可用的 LLM API Key (DeepSeek / GLM / OpenAI 均可)
- [ ] Mac 与 Windows 实盘机在同一局域网 (用于后续 Phase 1' 远程调用)

---

## Step 1: 克隆 + 配置

```bash
# 在 Mac 上执行
cd ~/projects  # 或你常用的代码目录
git clone https://github.com/Tencent/TencentDB-Agent-Memory.git
cd TencentDB-Agent-Memory

# 切到 feat/server_team 分支 (README 推荐分支)
git checkout feat/server_team

# 配置环境变量
cd deploy/global-images
cp .env.example .env
```

编辑 `.env` 文件, 填入两组 LLM 参数:
```bash
# 记忆组 (用于蒸馏 L0→L3)
MEMORY_LLM_PROVIDER=deepseek
MEMORY_LLM_API_KEY=sk-your-deepseek-key
MEMORY_LLM_MODEL=deepseek-chat

# 代理组 (用于 Proxy 转发)
PROXY_LLM_PROVIDER=deepseek
PROXY_LLM_API_KEY=sk-your-deepseek-key
PROXY_LLM_MODEL=deepseek-chat
```

---

## Step 2: 启动服务 (空数据)

```bash
./start-all.sh
```

启动成功后会输出:
- Memory Hub 面板: http://localhost:8125
- Memory Core API: http://localhost:8124 (或其他端口, 见输出)
- Proxy: http://localhost:8126

打开 http://localhost:8125 确认面板可访问。

---

## Step 3: 安全审计 (关键 — 必须在导入数据前完成)

### 3.1 检查 .env 是否有 telemetry 开关

```bash
# 搜索 telemetry / analytics / report / upload 相关配置
grep -i -E "telemetry|analytics|report|upload|tracking|beacon" .env .env.example
```

**预期结果**: 无匹配, 或匹配项可关闭 (设为 false/disable)。

### 3.2 抓包确认无对外网络请求

```bash
# 方法 1: 使用 lsof 检查活跃连接
# 启动服务后, 在另一个终端运行:
lsof -i -P | grep -E "memory|proxy" | grep -v "127.0.0.1|localhost"

# 方法 2: 使用 tcpdump 抓包 (60 秒)
sudo tcpdump -i any -n host not 127.0.0.1 and port not 22 and port not 53 \
  -c 100 -w /tmp/tdam_traffic.pcap &
TCPDUMP_PID=$!
sleep 60
kill $TCPDUMP_PID
tcpdump -r /tmp/tdam_traffic.pcap -n | head -50
```

**预期结果**: 仅有 LLM API 调用的出站连接 (如 api.deepseek.com:443), 无其他异常连接。
如果发现未知域名的出站连接, **停止部署并调查**。

### 3.3 检查 Docker 数据卷位置

```bash
# 确认数据存储在本地 volume, 不上传到云端
docker volume ls | grep -i memory
docker inspect $(docker ps -q) | grep -A5 "Mounts" | head -30
```

**预期结果**: 所有 volume 挂载在本地路径 (如 /var/lib/docker/volumes/)。

### 3.4 检查 SDK 语言

```bash
# 检查 sdk/memory-core 目录语言 (Phase 1' 前置依赖)
ls -la ../../sdk/memory-core/
cat ../../sdk/memory-core/package.json 2>/dev/null | head -5  # 如果是 JS
cat ../../sdk/memory-core/setup.py 2>/dev/null | head -5      # 如果是 Python
cat ../../sdk/memory-core/pyproject.toml 2>/dev/null | head -5
```

**记录结果**: SDK 语言是 JS / Python / 其他。

### 3.5 查找 OpenAPI 规范

```bash
find ../../ -name "openapi*" -o -name "swagger*" -o -name "*.openapi.yaml" 2>/dev/null
```

**记录结果**: 是否存在 openapi.yaml, 路径是什么。

---

## Step 4: REST 接口验证

### 4.1 健康检查

```bash
curl -s http://localhost:8125/health | python3 -m json.tool
# 或
curl -s http://localhost:8125/v3/tools/list | python3 -m json.tool
```

### 4.2 Python REST 调通测试

在 Windows 实盘机上执行 (验证跨机器可达):

```powershell
# 在 Windows 实盘机上执行 (替换 IP 为 Mac 的局域网 IP)
py -3.8 -c "
from utils.tdam_client import TDAMClient, TDAMConfig

client = TDAMClient(TDAMConfig(base_url='http://192.168.x.x:8125'))
print('Health check:', client.health_check())
print('Tools list:', client.list_tools())
"
```

**通过标准**: health_check 返回 True, list_tools 返回非空工具列表。

---

## Step 5: 通过标准检查清单

- [ ] Docker 服务启动, 面板 http://localhost:8125 可访问
- [ ] `.env` 无 telemetry 开关 (或已关闭)
- [ ] 抓包确认仅有 LLM API 出站连接, 无异常域名
- [ ] Docker 数据卷全部在本地
- [ ] `sdk/memory-core` 语言已确认 (JS / Python)
- [ ] `openapi.yaml` 存在性已确认
- [ ] Windows 实盘机 Python REST 调通 `/v3/tools/list`
- [ ] Mac 防火墙允许 8125 端口入站 (局域网)

---

## ⚠️ 如果安全审计发现异常

1. **立即停止 TDAM 服务**: `cd deploy/global-images && docker-compose down`
2. **不要导入任何项目数据**
3. 记录异常详情 (抓包日志、可疑域名、配置项)
4. 在 `TDAM_cairn_对接方案.md` 第 8 节修正记录中追加发现
5. 考虑替代方案: 仅使用 cairn + 本地向量检索 (如 chromadb)

---

## 下一步

Phase 0a 全部通过后, 进入 **Phase 0b (只读导入验证)**:

```bash
# 在 Windows 实盘机上导出 cairn 数据
py -3.8 scripts/tdam/export_cairn_for_tdam.py --output reports/tdam_cache/cairn_export.json

# 将导出文件传到 Mac, 通过 TDAM 面板 Cold-Start 导入
# 然后验证检索命中关键结论
```
