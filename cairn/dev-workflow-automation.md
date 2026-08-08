# 开发工作流自动化 (Dev Workflow Automation)

> **创建时间**: 2026-08-03
> **来源**: GitHub 一周热榜 Wave 0 集成 — claude-mem + claude-code-hooks-mastery
> **状态**: 当前真相
> **关联**: [AGENTS.md 知识沉淀规则](../AGENTS.md)、[cairn/LOG.md](LOG.md)

## 一、设计目标

零风险增强 Claude Code 开发工作流,解决以下痛点:

1. **跨会话上下文丢失**: 每次新会话需手动重建上下文,效率低
2. **质量检查靠手动**: P0 修复期间需手动跑 `_final_quality_scan.py` / `_scan_except_remaining.py`
3. **cairn 知识沉淀易遗漏**: 实质性推进后忘记追加 LOG.md 条目
4. **pre-commit 缺 P0 拦截**: 现有 hook 只查异常规范,不查 eval/exec/shell=True

## 二、协调规则: claude-mem 与 cairn

| 系统 | 职责 | 内容类型 | 维护方式 |
|------|------|----------|----------|
| `cairn/` | 项目级显式知识 | 结论、决策、踩坑、专题 | 手动维护,原位更新 |
| `claude-mem` | 会话级自动记忆 | 交互过程、临时上下文 | 自动捕获,压缩注入 |

**核心原则: 结论走 cairn,过程走 claude-mem**

- 凡是"决策结论/架构选择/踩坑教训"→ 写入 `cairn/` 专题文档 + LOG.md
- 凡是"上次修到哪/临时调试思路/会话级偏好"→ 由 claude-mem 自动留存
- 避免双写冲突: cairn 是单一事实源,claude-mem 仅作会话衔接

## 三、Claude Code Hooks 配置

配置文件: [.claude/settings.json](../.claude/settings.json)

### 3.1 Hook 矩阵

| Hook 事件 | 触发时机 | 执行脚本 | 作用 |
|-----------|----------|----------|------|
| `PreToolUse` | Edit/Write/MultiEdit 前 | `scripts/_claude_hook_quality_check.py --stdin` | 拦截 P0 安全风险 + P1 静默吞异常 |
| `PostToolUse` | Edit/Write/MultiEdit 后 | `scripts/_claude_hook_quality_check.py --stdin` | 复查同类违规 (双保险) |
| `SessionStart` | 会话启动 | `scripts/_claude_hook_cairn_check.py --mode session-start` | 自动加载 cairn/LOG.md 最近 3 条 + ROADMAP 焦点 |
| `Stop` | 会话结束前 | `scripts/_claude_hook_cairn_check.py --mode stop --stdin` | 校验代码修改后是否更新 LOG.md (信息性提醒) |

### 3.2 Hook 脚本职责

#### `scripts/_claude_hook_quality_check.py` (AST-based 质量检查器)

**检查项**:
- P0: `eval()` / `exec()` / `os.system()` / `subprocess shell=True` / `pickle.load` 无守护
- P1: 裸 `except:` / 宽泛 `except Exception` 既未记录也未 raise

**调用模式**:
```bash
# Claude Code Hook (stdin JSON)
python scripts/_claude_hook_quality_check.py --stdin

# 独立检查单文件
python scripts/_claude_hook_quality_check.py --file path/to/file.py

# 目录扫描 (默认 v8.3_institutional/src)
python scripts/_claude_hook_quality_check.py [dir1] [dir2]
```

**退出码**: 0=通过, 1=发现违规 (hook 阻止), 2=脚本错误 (放行)

**白名单**: `auto_retrain_scheduler.py` 的 `pickle.load` 已有 SHA256 + 大小限制 + 异常处理三层防护,跳过。

#### `scripts/_claude_hook_cairn_check.py` (Cairn 上下文 Hook)

**SessionStart 模式**: 输出 cairn/LOG.md 最近 3 条 + ROADMAP 当前焦点到 stdout,自动注入为会话上下文。

**Stop 模式**: 检测最近 30 分钟内是否有 .py 文件修改但 LOG.md 未更新,输出提醒到 stderr (不阻断)。

## 四、pre-commit 增强

配置文件: [.pre-commit-config.yaml](../.pre-commit-config.yaml)

新增 `forbid-p0-risk` local hook:
- **入口**: `python scripts/_claude_hook_quality_check.py`
- **范围**: `v8.3_institutional/(src|utils)/` + `ai_decision/` + `utils/` (与 `forbid-bare-except` 对齐)
- **排除**: `_*\.py$` (一次性脚本) + `llm_client.py` (历史代码)
- **阻断**: 发现 P0/P1 违规返回 1,阻止提交

## 五、claude-mem 安装与配置

> **状态**: 待用户手动安装 (需 npm 全局权限)

### 5.1 安装

```bash
npm install -g @thedotmack/claude-mem
```

### 5.2 项目初始化

在项目根目录执行:
```bash
claude-mem init
```

### 5.3 验证

1. 新开一个 Claude Code 会话,执行若干操作
2. 结束会话后,新开会话确认 claude-mem 自动注入上次上下文
3. 检查 `~/.claude-mem/` 或项目级 `.claude-mem/` 目录是否有记忆文件

### 5.4 卸载 (回退)

```bash
npm uninstall -g @thedotmack/claude-mem
rm -rf .claude-mem/  # 项目级记忆
```

## 六、端到端验证步骤

### 6.1 Hook 脚本独立验证

```bash
# 1. 质量检查器自检 (应通过)
python scripts/_claude_hook_quality_check.py --file scripts/_claude_hook_quality_check.py

# 2. 目录扫描 (应报告 P1 残留, P0 应为 0)
python scripts/_claude_hook_quality_check.py ai_decision

# 3. Cairn 上下文输出
python scripts/_claude_hook_cairn_check.py --mode session-start
```

### 6.2 Hook stdin 模式验证

```bash
# 模拟 PreToolUse hook 输入 (非 .py 文件, 应放行)
echo '{"tool_name":"Edit","tool_input":{"file_path":"README.md"}}' | python scripts/_claude_hook_quality_check.py --stdin
echo "退出码: $?"  # 应为 0

# 模拟 .py 文件检查
echo '{"tool_name":"Edit","tool_input":{"file_path":"scripts/_claude_hook_quality_check.py"}}' | python scripts/_claude_hook_quality_check.py --stdin
echo "退出码: $?"  # 应为 0 (自检通过)
```

### 6.3 pre-commit 验证

```bash
# 全量运行 (应只报已有违规, 不报新引入)
pre-commit run forbid-p0-risk --all-files
```

### 6.4 Claude Code 集成验证

1. 重启 Claude Code 会话,确认 SessionStart hook 输出 cairn 上下文
2. 编辑一个 .py 文件,确认 PreToolUse hook 触发
3. 结束会话,确认 Stop hook 输出 LOG.md 提醒 (若有代码修改)

## 七、回退方案

| 组件 | 回退操作 | 影响范围 |
|------|----------|----------|
| Claude Code Hooks | 删除 `.claude/settings.json` 中 `hooks` 字段 | 仅开发工作流,不影响交易系统 |
| pre-commit hook | 删除 `.pre-commit-config.yaml` 中 `forbid-p0-risk` 块 | 仅提交检查,不影响运行时 |
| claude-mem | `npm uninstall -g @thedotmack/claude-mem` | 仅会话记忆,不影响 cairn |
| Hook 脚本 | 保留 `scripts/_claude_hook_*.py` (可独立使用) | 无影响 |

**重要**: Wave 0 全部改动限于 `.claude/`、`scripts/`、`.pre-commit-config.yaml`、`AGENTS.md`、`cairn/`,**不触及任何交易系统代码**,回退零风险。

## 八、contains: 踩坑记录

### contains/windows-powershell-ls
Windows PowerShell 不支持 `ls -la` (Linux 语法),需用 `Get-ChildItem` 或 `dir`。验证 hook 脚本时避免用 `head`/`tail` 等 Unix 命令,改用 PowerShell 原生命令或 Python 脚本内置输出。

### contains/claude-hooks-stdin-format
Claude Code PreToolUse/PostToolUse hook 的 stdin JSON 格式为 `{"tool_name":"Edit","tool_input":{"file_path":"..."}}`。`file_path` 字段名可能因工具不同而异 (Edit/Write 用 `file_path`,部分工具用 `path`),hook 脚本应同时检查两个字段。
