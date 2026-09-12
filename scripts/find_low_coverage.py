"""find_low_coverage.py — 扫描 coverage.xml 输出低覆盖模块清单 (TODO_from_ROADMAP #5)

用法:
    python scripts/find_low_coverage.py [--threshold 0.6] [--xml reports/coverage.xml]
    python scripts/find_low_coverage.py --threshold 0.6 --top 20

退出码: 0 = 有输出; 1 = coverage.xml 缺失或解析失败
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parents[1]
_PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from utils.safe_xml import safe_xml_parse  # noqa: E402  (bandit B314: 统一加固解析入口)

_DEFAULT_XML = _PROJECT_ROOT / "reports" / "coverage.xml"


def find_low_coverage(xml_path: Path, threshold: float) -> list[tuple[str, float]]:
    """解析 Cobertura coverage.xml, 返回 line-rate < threshold 的 (filename, rate) 列表."""
    if not xml_path.exists():
        return []
    tree = safe_xml_parse(xml_path)  # 加固解析入口 (bandit B314)
    root = tree.getroot()
    low: list[tuple[str, float]] = []
    for cls in root.iter("class"):
        rate_str = cls.get("line-rate")
        filename = cls.get("filename")
        if not rate_str or not filename:
            continue
        try:
            rate = float(rate_str)
        except ValueError:
            continue
        if rate < threshold:
            low.append((filename, rate))
    low.sort(key=lambda x: x[1])
    return low


def main() -> int:
    parser = argparse.ArgumentParser(description="扫描 coverage.xml 输出低覆盖模块清单")
    parser.add_argument("--threshold", type=float, default=0.6, help="覆盖率阈值 (默认 0.6)")
    parser.add_argument("--xml", type=Path, default=_DEFAULT_XML, help="coverage.xml 路径")
    parser.add_argument("--top", type=int, default=0, help="只输出前 N 个 (0=全部)")
    args = parser.parse_args()

    if not args.xml.exists():
        print(f"coverage.xml 不存在: {args.xml}", file=sys.stderr)
        return 1

    low = find_low_coverage(args.xml, args.threshold)
    if args.top > 0:
        low = low[: args.top]

    print(f"低覆盖 (< {args.threshold}) 模块数: {len(low)}")
    print(f"数据源: {args.xml}")
    print("-" * 60)
    for filename, rate in low:
        print(f"  {rate:.2f}  {filename}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
