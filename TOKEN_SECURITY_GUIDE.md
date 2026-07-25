# iFinD JWT Token 安全管理指南

## 安全修复记录 (2026-07-23)

### 问题描述
**严重级别**: P0 - 高危  
**问题**: iFinD JWT Token 在配置文件中明文存储，存在泄露风险

### 修复内容
1. ✅ **移除 `skills/ifind-finance-data/call.py` 中对 `mcp_config.json` 的 Token 依赖**
   - 删除了从配置文件读取明文 Token 的逻辑
   - 仅允许通过环境变量 `IFIND_TOKEN` 获取 Token

2. ✅ **统一 Token 获取方式**
   - `utils/ifind_client.py` - 已正确从环境变量读取
   - `utils/ifind_futures_quotes.py` - 已正确从环境变量读取
   - `utils/data_provider.py` - 已正确从环境变量读取
   - `skills/ifind-finance-data/call.py` - **已修复**，现仅从环境变量读取

3. ✅ **配置文件清理**
   - `skills/ifind-finance-data/mcp_config.json` - 已移除 `auth_token` 字段
   - `.gitignore` - 已包含 `.env` 文件

### Token 配置步骤

#### Windows PowerShell
```powershell
# 设置环境变量（当前会话）
$env:IFIND_TOKEN='your_jwt_token_here'

# 永久设置环境变量（需要管理员权限）
[Environment]::SetEnvironmentVariable("IFIND_TOKEN", "your_jwt_token_here", "User")

# 验证
echo $env:IFIND_TOKEN
```

#### Linux / macOS
```bash
# 临时设置（当前终端会话）
export IFIND_TOKEN='your_jwt_token_here'

# 永久设置（添加到 shell 配置文件）
echo 'export IFIND_TOKEN="your_jwt_token_here"' >> ~/.bashrc  # bash
echo 'export IFIND_TOKEN="your_jwt_token_here"' >> ~/.zshrc   # zsh

# 验证
echo $IFIND_TOKEN
```

#### Python 项目启动脚本
```python
# 在项目启动文件中添加
import os
os.environ['IFIND_TOKEN'] = os.getenv('IFIND_TOKEN', '')

if not os.getenv('IFIND_TOKEN'):
    raise RuntimeError("请配置环境变量 IFIND_TOKEN")
```

### 安全最佳实践

1. **绝不将 Token 提交到版本控制系统**
   - 检查 `.gitignore` 是否包含 `.env` 文件
   - 定期运行 `git log --all --full-history -- "*token*"` 检查历史泄露

2. **使用环境变量而非配置文件**
   - 生产环境：通过部署平台（Kubernetes Secrets、Docker Secrets、AWS Secrets Manager 等）注入环境变量
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

### 审计清单

- [ ] 所有代码文件中的 Token 均从环境变量读取
- [ ] `mcp_config.json` 中不包含任何 Token 信息
- [ ] `.env.example` 文件已更新，包含配置说明
- [ ] 团队成员已通知 Token 迁移至环境变量
- [ ] 生产环境已配置环境变量
- [ ] 已设置 Token 轮换提醒（90天）

### 回滚方案

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

### 相关文件

- `.env` - 本地环境变量配置（已忽略）
- `.env.example` - 配置模板
- `system_config.json` - 系统配置（已有安全注释）
- `skills/ifind-finance-data/mcp_config.json` - MCP 服务器配置（已移除 Token）
- `utils/ifind_client.py` - iFinD 客户端封装
- `utils/data_provider.py` - 数据提供商统一接口

### 参考文档

- [Wind iFinD API 文档](https://www.windquant.com.cn/iFindhy/apiReference/apiConfiguration.html)
- [JWT Token 安全最佳实践](https://tools.ietf.org/html/rfc8725)
