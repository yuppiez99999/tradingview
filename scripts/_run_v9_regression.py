# -*- coding: utf-8 -*-
"""V9 基线回归测试 — 可执行入口.

模块整合 8.4 — T1.8
HC-1: 任何 PR 必须通过 V9 基线回归测试

用法:
    # 快速回归 (CI 默认, <30 秒, Layer 1-4)
    python scripts/_run_v9_regression.py

    # 含 nightly 完整回测 (>30 分钟, Layer 5)
    python scripts/_run_v9_regression.py --full

    # 仅打印基线指标摘要, 不执行测试
    python scripts/_run_v9_regression.py --summary

    # 指定 pytest 额外参数
    python scripts/_run_v9_regression.py -- -v --tb=short

退出码:
    0: 全部通过
    1: 回归失败 (指标退化或代码不可导入)
    2: 基线文件缺失 (无法执行回归)
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

# 项目根目录
PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger("v9_regression")


# ============================================================
# 基线摘要打印
# ============================================================

def print_baseline_summary() -> int:
    """打印 V9 基线指标摘要, 不执行测试.

    Returns:
        0 成功, 2 基线文件缺失
    """
    try:
        from tests.regression.conftest import (
            _find_latest_json,
            _parse_baseline_lock,
            BASELINE_LOCK_FILE,
        )
    except ImportError as e:
        logger.error("无法导入 tests.regression.conftest: %s", e)
        return 2

    # LOCK 文件
    if not BASELINE_LOCK_FILE.exists():
        logger.error("V9_BASELINE_LOCK.txt 不存在: %s", BASELINE_LOCK_FILE)
        return 2
    lock_text = BASELINE_LOCK_FILE.read_text(encoding="utf-8")
    lock = _parse_baseline_lock(lock_text)

    # DSR maxpass JSON
    try:
        dsr_path = _find_latest_json("v9_dsr_maxpass_")
    except FileNotFoundError as e:
        logger.error("DSR maxpass JSON 不存在: %s", e)
        return 2
    with open(dsr_path, "r", encoding="utf-8") as f:
        dsr = json.load(f)

    # 回测 JSON
    try:
        bt_path = _find_latest_json("v9_regime_specific_backtest_")
    except FileNotFoundError as e:
        logger.error("回测 JSON 不存在: %s", e)
        return 2
    with open(bt_path, "r", encoding="utf-8") as f:
        bt = json.load(f)

    print("=" * 72)
    print("V9 Regime-Specific LGB 生产基线摘要")
    print("=" * 72)
    print(f"LOCK 文件:           {BASELINE_LOCK_FILE}")
    print(f"基线 commit hash:    {lock.get('commit_hash', '?')}")
    print(f"基线 commit 主题:    {lock.get('commit_subject', '?')}")
    print(f"基线 commit 时间:    {lock.get('commit_time', '?')}")
    print(f"锁定日期:            {lock.get('lock_date', '?')}")
    print()
    print("基线评估指标 (Bailey & Lopez de Prado 2014 标准公式):")
    print(f"  DSR max_pass:      {dsr.get('max_pass', '?')} "
          f"(旧公式: {dsr.get('max_pass_old_formula', '?')}, "
          f"阈值: >=5)")
    print(f"  年化收益率:        {dsr.get('annual_return', 0)*100:.2f}% "
          f"(阈值: >=15%)")
    print(f"  最大回撤:          {dsr.get('max_drawdown', 0)*100:.2f}% "
          f"(阈值: <=10%)")
    print(f"  Sharpe CV (12月):  {dsr.get('sharpe_cv', 0):.4f} "
          f"(旧 2 点: {dsr.get('sharpe_cv_old_2point', 0):.4f}, "
          f"阈值: <1.0)")
    print(f"  胜率:              {dsr.get('win_rate', 0)*100:.2f}% "
          f"(额外阈值: >=60%)")
    print(f"  年化 Sharpe:       {dsr.get('sharpe_annual', 0):.4f}")
    print(f"  样本数 (月):       {dsr.get('n_months', '?')}")
    print(f"  all_pass:          {dsr.get('all_pass', '?')}")
    print()
    print("回测元信息:")
    print(f"  回测周期:          {bt.get('period', '?')}")
    print(f"  标的数:            {len(bt.get('symbols', []))}")
    print(f"  月度记录数:        {len(bt.get('records', []))}")
    print(f"  回测 JSON:         {bt_path}")
    print(f"  DSR JSON:          {dsr_path}")
    print("=" * 72)
    return 0


# ============================================================
# pytest 调用
# ============================================================

def run_pytest(extra_args: List[str], include_nightly: bool = False) -> int:
    """通过 pytest API 运行回归测试.

    Args:
        extra_args: 额外 pytest 参数
        include_nightly: 是否包含 nightly 测试 (Layer 5 完整回测)

    Returns:
        pytest 退出码 (0=pass, 非 0=fail)
    """
    import pytest

    test_dir = str(PROJECT_ROOT / "tests" / "regression")

    args = [test_dir, "-v", "--tb=short"]
    if not include_nightly:
        args.extend(["-m", "not nightly"])
    args.extend(extra_args)

    logger.info("启动 pytest: %s", " ".join(args))
    t0 = time.time()
    exit_code = pytest.main(args)
    elapsed = time.time() - t0

    logger.info("pytest 退出码: %d (耗时 %.1f 秒)", exit_code, elapsed)
    return int(exit_code)


# ============================================================
# 主入口
# ============================================================

def main() -> int:
    """主入口.

    Returns:
        0 全部通过 / 1 回归失败 / 2 基线文件缺失
    """
    parser = argparse.ArgumentParser(
        description="V9 基线回归测试入口 (模块整合 8.4 T1.8, HC-1)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
    python scripts/_run_v9_regression.py             # 快速回归 (默认)
    python scripts/_run_v9_regression.py --full       # 含 nightly 完整回测
    python scripts/_run_v9_regression.py --summary    # 仅打印基线摘要
    python scripts/_run_v9_regression.py -- -v        # 透传额外 pytest 参数
""",
    )
    parser.add_argument(
        "--full",
        action="store_true",
        help="包含 nightly 完整回测 (Layer 5, >30 分钟)",
    )
    parser.add_argument(
        "--summary",
        action="store_true",
        help="仅打印基线指标摘要, 不执行测试",
    )
    parser.add_argument(
        "extra",
        nargs="*",
        help="透传给 pytest 的额外参数 (需以 -- 分隔)",
    )

    args, extra_pytest = parser.parse_known_args()

    if args.summary:
        return print_baseline_summary()

    logger.info("=" * 72)
    logger.info("V9 基线回归测试启动 (T1.8, HC-1)")
    logger.info("=" * 72)
    logger.info("模式: %s", "完整 (含 nightly)" if args.full else "快速 (默认)")
    logger.info("项目根目录: %s", PROJECT_ROOT)

    # 先做基线文件存在性预检
    try:
        from tests.regression.conftest import (
            _find_latest_json,
            BASELINE_LOCK_FILE,
        )
        if not BASELINE_LOCK_FILE.exists():
            logger.error("预检失败: V9_BASELINE_LOCK.txt 不存在")
            return 2
        _find_latest_json("v9_dsr_maxpass_")
        _find_latest_json("v9_regime_specific_backtest_")
        logger.info("预检通过: 基线文件齐全")
    except FileNotFoundError as e:
        logger.error("预检失败: %s", e)
        return 2
    except Exception as e:
        logger.error("预检异常: %s", e)
        return 2

    # 运行 pytest
    exit_code = run_pytest(args.extra + extra_pytest, include_nightly=args.full)

    if exit_code == 0:
        logger.info("✓ V9 基线回归全部通过")
    else:
        logger.error("✗ V9 基线回归未通过 (exit_code=%d)", exit_code)
    return exit_code if exit_code in (0, 1) else 1


if __name__ == "__main__":
    sys.exit(main())
