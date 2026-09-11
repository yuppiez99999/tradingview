"""临时: 重生成 2026-09-10 动力煤舆情日报 (四组全接, 仅跑 coal)"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from nlp.sentiment_hub import run_all  # noqa: E402

res = run_all(
    target_date="2026-09-10",
    output_dir=str(ROOT / "每日报告归档" / "2026-09-10"),
    run_trend=False,
    run_coal=True,
    force=True,
)
print("ok=", res.get("ok"), "coal=", res.get("coal_report"))
