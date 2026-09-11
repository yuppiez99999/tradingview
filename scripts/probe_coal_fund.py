"""临时探针: 探测电厂日耗/螺纹钢/焦煤焦炭 EDB 指标 (用完即删)"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from tools.wind_mcp_fetcher import _get_wind_api_key, _wind_http_generic  # noqa: E402

EP = "https://mcp.wind.com.cn/vserver_economic_data/mcp/"
key = _get_wind_api_key() or ""


def metrics_of(res):
    if not res.get("ok"):
        return []
    data = res.get("data") or {}
    content = ((data.get("result") or data).get("content")) or []
    if not content:
        return []
    try:
        inner = json.loads(content[0].get("text") or "{}")
    except Exception:
        return []
    return inner.get("metrics") or []


for q in [
    "六大电厂日均耗煤量",
    "电厂日均耗煤",
    "沿海六大电厂日耗",
    "螺纹钢价格",
    "螺纹钢HRB400价格",
    "焦煤价格",
    "焦炭价格",
    "主焦煤价格",
    "冶金焦价格",
]:
    ms = metrics_of(_wind_http_generic(EP, "search_economic_indicator", {"question": q}, key))
    print("\n## " + q)
    for m in ms[:6]:
        print(
            "   ",
            m.get("code"),
            m.get("name"),
            m.get("unit"),
            m.get("freq"),
            m.get("source"),
            "upd=" + str(m.get("updateDate")),
        )

print("\n## 日期范围是否生效 (S5103725, 20260901-20260910)")
for m in metrics_of(
    _wind_http_generic(
        EP,
        "query_economic_indicator_data",
        {"question": "S5103725", "beginDate": "2026-09-01", "endDate": "2026-09-10"},
        key,
    )
):
    print("   ", (m.get("meta") or {}).get("code"), m.get("date"), m.get("value"))
