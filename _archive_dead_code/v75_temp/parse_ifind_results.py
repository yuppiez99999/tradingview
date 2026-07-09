# -*- coding: utf-8 -*-
"""
解析 iFinD 返回结果, 提取数值并按资产类别聚合
输入: ifind_raw_results_20260706.json
输出: calibrated_asset_params.json (用于 v3 优化的真实参数)
"""
import json
import re
from pathlib import Path
from collections import defaultdict

RAW_PATH = Path(r"e:\各种PY程序\每日报告归档") / datetime.now().strftime("%Y-%m-%d") / f"ifind_raw_results_{datetime.now().strftime('%Y%m%d')}.json"
OUT_PATH = Path("e:/各种PY程序/28-终极量化交易系统7.1/v7.5_institutional/calibrated_asset_params.json")


def parse_inner_text(text):
    """二次解析 iFinD 返回的 text (本身是 JSON 字符串)"""
    if not text:
        return None
    try:
        obj = json.loads(text)
    except Exception:
        return None
    if not isinstance(obj, dict):
        return None
    data = obj.get("data", {})
    if not isinstance(data, dict):
        return None
    # answer1 是表格, answer 是普通文本
    return data.get("answer1") or data.get("answer") or ""


def parse_markdown_table(md_text):
    """解析 markdown 表格, 返回 (header, rows)"""
    if not md_text:
        return [], []
    lines = md_text.split("\n")
    header = None
    rows = []
    for line in lines:
        m = re.match(r"^\|(.+)\|$", line)
        if not m:
            continue
        cells = [c.strip() for c in m.group(1).split("|")]
        cells = [c for c in cells if c != ""]
        if not cells:
            continue
        # 跳过分隔行 |---|---|
        if all(re.match(r"^[-:]+$", c) for c in cells):
            continue
        if header is None:
            header = cells
            continue
        rows.append(cells)
    return header or [], rows


def extract_numeric(cell):
    """从单元格提取数值, 失败返回 None"""
    if not cell:
        return None
    # 处理 \t 等空白
    cell = cell.strip()
    if not cell or cell == "\\t":
        return None
    # 提取第一个数字 (含负号、小数点)
    m = re.search(r"-?\d+\.?\d*", cell.replace(",", ""))
    if m:
        try:
            return float(m.group())
        except Exception:
            return None
    return None


def find_value_column(header, rows, prefer_keywords=None):
    """
    在表格中找到指标值列。
    - 优先匹配包含 prefer_keywords 的列
    - 否则跳过 代码/简称/日期 列, 取第一个数值列
    """
    if not header or not rows:
        return None
    skip_keywords = ["代码", "简称", "日期", "区间日"]
    # 优先列
    if prefer_keywords:
        for kw in prefer_keywords:
            for idx, h in enumerate(header):
                if kw in h:
                    for row in rows:
                        if idx < len(row):
                            v = extract_numeric(row[idx])
                            if v is not None:
                                return v
    # 默认: 跳过标识列, 取第一个数值
    for idx, h in enumerate(header):
        if any(kw in h for kw in skip_keywords):
            continue
        for row in rows:
            if idx < len(row):
                v = extract_numeric(row[idx])
                if v is not None:
                    return v
    return None


def extract_stat_table_mean(md_text, indicator_name=None):
    """
    从包含 "指标统计特征表" 的返回中提取均值。
    表格: | 证券代码 | 指标名称 | 均值 | 最大值 | 中位数 | 最小值 |
    """
    if not md_text:
        return None
    # 截取统计特征表部分
    m = re.search(r"# 指标统计特征表.*?(?=#|$)", md_text, re.DOTALL)
    if not m:
        return None
    stat_text = m.group()
    header, rows = parse_markdown_table(stat_text)
    if not header or not rows:
        return None
    # 找 "均值" 列
    mean_idx = -1
    for i, h in enumerate(header):
        if "均值" in h:
            mean_idx = i
            break
    if mean_idx < 0:
        return None
    for row in rows:
        if mean_idx < len(row):
            v = extract_numeric(row[mean_idx])
            if v is not None:
                return v
    return None


def parse_one_query(query_result):
    """
    解析单个 query 的结果, 返回数值 (可能为 None)
    query_result 结构: {query, ok, text, parsed}
    """
    if not query_result or not query_result.get("ok"):
        return None
    text = query_result.get("text", "")
    inner = parse_inner_text(text)
    if not inner:
        return None
    # 检查是否空结果
    if "结果为空" in inner:
        return None
    header, rows = parse_markdown_table(inner)
    if not rows:
        return None
    # 优先从统计特征表提取均值 (适用于多行历史数据)
    mean_val = extract_stat_table_mean(inner)
    if mean_val is not None:
        return mean_val
    # 否则取第一行数值
    return find_value_column(header, rows)


def main():
    with open(RAW_PATH, "r", encoding="utf-8") as f:
        raw = json.load(f)

    print("=" * 70)
    print("解析 iFinD 原始结果, 提取数值")
    print("=" * 70)

    # 提取每标的的 5 个指标
    instrument_metrics = {}
    for code, info in raw.items():
        if "error" in info:
            continue
        q = info.get("queries", {})
        metrics = {
            "windcode": code,
            "name": info.get("name", ""),
            "asset_class": info.get("asset_class", ""),
            "recent_1y_return_pct": parse_one_query(q.get("recent_1y_return")),
            "recent_3y_return_pct": parse_one_query(q.get("recent_3y_return")),
            "sharpe_1y": parse_one_query(q.get("sharpe_1y")),
            "volatility_1y_pct": parse_one_query(q.get("volatility_1y")),
            "max_drawdown_pct": parse_one_query(q.get("max_drawdown")),
        }
        instrument_metrics[code] = metrics
        # 简要打印
        print(f"\n  [{metrics['asset_class']}] {code} {metrics['name']}")
        for k in ["recent_1y_return_pct", "recent_3y_return_pct", "sharpe_1y", "volatility_1y_pct", "max_drawdown_pct"]:
            v = metrics[k]
            print(f"    {k:30s}: {v}")

    # 按资产类别聚合 (取均值)
    print("\n" + "=" * 70)
    print("按资产类别聚合 (取均值)")
    print("=" * 70)
    asset_class_data = defaultdict(list)
    for m in instrument_metrics.values():
        asset_class_data[m["asset_class"]].append(m)

    asset_class_summary = {}
    for ac, insts in asset_class_data.items():
        n = len(insts)
        agg = {
            "n_instruments": n,
            "instruments": [i["windcode"] for i in insts],
            "recent_1y_return_pct": _safe_mean([i["recent_1y_return_pct"] for i in insts]),
            "recent_3y_return_pct": _safe_mean([i["recent_3y_return_pct"] for i in insts]),
            "sharpe_1y": _safe_mean([i["sharpe_1y"] for i in insts]),
            "volatility_1y_pct": _safe_mean([i["volatility_1y_pct"] for i in insts]),
            "max_drawdown_pct": _safe_mean([i["max_drawdown_pct"] for i in insts]),
        }
        asset_class_summary[ac] = agg
        print(f"\n  [{ac}] (n={n})")
        for k in ["recent_1y_return_pct", "recent_3y_return_pct", "sharpe_1y", "volatility_1y_pct", "max_drawdown_pct"]:
            v = agg[k]
            print(f"    {k:30s}: {v}")

    # 保存结果
    out = {
        "data_source": "iFinD (ifind-finance-data MCP)",
        "query_date": "2026-07-05",
        "instrument_metrics": instrument_metrics,
        "asset_class_summary": asset_class_summary,
    }
    with open(OUT_PATH, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    print(f"\n校准结果已保存: {OUT_PATH}")


def _safe_mean(values):
    """安全均值, 忽略 None"""
    valid = [v for v in values if v is not None]
    if not valid:
        return None
    return sum(valid) / len(valid)


if __name__ == "__main__":
    main()
