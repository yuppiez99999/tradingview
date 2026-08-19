#!/usr/bin/env python3
"""
Pre-commit Check (Python 包装器)
================================
版本: v8.6.14
用途: git pre-commit hook 调用入口,在提交前自动执行 P0 自检。

调用方式 (3 种):
    1. 直接调用 (调试):
        python scripts/pre_commit_check.py

    2. 通过 git hook 自动调用 (.git/hooks/pre-commit):
        #!/bin/sh
        exec python scripts/pre_commit_check.py

    3. 通过 core.hooksPath 共享 hook (推荐团队使用):
        git config core.hooksPath githooks
        # githooks/pre-commit 调用此脚本

退出码:
    0 = 自检通过,允许提交
    1 = 自检失败,阻止提交 (必须修复后重试)
    2 = 自检脚本异常 (容错通过,允许提交)

设计原则:
    - 默认 --skip-datasource: 加速 pre-commit,避免每次提交都连数据源
    - 静默模式: 仅输出失败项与结论,不刷屏
    - 容错: 自检本身异常时 exit 0,不阻断开发流程
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

# 项目根目录 (此脚本位于 scripts/pre_commit_check.py)
PROJECT_ROOT = Path(__file__).resolve().parents[1]

# Windows 下强制 UTF-8, 避免控制台 GBK 编码错误
if sys.platform == "win32":
    os.environ.setdefault("PYTHONIOENCODING", "utf-8")
    os.environ.setdefault("PYTHONUTF8", "1")

# 延迟导入门禁子脚本 (避免 pre-commit 阶段不必要的 import 开销)
def _import_check_nan_pollution():
    """延迟导入 NaN 守卫检查脚本."""
    try:
        from scripts import check_nan_pollution
        return check_nan_pollution
    except ImportError:
        return None


def _utf8_env() -> dict[str, str]:
    """返回带有 UTF-8 编码设置的环境变量副本."""
    env = os.environ.copy()
    env.setdefault("PYTHONIOENCODING", "utf-8")
    env.setdefault("PYTHONUTF8", "1")
    return env


def main() -> int:
    # Windows 控制台默认 GBK, 强制 stdout/stderr 用 UTF-8 以支持 emoji/中文
    if sys.platform == "win32":
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")

    # 跳过条件 1: 非 pre-commit 阶段 (如 rebase/merge)
    # git 会在 MERGE_HEAD 存在时跳过 hook,但显式检查更稳妥
    git_dir = PROJECT_ROOT / ".git"
    if not git_dir.exists():
        print("[pre-commit] 非 git 仓库,跳过自检")
        return 0

    # 跳过条件 2: 环境变量 SKIP_P0_CHECK=1 (紧急提交时使用)
    import os
    if os.environ.get("SKIP_P0_CHECK") == "1":
        print("[pre-commit] SKIP_P0_CHECK=1,跳过 P0 自检")
        return 0

    # 跳过条件 3: 仅文档/配置变更 (可选优化,避免每次都跑)
    # 检查暂存区文件,若全是 .md/.txt/.gitignore,可跳过
    try:
        result = subprocess.run(
            ["git", "diff", "--cached", "--name-only", "--diff-filter=ACM"],
            capture_output=True, text=True, cwd=str(PROJECT_ROOT), timeout=10,
        )
        staged_files = [f for f in result.stdout.strip().split("\n") if f]
        if staged_files:
            doc_only = all(
                f.endswith((".md", ".txt", ".gitignore", ".editorconfig"))
                for f in staged_files
            )
            if doc_only:
                print(f"[pre-commit] 仅文档/配置变更 ({len(staged_files)} 文件),跳过 P0 自检")
                return 0
    except (ValueError, TypeError, KeyError, AttributeError, RuntimeError, OSError, TimeoutError, ConnectionError) as e:
        # 数据处理/计算/IO 异常: 格式/类型/字段/属性/运行时/网络/超时
        print(f"[pre-commit] 检查暂存区异常 (容错继续): {e}", file=sys.stderr)

    # === 第一道门禁: 硬编码绝对路径扫描 (v8.5+, 轻量快速) ===
    path_check_script = PROJECT_ROOT / "scripts" / "check_hardcoded_paths.py"
    if path_check_script.exists():
        print("[pre-commit] 扫描硬编码绝对路径...")
        try:
            path_result = subprocess.run(
                [sys.executable, str(path_check_script), "--staged", "--quiet"],
                cwd=str(PROJECT_ROOT),
                timeout=30,
            )
            if path_result.returncode != 0:
                # 运行非静默版本显示详细信息
                subprocess.run(
                    [sys.executable, str(path_check_script), "--staged"],
                    cwd=str(PROJECT_ROOT),
                    timeout=30,
                    env=_utf8_env(),
                )
                print("[pre-commit] ❌ 硬编码路径检查失败,阻止提交", file=sys.stderr)
                return 1
            print("[pre-commit] ✅ 硬编码路径检查通过")
        except subprocess.TimeoutExpired:
            print("[pre-commit] 路径检查超时,容错通过", file=sys.stderr)
        except (ValueError, TypeError, KeyError, AttributeError, RuntimeError, OSError, TimeoutError, ConnectionError) as e:
            # 数据处理/计算/IO 异常: 格式/类型/字段/属性/运行时/网络/超时
            print(f"[pre-commit] 路径检查异常 (容错通过): {e}", file=sys.stderr)

    # === 第二道门禁: 悬挂引用检查 (v9.0+ 防复发机制 #2) ===
    dangling_script = PROJECT_ROOT / "scripts" / "check_dangling_refs.py"
    if dangling_script.exists():
        print("[pre-commit] 扫描悬挂引用 (已删除模块的下游依赖)...")
        try:
            dangling_result = subprocess.run(
                [sys.executable, str(dangling_script)],
                cwd=str(PROJECT_ROOT),
                timeout=30,
                env=_utf8_env(),
            )
            if dangling_result.returncode != 0:
                print("[pre-commit] 悬挂引用检查失败,阻止提交", file=sys.stderr)
                print("[pre-commit] 修复方法: 更新 import 路径或归档陈旧测试", file=sys.stderr)
                return 1
            print("[pre-commit] 悬挂引用检查通过")
        except subprocess.TimeoutExpired:
            print("[pre-commit] 悬挂引用检查超时,容错通过", file=sys.stderr)
        except (ValueError, TypeError, KeyError, AttributeError, RuntimeError, OSError, TimeoutError, ConnectionError) as e:
            print(f"[pre-commit] 悬挂引用检查异常 (容错通过): {e}", file=sys.stderr)

    # === 第二点五道门禁: P0 文件 print 检查 (CODE_REVIEW_PLAN Task 1.1) ===
    # 仅扫描暂存区中的 P0 根目录文件 (最易发且最敏感的 T201 违规), 毫秒级, 不阻断开发流畅度。
    # 命中即阻止提交; 紧急可用 SKIP_P0_PRINT=1 跳过; 单文件豁免用 `# allow-print` 注释。
    # check_no_print_p0.py 仅接受位置参数(文件名, 自动过滤非 P0), 不支持 --staged 开关。
    if os.environ.get("SKIP_P0_PRINT") == "1":
        print("[pre-commit] SKIP_P0_PRINT=1,跳过 P0 print 检查")
    else:
        p0_print_script = PROJECT_ROOT / "scripts" / "check_no_print_p0.py"
        if p0_print_script.exists():
            try:
                diff_result = subprocess.run(
                    ["git", "diff", "--cached", "--name-only", "--diff-filter=ACM"],
                    capture_output=True, text=True, cwd=str(PROJECT_ROOT), timeout=10,
                    env=_utf8_env(),
                )
                staged = [f for f in diff_result.stdout.strip().split("\n") if f]
                # 仅取暂存区里的 P0 文件名 (check_no_print_p0 按文件名匹配 P0_FILES)
                p0_staged = [Path(f).name for f in staged]
                if p0_staged:
                    print("[pre-commit] 扫描 P0 文件裸 print (T201)...")
                    p0_result = subprocess.run(
                        [sys.executable, str(p0_print_script), *p0_staged],
                        cwd=str(PROJECT_ROOT),
                        timeout=30,
                        env=_utf8_env(),
                    )
                    if p0_result.returncode != 0:
                        print("[pre-commit] 裸 print 检查失败,阻止提交", file=sys.stderr)
                        print("[pre-commit] 修复: 改用 logger; 或加 `# allow-print` 豁免; 或 SKIP_P0_PRINT=1 临时跳过", file=sys.stderr)
                        return 1
                    print("[pre-commit] P0 print 检查通过")
                else:
                    print("[pre-commit] 暂存区无 P0 文件,跳过 P0 print 检查")
            except subprocess.TimeoutExpired:
                print("[pre-commit] P0 print 检查超时,容错通过", file=sys.stderr)
            except (ValueError, TypeError, KeyError, AttributeError, RuntimeError, OSError, TimeoutError, ConnectionError) as e:
                print(f"[pre-commit] P0 print 检查异常 (容错通过): {e}", file=sys.stderr)

    # === 第三道门禁: P0 启动自检 (--skip-datasource 加速) ===
    check_script = PROJECT_ROOT / "scripts" / "run_p0_startup_check.py"
    if not check_script.exists():
        print(f"[pre-commit] 自检脚本不存在: {check_script}", file=sys.stderr)
        return 0  # 容错通过

    print("[pre-commit] 执行 P0 启动自检 (--skip-datasource)...")
    try:
        result = subprocess.run(
            [sys.executable, str(check_script), "--skip-datasource"],
            cwd=str(PROJECT_ROOT),
            timeout=120,  # 2 分钟超时
            env=_utf8_env(),
        )
        exit_code = result.returncode
    except subprocess.TimeoutExpired:
        print("[pre-commit] P0 自检超时 (120s),容错通过", file=sys.stderr)
        return 0
    except (ValueError, TypeError, KeyError, AttributeError, RuntimeError, OSError, TimeoutError, ConnectionError) as e:
        # 数据处理/计算/IO 异常: 格式/类型/字段/属性/运行时/网络/超时
        print(f"[pre-commit] P0 自检异常 (容错通过): {e}", file=sys.stderr)
        return 0

    if exit_code == 0:
        print("[pre-commit] ✅ P0 自检通过")
    else:
        print(f"[pre-commit] ❌ P0 自检失败 (exit={exit_code}),阻止提交", file=sys.stderr)
        print("[pre-commit] 修复后重试,或临时用 SKIP_P0_CHECK=1 跳过", file=sys.stderr)
        return 1

    # === 第四道门禁: NaN 污染守卫 (F-4/F-5/F-6) ===
    nan_script = PROJECT_ROOT / "scripts" / "check_nan_pollution.py"
    if nan_script.exists():
        print("[pre-commit] NaN 污染守卫检查...")
        try:
            # 只检查暂存区中的 Python 文件, 避免历史代码阻断提交
            diff_result = subprocess.run(
                ["git", "diff", "--cached", "--name-only", "--diff-filter=ACM"],
                capture_output=True, text=True, cwd=str(PROJECT_ROOT), timeout=10,
                env=_utf8_env(),
            )
            staged_files = [
                PROJECT_ROOT / f for f in diff_result.stdout.splitlines()
                if f.endswith(".py") and (PROJECT_ROOT / f).exists()
            ]
            if staged_files:
                nan_result = subprocess.run(
                    [sys.executable, str(nan_script), "--files", *[str(f) for f in staged_files]],
                    cwd=str(PROJECT_ROOT),
                    timeout=60,
                    env=_utf8_env(),
                )
                if nan_result.returncode != 0:
                    print("[pre-commit] ❌ NaN 污染守卫检查失败,阻止提交", file=sys.stderr)
                    print("[pre-commit] 修复: 为 np.corrcoef / IC 计算添加 nan_to_num / std 检查防护", file=sys.stderr)
                    return 1
                print(f"[pre-commit] NaN 污染守卫检查通过 (检查 {len(staged_files)} 个暂存文件)")
            else:
                print("[pre-commit] NaN 污染守卫检查跳过 (无暂存 Python 文件)")
        except subprocess.TimeoutExpired:
            print("[pre-commit] NaN 守卫检查超时,容错通过", file=sys.stderr)
        except (ValueError, TypeError, KeyError, AttributeError, RuntimeError, OSError, TimeoutError, ConnectionError) as e:
            print(f"[pre-commit] NaN 守卫检查异常 (容错通过): {e}", file=sys.stderr)
    else:
        print("[pre-commit] ⚠️ NaN 守卫检查脚本未找到,跳过", file=sys.stderr)

    print("[pre-commit] ✅ 全部门禁通过,允许提交")
    return 0


if __name__ == "__main__":
    sys.exit(main())
