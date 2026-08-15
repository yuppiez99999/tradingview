#!/usr/bin/env python3
"""S6 纸交易跟踪脚本 — Wave 5 CHAIN_MOM_60D GNN 因子.

W7.2.5 Wave 5 S6 纸交易启动 (排期 09-13~10-12)

前置: S1-S5 全通过
- Gate1 PASS (effICIR=0.503 / 多空夏普 1.766)
- Gate2 FAIL (+0.039 增益证伪) → 回退 Layer1
- S5 边际夏普改善 PASS

功能:
  1. 读取 CHAIN_MOM_60D 因子值 (utils/alpha_factor/graph.py)
  2. 计算增强组合 = MOM_60D + direction * CHAIN_MOM_60D
  3. 跟踪每日纸交易收益 (Top20%/Bottom20% 多空)
  4. 输出到 reports/gnn_factor/s6_paper_trading.jsonl
  5. 检查 S6 -> S7 升级门槛 (Sharpe / 一致性 / 前视偏差)

运行:
  py -X utf8 scripts/s6_paper_trading_runner.py --check    # 检查门槛
  py -X utf8 scripts/s6_paper_trading_runner.py --run      # 跑当日纸交易
  py -X utf8 scripts/s6_paper_trading_runner.py --status   # 查看跟踪状态

配置: config/gnn_factor/s6_paper_trading.yaml
输出: reports/gnn_factor/s6_paper_trading.jsonl
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import date
from pathlib import Path

import yaml

logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parents[1]
CONFIG_PATH = PROJECT_ROOT / "config" / "gnn_factor" / "s6_paper_trading.yaml"
OUTPUT_PATH = PROJECT_ROOT / "reports" / "gnn_factor" / "s6_paper_trading.jsonl"


def load_config() -> dict:
    """加载 S6 纸交易配置."""
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def check_admission_criteria(records: list[dict], config: dict) -> dict:
    """检查 S6 -> S7 升级门槛."""
    criteria = config["admission_criteria"]
    days_tracked = len(records)

    if days_tracked < criteria["min_running_days"]:
        return {
            "ready_for_s7": False,
            "reason": f"跟踪天数不足: {days_tracked}/{criteria['min_running_days']}",
            "days_tracked": days_tracked,
        }

    # TODO: 实现 Sharpe / 一致性 / 前视偏差计算
    # 需要接入 utils/alpha_factor/s5_validation.py 的计算逻辑
    return {
        "ready_for_s7": False,
        "reason": "骨架阶段: 计算逻辑待实现",
        "days_tracked": days_tracked,
    }


def run_paper_trading(config: dict) -> dict:
    """跑当日纸交易.

    TODO: 接入 CHAIN_MOM_60D 因子计算 + 增强组合 + 多空收益
    """
    today = date.today().isoformat()
    record = {
        "date": today,
        "stage": "paper_trading",
        "capital_ratio": 0.0,
        "factor": "CHAIN_MOM_60D",
        "status": "skeleton",
        "note": "骨架阶段: 因子计算待接入(接入 utils/alpha_factor/graph.py)",
    }

    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(OUTPUT_PATH, "a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")

    return record


def show_status(config: dict) -> None:
    """查看跟踪状态."""
    if not OUTPUT_PATH.exists():
        print("S6 纸交易: 尚未启动 (无跟踪记录)")
        return

    records = []
    with open(OUTPUT_PATH, "r", encoding="utf-8") as f:
        for line in f:
            if line.strip():
                records.append(json.loads(line))

    print("S6 纸交易跟踪状态")
    print(f"  因子: {config['factor']['name']}")
    print(f"  跟踪天数: {len(records)}/{config['tracking']['min_running_days']}")
    print(f"  输出: {OUTPUT_PATH}")

    if records:
        latest = records[-1]
        print(f"  最新记录: {latest.get('date', '?')}")

    check = check_admission_criteria(records, config)
    ready = "✅ 就绪" if check["ready_for_s7"] else "❌ 未就绪"
    print(f"  S7 升级: {ready}")
    if not check["ready_for_s7"]:
        print(f"    原因: {check.get('reason', '?')}")


def main() -> int:
    parser = argparse.ArgumentParser(description="S6 纸交易跟踪 — CHAIN_MOM_60D")
    parser.add_argument("--check", action="store_true", help="检查 S7 升级门槛")
    parser.add_argument("--run", action="store_true", help="跑当日纸交易")
    parser.add_argument("--status", action="store_true", help="查看跟踪状态")
    args = parser.parse_args()

    config = load_config()

    if args.check:
        records = []
        if OUTPUT_PATH.exists():
            with open(OUTPUT_PATH, "r", encoding="utf-8") as f:
                records = [json.loads(l) for l in f if l.strip()]
        result = check_admission_criteria(records, config)
        print(json.dumps(result, indent=2, ensure_ascii=False))
    elif args.run:
        record = run_paper_trading(config)
        print(json.dumps(record, indent=2, ensure_ascii=False))
    elif args.status:
        show_status(config)
    else:
        parser.print_help()

    return 0


if __name__ == "__main__":
    sys.exit(main())