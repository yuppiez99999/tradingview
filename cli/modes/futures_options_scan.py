# -*- coding: utf-8 -*-
"""
期货期权扫描模式
期货/期权/套利全扫描
"""

from __future__ import annotations

from core.context import (
    BASE_DIR,
    logger,
    ProgressIndicator,
)

from utils.cli_helpers import log_execution_summary
import time


def run_futures_options_scan(args):
    """期货期权扫描模式"""
    print("\n📊 期货期权扫描模式")
    print("=" * 70)

    start_time = time.time()

    try:
        from quant_modules.futures_options_scanner import run_full_scan
        print("\n[INFO] 开始期货/期权/套利全扫描...")
        result = run_full_scan(use_wind=True, use_deepseek=False)

        # 显示结果摘要
        num_arb = len(result.get('arbitrage_signals', []))
        num_futures = len(result.get('futures', {}))
        num_options = len(result.get('options', {}))

        print(f"\n[OK] 扫描完成!")
        print(f"  - 期货品种: {num_futures} 个")
        print(f"  - 期权品种: {num_options} 个")
        print(f"  - 套利机会: {num_arb} 个")

        if num_arb > 0:
            print("\n[ARBITRAGE] 套利机会:")
            for i, signal in enumerate(result['arbitrage_signals'][:5], 1):
                print(f"  {i}. {signal}")

        duration = time.time() - start_time
        log_execution_summary("期货期权扫描", duration, True, f"期货:{num_futures} 期权:{num_options} 套利:{num_arb}")
        return result

    except ImportError as e:
        print(f"\n❌ 期货期权模块导入失败: {e}")
        print("💡 请确保 quant_modules/futures_options_scanner.py 存在")
        duration = time.time() - start_time
        log_execution_summary("期货期权扫描", duration, False, str(e))
        return None
    except Exception as e:
        print(f"\n❌ 期货期权扫描失败: {e}")
        import traceback
        traceback.print_exc()
        duration = time.time() - start_time
        log_execution_summary("期货期权扫描", duration, False, str(e))
        return None
