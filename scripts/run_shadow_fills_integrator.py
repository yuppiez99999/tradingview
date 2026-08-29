"""Shadow 真实撮合桥接器 CLI 入口 (P0-1).

由 15_每日工作流/run_daily_eod_workflow.py 阶段 4.5B+1 调用 (fail-open):
    python scripts/run_shadow_fills_integrator.py [YYYY-MM-DD]

退出码:
    0 = 桥接成功 (或成功标记 data_source_real=False)
    1 = 桥接异常 (EOD 主流程会记 WARN 但不中断)

设计: 仅做薄封装, 业务全部在 utils.alpha.shadow_fills_integrator.ShadowFillsIntegrator.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

# 允许直接以脚本方式运行 (python scripts/run_shadow_fills_integrator.py)
_PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from utils.alpha.shadow_fills_integrator import run_integration  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Shadow 真实撮合桥接器 (P0-1): 消费 FillsStore 成交写入 trade_log"
    )
    parser.add_argument(
        "date",
        nargs="?",
        default=None,
        help="指定交易日 YYYY-MM-DD (默认: 全部日期)",
    )
    args = parser.parse_args(argv)

    result = run_integration(args.date)
    print(json.dumps(result.to_dict(), ensure_ascii=False, indent=2))
    return 0 if result.success else 1


if __name__ == "__main__":
    sys.exit(main())
