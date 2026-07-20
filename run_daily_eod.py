# -*- coding: utf-8 -*-
"""
盘后报告自动运行入口
====================

每个交易日 16:00 由 Windows Task Scheduler 自动触发,
非交易日自动跳过, 收盘后生成持仓盈亏报告 (含对冲明细 + 持仓明细 + 收益 + 第二天交易计划)。

工作流程:
    1. 检查今日是否为 A 股交易日 (akshare 交易日历)
    2. 如非交易日, 写入跳过日志并退出
    3. 切换到项目根目录, 调用 generate_daily_report.main()
    4. 输出报告路径 (Markdown + JSON)

用法:
    python run_daily_eod.py                 # 今日
    python run_daily_eod.py 2026-07-09       # 指定日期 (用于手动补生成)

注册定时任务 (管理员 PowerShell):
    .\\register_eod_task.ps1
"""
from __future__ import annotations

import os
import sys
from datetime import datetime
from pathlib import Path

# ============================================================
# 路径初始化
# ============================================================
SCRIPT_DIR = Path(__file__).resolve().parent
os.chdir(SCRIPT_DIR)
sys.path.insert(0, str(SCRIPT_DIR))

# ============================================================
# 日志
# ============================================================
LOG_DIR = SCRIPT_DIR / "logs"
LOG_DIR.mkdir(exist_ok=True)
LOG_FILE = LOG_DIR / f"run_daily_eod_{datetime.now():%Y%m%d}.log"


def log(msg: str) -> None:
    """输出带时间戳的日志"""
    ts = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    line = f"[{ts}] {msg}"
    print(line, flush=True)
    try:
        with open(LOG_FILE, 'a', encoding='utf-8') as f:
            f.write(line + "\n")
    except Exception:
        pass


def main() -> int:
    """主入口: 判断交易日 → 调用 generate_daily_report

    Returns:
        0 = 成功 / 跳过, 非 0 = 错误
    """
    # 支持命令行参数: 指定报告日期
    if len(sys.argv) > 1:
        report_date = sys.argv[1]
    else:
        report_date = datetime.now().strftime('%Y-%m-%d')

    log("=" * 70)
    log(f"盘后报告自动运行入口启动 (报告日期: {report_date})")
    log("=" * 70)

    # 1. 检查交易日
    try:
        from utils.trade_calendar import is_trading_day
        if not is_trading_day(report_date):
            log(f"⏭️  {report_date} 非交易日, 跳过报告生成")
            return 0
        log(f"✅ {report_date} 是交易日, 继续生成报告")
    except Exception as e:
        log(f"⚠️ 交易日历检查失败 ({e}), 继续生成报告 (降级模式)")
        # 不阻止报告生成, 仅警告

    # 2. 调用 generate_daily_report.main()
    try:
        from generate_daily_report import main as gen_report_main
        # 通过 sys.argv 传递报告日期给 generate_daily_report
        sys.argv = ['generate_daily_report.py', report_date]
        report = gen_report_main()
        if report is None:
            log("❌ 报告生成失败 (返回 None)")
            return 1

        # 输出报告路径
        md_path = SCRIPT_DIR / "v7.5_institutional" / "reports" / f"daily_pnl_report_{report_date}.md"
        json_path = SCRIPT_DIR / "v7.5_institutional" / "reports" / f"daily_pnl_report_{report_date}.json"

        log(f"✅ 报告生成成功:")
        log(f"   Markdown: {md_path}")
        log(f"   JSON:     {json_path}")

        # 第二天交易计划摘要
        next_day_plan = report.get('next_day_plan', {})
        if next_day_plan and not next_day_plan.get('error'):
            nd = next_day_plan.get('next_trading_day', '')
            wd = next_day_plan.get('weekday', '')
            phase = next_day_plan.get('phase', {}).get('name_cn', '')
            day_idx = next_day_plan.get('phase', {}).get('day_index', 0)
            daily_capital = next_day_plan.get('stock_etf_account', {}).get('daily_capital', 0)
            log(f"   下一交易日: {nd} ({wd})")
            log(f"   阶段: {phase} (第 {day_idx} 天)")
            log(f"   当日预算: {daily_capital:,.2f} 元")

        # 3. 自动把 LLM 决策灌入次日计划
        try:
            next_trading_day = ''
            if isinstance(next_day_plan, dict):
                next_trading_day = next_day_plan.get('next_trading_day', '')
            if not next_trading_day:
                from datetime import datetime as _dt, timedelta as _td
                _today = _dt.strptime(report_date, '%Y-%m-%d')
                _next = _today + _td(days=1)
                while _next.weekday() >= 5:
                    _next += _td(days=1)
                next_trading_day = _next.strftime('%Y-%m-%d')

            import subprocess
            _script = SCRIPT_DIR / 'apply_llm_decisions_to_plan.py'
            _result = subprocess.run(
                [sys.executable, str(_script), report_date, next_trading_day],
                capture_output=True,
                text=True,
                encoding='utf-8',
                errors='replace',
                timeout=60,
            )
            if _result.returncode == 0:
                _out = (_result.stdout or '').strip().splitlines()
                if _out:
                    log(f"[OK] LLM决策已自动写入次日计划: {_out[-1]}")
                else:
                    log(f"[OK] LLM决策已自动写入次日计划: {next_trading_day}")
            else:
                _err = (_result.stderr or '').strip().splitlines()
                _msg = _err[-1] if _err else 'unknown error'
                log(f"[WARN] LLM决策写入失败: {_msg}")
        except Exception as _e:
            log(f"[WARN] LLM决策写入异常: {_e}")

        # 4. 为下一交易日预生成 LLM 盘中决策初始状态
        try:
            _intraday_script = SCRIPT_DIR / 'v7.5_institutional' / 'llm_intraday_decision_engine.py'
            _intraday_result = subprocess.run(
                [sys.executable, str(_intraday_script), next_trading_day, '--mode', 'eod'],
                capture_output=True,
                text=True,
                encoding='utf-8',
                errors='replace',
                timeout=60,
            )
            if _intraday_result.returncode == 0:
                _out = (_intraday_result.stdout or '').strip().splitlines()
                if _out:
                    log(f"[OK] 下一交易日盘中决策引擎已就绪: {_out[-1]}")
                else:
                    log(f"[OK] 下一交易日盘中决策引擎已就绪: {next_trading_day}")
            else:
                _err = (_intraday_result.stderr or '').strip().splitlines()
                _msg = _err[-1] if _err else 'unknown error'
                log(f"[WARN] 盘中决策引擎初始化失败: {_msg}")
        except Exception as _e:
            log(f"[WARN] 盘中决策引擎初始化异常: {_e}")

        # 5. ETF 资金流向盘后报告
        try:
            _etf_script = SCRIPT_DIR / 'v7.5_institutional' / 'etf_flow_monitor.py'
            _etf_result = subprocess.run(
                [sys.executable, str(_etf_script), '--mode', 'eod'],
                capture_output=True,
                text=True,
                encoding='utf-8',
                errors='replace',
                timeout=120,
            )
            if _etf_result.returncode == 0:
                _out = (_etf_result.stdout or '').strip().splitlines()
                if _out:
                    log(f"[OK] ETF资金流向报告已生成: {_out[-1]}")
                else:
                    log(f"[OK] ETF资金流向报告已生成: {next_trading_day}")
            else:
                _err = (_etf_result.stderr or '').strip().splitlines()
                _msg = _err[-1] if _err else 'unknown error'
                log(f"[WARN] ETF资金流向报告生成失败: {_msg}")
        except Exception as _e:
            log(f"[WARN] ETF资金流向报告生成异常: {_e}")

        # 6. 将当日 ETF 信号摘要写入当日 trade_plan
        try:
            import json
            from pathlib import Path as _P
            _reports_dir = SCRIPT_DIR / 'v7.5_institutional' / 'reports'
            _plan_dir = SCRIPT_DIR / 'v7.5_institutional' / 'trade_plans'
            _etf_candidates = sorted(_reports_dir.glob('etf_flow_report_*.json'), reverse=True)
            _plan_path = _plan_dir / f"trade_plan_{report_date.replace('-', '')}.json"
            if _etf_candidates and _plan_path.exists():
                _etf_json = _etf_candidates[0]
                with open(_etf_json, 'r', encoding='utf-8') as _f:
                    _etf_data = json.load(_f)
                with open(_plan_path, 'r', encoding='utf-8') as _f:
                    _plan = json.load(_f)
                _plan['etf_flow_signals'] = {
                    'updated_at': datetime.now().isoformat(),
                    'report_date': _etf_data.get('report_date'),
                    'market_stance': _etf_data.get('market_stance'),
                    'total_flow_yi': _etf_data.get('total_flow_yi', 0),
                    'signal_count': _etf_data.get('signal_count', 0),
                    'strong_buy_signals': [
                        {
                            'code': s.get('code'),
                            'name': s.get('name'),
                            'net_flow_yi': s.get('net_flow_yi'),
                            'confidence': s.get('confidence'),
                            'signal_type': s.get('signal_type'),
                        }
                        for s in _etf_data.get('signals', [])[:8]
                        if '加仓' in s.get('signal_type', '')
                    ],
                    'strong_sell_signals': [
                        {
                            'code': s.get('code'),
                            'name': s.get('name'),
                            'net_flow_yi': s.get('net_flow_yi'),
                            'confidence': s.get('confidence'),
                            'signal_type': s.get('signal_type'),
                        }
                        for s in _etf_data.get('signals', [])[:8]
                        if '减仓' in s.get('signal_type', '')
                    ],
                }
                with open(_plan_path, 'w', encoding='utf-8') as _f:
                    json.dump(_plan, _f, ensure_ascii=False, indent=2)
                log(f"[OK] 当日ETF信号已写入 trade_plan_{report_date.replace('-', '')}.json")
        except Exception as _e:
            log(f"[WARN] 写入当日ETF信号失败: {_e}")

        return 0

    except Exception as e:
        import traceback
        log(f"❌ 报告生成异常: {e}")
        log(traceback.format_exc())
        return 2


if __name__ == '__main__':
    exit_code = main()
    sys.exit(exit_code)
