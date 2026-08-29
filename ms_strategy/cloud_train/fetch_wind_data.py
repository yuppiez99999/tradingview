import json
import subprocess
from pathlib import Path

SKILL_DIR = Path(".agents/skills/wind-mcp-skill")
CLI_PATH = SKILL_DIR / "scripts" / "cli.mjs"

def wind_call(server_type, tool_name, params):
    """调用 Wind MCP"""
    param_file = Path("wind_params_tmp.json")
    param_file.write_text(json.dumps(params, ensure_ascii=False))
    result = subprocess.run(
        ["node", str(CLI_PATH), "call", server_type, tool_name, f"@{param_file}"],
        capture_output=True, text=True, encoding="utf-8", timeout=30
    )
    param_file.unlink(missing_ok=True)
    if result.returncode != 0:
        return None
    try:
        resp = json.loads(result.stdout)
        if resp.get("isError"):
            return None
        text = resp["content"][0]["text"]
        return json.loads(text)
    except Exception:
        return None

# 获取沪深300指数 2020-09-25 到 2026-07-08 的日线数据
print("从 Wind 获取沪深300指数日线数据...")
result = wind_call("index_data", "get_index_kline", {
    "windcode": "000300.SH",
    "begin_date": "2020-09-25",
    "end_date": "2026-07-08",
    "period": "1d"
})
if result:
    data = result.get("data", {})
    rows = data.get("rows", [])
    columns = [c["name"] for c in data.get("columns", [])]
    print(f"获取到 {len(rows)} 行数据")
    print(f"列: {columns}")
    if rows:
        print(f"第一行: {rows[0]}")
        print(f"最后行: {rows[-1]}")

    # 保存到 CSV
    import csv
    output_file = Path("reports/wind_csi300_index.csv")
    output_file.parent.mkdir(exist_ok=True)
    with open(output_file, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["date"] + columns)
        for row in rows:
            writer.writerow(row)
    print(f"\n已保存到: {output_file}")
else:
    print("获取失败!")
