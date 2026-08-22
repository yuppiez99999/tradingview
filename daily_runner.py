"""
每日自动运行主程序 - 量化策略 v4.1

每天早上7:00由Windows计划任务自动触发，按顺序执行:
  1. 数据更新    — 下载最新行情数据
  2. 快速回测    — 验证策略参数有效性
  3. 每日报告    — 生成含AI分析的完整日报
  4. 模拟交易(可选) — 启动盘中模拟交易

用法:
  python daily_runner.py                    # 完整流程（数据+回测+报告）
  python daily_runner.py --skip-download    # 跳过数据下载
  python daily_runner.py --skip-backtest   # 跳过回测
  python daily_runner.py --trading          # 报告后启动模拟交易
  python daily_runner.py --report-only      # 仅生成报告
"""

from __future__ import annotations

# === 清除代理设置（必须最早执行） ===
import os

for k in list(os.environ.keys()):
    if 'proxy' in k.lower():
        del os.environ[k]
os.environ['NO_PROXY'] = '*'

import argparse
import json
import logging
import sys
import traceback
from collections.abc import Callable
from datetime import datetime
from typing import Any, Optional

# Windows编码修复
if sys.platform == 'win32':
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    sys.stderr.reconfigure(encoding='utf-8', errors='replace')

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
# Wave 3 第三阶段: 改用 utils.path_config.setup_sys_path() 统一管理
sys.path.insert(0, BASE_DIR)  # bootstrap: 确保 utils 包可导入
from utils.path_config import setup_sys_path  # noqa: E402

setup_sys_path()  # noqa: E402  # 统一注入 v8.3 根 / v8.3 src / utils

LOG_DIR = os.path.join(BASE_DIR, 'logs')
REPORTS_DIR = os.path.join(os.path.dirname(BASE_DIR), '每日报告归档')

# ---- 日志配置 ----
os.makedirs(LOG_DIR, exist_ok=True)
os.makedirs(REPORTS_DIR, exist_ok=True)

log_file = os.path.join(LOG_DIR, f'daily_runner_{datetime.now().strftime("%Y%m%d")}.log')

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    handlers=[
        logging.FileHandler(log_file, encoding='utf-8'),
        logging.StreamHandler(sys.stdout),
    ]
)
logger = logging.getLogger('DailyRunner')


class StepResult:
    """单步执行结果"""
    def __init__(self, name: str):
        self.name = name
        self.success = False
        self.start_time: Optional[datetime] = None
        self.end_time: Optional[datetime] = None
        self.output = ''
        self.error = ''
        self.duration_seconds = 0

    @property
    def duration_str(self) -> str:
        s = self.duration_seconds
        if s < 60:
            return f'{s:.1f}s'
        return f'{s//60}m{s%60:.0f}s'

    @property
    def status_emoji(self) -> str:
        return 'OK' if self.success else 'FAIL'


def run_step(name: str, func: Callable[..., Any], *args: Any, **kwargs: Any) -> StepResult:
    """执行单个步骤，捕获异常"""
    result = StepResult(name)
    logger.info(f'>>> 开始执行: {name}')
    result.start_time = datetime.now()

    try:
        output = func(*args, **kwargs)
        result.success = True
        result.output = output or ''
        result.end_time = datetime.now()
        result.duration_seconds = (result.end_time - result.start_time).total_seconds()
        logger.info(f'<<< {name} 完成 ({result.status_emoji}, 耗时{result.duration_str})')
        return result
    except Exception as e:  # noqa: BLE001  # fail-safe, 待后续精确化
        result.end_time = datetime.now()
        result.duration_seconds = (result.end_time - result.start_time).total_seconds()
        result.error = str(e)
        result.end_time = datetime.now()
        result.duration_seconds = (result.end_time - result.start_time).total_seconds()
        logger.error(f'!!! {name} 失败 ({result.status_emoji}): {e}\n'
                     f'{traceback.format_exc()}')
        return result


# ============================================================
# 各步骤实现
# ============================================================

def step_update_data() -> str:
    """
    步骤1：更新历史数据
    通过iFinD或新浪API下载最新行情数据到本地缓存
    """
    logger.info('[数据更新] 尝试通过 iFinD 下载数据...')

    # 方案A: 使用 data_download.py 的逻辑
    try:
        import subprocess
        r = subprocess.run(
            [sys.executable, os.path.join(BASE_DIR, 'data_download.py')],
            capture_output=True, text=True, encoding='utf-8', errors='replace',
            timeout=120,
            cwd=BASE_DIR
        )
        if r.returncode == 0:
            logger.info(f'[数据更新] data_download.py 执行成功\n{r.stdout[-500:] if len(r.stdout) > 500 else r.stdout}')
            return 'iFinD数据更新完成'
        else:
            logger.warning(f'[数据更新] data_download.py 返回非零: {r.returncode}, stderr={r.stderr[:200]}')
    except FileNotFoundError:
        pass
    except subprocess.TimeoutExpired:
        logger.warning('[数据更新] data_download.py 超时')
    except Exception as e:  # noqa: BLE001  # fail-safe, 待后续精确化
        logger.warning(f'[数据更新] data_download.py 异常: {e}')

    # 方案B: 检查缓存文件是否需要刷新（检查最后修改时间）
    cache_dir = os.path.join(BASE_DIR, 'data', 'cache')
    if os.path.isdir(cache_dir):
        parquet_files = [
            f for f in os.listdir(cache_dir) if f.endswith('.parquet')
        ]
        if parquet_files:
            latest = max(
                os.path.getmtime(os.path.join(cache_dir, f))
                for f in parquet_files
            )
            latest_dt = datetime.fromtimestamp(latest)
            age_days = (datetime.now() - latest_dt).days
            if age_days <= 2:
                logger.info(f'[数据更新] 缓存较新 ({age_days}天前)，跳过下载')
                return f'使用本地缓存 ({age_days}天前)'

    # 方案C: 尝试从新浪API补充最新数据
    try:
        from sina_api_helper import update_latest_quotes
        count = update_latest_quotes()
        return f'新浪API更新了{count}只标的的数据'
    except ImportError:
        pass

    logger.info('[数据更新] 数据源均不可用，使用现有缓存继续')
    return '使用现有缓存'


def step_fast_backtest() -> str:
    """
    步骤2：快速回测验证
    运行最近252个交易日的快速回测，确认策略参数仍然有效
    """
    logger.info('[快速回测] 开始运行 fast_backtest...')

    import subprocess
    r = subprocess.run(
        [sys.executable, os.path.join(BASE_DIR, 'fast_backtest.py')],
        capture_output=True, text=True, encoding='utf-8', errors='replace',
        timeout=180,
        cwd=BASE_DIR
    )

    output = r.stdout + ('\n[STDERR]' + r.stderr if r.stderr else '')

    if r.returncode == 0:
        # 提取关键指标
        lines = output.strip().split('\n')
        summary_lines = [line for line in lines if any(kw in line for kw in
            ['年化', '回撤', '胜率', '夏普', '收益'])]
        key_info = '\n'.join(summary_lines[-10:]) if summary_lines else output[-300:]
        logger.info(f'[快速回测] 成功\n{key_info}')
        return f'回测完成:\n{key_info}'
    else:
        logger.warning(f'[快速回测] 异常退出({r.returncode}): {r.stderr[:200]}')
        # 回测失败不阻塞后续流程
        return f'回测异常(r={r.returncode}), 继续后续步骤'


def step_daily_report(enable_ai: bool = True) -> str:
    """
    步骤3：生成每日报告
    包含实时行情、持仓分析、风控状态、AI智能分析、事件驱动因子
    """
    logger.info('[每日报告] 开始生成 daily_report...')

    try:
        from daily_report import generate_daily_report
        report_date = datetime.now().strftime('%Y-%m-%d')

        # 保存到日期子目录
        date_dir = os.path.join(REPORTS_DIR, report_date)
        os.makedirs(date_dir, exist_ok=True)
        report_path = os.path.join(date_dir, f'daily_{datetime.now().strftime("%H%M%S")}.txt')

        content = generate_daily_report(
            portfolio_file=os.path.join(BASE_DIR, 'config', 'portfolio.yaml'),
            report_file=report_path,
            enable_ai_analysis=enable_ai
        )

        # 同时写入根目录的 daily_report.txt (方便查看)
        root_report = os.path.join(BASE_DIR, 'daily_report.txt')
        with open(root_report, 'w', encoding='utf-8') as f:
            f.write(content)

        lines = content.count('\n') + 1
        size_kb = len(content.encode('utf-8')) / 1024
        logger.info(f'[每日报告] 完成: {report_path} ({lines}行, {size_kb:.1f}KB)')
        return f'报告已生成 ({size_kb:.1f}KB)'

    except ImportError as e:
        logger.error(f'[每日报告] 导入失败: {e}')
        raise
    except Exception as e:  # noqa: BLE001  # fail-safe, 待后续精确化
        logger.error(f'[每日报告] 生成失败: {e}')
        raise


def step_trendcast_predict() -> str:
    """
    步骤2.5：TrendCast Pro AI预测 + 审计验证
    调用 TrendCast Pro API 获取14只核心持仓的方向预测，
    记录到审计系统，并回溯验证已到期的历史预测。
    """
    logger.info('[TrendCast] 开始获取 AI 预测信号...')

    lines = []
    try:
        from trendcast_audit import TrendCastAudit
        from trendcast_client import TrendCastClient

        client = TrendCastClient()
        audit = TrendCastAudit()

        # 1. 健康检查
        health = client.health_check()
        if "error" in health:
            logger.warning(f'[TrendCast] API 服务不可用: {health["error"]}')
            return "TrendCast API 不可用，跳过预测"

        logger.info(f'[TrendCast] API 健康: {json.dumps(health, ensure_ascii=False)}')

        # 2. 批量预测全部核心持仓
        summary = client.get_portfolio_summary()
        if "error" in summary:
            logger.warning(f'[TrendCast] 批量预测失败: {summary["error"]}')
            return f"预测失败: {summary['error']}"

        predictions = summary.get("predictions", [])
        sector_signals = summary.get("sector_signals", {})

        # 3. 记录预测到审计系统
        audit_count = 0
        for p in predictions:
            symbol = p.get("symbol", "")
            directions = p.get("directions", {})
            for horizon, direction in directions.items():
                if direction not in ("看涨", "看跌"):
                    continue
                audit.record_prediction(
                    symbol=symbol,
                    horizon=horizon,
                    direction=direction,
                    probability=0.65,  # 模型默认置信度
                    source="trendcast_pro",
                )
                audit_count += 1

        # 4. 回溯验证已到期预测
        audit.verify_predictions()
        stats = audit.get_stats()

        # 5. 组装摘要
        lines.append(f"AI 预测完成: {len(predictions)} 只标的, {audit_count} 条预测记录")
        lines.append(f"审计: 总记录 {stats['total_records']}, 已验证 {stats['verified']}, "
                     f"命中率 {stats['hit_rate']:.1%}")

        # 板块偏向
        for sector, sig in sector_signals.items():
            lines.append(f"  {sector}: {sig['signal']} (bias={sig['bias']:.2f}, "
                         f"看涨{sig['bullish']}/看跌{sig['bearish']})")

        # 漂移检测
        verified_count = stats["verified"]
        if verified_count > 10 and stats["hit_rate"] < 0.5:
            lines.append("⚠ 漂移告警: 整体命中率 < 50%，建议关注模型性能")

        result = "\n".join(lines)
        logger.info(f'[TrendCast] {result.replace(chr(10), " | ")}')
        return result

    except ImportError as e:
        logger.warning(f'[TrendCast] 模块导入失败: {e}')
        return f"TrendCast 模块未安装: {e}"
    except Exception as e:  # noqa: BLE001  # fail-safe, 待后续精确化
        logger.error(f'[TrendCast] 异常: {e}\n{traceback.format_exc()}')
        return f"TrendCast 预测异常: {e}"


def step_start_trading() -> str:
    """
    步骤4(可选)：启动模拟交易
    在报告中提到后，启动盘中模拟交易系统
    仅在交易日且当前时间在交易时段内才实际启动
    """
    now = datetime.now()
    weekday = now.weekday()

    # 周末不启动
    if weekday >= 5:
        logger.info('[模拟交易] 周末，跳过启动')
        return '周末，跳过'

    # 非交易时段仅记录
    current_time = now.time()
    morning_start = __import__('datetime').time(9, 15)
    afternoon_end = __import__('datetime').time(15, 5)

    if current_time < morning_start:
        logger.info(f'[模拟交易] 未到开盘时间({now.strftime("%H:%M")})，跳过')
        return f'未到开盘时间({now.strftime("%H:%M")})'
    elif current_time > afternoon_end:
        logger.info(f'[模拟交易] 已收盘({now.strftime("%H:%M")})，跳过')
        return f'已收盘({now.strftime("%H:%M")})'

    # 启动 local_simulation (不需要外部数据源的方案)
    try:
        import subprocess
        proc = subprocess.Popen(
            [sys.executable, os.path.join(BASE_DIR, 'local_simulation.py')],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            cwd=BASE_DIR, creationflags=subprocess.CREATE_NEW_PROCESS_GROUP
        )
        logger.info(f'[模拟交易] 已启动 (PID={proc.pid})')
        return f'local_simulation 已启动 (PID={proc.pid})'
    except Exception as e:  # noqa: BLE001  # fail-safe, 待后续精确化
        logger.error(f'[模拟交易] 启动失败: {e}')
        return f'启动失败: {e}'


# ============================================================
# 主流程
# ============================================================

def run_daily_pipeline(skip_download: bool = False, skip_backtest: bool = False,
                        enable_trading: bool = False, enable_ai: bool = True,
                        enable_trendcast: bool = True, report_only: bool = False) -> bool:
    """运行完整的每日流程"""

    total_start = datetime.now()
    logger.info('='*70)
    logger.info(f'  量化策略每日自动任务 v4.1 | {total_start.strftime("%Y-%m-%d %H:%M:%S")}')
    logger.info('='*70)

    results = []

    if report_only:
        # 仅报告模式
        r = run_step('每日报告生成', step_daily_report, enable_ai)
        results.append(r)
    else:
        # 完整模式

        # 步骤1: 数据更新
        if not skip_download:
            r = run_step('数据更新', step_update_data)
            results.append(r)
        else:
            logger.info('[数据更新] --skip-download, 跳过')
            sr = StepResult('数据更新')
            sr.success = True
            sr.output = '(跳过)'
            results.append(sr)

        # 步骤2: 快速回测
        if not skip_backtest:
            r = run_step('快速回测', step_fast_backtest)
            results.append(r)
        else:
            logger.info('[快速回测] --skip-backtest, 跳过')
            sr = StepResult('快速回测')
            sr.success = True
            sr.output = '(跳过)'
            results.append(sr)

        # 步骤2.5: TrendCast Pro AI预测 + 审计（可选）
        if enable_trendcast:
            r = run_step('TrendCast AI预测', step_trendcast_predict)
            results.append(r)
        else:
            logger.info('[TrendCast] 已禁用 (--no-trendcast)')

        # 步骤3: 每日报告
        r = run_step('每日报告生成', step_daily_report, enable_ai)
        results.append(r)

        # 步骤4(可选): 模拟交易
        if enable_trading:
            r = run_step('模拟交易启动', step_start_trading)
            results.append(r)

    # 汇总
    total_duration = (datetime.now() - total_start).total_seconds()
    success_count = sum(1 for r in results if r.success)
    fail_count = len(results) - success_count

    logger.info('')
    logger.info('='*70)
    logger.info(f'  任务完成 | 总耗时: {total_duration/60:.1f}分钟 | '
                f'成功: {success_count}/{len(results)} | 失败: {fail_count}')
    logger.info('-'*70)

    for r in results:
        status_icon = '[OK]' if r.success else '[!!]'
        err_detail = f' | 错误: {r.error[:60]}' if r.error else ''
        logger.info(f'  {status_icon} {r.name:<20s} {r.duration_str:<10s}{err_detail}')

    logger.info('='*70)

    # 输出摘要到独立文件
    summary_path = os.path.join(LOG_DIR, f'summary_{datetime.now().strftime("%Y%m%d")}.txt')
    with open(summary_path, 'w', encoding='utf-8') as f:
        f.write(f'每日任务摘要 - {datetime.now().strftime("%Y-%m-%d %H:%M")}\n')
        f.write(f'总耗时: {total_duration/60:.1f}分钟\n')
        f.write(f'成功: {success_count}/{len(results)}\n\n')
        for r in results:
            f.write(f'{"OK" if r.success else "FAIL"}\t{r.name}\t{r.duration_str}\n')
            if r.error:
                f.write(f'\tError: {r.error}\n')
            if r.output:
                out_lines = r.output.split('\n')
                for line in out_lines[:5]:
                    f.write(f'\t> {line}\n')

    return fail_count == 0


# ============================================================
# 入口
# ============================================================

def main() -> None:
    parser = argparse.ArgumentParser(
        description='量化策略 v4.1 每日自动运行主程序',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  python daily_runner.py                  完整流程（数据+回测+报告+AI预测）
  python daily_runner.py --skip-download  跳过数据下载
  python daily_runner.py --skip-backtest  跳过回测
  python daily_runner.py --trading         报告后启动模拟交易
  python daily_runner.py --report-only     仅生成报告
  python daily_runner.py --no-ai           禁用AI分析模块
  python daily_runner.py --no-trendcast    禁用 TrendCast AI 预测（默认开启）
        """
    )
    parser.add_argument('--skip-download', action='store_true',
                        help='跳过数据更新步骤')
    parser.add_argument('--skip-backtest', action='store_true',
                        help='跳过快速回测步骤')
    parser.add_argument('--trading', action='store_true',
                        help='报告生成后启动模拟交易')
    parser.add_argument('--report-only', action='store_true',
                        help='仅生成每日报告')
    parser.add_argument('--no-ai', action='store_true',
                        help='禁用AI分析（YiZhao增强模块）')
    parser.add_argument('--trendcast', action='store_true', default=True,
                        help='启用 TrendCast Pro AI 预测信号（默认开启，需先启动 API 服务）')
    parser.add_argument('--no-trendcast', action='store_false', dest='trendcast',
                        help='禁用 TrendCast Pro AI 预测信号')

    args = parser.parse_args()

    success = run_daily_pipeline(
        skip_download=args.skip_download,
        skip_backtest=args.skip_backtest,
        enable_trading=args.trading,
        enable_ai=not args.no_ai,
        enable_trendcast=args.trendcast,
        report_only=args.report_only,
    )

    sys.exit(0 if success else 1)


if __name__ == '__main__':
    main()
