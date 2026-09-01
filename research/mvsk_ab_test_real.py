"""MVSK vs MV A/B 对比实验 — 真实 A 股数据版 (P1 验证)

用 Wind MCP 获取 30 只跨行业 A 股 504 交易日日 K 线,
构建真实收益矩阵, 跑 MV vs MVSK-轻 vs MVSK-重 三组对比.

前置:
    - Wind MCP skill 已安装 (.agents/skills/wind-mcp-skill)
    - WIND_API_KEY 已配置 (.agents/skills/wind-mcp-skill/config.json 或环境变量)

Usage:
    .venv\\Scripts\\python.exe research\\mvsk_ab_test_real.py
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import numpy as np

_PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

os.environ.setdefault("WIND_API_KEY", "ak_Tk4Y_UE-MfUof8DLLbKpHZZY-kh1q5KD")

from research.mvsk_ab_test import (  # noqa: E402
    print_comparison,
    run_experiment,
)
from tools.wind_mcp_fetcher import wind_get_kline  # noqa: E402

# ============================================================
# 30 只跨行业 A 股 (覆盖白酒/银行/科技/消费/周期/新能源/半导体/医药/地产/基建/券商/电力/化工/钢铁/煤炭/航空/零售)
# ============================================================

STOCKS: list[tuple[str, str]] = [
    ("600519.SH", "贵州茅台"),
    ("000858.SZ", "五粮液"),
    ("600036.SH", "招商银行"),
    ("601318.SH", "中国平安"),
    ("601398.SH", "工商银行"),
    ("000725.SZ", "京东方A"),
    ("002415.SZ", "海康威视"),
    ("300750.SZ", "宁德时代"),
    ("600276.SH", "药明康德"),
    ("000333.SZ", "美的集团"),
    ("601899.SH", "紫金矿业"),
    ("600028.SH", "中国石化"),
    ("002594.SZ", "比亚迪"),
    ("601012.SH", "隆基绿能"),
    ("002049.SZ", "紫光国微"),
    ("600436.SH", "片仔癀"),
    ("300015.SZ", "爱尔眼科"),
    ("001979.SZ", "招商蛇口"),
    ("601668.SH", "中国建筑"),
    ("600104.SH", "上汽集团"),
    ("600887.SH", "伊利股份"),
    ("600030.SH", "中信证券"),
    ("600900.SH", "长江电力"),
    ("600050.SH", "中国联通"),
    ("600309.SH", "万华化学"),
    ("600031.SH", "三一重工"),
    ("600019.SH", "宝钢股份"),
    ("601225.SH", "陕西煤业"),
    ("600029.SH", "南方航空"),
    ("601888.SH", "中国中免"),
]

N_DAYS = 504  # 约 2 年交易日
CACHE_PATH = _PROJECT_ROOT / "research" / "mvsk_real_data_cache.npz"


# ============================================================
# 数据获取与对齐
# ============================================================


def fetch_and_align() -> tuple[np.ndarray, list[str], list[str]]:
    """获取 30 只股票 K 线, 对齐日期, 构建 T×N 日收益矩阵.

    Returns:
        returns: T×N 日收益矩阵
        codes: N 个股票代码
        dates: T 个日期字符串
    """
    if CACHE_PATH.exists():
        cache = np.load(CACHE_PATH, allow_pickle=True)
        print(
            f"加载缓存: {CACHE_PATH} ( {cache['returns'].shape[0]} 日 × {cache['returns'].shape[1]} 股 )"
        )
        return cache["returns"], list(cache["codes"]), list(cache["dates"])

    print(f"获取 {len(STOCKS)} 只股票 × {N_DAYS} 交易日 K 线 (Wind MCP)...")
    all_klines: dict[str, dict[str, float]] = {}  # code -> {date: close}
    failed: list[str] = []

    for i, (code, name) in enumerate(STOCKS, 1):
        records = wind_get_kline(code, days=N_DAYS)
        if not records or len(records) < 252:
            print(
                f"  [{i:2d}/{len(STOCKS)}] {code} {name}: 数据不足 ({len(records) if records else 0} 条), 跳过"
            )
            failed.append(code)
            continue
        # 提取 (date, close) — MATCH 字段是收盘价
        klines = {}
        for r in records:
            date = str(r.get("TIME", ""))[:10]  # YYYY-MM-DD
            close = r.get("MATCH")
            if close:
                klines[date] = float(close)
        all_klines[code] = klines
        print(f"  [{i:2d}/{len(STOCKS)}] {code} {name}: {len(klines)} 条")

    if failed:
        print(f"失败/跳过: {failed}")

    # 对齐日期: 取所有股票都有数据的日期交集
    codes = list(all_klines.keys())
    date_sets = [set(k.keys()) for k in all_klines.values()]
    common_dates = sorted(set.intersection(*date_sets))
    print(f"公共交易日: {len(common_dates)} (交集对齐)")

    if len(common_dates) < 252:
        raise ValueError(f"公共交易日 {len(common_dates)} < 252, 数据不足")

    # 构建 T×N 收益矩阵
    T = len(common_dates) - 1  # 收益天数比价格少 1
    N = len(codes)
    returns = np.zeros((T, N))
    for j, code in enumerate(codes):
        klines = all_klines[code]
        prices = np.array([klines[d] for d in common_dates])
        returns[:, j] = prices[1:] / prices[:-1] - 1.0

    # 缓存
    np.savez(
        CACHE_PATH,
        returns=returns,
        codes=np.array(codes),
        dates=np.array(common_dates[1:]),
    )
    print(f"缓存已保存: {CACHE_PATH}")

    return returns, codes, common_dates[1:]


# ============================================================
# 主流程
# ============================================================


def main() -> int:
    R, codes, dates = fetch_and_align()  # noqa: N806
    T, N = R.shape  # noqa: N806
    print(f"\n收益矩阵: {T} 日 × {N} 股")

    # 年化 mu 与 cov
    mu = R.mean(axis=0) * 252
    cov = np.cov(R, rowvar=False) * 252

    # 基准权重: 等权
    w_bench = np.ones(N) / N

    # 数据诊断
    rp_bench = R @ w_bench
    bench_skew = float(((rp_bench - rp_bench.mean()) ** 3).mean() / rp_bench.std() ** 3)
    bench_kurt = float(
        ((rp_bench - rp_bench.mean()) ** 4).mean() / rp_bench.std() ** 4 - 3.0
    )
    bench_ann_ret = float(rp_bench.mean() * 252)
    bench_ann_vol = float(rp_bench.std() * np.sqrt(252))
    print(
        f"等权基准: 年化收益={bench_ann_ret:.4f}  年化波动={bench_ann_vol:.4f}  偏度={bench_skew:.4f}  超额峰度={bench_kurt:.4f}"  # noqa: E501
    )

    # 三组实验
    results = [
        run_experiment("MV(纯均值方差)", R, mu, cov, w_bench, 0.0, 0.0, max_te=0.06),
        run_experiment(
            "MVSK-轻(γs0.5γk0.1)", R, mu, cov, w_bench, 0.5, 0.1, max_te=0.06
        ),
        run_experiment(
            "MVSK-重(γs1.5γk0.5)", R, mu, cov, w_bench, 1.5, 0.5, max_te=0.06
        ),
    ]

    print_comparison(results)

    # 保存结果
    out = {
        "数据源": "Wind MCP 真实 A 股",
        "股票数": N,
        "交易日": T,
        "股票代码": codes,
        "等权基准": {
            "年化收益": bench_ann_ret,
            "年化波动": bench_ann_vol,
            "组合偏度": bench_skew,
            "超额峰度": bench_kurt,
        },
        "实验": [
            {
                "label": r.label,
                "metrics": r.metrics,
                "换手率": r.turnover,
                "交易成本年化": r.trade_cost * 252,
                "成本后净收益": r.net_return,
                "求解器": r.solver,
            }
            for r in results
        ],
    }
    out_path = _PROJECT_ROOT / "research" / "mvsk_ab_test_real_result.json"
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    print(f"\n结果已保存: {out_path}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
