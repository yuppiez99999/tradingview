# P0-1 安全修复报告：iFinD JWT Token 迁移至环境变量

## 修复日期
2026-07-23

## 问题描述
**严重级别**: P0 - 高危  
**问题**: iFinD JWT Token 在代码和配置文件中明文存储，存在泄露风险

### 原始问题
1. `skills/ifind-finance-data/call.py` 从 `mcp_config.json` 读取明文 Token
2. 多个文件存在回退到配置文件或硬编码 Token 的逻辑
3. 缺少统一的 Token 安全管理文档

## 修复内容

### 1. 代码修复清单

#### ✅ `skills/ifind-finance-data/call.py`
**修改前**:
```python
CONFIG = json.loads((Path(__file__).resolve().parent / "mcp_config.json").read_text(encoding="utf-8"))
_RAW_TOKEN = CONFIG.get("auth_token", "")
AUTH_TOKEN = os.environ.get("IFIND_TOKEN", "")

if _RAW_TOKEN and _RAW_TOKEN != "${IFIND_TOKEN}" and not AUTH_TOKEN:
    raise RuntimeError("检测到 mcp_config.json 中存在明文 Token!")
```

**修改后**:
```python
AUTH_TOKEN = os.environ.get("IFIND_TOKEN", "")

if not AUTH_TOKEN:
    raise RuntimeError(
        "iFinD JWT Token 未配置: 请设置环境变量 IFIND_TOKEN\n"
        "Windows PowerShell: $env:IFIND_TOKEN='your_token_here'\n"
        "Linux/Mac: export IFIND_TOKEN='your_token_here'"
    )
```

**影响**: 完全移除了对 `mcp_config.json` 中明文 Token 的依赖

---

#### ✅ `utils/ifind_client.py` (已修复，无需修改)
- 已正确从环境变量读取 Token
- 包含完整的错误提示和安全注释

---

#### ✅ `utils/ifind_futures_quotes.py` (已修复，无需修改)
- `_get_ifind_token()` 函数已移除对 `WIND_API_KEY` 的回退
- 仅从环境变量 `IFIND_TOKEN` 读取
- 包含详细的安全要求注释

---

#### ✅ `utils/data_provider.py` (已修复，无需修改)
- 已正确从环境变量读取 Token
- 包含完整的错误提示

---

### 2. 配置文件清理

#### ✅ `skills/ifind-finance-data/mcp_config.json`
**修改前**:
```json
{
    "auth_token": "eyJraWQiOiJtY2AtYXBpIiwidHlwZSI6IkpXVCIsImFsZyI6IlJTMjU2In0..."
}
```

**修改后**:
```json
{
    "_security_note": "iFinD JWT Token 必须通过环境变量 IFIND_TOKEN 配置,禁止在此文件中明文存储",
    "auth_token_placeholder": "${IFIND_TOKEN}"
}
```

---

### 3. 文档更新

#### ✅ 创建 `TOKEN_SECURITY_GUIDE.md`
包含以下内容：
- 安全修复记录
- Token 配置步骤（Windows/Linux/macOS）
- 安全最佳实践
- 审计清单
- 回滚方案

#### ✅ 更新 `.env.example`
- 添加详细的配置说明
- 包含获取 Token 的步骤
- 强调安全注意事项

---

### 4. 验证清单

- [x] 所有代码文件中的 Token 均从环境变量读取
- [x] `mcp_config.json` 中不包含任何 Token 信息
- [x] `.gitignore` 已包含 `.env` 文件
- [x] `.env.example` 文件已更新，包含配置说明
- [x] 创建了 Token 安全管理指南文档

---

## 迁移步骤

### Windows PowerShell
```powershell
# 设置环境变量（当前会话）
$env:IFIND_TOKEN='your_jwt_token_here'

# 永久设置环境变量（需要管理员权限）
[Environment]::SetEnvironmentVariable("IFIND_TOKEN", "your_jwt_token_here", "User")

# 验证
echo $env:IFIND_TOKEN
```

### Linux / macOS
```bash
# 临时设置（当前终端会话）
export IFIND_TOKEN='your_jwt_token_here'

# 永久设置（添加到 shell 配置文件）
echo 'export IFIND_TOKEN="your_jwt_token_here"' >> ~/.bashrc  # bash
echo 'export IFIND_TOKEN="your_jwt_token_here"' >> ~/.zshrc   # zsh

# 验证
echo $IFIND_TOKEN
```

---

## 安全最佳实践

1. **绝不将 Token 提交到版本控制系统**
   - 检查 `.gitignore` 是否包含 `.env` 文件
   - 定期运行 `git log --all --full-history -- "*token*"` 检查历史泄露

2. **使用环境变量而非配置文件**
   - 生产环境：通过部署平台（Kubernetes Secrets、Docker Secrets 等）注入环境变量
   - 开发环境：使用 `.env` 文件（已被 `.gitignore` 排除）

3. **定期轮换 Token**
   - 建议每 90 天更换一次 Token
   - 在 Wind 终端执行：`client.logout()` → 重新登录获取新 Token

4. **监控和告警**
   - 如果 Token 疑似泄露，立即在 Wind 终端执行 `client.logout()` 使旧 Token 失效
   - 重新登录获取新 Token 并更新环境变量

5. **最小权限原则**
   - 为不同用途创建不同的 Token（如只读 Token、交易 Token）
   - 避免在所有服务中使用同一个 Token

---

## 受影响的服务

以下服务依赖 iFinD Token，迁移后需确保环境变量已配置：

1. **数据源服务** (`utils/data_provider.py`)
   - A股实时行情
   - ETF 资金流监控
   - 宏观数据查询

2. **期货行情服务** (`utils/ifind_futures_quotes.py`)
   - 期货实时行情
   - THS_RQ 指标查询

3. **MCP 客户端** (`utils/ifind_client.py`)
   - 股票/基金/债券行情
   - 新闻公告查询
   - 财报数据获取

4. **技能工具** (`skills/ifind-finance-data/call.py`)
   - 命令行数据查询
   - 自动化脚本调用

5. **实时监控** (`realtime_monitor/*.py`)
   - 持仓监控
   - 自选股监控

---

## 回滚方案

如果因兼容性问题需要临时回滚到配置文件方式（**不推荐**）：

```python
# 仅在紧急情况下使用，且必须确保配置文件不被提交到 Git
import json
from pathlib import Path

CONFIG = json.loads(Path("skills/ifind-finance-data/mcp_config.json").read_text(encoding="utf-8"))
_RAW_TOKEN = CONFIG.get("auth_token", "")

if not _RAW_TOKEN:
    raise RuntimeError("Token 未配置")

print("WARNING: 正在使用配置文件中的 Token，请尽快迁移至环境变量！")
```

---

## 验证测试

### 1. 环境变量配置验证
```bash
# Linux/Mac
export IFIND_TOKEN="test_token"
python -c "import os; print('IFIND_TOKEN:', os.getenv('IFIND_TOKEN'))"

# Windows PowerShell
$env:IFIND_TOKEN="test_token"
python -c "import os; print('IFIND_TOKEN:', os.getenv('IFIND_TOKEN'))"
```

### 2. 代码导入验证
```python
# 验证 utils/ifind_client.py
from utils.ifind_client import IfindClient
client = IfindClient()
print("iFinD client initialized successfully")

# 验证 skills/ifind-finance-data/call.py
from skills.ifind_finance_data.call import AUTH_TOKEN
print(f"Token loaded from environment: {AUTH_TOKEN[:10]}...")
```

### 3. 安全验证
```python
# 验证 Token 不从配置文件读取
import json
with open("skills/ifind-finance-data/mcp_config.json") as f:
    config = json.load(f)
assert "auth_token" not in config or config["auth_token"] == "${IFIND_TOKEN}", \
    "Token should not be stored in mcp_config.json"
print("✅ Security check passed: No hardcoded token in config")
```

---

## 后续任务

- [ ] P0-2: 检查并修复其他 API Key 的存储方式（WIND_API_KEY、TDX_IP 等）
- [ ] P0-3: 添加 Token 泄露检测和自动轮换机制
- [ ] P1-1: 实现加密的密钥管理系统（如 HashiCorp Vault）
- [ ] P1-2: 添加 Token 访问审计日志

---

## 参考文档

- [TOKEN_SECURITY_GUIDE.md](TOKEN_SECURITY_GUIDE.md) - iFinD JWT Token 安全管理指南
- [.env.example](.env.example) - 环境变量配置模板
- [system_config.json](system_config.json) - 系统配置（含安全注释）
- [Wind iFinD API 文档](https://www.windquant.com.cn/iFindhy/apiReference/apiConfiguration.html)

---

## 修复总结

| 项目 | 状态 |
|------|------|
| 移除 `skills/ifind-finance-data/call.py` 中的明文 Token | ✅ 已完成 |
| 统一所有文件从环境变量读取 Token | ✅ 已完成 |
| 清理 `mcp_config.json` 中的 Token | ✅ 已完成 |
| 创建 Token 安全管理文档 | ✅ 已完成 |
| 更新 `.env.example` 配置模板 | ✅ 已完成 |
| 验证 `.gitignore` 排除 `.env` | ✅ 已完成 |

**修复结果**: P0-1 任务已 100% 完成，所有 iFinD JWT Token 已迁移至环境变量存储。
