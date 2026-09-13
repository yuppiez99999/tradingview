"""shadow 批次计划渲染入口（v9.5 缺口③ · 只读渲染器）

用法:
    python scripts/shadow_batch_plan.py --date 2026-09-13 --inputs inputs.json
    python scripts/shadow_batch_plan.py --demo            # 内置演示输入（非真实数据）

产出:
    reports/shadow/batch_plan_{date}.json（默认目录，可用 --out-dir 覆盖）；
    本脚本不触任何下单接口、不修改任何生产状态。
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from utils.datetime_utils import today_bj  # noqa: E402
from utils.execution.batch_state_machine import BatchInputs, render_batch_plan  # noqa: E402

logger = logging.getLogger("shadow_batch_plan")

# 演示输入（非真实数据；演示「下跌触发 + 预验收上限」组合）
_DEMO_INPUTS: dict[str, Any] = {
    "batch1_done": True,
    "b1_entry_date": "2026-07-01",
    "acceptance_passed": False,
    "cns_level": "L0",
    "portfolio_dd_pct": -0.038,
    "dd_since_last_invest_pct": -0.056,
    "intraday_cum_dd_pct": -0.041,
    "pe_pct_8y": 0.48,
    "close_above_ma250": False,
    "ma250_slope_20d": -0.004,
    "low_point_date": None,
    "rebound_from_low_pct": 0.0,
    "opposite_tranches_done": 0,
    "recovery_tranches_done": 0,
}


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="shadow 批次计划渲染（只读，v9.5 缺口③）")
    parser.add_argument("--date", default=None, help="渲染日期 YYYY-MM-DD（默认今天，北京时间）")
    parser.add_argument("--inputs", default=None, help="输入 JSON 路径（与 BatchInputs 字段同名）")
    parser.add_argument("--demo", action="store_true", help="使用内置演示输入（非真实数据）")
    parser.add_argument("--out-dir", default=None, help="输出目录（默认 reports/shadow）")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    if not args.inputs and not args.demo:
        logger.error("需显式指定 --inputs <path> 或 --demo（拒绝静默使用演示数据）")
        return 2

    date_str = args.date or today_bj().isoformat()
    if args.demo:
        data: dict[str, Any] = dict(_DEMO_INPUTS)
        logger.warning("使用内置演示输入（非真实数据）")
    else:
        data = json.loads(Path(args.inputs).read_text(encoding="utf-8"))
    data["as_of"] = date_str

    fields = set(BatchInputs.__dataclass_fields__)
    filtered = {k: v for k, v in data.items() if k in fields}
    dropped = sorted(set(data) - fields)
    if dropped:
        logger.warning("输入中已忽略未知字段: %s", ", ".join(dropped))

    inp = BatchInputs(**filtered)
    report = render_batch_plan(inp)

    out_dir = Path(args.out_dir) if args.out_dir else PROJECT_ROOT / "reports" / "shadow"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"batch_plan_{date_str}.json"
    out_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    logger.info("shadow 批次计划已写入 %s（batch2=%s, actions=%d）", out_path, report["state"]["batch2"], len(report["proposed_actions"]))
    return 0


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
    raise SystemExit(main())
