# Git Hooks — P0 启动自检集成 (v8.6.12)

本目录的 hook 脚本可入 git 共享,团队成员通过 `git config core.hooksPath githooks` 启用。

## 安装

### 方式 A (推荐,团队共享)

```bash
# 在仓库根目录执行一次,永久生效
git config core.hooksPath githooks

# Windows 下若需可执行权限
# (git for windows 通常自动处理,无需 chmod)
```

### 方式 B (个人,立即生效)

```bash
# 复制到 .git/hooks/ 并赋予可执行权限
cp githooks/pre-commit .git/hooks/pre-commit
chmod +x .git/hooks/pre-commit   # Linux/Mac
```

## 使用

### 正常提交 (自动触发自检)

```bash
git commit -m "feat(xxx): 新功能"
# → 自动执行 P0 自检 (--skip-datasource)
# → 失败时阻止提交
```

### 跳过自检 (紧急情况)

```bash
# Linux/Mac
SKIP_P0_CHECK=1 git commit -m "hotfix: 紧急修复"

# Windows PowerShell
$env:SKIP_P0_CHECK=1; git commit -m "hotfix: 紧急修复"
# 或
$env:SKIP_P0_CHECK="1"; git commit -m "..."
```

### 仅文档变更 (自动跳过)

若暂存区全是 `.md/.txt/.gitignore/.editorconfig` 文件,自动跳过自检,加速提交。

## Hook 列表

| Hook | 触发时机 | 作用 |
|------|----------|------|
| `pre-commit` | `git commit` 之前 | 执行 P0 启动自检,阻止有问题的代码进入仓库 |

## 故障排查

### Hook 没生效

```bash
# 检查 hooksPath 配置
git config --get core.hooksPath
# 应输出: githooks

# 检查文件权限 (Linux/Mac)
ls -la githooks/pre-commit
# 应有 x 权限
```

### 自检超时

默认 120 秒超时。若项目庞大,可修改 `scripts/pre_commit_check.py` 的 `timeout=120`。

### 自检本身异常

Python 包装器设计为容错:若自检脚本本身崩溃,会输出 `容错通过` 并允许提交,避免阻断开发流程。

## 与 P0 自检系统的关系

| 触发场景 | 命令 | 严格度 |
|----------|------|--------|
| 盘前最终核查 | `python scripts/run_p0_startup_check.py --strict` | 严格 (WARN 也算失败) |
| 工作流启动 | `assert_system_ready()` (代码内) | 常规 (ERROR 才失败) |
| git 提交 | `githooks/pre-commit` → `pre_commit_check.py` | 常规 + 跳过数据源 |

三层防线协同,从源头减少 bug 进入生产环境。
