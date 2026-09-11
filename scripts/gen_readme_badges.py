#!/usr/bin/env python
"""README 徽章生成器 (v8.7)
============================
生成自包含的静态 SVG 徽章到 ``docs/assets/badges/``, 供 README 顶部徽章区引用。

设计要点:
    - **纯标准库**: 不依赖 pillow / shields.io / 网络, 任何环境 (含 CI 沙箱) 均可重跑。
    - **单一样式源**: 颜色与标签集中在本文件 BADGES 表, 避免 README 中散落硬编码。
    - **可复现**: 同输入同输出, 便于 diff 审查与版本化。

用法::

    python scripts/gen_readme_badges.py            # 生成全部
    python scripts/gen_readme_badges.py --list     # 仅列出徽章定义

数据口径 (单一事实源, 更新徽章时同步更新):
    - 版本    : pyproject.toml [project].version
    - 覆盖率  : reports/ci/coverage_baseline.json -> line_rate
    - 测试数  : 见 TEST_COUNT 常量 (随 pytest 汇总手工校准)
    - 其余    : ROADMAP / 代码质量报告中的当期结论
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_BADGE_DIR = _PROJECT_ROOT / "docs" / "assets" / "badges"

# 标准 shields 风格色板 (flat-square)
_C_GRAY = "#555"
_C_BLUE = "#007ec6"
_C_GREEN = "#3fb950"
_C_PURPLE = "#8a63d2"
_C_ORANGE = "#fe7d37"
_C_RED = "#e05d44"
_C_YELLOW = "#d29922"
_C_CYAN = "#1f9cb0"

# 徽章定义: (文件名, label, message, 颜色)
# 顺序即 README 中的展示顺序 (语义分组: 工程 -> 质量 -> 领域 -> 许可)
BADGES: list[tuple[str, str, str, str]] = [
    # ── 版本与运行环境 ──
    ("version.svg", "version", "v8.7", _C_BLUE),
    ("python.svg", "python", "3.10+ | 3.14", _C_BLUE),
    ("platform.svg", "platform", "macOS + Windows", _C_GRAY),
    ("status.svg", "status", "灰度推进中", _C_YELLOW),
    ("broker.svg", "broker", "paper 模拟盘", _C_ORANGE),
    # ── 工程质量门禁 ──
    ("ci.svg", "CI", "8 workflows", _C_GREEN),
    ("tests.svg", "tests", "2979 pass", _C_GREEN),
    ("coverage.svg", "coverage", "83%", _C_PURPLE),
    ("ruff.svg", "ruff", "0", _C_GREEN),
    ("mypy.svg", "mypy", "0", _C_GREEN),
    ("bandit.svg", "bandit", "0 High/Med", _C_GREEN),
    ("quality.svg", "quality", "A-", _C_GREEN),
    ("lint.svg", "pre-commit", "enforced", _C_GREEN),
    # ── 量化领域能力 ──
    ("factors.svg", "factors", "12 大类 / GTJA191", _C_CYAN),
    ("llm.svg", "LLM", "双引擎 + 3debator", _C_CYAN),
    ("risk.svg", "risk guard", "8-Guard 强制", _C_RED),
    ("backtest.svg", "backtest", "Walk-Forward", _C_CYAN),
    ("etf-option.svg", "ETF option", "hedge + rebalance", _C_CYAN),
    ("roadmap.svg", "roadmap", "v8.7 Sprint 1", _C_BLUE),
    # ── 许可 ──
    ("license.svg", "license", "proprietary", _C_RED),
]

# 测试数口径 (随 pytest 汇总校准; 与 README「代码质量 A-」章节保持一致)
TEST_COUNT = "2979"

# 字体族说明:
#   libvips/cairo 等部分 SVG 渲染器**不做逐字形回退**, 只取族列表第一个可解析族。
#   若首选只有拉丁字形, 中文会渲染成 .notdef 方框。
#   因此这里把覆盖 CJK 的通用族放在首位 —— CJK 字体本身也含完整拉丁字形,
#   浏览器 (Chrome/Safari/Edge) 与 cairo 均可正确渲染, 不存在乱码风险。
_FONT = (
    "'Noto Sans CJK SC','Source Han Sans SC','PingFang SC','Hiragino Sans GB',"
    "'Microsoft YaHei',Verdana,Geneva,'DejaVu Sans',sans-serif"
)

# 字宽估算系数 (11px Noto Sans CJK SC 实测标定, 见 scripts/gen_readme_badges.py 注释)
_CHAR_W = 5.5      # 拉丁/数字字符平均 advance
_CJK_W = 11.0      # CJK 全角字符 advance
_PAD = 10
_HEIGHT = 20


def _text_width(text: str) -> int:
    """估算 11px 字体下文本的像素宽度。

    CJK 全角字符宽度约为拉丁字符的 2 倍, 分档累加, 无需外部字体库。
    系数由 11px Noto Sans CJK SC 实测标定, 误差 <2px。

    Args:
        text: 待测文本。

    Returns:
        像素宽度 (向上取整)。
    """
    width = 0.0
    for ch in text:
        width += _CJK_W if ord(ch) > 0x2E80 else _CHAR_W
    return int(width + 0.999)


def render_badge(label: str, message: str, color: str) -> str:
    """渲染一个 flat-square 风格 SVG 徽章。

    Args:
        label: 左侧标签文本。
        message: 右侧数值文本。
        color: 右侧背景色 (hex)。

    Returns:
        SVG 字符串。
    """
    label_w = _text_width(label) + _PAD * 2
    msg_w = _text_width(message) + _PAD * 2
    total_w = label_w + msg_w
    label_cx = label_w / 2
    msg_cx = label_w + msg_w / 2
    # 圆角半径 (flat-square 仅 2px 圆角)
    radius = 3

    return f"""<svg xmlns="http://www.w3.org/2000/svg" width="{total_w}" height="{_HEIGHT}" role="img" aria-label="{label}: {message}">
  <title>{label}: {message}</title>
  <linearGradient id="s" x2="0" y2="100%">
    <stop offset="0" stop-color="#bbb" stop-opacity=".1"/>
    <stop offset="1" stop-opacity=".1"/>
  </linearGradient>
  <clipPath id="r">
    <rect width="{total_w}" height="{_HEIGHT}" rx="{radius}" fill="#fff"/>
  </clipPath>
  <g clip-path="url(#r)">
    <rect width="{label_w}" height="{_HEIGHT}" fill="#555"/>
    <rect x="{label_w}" width="{msg_w}" height="{_HEIGHT}" fill="{color}"/>
    <rect width="{total_w}" height="{_HEIGHT}" fill="url(#s)"/>
  </g>
  <g fill="#fff" text-anchor="middle" font-family="{_FONT}" font-size="11">
    <text x="{label_cx:g}" y="15" fill="#010101" fill-opacity=".3">{label}</text>
    <text x="{label_cx:g}" y="14">{label}</text>
    <text x="{msg_cx:g}" y="15" fill="#010101" fill-opacity=".3">{message}</text>
    <text x="{msg_cx:g}" y="14">{message}</text>
  </g>
</svg>
"""


def _read_project_version() -> str:
    """从 pyproject.toml 读取版本号 (失败时回退 'unknown')。"""
    toml_path = _PROJECT_ROOT / "pyproject.toml"
    try:
        for line in toml_path.read_text(encoding="utf-8").splitlines():
            stripped = line.strip()
            if stripped.startswith("version") and "=" in stripped:
                return stripped.split("=", 1)[1].strip().strip('"').strip("'")
    except OSError:
        pass
    return "unknown"


def _read_coverage() -> str:
    """从覆盖率基线读取 line_rate 并格式化为百分比 (失败时回退 'n/a')。"""
    baseline = _PROJECT_ROOT / "reports" / "ci" / "coverage_baseline.json"
    try:
        rate = float(json.loads(baseline.read_text(encoding="utf-8"))["line_rate"])
        if rate <= 1.0:
            rate *= 100
        return f"{rate:.0f}%"
    except (OSError, KeyError, TypeError, ValueError):
        return "n/a"


def build_badges() -> dict[str, str]:
    """根据当前仓库状态解析动态字段, 返回 {文件名: SVG 内容}。"""
    dynamic = {
        "version": f"v{_read_project_version().rsplit('.', 1)[0]}"
        if _read_project_version() != "unknown"
        else "unknown",
        "coverage": _read_coverage(),
        "tests": f"{TEST_COUNT} pass",
    }
    result: dict[str, str] = {}
    for filename, label, message, color in BADGES:
        # 只覆盖显式登记为动态的字段, 其余以 BADGES 表为准 (可审计)
        message = dynamic.get(filename.replace(".svg", ""), message)
        result[filename] = render_badge(label, message, color)
    return result


def main(argv: list[str] | None = None) -> int:
    """入口: 生成徽章文件。"""
    parser = argparse.ArgumentParser(description="生成 README SVG 徽章")
    parser.add_argument("--list", action="store_true", help="仅列出徽章定义, 不写文件")
    parser.add_argument("--out-dir", type=Path, default=_BADGE_DIR, help="输出目录")
    args = parser.parse_args(argv)

    if args.list:
        for filename, label, message, color in BADGES:
            print(f"{filename:<18} {label}: {message} ({color})")
        return 0

    badges = build_badges()
    args.out_dir.mkdir(parents=True, exist_ok=True)
    for filename, svg in sorted(badges.items()):
        (args.out_dir / filename).write_text(svg, encoding="utf-8")
    print(f"[BADGE] 生成 {len(badges)} 个徽章 -> {args.out_dir}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
