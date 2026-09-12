#!/usr/bin/env python
"""
UTF-8 / mojibake 有效性检查 (2026-09-11 编码事故防复发)
========================================================
背景: 2026-09-11 `9e884552` (cnb/main 合并冲突解决) 对 `cairn/ROADMAP.md` 与
`docs/LLM权限边界规范.md` 写成 mojibake 双重编码 (UTF-8 字节被按 GBK 解码后
再存回 UTF-8), 全文花屏且已入 main。现有门禁对编码损坏无感 → 本脚本补位。

检测三类问题 (cairn/LOG.md 2026-09-11 编码事故条目的 backlog 建议):
    1. 无效 UTF-8: 文件字节流不能按 UTF-8 解码 (硬损坏)
    2. mojibake 双重编码特征: UTF-8 中文经 GBK 误读再存回 UTF-8 后产生
       的高频特征字符 — 具体字形见下方 _MOJIBAKE_SIGNATURES 常量
       (说明处不写字形本体, 否则门禁"自伤": 本文档自身会命中特征字符)
    3. U+FFFD 替换字符堆积: 历史 damage 遗留 (>= 阈值)

误报控制 (关键设计):
    - 特征字符阈值 >= 3: 正常简体中文文本中这些字符出现概率为 0
      (2026-09-11 全仓 594 个 .md 实测: 除 1 个已知归档损坏文件外全部为 0)
    - U+FFFD 阈值 >= 3: 正常文本 cairn/LOG.md 仅 1 处单字符损耗 (实测)

扫描范围: cairn/ docs/ specs/ 根目录 *.md (知识层, 与编码事故同面)
排除: Reference/ (外部原始输入, 仅追加), 归档目录白名单 (已确认不可逆损坏)

用法:
    python scripts/check_utf8_mojibake.py             # 全量扫描
    python scripts/check_utf8_mojibake.py --staged    # 只检查 git 暂存区文件

退出码:
    0 = 无编码问题
    1 = 发现编码问题 (应阻断提交/构建)
"""
from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent

# mojibake 双重编码高频特征字符 (UTF-8 3 字节序列被按 GBK 2 字节切分重组的产物,
# 在正常简体中文技术文档中出现概率≈0 — 2026-09-11 全仓实测为 0)。
# 字形一律只在本常量行出现, 不得复制到任何被扫描文档/注释/字符串 (否则自伤)。
_MOJIBAKE_SIGNATURES = "鐨锛鈥鏄鍦囦鍚庢嵁勬"

# 单文件特征字符计数阈值
_SIG_THRESHOLD = 3
# 单文件 U+FFFD 计数阈值
_FFFD_THRESHOLD = 3

# 扫描目录 (知识层 = 编码事故受损面)
_SCAN_DIRS = ("cairn", "docs", "specs")
# 根目录单文件也扫 (README/CHANGELOG 等)
_ROOT_FILES = tuple(_PROJECT_ROOT.glob("*.md"))

# 已确认的不可逆历史损坏 (豁免登记, 勿删 — 原始字节已丢失无法恢复):
#   - 每日报告归档/...: 0f554c92 收敛提交时已存在 4 处 U+FFFD (归档, 不再编辑)
_EXEMPT_SUBSTRINGS = (
    "每日报告归档",
)


def _is_exempt(path: Path) -> bool:
    rel = str(path)
    return any(part in rel for part in _EXEMPT_SUBSTRINGS)


def _analyze_file(path: Path) -> dict | None:
    """分析单个文件, 返回违例 dict 或 None (通过).

    违例 dict: {file, kind, detail}
    """
    try:
        raw = path.read_bytes()
    except OSError as e:
        return {"file": str(path), "kind": "READ_ERROR", "detail": str(e)[:120]}

    # 1. 无效 UTF-8 (硬损坏)
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as e:
        try:
            rel = str(path.relative_to(_PROJECT_ROOT))
        except ValueError:
            rel = str(path)
        return {
            "file": rel,
            "kind": "INVALID_UTF8",
            "detail": f"字节 {e.start}~{e.end} 附近无法按 UTF-8 解码: {str(e)[:80]}",
        }

    # 2. mojibake 特征字符
    sig_count = sum(text.count(ch) for ch in _MOJIBAKE_SIGNATURES)
    if sig_count >= _SIG_THRESHOLD:
        first = next(
            (ch for ch in _MOJIBAKE_SIGNATURES if ch in text),
            "?",
        )
        try:
            rel = str(path.relative_to(_PROJECT_ROOT))
        except ValueError:
            rel = str(path)
        return {
            "file": rel,
            "kind": "MOJIBAKE_DOUBLE_ENCODING",
            "detail": (
                f"特征字符 {sig_count} 处 (如 {first!r}) ≥{_SIG_THRESHOLD} — "
                "疑似 UTF-8 被 GBK 误读后写回 (参照 cairn/LOG.md 2026-09-11 编码事故)"
            ),
        }

    # 3. U+FFFD 堆积
    fffd_count = text.count("\ufffd")
    if fffd_count >= _FFFD_THRESHOLD:
        try:
            rel = str(path.relative_to(_PROJECT_ROOT))
        except ValueError:
            rel = str(path)
        return {
            "file": rel,
            "kind": "REPLACEMENT_CHAR_PILEUP",
            "detail": f"U+FFFD 替换字符 {fffd_count} 处 ≥{_FFFD_THRESHOLD} — 历史损坏残留",
        }

    return None


def _collect_files(staged_only: bool) -> list[Path]:
    """收集待检文件: 全量 (扫描目录+根 md) 或仅暂存区 .md/.yaml/.json."""
    if not staged_only:
        files: list[Path] = list(_ROOT_FILES)
        for d in _SCAN_DIRS:
            p = _PROJECT_ROOT / d
            if p.exists():
                files.extend(p.rglob("*.md"))
        return [f for f in files if f.is_file() and not _is_exempt(f)]

    # 暂存区模式: 只查 git 暂存的文本知识文件 (.md)
    try:
        r = subprocess.run(
            ["git", "diff", "--cached", "--name-only", "--diff-filter=ACM"],
            capture_output=True,
            text=True,
            cwd=str(_PROJECT_ROOT),
            timeout=10,
        )
        names = r.stdout.splitlines()
    except (OSError, subprocess.TimeoutExpired) as e:
        print(f"[utf8-mojibake] 暂存区列表获取异常 (容错跳过): {e}", file=sys.stderr)
        return []
    return [
        _PROJECT_ROOT / n
        for n in names
        if n.endswith(".md")
        and (_PROJECT_ROOT / n).exists()
        and not _is_exempt(_PROJECT_ROOT / n)
    ]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="UTF-8 / mojibake 编码有效性检查")
    parser.add_argument(
        "--staged", action="store_true", help="只检查 git 暂存区涉及的 .md 文件"
    )
    parser.add_argument(
        "--quiet", action="store_true", help="只输出违例与结论, 不输出扫描统计"
    )
    args = parser.parse_args(argv)

    files = _collect_files(args.staged)
    violations: list[dict] = []
    for f in files:
        v = _analyze_file(f)
        if v is not None:
            violations.append(v)

    if not args.quiet:
        mode = "暂存区" if args.staged else "全量"
        print(f"[utf8-mojibake] {mode}扫描 {len(files)} 个文件")

    if violations:
        print(f"[utf8-mojibake] ❌ 发现 {len(violations)} 处编码问题:")
        for v in violations:
            print(f"  {v['file']}: [{v['kind']}] {v['detail']}")
        print()
        print("修复方法: 若为合并冲突解决导致, 按 HEAD 侧原字节恢复并显式以 UTF-8 写回")
        print("  (python encoding='utf-8'; 勿用 PowerShell 默认编码直写)")
        return 1

    print("[utf8-mojibake] ✅ 无编码问题 — UTF-8 全部有效且无 mojibake 特征")
    return 0


if __name__ == "__main__":
    sys.exit(main())
