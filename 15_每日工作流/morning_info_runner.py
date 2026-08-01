# -*- coding: utf-8 -*-
"""
晨间信息采集工作流 — 统一调度信息采集类报告
================================================
被 run_daily_morning.py --phase info 调用。

7 项任务:
  1. 晨间行情摘要 (morning_market_fetcher, 含 DeepSeek AI)
  2. 康波周期分析 (28 项目内 v8.3_institutional/src/macro/kondratiev.py)
  3. 实时ETF资金流向 (11_量化策略/engine/etf_flow.py)
  4. 舆情综合日报 + 动力煤舆情日报 (28 项目内 sentiment_hub.run_all)
  5. CNEMC 空气质量日报 (15_每日工作流/cnemc_air_quality_runner)
  6. iFinD 自动标的研判 (复制源文件或占位)
  7. 棉花加仓方案归档 (复制源文件)

用法:
  python morning_info_runner.py                # 当日
  python morning_info_runner.py --force        # 强制重新生成
  python morning_info_runner.py --date 2026-07-27
"""

import os
import sys
import shutil
import argparse
import glob as _glob
from pathlib import Path
from datetime import datetime

# UTF-8 编码修复
if sys.platform == 'win32':
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    sys.stderr.reconfigure(encoding='utf-8', errors='replace')

# ═══════════════════════════════════════════════════════════════
# 路径常量
# ═══════════════════════════════════════════════════════════════
SCRIPT_DIR = Path(__file__).resolve().parent                    # 28/15_每日工作流
PROJECT_ROOT = SCRIPT_DIR.parent                                # 28-终极量化交易系统8.4
BASE_ROOT = PROJECT_ROOT.parent                                 # e:\各种PY程序
ARCHIVE_DIR = PROJECT_ROOT / "每日报告归档"                         # 项目内归档根

# 跨目录复用的模块路径
WORKFLOW_15 = BASE_ROOT / "15_每日工作流"                       # 晨间行情/CNEMC/DeepSeek 摘要
STRATEGY_11 = BASE_ROOT / "11_量化策略"                          # ETF 资金流向

# 28 项目内模块
V83_SRC = PROJECT_ROOT / "v8.3_institutional" / "src"

# 将复用模块加入 sys.path
for _p in (str(WORKFLOW_15), str(STRATEGY_11), str(V83_SRC)):
    if _p not in sys.path:
        sys.path.insert(0, _p)


def _archive_today(target_date: str) -> Path:
    """返回归档目录, 不存在则创建"""
    d = ARCHIVE_DIR / target_date
    d.mkdir(parents=True, exist_ok=True)
    return d


def _exists_nonempty(path: Path, min_size: int = 500) -> bool:
    """检查文件存在且非空"""
    return path.is_file() and path.stat().st_size > min_size


# ═══════════════════════════════════════════════════════════════
# 任务 1: 晨间行情摘要 (含 DeepSeek AI)
# ═══════════════════════════════════════════════════════════════
def task_morning_market(archive: Path, target_date: str, force: bool) -> bool:
    """调用 morning_market_fetcher.main() 生成晨间行情摘要"""
    date_short = target_date.replace('-', '')
    out_file = archive / f"晨间行情摘要_{date_short}.md"
    if _exists_nonempty(out_file) and not force:
        print(f"  ✅ 晨间行情摘要已存在, 跳过 ({out_file.stat().st_size/1024:.1f} KB)")
        return True
    try:
        from morning_market_fetcher import main as market_main
        result = market_main(output_dir=str(archive))
        ok = bool(result and result.get("path"))
        if ok:
            print("  ✅ 晨间行情摘要已生成")
        else:
            print("  ⚠️ 晨间行情数据采集完成, 但未生成报告")
        return ok
    except Exception as e:
        print(f"  ❌ 晨间行情采集失败: {e}")
        return False


# ═══════════════════════════════════════════════════════════════
# 任务 2: 康波周期分析 (28 项目内模块)
# ═══════════════════════════════════════════════════════════════
def task_kondratiev(archive: Path, target_date: str, force: bool) -> bool:
    """调用 28 项目内 KondratievCycleAnalyzer.generate_report()"""
    date_short = target_date.replace('-', '')
    out_file = archive / f"康波周期分析_{date_short}.md"
    if _exists_nonempty(out_file) and not force:
        print(f"  ✅ 康波周期分析已存在, 跳过 ({out_file.stat().st_size/1024:.1f} KB)")
        return True
    try:
        from macro.kondratiev import KondratievCycleAnalyzer
        analyzer = KondratievCycleAnalyzer()
        report = analyzer.generate_report(save_dir=None)
        if not report or len(report) < 100:
            print("  ⚠️ 康波周期报告内容为空")
            return False
        out_file.write_text(report, encoding='utf-8')
        print(f"  ✅ 康波周期分析已生成 ({len(report)/1024:.1f} KB)")
        return True
    except Exception as e:
        print(f"  ❌ 康波周期生成失败: {e}")
        return False


# ═══════════════════════════════════════════════════════════════
# 任务 3: 实时 ETF 资金流向 (11_量化策略/engine/etf_flow.py)
# ═══════════════════════════════════════════════════════════════
def task_etf_flow(archive: Path, target_date: str, force: bool) -> bool:
    """调用 11_量化策略 ETFRealTimeTracker.run(archive_dir=...)"""
    date_short = target_date.replace('-', '')
    pattern = str(archive / f"实时ETF资金流向_{date_short}_*.md")
    existing = _glob.glob(pattern)
    if existing and not force:
        print("  ✅ ETF 资金流向已存在, 跳过")
        return True
    try:
        from engine.etf_flow import ETFRealTimeTracker
        tracker = ETFRealTimeTracker()
        tracker.run(archive_dir=str(archive))
        etf_files = [f for f in os.listdir(archive) if f.startswith("实时ETF资金流向_")]
        if etf_files:
            print(f"  ✅ ETF 资金流向已生成 ({len(etf_files)} 个文件)")
            return True
        print("  ⚠️ ETF 资金流向报告内容为空")
        return False
    except Exception as e:
        print(f"  ❌ ETF 资金流向生成失败: {e}")
        return False


# ═══════════════════════════════════════════════════════════════
# 任务 4: 舆情综合日报 + 动力煤舆情日报 (28 项目内 sentiment_hub)
# ═══════════════════════════════════════════════════════════════
def task_sentiment(archive: Path, target_date: str, force: bool) -> bool:
    """调用 28 项目内 sentiment_hub.run_all()"""
    date_short = target_date.replace('-', '')
    sentiment_md = archive / f"舆情综合日报_{date_short}.md"
    coal_md = archive / f"动力煤舆情日报_{date_short}.md"
    if _exists_nonempty(sentiment_md) and _exists_nonempty(coal_md) and not force:
        print("  ✅ 舆情报告已存在, 跳过")
        return True
    try:
        from nlp.sentiment_hub import run_all
        result = run_all(
            target_date=target_date,
            output_dir=str(archive),
            run_trend=True,
            run_coal=True,
            force=force,
        )
        return bool(result.get("ok"))
    except Exception as e:
        print(f"  ❌ 舆情监控失败: {e}")
        return False


# ═══════════════════════════════════════════════════════════════
# 任务 5: CNEMC 空气质量日报 (15_每日工作流/cnemc_air_quality_runner)
# ═══════════════════════════════════════════════════════════════
def task_cnemc(archive: Path, target_date: str, force: bool) -> bool:
    """调用 cnemc_air_quality_runner.generate_cnemc_report()"""
    date_short = target_date.replace('-', '')
    out_file = archive / f"空气质量CNEMC日报_{date_short}.md"
    if _exists_nonempty(out_file) and not force:
        print(f"  ✅ CNEMC 空气质量日报已存在, 跳过 ({out_file.stat().st_size/1024:.1f} KB)")
        return True
    try:
        from cnemc_air_quality_runner import generate_cnemc_report
        result = generate_cnemc_report(output_dir=str(archive), target_date=target_date)
        return bool(result.get("ok"))
    except Exception as e:
        print(f"  ❌ CNEMC 空气质量日报失败: {e}")
        return False


# ═══════════════════════════════════════════════════════════════
# 任务 6: iFinD 自动标的研判 (自动运行 ifind_auto_analysis.py)
# ═══════════════════════════════════════════════════════════════
def task_ifind_analysis(archive: Path, target_date: str, force: bool) -> bool:
    """自动运行 ifind_auto_analysis.py 生成 iFinD 研判报告"""
    date_short = target_date.replace('-', '')
    dst = archive / f"iFinD自动标的研判报告_{date_short}.md"
    src = BASE_ROOT / "ifind_auto_analysis_report.md"

    # 如果源文件存在且非空，直接复制归档
    if _exists_nonempty(src) and not force:
        shutil.copy2(src, dst)
        print(f"  ✅ iFinD 研判源文件已存在，已归档 ({src.stat().st_size/1024:.1f} KB)")
        return True

    # 运行 ifind_auto_analysis.py 生成报告
    print("  🔄 正在运行 ifind_auto_analysis.py 生成 iFinD 研判报告...")
    try:
        import subprocess
        import sys as python_sys

        # 构建命令：使用正确的 Python 解释器
        base_dir = PROJECT_ROOT  # 28-终极量化交易系统8.4
        script_path = base_dir / "research" / "ifind_auto_analysis.py"

        # portfolio.yaml 位于项目根目录的 configs/ 下
        portfolio_path = PROJECT_ROOT / "configs" / "portfolio.yaml"

        # 指定输出到归档目录
        output_path = str(dst)

        # 使用正确的 Python 可执行文件路径
        python_exe = r"C:\Users\Administrator\AppData\Local\Programs\Python\Python311\python.exe"
        if not os.path.exists(python_exe):
            python_exe = python_sys.executable

        cmd = [
            python_exe,
            str(script_path),
            "--portfolio", str(portfolio_path),
            "--output", output_path,
        ]

        # 设置环境变量，确保模块可导入
        env = os.environ.copy()
        env["PYTHONPATH"] = str(base_dir) + ";" + env.get("PYTHONPATH", "")

        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            cwd=str(base_dir),
            env=env,
            timeout=120,
        )

        if result.returncode == 0:
            # 直接检查输出到归档目录的文件
            if dst.is_file() and dst.stat().st_size > 500:
                print(f"  ✅ iFinD 研判报告生成成功 ({dst.stat().st_size/1024:.1f} KB)")
                return True
            else:
                print("  ⚠️ 执行成功但输出文件为空或不存在")
        else:
            print("  ❌ ifind_auto_analysis.py 执行失败:")
            print(f"     错误输出: {result.stderr[:500]}")
            # 即使执行失败，也尝试用生成的文件（如果有）
            if dst.is_file() and dst.stat().st_size > 0:
                print(f"  ℹ️ 使用部分生成的文件 ({dst.stat().st_size/1024:.1f} KB)")
                return True
    except FileNotFoundError:
        print("  ❌ 找不到 ifind_auto_analysis.py 脚本")
    except subprocess.TimeoutExpired:
        print("  ❌ ifind_auto_analysis.py 执行超时")
    except Exception as e:
        print(f"  ❌ 执行 ifind_auto_analysis.py 时发生异常: {e}")

    # 如果所有方法都失败，生成占位报告
    placeholder = f"""# iFinD 自动标的研判报告

**日期**: {target_date}
**状态**: iFinD 研判模块生成失败

> 注：iFinD 自动研判功能需要正常运行 ifind_auto_analysis.py 脚本。请检查：
> 1. portfolio.yaml 是否存在并包含持仓标的
> 2. iFinD MCP 连接是否正常配置
> 3. ifind-finance-data skill 是否正确安装

---
*本报告由 morning_info_runner.py 自动生成*
"""
    dst.write_text(placeholder, encoding='utf-8')
    print("  ⚠️ iFinD 研判报告生成失败，已生成占位报告")
    return True


# ═══════════════════════════════════════════════════════════════
# 任务 7: 棉花加仓方案归档 (复制源文件)
# ═══════════════════════════════════════════════════════════════
def task_cotton_archive(archive: Path, target_date: str, force: bool) -> bool:
    """复制棉花加仓方案源文件, 不存在则生成占位"""
    dst = archive / "棉花的加仓方案与期权保护策略_20260704.md"
    if _exists_nonempty(dst) and not force:
        print(f"  ✅ 棉花加仓方案已存在, 跳过 ({dst.stat().st_size/1024:.1f} KB)")
        return True
    src = BASE_ROOT / "棉花的加仓方案与期权保护策略_20260704.md"
    if src.is_file():
        shutil.copy2(src, dst)
        print(f"  ✅ 棉花加仓方案报告已归档 ({dst.stat().st_size/1024:.1f} KB)")
        return True
    placeholder = f"""# 棉花加仓方案与期权保护策略

**日期**: {target_date}
**状态**: 棉花加仓方案源文件未生成

---
*本报告由 morning_info_runner.py 自动生成*
"""
    dst.write_text(placeholder, encoding='utf-8')
    print("  ℹ️ 棉花报告源文件不存在, 已生成占位报告")
    return True


# ═══════════════════════════════════════════════════════════════
# 主调度
# ═══════════════════════════════════════════════════════════════
TASKS = [
    ("晨间行情摘要", task_morning_market),
    ("康波周期分析", task_kondratiev),
    ("实时ETF资金流向", task_etf_flow),
    ("舆情综合+动力煤", task_sentiment),
    ("CNEMC空气质量", task_cnemc),
    ("iFinD自动研判", task_ifind_analysis),
    ("棉花加仓方案归档", task_cotton_archive),
]


def run_all(target_date: str = None, force: bool = False) -> dict:
    """运行全部信息采集任务

    Args:
        target_date: 目标日期 YYYY-MM-DD (默认今日)
        force: 强制重新生成 (忽略已存在文件)

    Returns:
        {"ok": bool, "success": int, "total": int, "archive_dir": str, "date": str}
    """
    if target_date is None:
        target_date = datetime.now().strftime('%Y-%m-%d')
    archive = _archive_today(target_date)

    print("=" * 70)
    print("📡 晨间信息采集工作流 (DeepSeek 驱动)")
    print(f"🕒 {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"📅 目标日期: {target_date}")
    print(f"📁 归档目录: {archive}")
    print("🤖 LLM: DeepSeek (主) → Ollama (备)")
    print("=" * 70)

    success, total = 0, 0
    for name, fn in TASKS:
        total += 1
        print(f"\n📊 [{total}/{len(TASKS)}] {name}...")
        try:
            if fn(archive, target_date, force):
                success += 1
        except Exception as e:
            print(f"  ❌ {name} 异常: {e}")

    print("\n" + "=" * 70)
    print(f"📊 信息采集结果: {success}/{total} 项完成")
    print(f"📁 归档目录: {archive}")
    print("=" * 70)

    return {
        "ok": success == total,
        "success": success,
        "total": total,
        "archive_dir": str(archive),
        "date": target_date,
    }


def main():
    parser = argparse.ArgumentParser(description="晨间信息采集工作流")
    parser.add_argument('--force', action='store_true', help='强制重新生成')
    parser.add_argument('--date', type=str, default=None, help='目标日期 YYYY-MM-DD')
    args = parser.parse_args()
    result = run_all(target_date=args.date, force=args.force)
    sys.exit(0 if result["ok"] else 1)


if __name__ == '__main__':
    main()
