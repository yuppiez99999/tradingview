"""
回测模式 — 委托统一入口的真实组合回测引擎。

P0-4 修复 (2026-09-04): 原实现两条分支均为死代码 —— 快速回测缺 returns
必填参数抛 TypeError (不被 except ImportError 捕获), 备用分支引用不存在
的 BacktestEngine.run_backtest / run_backtest() 方法。

本文件为 cli/modes 兼容薄壳: 直接委托 `量化策略系统_统一入口_v8.6.py --backtest`
运行 backtests/ 下的真实组合回测 (portfolio_v510_backtest / portfolio_etf200w_backtest)。
"""
from __future__ import annotations

import os
import subprocess
import sys

from core.context import BASE_DIR


def run_backtest(args) -> None:  # noqa: ARG001  # 参数契约保留, 当前无需额外选项
    """回测模式 - 委托真实组合回测引擎 (backtests/portfolio_*_backtest.py)"""
    print("\n📊 运行回测 (组合回测引擎)")
    print("=" * 70)

    candidates = [
        ("v5.10 组合 (23 标的, 阈值再平衡)",
         "v510_portfolio_20260903", "portfolio_v510_backtest.py"),
        ("ETF200 万 (6 ETF, 月度再平衡)",
         "etf200w_20260903", "portfolio_etf200w_backtest.py"),
    ]
    finished = []
    for name, folder, script in candidates:
        script_path = os.path.join(str(BASE_DIR), "backtests", folder, script)
        if not os.path.isfile(script_path):
            print(f"❌ 回测脚本缺失: {script_path}")
            continue
        print(f"▶ {name}: {script_path}")
        try:
            proc = subprocess.run(
                [sys.executable, script_path],
                capture_output=True,
                encoding="utf-8",
                errors="replace",
                timeout=1800,
                cwd=os.path.dirname(script_path),
            )
            tail = "\n".join((proc.stdout or "").strip().splitlines()[-8:])
            if proc.returncode == 0:
                print(f"✅ {name} 完成\n{tail or '(无输出)'}")
                finished.append(script_path)
            else:
                err_tail = "\n".join((proc.stderr or "").strip().splitlines()[-8:])
                print(f"❌ {name} 失败 (rc={proc.returncode})\n{err_tail or '(无错误输出)'}")
        except Exception as exc:  # noqa: BLE001  # 单个脚本失败不影响其它回测
            print(f"❌ {name} 执行异常: {exc}")

    if finished:
        print("\n✅ 回测完成, 产物位于各回测目录 (*_summary.json / *_trades.csv / *_equity.csv)")
    else:
        print("\n❌ 回测未产出任何结果")
