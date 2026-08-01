"""
v7.5 批量训练脚本 — iFinD 数据 + 本地 LightGBM

功能：
    1. 读取用户真实交易计划中的标的列表
    2. 从 iFinD MCP 获取真实 OHLCV 历史数据
    3. 对每个标的训练本地 LightGBM 模型
    4. 保存模型到 models/qlib_local/{symbol}_lgb_model.pkl
    5. 输出训练报告（R²、信号值、数据量）

用法：
    python train_qlib_models.py                          # 使用默认真实交易计划
    python train_qlib_models.py --plan trade_plan_20260705.json
    python train_qlib_models.py --symbols 510300 300308   # 指定标的
    python train_qlib_models.py --days 250                 # 获取 250 天数据
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional


# 添加项目路径
PROJECT_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(PROJECT_ROOT))

from src.alpha.qlib_signal_adapter import (  # noqa: E402
    fetch_ifind_ohlcv,
    _local_lightgbm_signal,
    load_local_model,
    MODEL_DIR,
)

# 日志
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("v75.train_qlib")


# ============================================================
# 用户真实交易计划标的列表
# ============================================================

USER_SYMBOLS: List[Dict[str, str]] = [
    # 宽基 ETF（第一梯队）
    {"code": "510300", "name": "沪深300ETF"},
    {"code": "510500", "name": "中证500ETF"},
    {"code": "512100", "name": "中证1000ETF"},
    {"code": "588000", "name": "科创50ETF"},
    {"code": "159915", "name": "创业板ETF"},
    # 科技成长个股（第二梯队）
    {"code": "688041", "name": "海光信息"},
    {"code": "300308", "name": "中际旭创"},
    {"code": "300274", "name": "阳光电源"},
    {"code": "002371", "name": "北方华创"},
    {"code": "688017", "name": "绿的谐波"},
    {"code": "600276", "name": "恒瑞医药"},
    {"code": "688981", "name": "中芯国际"},
    {"code": "603019", "name": "中科曙光"},
    # 高端制造/基建（第三梯队）
    {"code": "600089", "name": "特变电工"},
    {"code": "600875", "name": "东方电气"},
    {"code": "000425", "name": "徐工机械"},
    {"code": "600406", "name": "国电南瑞"},
    {"code": "600989", "name": "宝丰能源"},
    {"code": "600219", "name": "南山铝业"},
    {"code": "600019", "name": "宝钢股份"},
    {"code": "000792", "name": "盐湖股份"},
    # 防御/红利（第四梯队）
    {"code": "515180", "name": "红利ETF"},
    {"code": "600036", "name": "招商银行"},
    {"code": "600900", "name": "长江电力"},
    {"code": "601088", "name": "中国神华"},
    {"code": "601318", "name": "中国平安"},
    {"code": "000858", "name": "五粮液"},
    # 黄金（第五梯队）
    {"code": "518880", "name": "黄金ETF"},
]


def load_trade_plan(plan_path: str) -> List[Dict]:
    """加载交易计划，提取标的信息"""
    with open(plan_path, 'r', encoding='utf-8') as f:
        plan = json.load(f)

    orders = (
        plan.get("execution_plan", {}).get("morning_orders", []) +
        plan.get("execution_plan", {}).get("afternoon_orders", [])
    )

    symbols = []
    seen = set()
    for order in orders:
        code = order.get("code", "")
        name = order.get("name", "")
        if code and code not in seen:
            seen.add(code)
            symbols.append({"code": code, "name": name})

    return symbols


def train_symbol(symbol_info: Dict, days: int = 120, force_retrain: bool = False) -> Optional[Dict]:
    """训练单个标的的模型

    Args:
        symbol_info: {"code": "601088", "name": "中国神华"}
        days: 获取历史数据天数
        force_retrain: 是否强制重新训练

    Returns:
        训练结果字典
    """
    code = symbol_info["code"]
    name = symbol_info.get("name", code)

    # 检查是否已有模型
    if not force_retrain:
        existing = load_local_model(code)
        if existing and existing.get("meta"):
            meta = existing["meta"]
            logger.info(f"[{code}] {name} — 已有模型 (train_r2={meta.get('metrics', {}).get('train_r2', 'N/A')})")
            return {
                "code": code,
                "name": name,
                "status": "SKIP",
                "reason": "模型已存在",
                "metrics": meta.get("metrics", {}),
                "signal": None,
            }

    # 获取 iFinD 数据
    logger.info(f"[{code}] {name} — 获取 iFinD 数据 ({days} 天)")
    df = fetch_ifind_ohlcv(code, days=days)
    if df is None or len(df) < 60:
        logger.warning(f"[{code}] {name} — 数据不足，跳过")
        return {
            "code": code,
            "name": name,
            "status": "FAIL",
            "reason": "iFinD 数据不足",
            "metrics": {},
            "signal": None,
        }

    # 训练模型
    logger.info(f"[{code}] {name} — 开始训练 (数据 {len(df)} 行)")
    result = _local_lightgbm_signal(df, code, save_model=True)

    if result.get("signal") is None:
        return {
            "code": code,
            "name": name,
            "status": "FAIL",
            "reason": "训练失败",
            "metrics": result.get("metrics", {}),
            "signal": None,
        }

    signal_value = float(result["signal"].iloc[-1])
    metrics = result.get("metrics", {})

    logger.info(
        f"[{code}] {name} — 训练完成: "
        f"train_r2={metrics.get('train_r2', 'N/A')}, "
        f"test_r2={metrics.get('test_r2', 'N/A')}, "
        f"signal={signal_value:+.4f}"
    )

    return {
        "code": code,
        "name": name,
        "status": "TRAINED",
        "metrics": metrics,
        "signal": round(signal_value, 4),
        "model_path": str(MODEL_DIR / f"{code}_lgb_model.pkl"),
    }


def batch_train(symbols: List[Dict], days: int = 120, force_retrain: bool = False) -> List[Dict]:
    """批量训练"""
    results = []
    total = len(symbols)

    for i, sym in enumerate(symbols, 1):
        logger.info(f"[{i}/{total}] 训练 {sym['code']} {sym.get('name', '')}")
        result = train_symbol(sym, days=days, force_retrain=force_retrain)
        results.append(result)

    return results


def print_report(results: List[Dict]):
    """打印训练报告"""
    print("\n" + "=" * 90)
    print(f"v7.5 模型训练报告 — {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 90)

    trained = [r for r in results if r["status"] == "TRAINED"]
    skipped = [r for r in results if r["status"] == "SKIP"]
    failed = [r for r in results if r["status"] == "FAIL"]

    print(f"\n总计: {len(results)} | 训练: {len(trained)} | 跳过: {len(skipped)} | 失败: {len(failed)}")

    if trained:
        print("\n" + "-" * 90)
        print(f"{'代码':<10} {'名称':<12} {'Train R²':<12} {'Test R²':<12} {'信号值':<10} {'模型路径'}")
        print("-" * 90)
        for r in trained:
            m = r.get("metrics", {})
            print(
                f"{r['code']:<10} {r.get('name', ''):<12} "
                f"{m.get('train_r2', 'N/A'):<12} {m.get('test_r2', 'N/A'):<12} "
                f"{r.get('signal', 'N/A'):<10} {r.get('model_path', '')}"
            )

    if failed:
        print("\n" + "-" * 90)
        print("失败标的:")
        for r in failed:
            print(f"  {r['code']} {r.get('name', '')} — {r.get('reason', '未知')}")

    print("\n" + "=" * 90)


def main():
    parser = argparse.ArgumentParser(description="v7.5 批量模型训练 (iFinD + LightGBM)")
    parser.add_argument("--plan", type=str, default=None, help="交易计划 JSON 路径")
    parser.add_argument("--symbols", type=str, nargs="+", help="指定标的代码列表")
    parser.add_argument("--days", type=int, default=500, help="历史数据天数 (默认 500)")
    parser.add_argument("--force", action="store_true", help="强制重新训练")
    parser.add_argument("--output", type=str, default=None, help="报告输出路径")
    args = parser.parse_args()

    # 确定标的列表
    if args.symbols:
        symbols = [{"code": s, "name": s} for s in args.symbols]
    elif args.plan:
        symbols = load_trade_plan(args.plan)
    else:
        # 默认使用用户的真实交易计划标的
        symbols = USER_SYMBOLS

    if not symbols:
        logger.error("没有可训练的标的")
        sys.exit(1)

    logger.info(f"开始批量训练: {len(symbols)} 个标的")

    # 批量训练
    results = batch_train(symbols, days=args.days, force_retrain=args.force)

    # 打印报告
    print_report(results)

    # 保存报告
    if args.output:
        report_path = Path(args.output)
    else:
        report_dir = PROJECT_ROOT / "reports"
        report_dir.mkdir(exist_ok=True)
        report_path = report_dir / f"train_report_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"

    with open(report_path, 'w', encoding='utf-8') as f:
        json.dump({
            "timestamp": datetime.now().isoformat(),
            "total": len(results),
            "trained": len([r for r in results if r["status"] == "TRAINED"]),
            "skipped": len([r for r in results if r["status"] == "SKIP"]),
            "failed": len([r for r in results if r["status"] == "FAIL"]),
            "results": results,
        }, f, ensure_ascii=False, indent=2)

    logger.info(f"报告已保存: {report_path}")


if __name__ == "__main__":
    main()
