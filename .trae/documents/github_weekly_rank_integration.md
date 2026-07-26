# GitHub 周榜项目集成到 28-终极量化交易系统8.4

## Context（为什么做这个）

用户分享了 [OpenGithubs/github-weekly-rank](https://github.com/OpenGithubs/github-weekly-rank)（GitHub 每周飙升榜 Top 20，2026.07.19 期），梳理后发现 4 个候选项目对当前量化交易系统可能有用，用户要求"对本项目有用的，下载并集成"。

**当前项目痛点**（来自项目记忆）：
- v8.6.3 因子流水线"方法学闭环"实为文字游戏，**全项目除 `research/vibe_trading_factor_analysis/` 外无任何 import PipelineOrchestrator**（P0 级 bug，需要代码理解工具审计）
- `second-brain/docs/03-MCP记忆服务器.md` 已描述 MCP 记忆服务器需求但未落地
- 跨模块代码理解困难（v8.3_institutional、ms_strategy、research 等模块分散）

**已验证的关键事实**：
- 系统 Python 3.8.9（太老，graphify 需要 3.10+），但 uv 0.11.14 已装 → 用 `uv tool install` 隔离安装
- `C:\Users\Administrator\.claude\.mcp.json` 已有 codebase-memory-mcp 配置但**路径失效**（指向不存在的 npm 路径）→ 需替换为本地二进制
- `ms_strategy` 是**孤儿 submodule**（无 .gitmodules 映射）→ 绝对不能动
- `research/vibe_trading_factor_analysis/` 已存在（是适配器层，不是 Vibe-Trading 源码）→ Vibe-Trading 源码应克隆到 `research/references/`

---

## 集成范围

### ✅ 集成 3 个项目

| 序号 | 项目 | 集成形态 | License | 集成位置 |
|---|---|---|---|---|
| 1 | **graphify** (safishamsi/graphify, 95k+⭐) | AI 助手 skill（按需运行） | Apache 2.0 + MIT | uv 全局工具 + 用户级 skill 注册 |
| 2 | **codebase-memory-mcp** (DeusData/codebase-memory-mcp, 35k+⭐) | 常驻 MCP server（静态二进制） | MIT | `tools/codebase-memory-mcp/` |
| 3 | **Vibe-Trading** (HKUDS/Vibe-Trading, 27k+⭐) | 仅方法学参考（不集成代码） | MIT | `research/references/Vibe-Trading/` |

### ❌ 排除 1 个项目

**destructive_command_guard** — License 是 "MIT License (with OpenAI/Anthropic Rider)"，明确禁止 OpenAI、Anthropic 及其代理人使用。当前用户在用 Claude（Anthropic 模型）辅助工作，集成 dcg 让 Claude Code 调用属于"acting on behalf of Anthropic"，**违反 License**。

- 替代方案：项目已有 Kill Switch + EOD 四 Guard 链覆盖交易安全；开发时命令安全通过 Claude Code 内置权限模式（`/careful`、`/freeze`、`/guard` skill）实现。

---

## 实施步骤（按风险递增顺序）

### 阶段 1: Vibe-Trading（风险极低，1-3 分钟）

```powershell
# 1.1 创建 research/references/ 目录（当前不存在）
New-Item -ItemType Directory -Path "e:\各种PY程序\28-终极量化交易系统8.4\research\references" -Force

# 1.2 浅克隆 Vibe-Trading（--depth 1 节省空间，仅需最新代码作参考）
git clone --depth 1 https://github.com/HKUDS/Vibe-Trading.git "e:\各种PY程序\28-终极量化交易系统8.4\research\references\Vibe-Trading"

# 1.3 验证克隆成功
Test-Path "e:\各种PY程序\28-终极量化交易系统8.4\research\references\Vibe-Trading\README.md"
```

**完成后追加** `research/references/Vibe-Trading/REFERENCE_ONLY.md`（自建文件，标注"仅参考，不集成"），内容包括：
- 定位：只读参考副本，用于对比 HKUDS 的交易 Agent 方法学
- 严禁操作：不得 import 到生产代码、不得修改、不得在 ms_strategy/ 或 v8.3_institutional/ 中引用
- 与生产系统的关系：生产因子库是 `utils/alpha_factor_library.py`（V9 LGB 基线），适配器层在 `research/vibe_trading_factor_analysis/adapters/`
- 上游信息：仓库地址、License MIT、克隆日期 2026-07-26、克隆方式 `git clone --depth 1`

---

### 阶段 2: codebase-memory-mcp（中等风险，5-10 分钟，需重启 Claude Code）

```powershell
# 2.1 创建目标目录（tools/ 已存在）
New-Item -ItemType Directory -Path "e:\各种PY程序\28-终极量化交易系统8.4\tools\codebase-memory-mcp" -Force

# 2.2 下载 Windows 二进制 v0.9.0（37MB）
Invoke-WebRequest -Uri "https://github.com/DeusData/codebase-memory-mcp/releases/download/v0.9.0/codebase-memory-mcp-windows-amd64.zip" -OutFile "e:\各种PY程序\28-终极量化交易系统8.4\tools\codebase-memory-mcp\codebase-memory-mcp-windows-amd64.zip"

# 2.3 解压
Expand-Archive -Path "e:\各种PY程序\28-终极量化交易系统8.4\tools\codebase-memory-mcp\codebase-memory-mcp-windows-amd64.zip" -DestinationPath "e:\各种PY程序\28-终极量化交易系统8.4\tools\codebase-memory-mcp\" -Force

# 2.4 删除 zip 包（节省 37MB）
Remove-Item "e:\各种PY程序\28-终极量化交易系统8.4\tools\codebase-memory-mcp\codebase-memory-mcp-windows-amd64.zip"

# 2.5 验证二进制可执行
& "e:\各种PY程序\28-终极量化交易系统8.4\tools\codebase-memory-mcp\codebase-memory-mcp.exe" --version
```

**更新 MCP 配置**（替换 `C:\Users\Administrator\.claude\.mcp.json` 中失效的 npm 路径）：

```json
{
    "mcpServers": {
        "codebase-memory-mcp": {
            "command": "e:/各种PY程序/28-终极量化交易系统8.4/tools/codebase-memory-mcp/codebase-memory-mcp.exe",
            "args": ["--index", "e:/各种PY程序/28-终极量化交易系统8.4"],
            "env": {
                "CODEBASE_MEMORY_DB": "e:/各种PY程序/28-终极量化交易系统8.4/tools/codebase-memory-mcp/index.db"
            }
        }
    }
}
```

**中文路径风险预案**：若 MCP 启动失败（路径含中文 `各种PY程序`），创建符号链接到无中文路径：
```powershell
New-Item -ItemType SymbolicLink -Path "C:\codebase-memory-mcp" -Target "e:\各种PY程序\28-终极量化交易系统8.4\tools\codebase-memory-mcp"
# 然后 command 改为 "C:/codebase-memory-mcp/codebase-memory-mcp.exe"
```

**生效方式**：重启 Claude Code（关闭所有 Claude Code 窗口，重新打开）。

---

### 阶段 3: graphify（核心验证，15-30 分钟）

```powershell
# 3.1 用 uv 隔离安装 graphifyy（自动用 Python 3.14+，不污染系统 Python 3.8.9）
uv tool install graphifyy

# 3.2 验证 graphify 命令可用
graphify --version
where.exe graphify

# 3.3 注册 skill 到 Claude Code（自动写入 C:\Users\Administrator\.claude\skills\graphify\）
graphify install

# 3.4 在项目根目录运行 graphify（生成代码图谱，预计 5-15 分钟）
Set-Location "e:\各种PY程序\28-终极量化交易系统8.4"
graphify .

# 3.5 关键验证：查询 PipelineOrchestrator 的真实引用关系（验证 P0 级 bug）
graphify query PipelineOrchestrator
graphify explain PipelineOrchestrator
```

---

## 集成位置总览

```
e:\各种PY程序\28-终极量化交易系统8.4\
├── tools/
│   └── codebase-memory-mcp/                    # 【新建】MCP server 二进制
│       ├── codebase-memory-mcp.exe              # 37MB 静态二进制
│       └── index.db                             # 运行时生成（不入版本库）
├── research/
│   ├── references/                              # 【新建】参考项目目录
│   │   └── Vibe-Trading/                        # 【新建】浅克隆参考副本
│   │       ├── README.md                        # 上游原文件
│   │       └── REFERENCE_ONLY.md                # 【自建】仅参考说明
│   └── vibe_trading_factor_analysis/            # 【已存在，不动】适配器层
├── graphify-out/                                # 【运行时生成】graphify 输出
│   ├── graph.html
│   ├── GRAPH_REPORT.md
│   └── graph.json
└── (其他生产目录均不动)

# 用户级配置（项目外）
C:\Users\Administrator\.claude\
├── .mcp.json                                    # 【更新】替换失效的 npm 路径
└── skills\
    └── graphify\                                # 【自动注册】graphify skill
```

---

## 验证方案

### graphify 验证（核心：验证 P0 级 PipelineOrchestrator bug）

```powershell
# 命令可用
graphify --version
# skill 已注册
Test-Path "C:\Users\Administrator\.claude\skills\graphify\SKILL.md"
# 输出已生成
Test-Path "e:\各种PY程序\28-终极量化交易系统8.4\graphify-out\graph.html"
# 关键查询：期望仅 research/vibe_trading_factor_analysis/ 下的文件引用 PipelineOrchestrator
graphify query PipelineOrchestrator
```

### codebase-memory-mcp 验证

```powershell
# 二进制可执行
& "e:\各种PY程序\28-终极量化交易系统8.4\tools\codebase-memory-mcp\codebase-memory-mcp.exe" --version
# 配置已更新（command 指向本地二进制）
Get-Content "C:\Users\Administrator\.claude\.mcp.json"
# 重启 Claude Code 后，运行 /mcp 命令确认状态为 connected
# 测试问题: "列出项目中所有引用 PipelineOrchestrator 的文件"
```

### Vibe-Trading 验证

```powershell
Test-Path "e:\各种PY程序\28-终极量化交易系统8.4\research\references\Vibe-Trading\README.md"
Test-Path "e:\各种PY程序\28-终极量化交易系统8.4\research\references\Vibe-Trading\REFERENCE_ONLY.md"
# 反向验证：未被生产代码引用
findstr /s /i "research.references.Vibe-Trading" "e:\各种PY程序\28-终极量化交易系统8.4\*.py" 2>nul
# 期望: 无任何匹配
```

---

## 不修改的文件清单

### 生产代码（绝对不动）
- `ms_strategy/` 下所有文件（生产策略代码 + 孤儿 submodule）
- `v8.3_institutional/`、`v7.5_institutional/` 下所有文件
- `research/vibe_trading_factor_analysis/` 下所有文件（已有适配器层 + Kill Switch 实现）
- 顶层 `*.py` 生产脚本（`alpha_hedge_engine.py`、`institutional_pipeline_runner.py`、`daily_trade_executor.py`、`run_daily_eod.py`、`stop_loss_monitor.py` 等）
- `research/vibe_trading_factor_analysis/safety/factor_kill_switch.py`（Kill Switch 实现）

### 配置文件（绝对不动）
- `system_config.json`（生产配置）
- `.env` / `.env.example`（敏感配置）
- `config/` / `configs/` 目录
- `500万建仓计划_*.json` / `*.md`

### 依赖与锁文件（绝对不动）
- `requirements.txt` / `requirements_lock.txt` / `requirements_dev.txt`
- `skills-lock.json`
- `.gitmodules`（保持现状，ms_strategy 孤儿 submodule 不动）

### 文档（仅追加，不修改）
- `README.md` / `CHANGELOG.md` 不动
- `skills_installation_report.md` 可追加"GitHub 周榜项目集成（2026-07-26）"一节

### .gitignore（建议追加，需用户确认）

```gitignore
# graphify 输出目录
graphify-out/

# codebase-memory-mcp 二进制与索引
tools/codebase-memory-mcp/codebase-memory-mcp.exe
tools/codebase-memory-mcp/index.db
tools/codebase-memory-mcp/*.zip
```

---

## 风险与回滚

| 集成项 | 风险等级 | 主要风险 | 可逆性 |
|---|---|---|---|
| graphify | 低 | uv install 网络失败；tree-sitter 解析大项目耗时 5-15 分钟 | 完全可逆：`uv tool uninstall graphifyy` + 删 skill + 删 graphify-out/ |
| codebase-memory-mcp | 中 | 中文路径导致 MCP 启动失败（有符号链接预案） | 完全可逆：还原 .mcp.json + 删 tools/codebase-memory-mcp/ |
| Vibe-Trading | 极低 | git clone 网络失败 | 完全可逆：直接删除目录 |

---

## 执行前置条件

1. ✅ 网络可用（需下载 37MB 二进制 + git clone Vibe-Trading）
2. ⚠️ **避开交易时段**（9:00-15:00）和 EOD 时段（15:00-16:00），建议盘后或周末执行
3. ⚠️ 确认 `TRADING_ENV` 当前值（若是 production，建议先切 shadow 或暂停）
4. ⚠️ 备份 `C:\Users\Administrator\.claude\.mcp.json`（虽已失效，仍备份以便回滚）

---

## 后续建议（不在本方案范围）

1. 集成完成后，用 graphify + codebase-memory-mcp 联合审计 PipelineOrchestrator 引用问题，生成 P0 修复报告
2. 在 `skills_installation_report.md` 追加本次集成记录
3. 评估 codebase-memory-mcp 是否能替代 `second-brain/docs/03-MCP记忆服务器.md` 中描述的 Cherry Studio 方案
