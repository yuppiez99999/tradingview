# -*- coding: utf-8 -*-
"""
拉取 5 个新标的的历史日K并更新 returns_history.json, 然后训练 ML 模型
=====================================================================
新增标的: 000680 山推股份 / 000333 美的集团 / 000408 藏格矿业 / 000975 山金国际 / 002422 科伦药业
"""
import os
import sys
import json
import subprocess
import logging
import urllib.request
import urllib.parse
from pathlib import Path
from datetime import datetime, timedelta

# ============================================================
# 配置
# ============================================================
PROJECT_ROOT = Path(r"e:\各种PY程序\28-终极量化交易系统7.1")
CONFIG_DIR = PROJECT_ROOT / "config"
RETURNS_HISTORY = CONFIG_DIR / "returns_history.json"

# Wind MCP CLI
WIND_SKILL_DIR = Path(r"C:\Users\Administrator\.agents\skills\wind-mcp-skill")
WIND_CLI = WIND_SKILL_DIR / "scripts" / "cli.mjs"

# API Key
WIND_API_KEY = os.environ.get("WIND_API_KEY", "ak_Tk4Y_UE-MfUof8DLLbKpHZZY-kh1q5KD")
os.environ["WIND_API_KEY"] = WIND_API_KEY

# 5 个新标的: (代码, 后缀, server_type, 名称)
NEW_SYMBOLS = [
    ("000680", ".SZ", "stock_data", "山推股份"),
    ("000333", ".SZ", "stock_data", "美的集团"),
    ("000408", ".SZ", "stock_data", "藏格矿业"),
    ("000975", ".SZ", "stock_data", "山金国际"),
    ("002422", ".SZ", "stock_data", "科伦药业"),
]

# 拉取近 500 日数据
END_DATE = datetime.now().strftime("%Y%m%d")
BEGIN_DATE = (datetime.now() - timedelta(days=720)).strftime("%Y%m%d")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger("fetch_5new")


def call_wind_kline(windcode: str, server_type: str,
                    begin_date: str, end_date: str):
    """调用 Wind MCP CLI 拉取日 K 线"""
    tool_name = "get_stock_kline" if server_type == "stock_data" else "get_fund_kline"
    params = {
        "windcode": windcode,
        "begin_date": begin_date,
        "end_date": end_date,
        "period": "10",  # 日K
    }
    params_json = json.dumps(params, ensure_ascii=False)
    cmd = ["node", "scripts/cli.mjs", "call", server_type, tool_name, params_json]
    try:
        result = subprocess.run(
            cmd,
            cwd=str(WIND_SKILL_DIR),
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=90,
            env=os.environ,
        )
        output = result.stdout.strip()
        if not output:
            logger.warning(f"  [FAIL] {windcode}: stdout 为空")
            return None
        parsed = json.loads(output)
        if result.returncode != 0:
            err = parsed.get("error") or {}
            err_code = err.get("code", "UNKNOWN")
            logger.warning(f"  [FAIL] {windcode}: code={err_code}")
            return None
        return parsed
    except subprocess.TimeoutExpired:
        logger.warning(f"  [FAIL] {windcode}: 超时")
        return None
    except Exception as e:
        logger.warning(f"  [FAIL] {windcode}: {e}")
        return None


def call_sina_kline(code: str, suffix: str, datalen: int = 500):
    """调用新浪财经 API 拉取日 K 线 (Wind MCP 配额耗尽时的回退方案)

    Args:
        code: 6 位代码, 如 "000680"
        suffix: ".SZ" 或 ".SH"
        datalen: 拉取的日数 (最大 1023)

    Returns:
        list of dict: [{"day": "2025-06-23", "close": "7.50", ...}, ...]
    """
    prefix = "sz" if suffix == ".SZ" else "sh"
    sina_code = f"{prefix}{code}"
    url = (f"https://money.finance.sina.com.cn/quotes_service/api/json_v2.php/"
           f"CN_MarketData.getKLineData?symbol={sina_code}&scale=240&ma=no&datalen={datalen}")
    try:
        req = urllib.request.Request(url, headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
        })
        with urllib.request.urlopen(req, timeout=30) as resp:
            text = resp.read().decode("utf-8")
        if not text or text == "null":
            return None
        data = json.loads(text)
        if not isinstance(data, list) or not data:
            return None
        return data
    except Exception as e:
        logger.warning(f"  [SINA FAIL] {sina_code}: {e}")
        return None


def parse_sina_to_returns(sina_data: list):
    """解析新浪 K 线数据为 (日期列表, 日收益率列表)"""
    if not sina_data:
        return [], []

    pairs = []
    for item in sina_data:
        day = item.get("day", "")
        close = item.get("close", "")
        if not day or not close:
            continue
        date_str = day[:10]
        try:
            close_price = float(close)
        except (TypeError, ValueError):
            continue
        pairs.append((date_str, close_price))

    if len(pairs) < 2:
        return [], []

    pairs.sort(key=lambda x: x[0])
    dates = [p[0] for p in pairs]
    closes = [p[1] for p in pairs]

    returns = [0.0]
    for i in range(1, len(closes)):
        if closes[i - 1] == 0:
            returns.append(0.0)
        else:
            returns.append(round((closes[i] / closes[i - 1] - 1), 6))

    return dates, returns


def parse_kline_to_returns(resp: dict):
    """解析 Wind 返回的 K 线, 转为 (日期列表, 日收益率列表)"""
    if not resp or resp.get("isError") is True:
        return [], []

    content = resp.get("content")
    if not isinstance(content, list) or not content:
        return [], []

    first = content[0]
    if not isinstance(first, dict) or first.get("type") != "text":
        return [], []

    text = first.get("text", "")
    if not text:
        return [], []

    try:
        inner = json.loads(text)
    except json.JSONDecodeError:
        return [], []

    data = inner.get("data") or {}
    columns = data.get("columns") or []
    rows = data.get("rows") or []
    if not columns or not rows:
        return [], []

    # 找 TIME 与 MATCH/CLOSE 列
    col_names = [c.get("name", "").upper() for c in columns]
    time_idx = None
    close_idx = None
    for i, name in enumerate(col_names):
        if name == "TIME" and time_idx is None:
            time_idx = i
        elif name in ("MATCH", "CLOSE", "CLOSE_PRICE") and close_idx is None:
            close_idx = i

    if time_idx is None or close_idx is None:
        return [], []

    # 提取 (日期, 收盘价)
    pairs = []
    for row in rows:
        if not isinstance(row, list) or len(row) <= max(time_idx, close_idx):
            continue
        t = row[time_idx]
        c = row[close_idx]
        if t is None or c is None:
            continue
        # 日期格式: "2025-06-23" 或 ISO
        date_str = str(t)[:10]
        try:
            close = float(c)
        except (TypeError, ValueError):
            continue
        pairs.append((date_str, close))

    if len(pairs) < 2:
        return [], []

    # 按日期排序
    pairs.sort(key=lambda x: x[0])

    dates = [p[0] for p in pairs]
    closes = [p[1] for p in pairs]

    # 计算日收益率
    returns = [0.0]  # 第一日为 0
    for i in range(1, len(closes)):
        if closes[i - 1] == 0:
            returns.append(0.0)
        else:
            returns.append(round((closes[i] / closes[i - 1] - 1), 6))

    return dates, returns


def main():
    print("=" * 70)
    print("拉取 5 个新标的的历史日K并更新 returns_history.json")
    print(f"日期范围: {BEGIN_DATE} → {END_DATE}")
    print("=" * 70)

    # Step 1: 拉取 Wind 数据 (失败则回退到新浪 API)
    print("\n[Step 1] 拉取历史日K数据 (Wind MCP → 新浪回退)...")
    new_data = {}  # {code: {"dates": [...], "returns": [...], "name": ...}}
    for code, suffix, server_type, name in NEW_SYMBOLS:
        windcode = f"{code}{suffix}"
        print(f"  拉取 {windcode} ({name})...")

        # 优先 Wind MCP
        resp = call_wind_kline(windcode, server_type, BEGIN_DATE, END_DATE)
        dates, returns = parse_kline_to_returns(resp)

        # Wind 失败 → 回退新浪
        if not dates:
            print(f"    Wind 失败, 回退新浪 API...")
            sina_data = call_sina_kline(code, suffix, datalen=500)
            dates, returns = parse_sina_to_returns(sina_data)
            if dates:
                print(f"    ✓ [新浪] {len(dates)} 条记录 ({dates[0]} → {dates[-1]})")
                new_data[code] = {"dates": dates, "returns": returns, "name": name}
            else:
                print(f"    ✗ 新浪也失败, 无数据")
            continue

        print(f"    ✓ [Wind] {len(dates)} 条记录 ({dates[0]} → {dates[-1]})")
        new_data[code] = {"dates": dates, "returns": returns, "name": name}

    if not new_data:
        print("\n✗ 所有标的拉取失败, 无法继续")
        return False

    # Step 2: 加载现有 returns_history.json
    print("\n[Step 2] 加载现有 returns_history.json...")
    with open(RETURNS_HISTORY, "r", encoding="utf-8") as f:
        rh = json.load(f)
    existing_cols = set(rh["columns"])
    existing_index = rh["index"]
    print(f"  现有: {len(existing_cols)} 标的 × {len(existing_index)} 日")

    # Step 3: 合并新标的数据
    print("\n[Step 3] 合并新标的数据...")
    added = 0
    for code, info in new_data.items():
        if code in existing_cols:
            print(f"  [SKIP] {code} ({info['name']}): 已存在")
            continue

        # 扩展 columns
        rh["columns"].append(code)

        # 扩展 data: 对齐到现有 index
        new_dates = info["dates"]
        new_returns = info["returns"]
        date_to_ret = dict(zip(new_dates, new_returns))

        # 为每一行追加新列的值
        for row in rh["data"]:
            date_str = existing_index[rh["data"].index(row)][:10]
            row.append(date_to_ret.get(date_str, 0.0))

        # 处理新日期 (新标的有但 index 中没有的日期)
        existing_dates = set(d[:10] for d in existing_index)
        for i, d in enumerate(new_dates):
            if d not in existing_dates:
                # 添加新行
                full_date = d + "T00:00:00"
                rh["index"].append(full_date)
                new_row = [0.0] * len(rh["columns"])
                new_row[-1] = new_returns[i]
                rh["data"].append(new_row)
                existing_dates.add(d)

        added += 1
        print(f"  [ADD] {code} ({info['name']}): {len(new_dates)} 条记录")

    print(f"\n  新增标的数: {added}")
    print(f"  更新后: {len(rh['columns'])} 标的 × {len(rh['index'])} 日")

    # Step 4: 备份并写入
    import shutil
    bak_path = RETURNS_HISTORY.with_suffix(
        f".json.bak_5new_{datetime.now():%Y%m%d_%H%M%S}"
    )
    shutil.copy(RETURNS_HISTORY, bak_path)
    print(f"\n[Step 4] 备份: {bak_path.name}")

    with open(RETURNS_HISTORY, "w", encoding="utf-8") as f:
        json.dump(rh, f, ensure_ascii=False)
    print(f"  已写入: {RETURNS_HISTORY}")

    # Step 5: 运行 autolearn_trainer
    print("\n[Step 5] 运行 autolearn_trainer 训练 5 个新标的...")
    autolearn_script = PROJECT_ROOT / "v7.5_institutional" / "autolearn_trainer.py"
    cmd = [
        "py", "-3.11",
        str(autolearn_script),
        "--force-retrain",
        "--symbols", "000680", "000333", "000408", "000975", "002422",
    ]
    print(f"  命令: {' '.join(cmd)}")
    try:
        result = subprocess.run(cmd, capture_output=True, text=True,
                                encoding="utf-8", timeout=600)
        print(result.stdout[-3000:] if len(result.stdout) > 3000 else result.stdout)
        if result.stderr:
            print("--- stderr ---")
            print(result.stderr[-2000:] if len(result.stderr) > 2000 else result.stderr)
        print(f"\n  退出码: {result.returncode}")
        return result.returncode == 0
    except subprocess.TimeoutExpired:
        print("  ✗ 训练超时")
        return False
    except Exception as e:
        print(f"  ✗ 训练异常: {e}")
        return False


if __name__ == "__main__":
    success = main()
    print("\n" + "=" * 70)
    print("✓ 完成" if success else "✗ 失败")
    print("=" * 70)
    sys.exit(0 if success else 1)
