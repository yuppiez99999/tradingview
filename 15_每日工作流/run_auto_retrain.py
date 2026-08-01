#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ML 模型自动重训工作流 (Auto Retrain Workflow)
==============================================

功能:
  1. 扫描所有 LGB Enhanced 模型元数据
  2. 识别需要重训的模型:
     - 年龄超过 30 天 (按 model_cache.retrain_days 配置)
     - IC < 0 (性能退化, 概念漂移)
     - Sharpe < 0 (风险调整收益为负)
  3. 调用 lgb_enhanced_trainer.py 执行重训
  4. 生成重训报告并归档
  5. 更新 lgb_enhanced_signals.json 信号文件

触发模式:
  - 定时: 每月 1 日 06:00 (Windows 计划任务)
  - 事件: DriftMonitor 检测到概念漂移 (IC 衰减 > 30%)
  - 手动: python run_auto_retrain.py [--force] [--symbols 688041,000333]

重训策略:
  - 全量重训: 所有模型 (每月 1 次)
  - 增量重训: 仅重训退化模型 (IC<0 或年龄>30天)
  - 强制重训: 忽略年龄, 全部重训 (--force)

使用方式:
  python run_auto_retrain.py                      # 增量重训 (仅退化模型)
  python run_auto_retrain.py --force              # 强制全量重训
  python run_auto_retrain.py --symbols 688041     # 仅重训指定标的
  python run_auto_retrain.py --dry-run            # 试运行 (仅显示需要重训的模型)
  python run_auto_retrain.py --no-news            # 跳过新闻因子 (加速)

环境变量:
  · DEEPSEEK_API_KEY  (DeepSeek API, 用于新闻情绪分析)
  · WIND_API_KEY      (Wind MCP, 用于真实 OHLCV 数据)
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional, Tuple

# 强制 UTF-8 输出
if sys.stdout.encoding != 'utf-8':
    try:
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    except Exception:
        pass
if sys.stderr.encoding != 'utf-8':
    try:
        sys.stderr.reconfigure(encoding='utf-8', errors='replace')
    except Exception:
        pass

# ═══════════════════════════════════════════════════════════════
# 项目路径配置
# ═══════════════════════════════════════════════════════════════
SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_DIR.parent  # e:\各种PY程序\28-终极量化交易系统8.4
ARCHIVE_DIR = PROJECT_ROOT / "每日报告归档"
MODELS_DIR = PROJECT_ROOT / "models" / "lgb_enhanced"
TRAINER_SCRIPT = PROJECT_ROOT / "lgb_enhanced_trainer.py"
# C8 修复: 不再硬编码 Python 解释器路径, 优先使用环境变量或当前解释器
VENV_PYTHON = os.environ.get("QUANT_PYTHON") or sys.executable

# ═══════════════════════════════════════════════════════════════
# 重训配置
# ═══════════════════════════════════════════════════════════════
RETRAIN_CONFIG = {
    "max_age_days": 30,              # 模型最大年龄 (超过则重训)
    "min_ic_threshold": 0.0,         # IC 低于此值则重训 (性能退化)
    "min_sharpe_threshold": 0.0,     # Sharpe 低于此值则重训
    "training_timeout_min": 60,      # 单次训练超时 (分钟)
    "backup_old_models": True,       # 重训前备份旧模型
    "archive_retrain_report": True,  # 归档重训报告
}


def get_python() -> str:
    """获取 Python 解释器路径"""
    if os.path.exists(VENV_PYTHON):
        return VENV_PYTHON
    return sys.executable


def get_log_file() -> Path:
    """获取日志文件路径"""
    log_dir = PROJECT_ROOT / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    today = datetime.now().strftime("%Y-%m-%d")
    return log_dir / f"auto_retrain_{today}.log"


def log(msg: str, level: str = "INFO") -> None:
    """写日志到文件并打印"""
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{timestamp}] [{level}] {msg}"
    print(line)
    try:
        log_file = get_log_file()
        with open(log_file, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass


def scan_models() -> List[Dict]:
    """扫描所有模型元数据, 返回模型状态列表"""
    models = []
    if not MODELS_DIR.exists():
        log(f"模型目录不存在: {MODELS_DIR}", "WARN")
        return models

    for symbol_dir in sorted(MODELS_DIR.iterdir()):
        if not symbol_dir.is_dir():
            continue
        symbol = symbol_dir.name
        meta_path = symbol_dir / f"{symbol}_meta.json"
        model_path = symbol_dir / f"{symbol}_lgb_enhanced_model.pkl"

        if not meta_path.exists() or not model_path.exists():
            log(f"  ⚠️ {symbol}: 元数据或模型文件缺失", "WARN")
            continue

        try:
            with open(meta_path, "r", encoding="utf-8") as f:
                meta = json.load(f)

            saved_at = meta.get("saved_at", "")
            try:
                trained_time = datetime.fromisoformat(saved_at)
            except Exception:
                trained_time = datetime.now() - timedelta(days=999)

            age_days = (datetime.now() - trained_time).days
            final_metrics = meta.get("final_metrics", {})
            cv_metrics = meta.get("cv_after_selection", {})
            signal = meta.get("signal", 0.0)

            # C12 修复: 类型转换容错 (metadata 中值可能为 None/字符串)
            def _safe_float(val, default=0.0):
                try:
                    return float(val) if val is not None else default
                except (TypeError, ValueError):
                    return default

            def _safe_int(val, default=0):
                try:
                    return int(val) if val is not None else default
                except (TypeError, ValueError):
                    return default

            model_info = {
                "symbol": symbol,
                "trained_at": saved_at,
                "age_days": age_days,
                "ic": _safe_float(final_metrics.get("ic", 0)),
                "sharpe": _safe_float(final_metrics.get("sharpe", 0)),
                "r2": _safe_float(final_metrics.get("r2", 0)),
                "cv_ic": _safe_float(cv_metrics.get("mean_ic", 0)),
                "cv_sharpe": _safe_float(cv_metrics.get("mean_sharpe", 0)),
                "signal": _safe_float(signal),
                "n_samples": _safe_int(meta.get("n_samples", 0)),
                "n_features": _safe_int(meta.get("n_features_after", 0)),
                "best_iteration": _safe_int(meta.get("best_iteration", 0)),
                "model_path": str(model_path),
                "meta_path": str(meta_path),
                "train_period": meta.get("train_period", ""),
                "test_period": meta.get("test_period", ""),
            }
            models.append(model_info)
        except json.JSONDecodeError as e:
            # C12 修复: 单个模型 metadata 损坏不崩溃整个重训流程
            log(f"  ⚠️ {symbol}: metadata JSON 损坏, 跳过: {e}", "WARN")
        except Exception as e:
            log(f"  ⚠️ {symbol}: 解析元数据失败: {e}", "WARN")

    return models


def identify_retrain_candidates(
    models: List[Dict],
    force: bool = False,
    symbols: Optional[List[str]] = None,
) -> Tuple[List[Dict], List[Dict]]:
    """识别需要重训的模型, 返回 (需要重训, 不需要重训)"""
    to_retrain = []
    to_skip = []

    for m in models:
        symbol = m["symbol"]

        # 指定标的模式: 只处理指定标的
        if symbols:
            if symbol in symbols:
                to_retrain.append(m)
            else:
                to_skip.append(m)
            continue

        # 强制模式: 全部重训
        if force:
            to_retrain.append(m)
            continue

        # 增量模式: 检查重训条件
        reasons = []
        if m["age_days"] > RETRAIN_CONFIG["max_age_days"]:
            reasons.append(f"年龄{m['age_days']}天>{RETRAIN_CONFIG['max_age_days']}天")

        if m["ic"] < RETRAIN_CONFIG["min_ic_threshold"]:
            reasons.append(f"IC={m['ic']:.3f}<0")

        if m["sharpe"] < RETRAIN_CONFIG["min_sharpe_threshold"]:
            reasons.append(f"Sharpe={m['sharpe']:.2f}<0")

        if reasons:
            m["retrain_reasons"] = reasons
            to_retrain.append(m)
        else:
            to_skip.append(m)

    return to_retrain, to_skip


def backup_model(model_info: Dict) -> Optional[Path]:
    """重训前备份旧模型"""
    if not RETRAIN_CONFIG["backup_old_models"]:
        return None

    model_path = Path(model_info["model_path"])
    meta_path = Path(model_info["meta_path"])

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_dir = model_path.parent / "backups"
    backup_dir.mkdir(parents=True, exist_ok=True)

    backup_model_path = backup_dir / f"{model_path.stem}_bak_{timestamp}.pkl"
    backup_meta_path = backup_dir / f"{meta_path.stem}_bak_{timestamp}.json"

    try:
        if model_path.exists():
            shutil.copy2(model_path, backup_model_path)
        if meta_path.exists():
            shutil.copy2(meta_path, backup_meta_path)
        return backup_model_path
    except Exception as e:
        log(f"  ⚠️ 备份失败 {model_info['symbol']}: {e}", "WARN")
        return None


def retrain_model(
    symbol: str,
    no_news: bool = False,
    timeout_min: int = 60,
) -> Tuple[bool, Dict]:
    """调用 lgb_enhanced_trainer.py 重训单个标的

    返回 (success, result_info)
    """
    python = get_python()
    cmd = [python, str(TRAINER_SCRIPT), "--symbols", symbol]
    if no_news:
        cmd.append("--no-news")
    cmd.append("--force-retrain")

    log(f"  [CMD] {' '.join(cmd)}")

    if not TRAINER_SCRIPT.exists():
        return False, {"error": f"训练脚本不存在: {TRAINER_SCRIPT}"}

    try:
        env = os.environ.copy()
        env.pop('PYTHONHOME', None)
        env.pop('PYTHONPATH', None)
        env['PYTHONIOENCODING'] = 'utf-8'
        env['PYTHONUTF8'] = '1'

        result = subprocess.run(
            cmd,
            cwd=str(PROJECT_ROOT),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout_min * 60,
            env=env,
        )

        info = {
            "exit_code": result.returncode,
            "stdout_tail": (result.stdout or "")[-2000:] if result.stdout else "",
            "stderr_tail": (result.stderr or "")[-1000:] if result.stderr else "",
        }

        if result.returncode == 0:
            return True, info
        else:
            return False, info

    except subprocess.TimeoutExpired:
        return False, {"error": f"训练超时 (>{timeout_min}分钟)"}
    except Exception as e:
        return False, {"error": str(e)}


def verify_retrained_model(symbol: str, old_info: Dict) -> Dict:
    """验证重训后的模型, 返回新旧对比"""
    meta_path = Path(old_info["meta_path"])
    if not meta_path.exists():
        return {"verified": False, "reason": "meta file not found after retrain"}

    try:
        with open(meta_path, "r", encoding="utf-8") as f:
            new_meta = json.load(f)

        saved_at = new_meta.get("saved_at", "")
        try:
            new_trained_time = datetime.fromisoformat(saved_at)
        except Exception:
            new_trained_time = datetime.now()

        new_age_days = (datetime.now() - new_trained_time).days
        new_metrics = new_meta.get("final_metrics", {})

        # C12 修复: 类型转换容错
        def _safe_float_v(val, default=0.0):
            try:
                return float(val) if val is not None else default
            except (TypeError, ValueError):
                return default

        def _safe_int_v(val, default=0):
            try:
                return int(val) if val is not None else default
            except (TypeError, ValueError):
                return default

        new_ic = _safe_float_v(new_metrics.get("ic", 0))
        new_sharpe = _safe_float_v(new_metrics.get("sharpe", 0))
        comparison = {
            "verified": new_age_days <= 1,  # 重训后年龄应 <= 1天
            "symbol": symbol,
            "old_trained_at": old_info["trained_at"],
            "new_trained_at": saved_at,
            "old_ic": old_info["ic"],
            "new_ic": new_ic,
            "ic_improvement": new_ic - old_info["ic"],
            "old_sharpe": old_info["sharpe"],
            "new_sharpe": new_sharpe,
            "sharpe_improvement": new_sharpe - old_info["sharpe"],
            "old_signal": old_info["signal"],
            "new_signal": _safe_float_v(new_meta.get("signal", 0)),
            "new_n_samples": _safe_int_v(new_meta.get("n_samples", 0)),
            "new_best_iteration": _safe_int_v(new_meta.get("best_iteration", 0)),
        }
        return comparison
    except json.JSONDecodeError as e:
        # C12 修复: 重训后 metadata 损坏时明确报告原因
        return {"verified": False, "reason": f"metadata JSON 损坏: {e}"}
    except Exception as e:
        return {"verified": False, "reason": str(e)}


def generate_retrain_report(
    report_date: str,
    to_retrain: List[Dict],
    to_skip: List[Dict],
    retrain_results: List[Dict],
    verifications: List[Dict],
) -> Path:
    """生成重训报告并归档"""
    lines = [
        f"# ML 模型自动重训报告 — {report_date}",
        "",
        f"**报告日期**: {report_date}",
        f"**执行时间**: {datetime.now().isoformat()}",
        f"**训练脚本**: {TRAINER_SCRIPT.name}",
        "",
        "## 一、模型扫描摘要",
        "",
        "| 指标 | 数值 |",
        "|------|------|",
        f"| 总模型数 | {len(to_retrain) + len(to_skip)} |",
        f"| 需重训数 | {len(to_retrain)} |",
        f"| 跳过数 | {len(to_skip)} |",
        f"| 重训成功 | {sum(1 for r in retrain_results if r['success'])} |",
        f"| 重训失败 | {sum(1 for r in retrain_results if not r['success'])} |",
        "",
        "## 二、重训模型清单",
        "",
        "| 代码 | 旧训练时间 | 年龄 | 旧IC | 旧Sharpe | 重训原因 | 结果 |",
        "|------|-----------|------|------|---------|---------|------|",
    ]

    for m in to_retrain:
        reasons = ", ".join(m.get("retrain_reasons", ["强制重训"]))
        # 找对应结果
        result = next((r for r in retrain_results if r["symbol"] == m["symbol"]), None)
        status = "✅ 成功" if result and result["success"] else "❌ 失败"
        lines.append(
            f"| {m['symbol']} | {m['trained_at'][:19]} | {m['age_days']}天 | "
            f"{m['ic']:.3f} | {m['sharpe']:.2f} | {reasons} | {status} |"
        )

    lines.extend([
        "",
        "## 三、重训前后性能对比",
        "",
        "| 代码 | 旧IC | 新IC | IC改进 | 旧Sharpe | 新Sharpe | Sharpe改进 | 验证 |",
        "|------|------|------|--------|---------|---------|-----------|------|",
    ])

    for v in verifications:
        if v.get("verified"):
            ic_imp = v.get("ic_improvement", 0)
            sh_imp = v.get("sharpe_improvement", 0)
            ic_emoji = "📈" if ic_imp > 0 else "📉"
            sh_emoji = "📈" if sh_imp > 0 else "📉"
            lines.append(
                f"| {v['symbol']} | {v['old_ic']:.3f} | {v['new_ic']:.3f} | "
                f"{ic_emoji} {ic_imp:+.3f} | {v['old_sharpe']:.2f} | {v['new_sharpe']:.2f} | "
                f"{sh_emoji} {sh_imp:+.2f} | ✅ |"
            )
        else:
            lines.append(f"| {v.get('symbol', '?')} | - | - | - | - | - | - | ❌ {v.get('reason', '')} |")

    lines.extend([
        "",
        "## 四、跳过的模型 (无需重训)",
        "",
        "| 代码 | 训练时间 | 年龄 | IC | Sharpe |",
        "|------|---------|------|-----|--------|",
    ])
    for m in to_skip:
        lines.append(
            f"| {m['symbol']} | {m['trained_at'][:19]} | {m['age_days']}天 | "
            f"{m['ic']:.3f} | {m['sharpe']:.2f} |"
        )

    lines.extend([
        "",
        "---",
        f"*本报告由 run_auto_retrain.py 自动生成 — {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}*",
    ])

    # 归档到每日报告归档目录
    archive_date_dir = ARCHIVE_DIR / report_date
    archive_date_dir.mkdir(parents=True, exist_ok=True)
    report_path = archive_date_dir / f"ml_retrain_report_{report_date}.md"

    try:
        report_path.write_text("\n".join(lines), encoding="utf-8")
        log(f"📋 重训报告已保存: {report_path}")
    except Exception as e:
        log(f"  ⚠️ 报告保存失败: {e}", "WARN")
        # Fallback
        fallback = PROJECT_ROOT / "logs" / f"ml_retrain_report_{report_date}.md"
        fallback.write_text("\n".join(lines), encoding="utf-8")
        report_path = fallback

    # 同时保存 JSON 摘要
    summary = {
        "report_date": report_date,
        "generated_at": datetime.now().isoformat(),
        "total_models": len(to_retrain) + len(to_skip),
        "retrained_count": len(to_retrain),
        "skipped_count": len(to_skip),
        "success_count": sum(1 for r in retrain_results if r["success"]),
        "fail_count": sum(1 for r in retrain_results if not r["success"]),
        "retrain_candidates": to_retrain,
        "skipped_models": to_skip,
        "retrain_results": retrain_results,
        "verifications": verifications,
    }
    summary_path = archive_date_dir / f"ml_retrain_summary_{report_date}.json"
    try:
        with open(summary_path, "w", encoding="utf-8") as f:
            json.dump(summary, f, ensure_ascii=False, indent=2, default=str)
    except Exception:
        pass

    return report_path


# ═══════════════════════════════════════════════════════════════
# 主流程
# ═══════════════════════════════════════════════════════════════

def parse_retrain_args():
    """解析命令行参数"""
    parser = argparse.ArgumentParser(description="ML 模型自动重训工作流")
    parser.add_argument("--force", action="store_true",
                        help="强制全量重训 (忽略年龄)")
    parser.add_argument("--symbols", type=str, default=None,
                        help="仅重训指定标的 (逗号分隔, 如 688041,000333)")
    parser.add_argument("--dry-run", action="store_true",
                        help="试运行 (仅显示需要重训的模型, 不实际执行)")
    parser.add_argument("--no-news", action="store_true",
                        help="跳过新闻因子 (加速训练)")
    parser.add_argument("--date", type=str, default=None,
                        help="报告日期 YYYY-MM-DD (默认今天)")
    return parser.parse_args()


def print_retrain_banner(args, report_date):
    """打印启动 banner"""
    log("╔" + "═" * 60 + "╗")
    log("║  ML 模型自动重训工作流启动                            ║")
    log(f"║  报告日期: {report_date}                              ║")
    log(f"║  执行时间: {datetime.now().strftime('%H:%M:%S')}                 ║")
    log(f"║  模式: {'强制全量' if args.force else '增量'} "
        f"{'试运行' if args.dry_run else '生产'}                        ║")
    log("╚" + "═" * 60 + "╝")


def run_phase1_scan_models():
    """阶段一: 扫描所有 LGB Enhanced 模型"""
    log("\n>>> 阶段一: 扫描所有 LGB Enhanced 模型 <<<")
    models = scan_models()
    log(f"  ✅ 扫描到 {len(models)} 个模型")
    if not models:
        log("  ⚠️ 未找到任何模型, 退出", "WARN")
        sys.exit(1)

    # 打印模型摘要
    log(f"\n  {'代码':<8} {'训练时间':<22} {'年龄':<6} {'IC':<8} {'Sharpe':<8} {'信号':<8}")
    log(f"  {'-'*70}")
    for m in models:
        log(f"  {m['symbol']:<8} {m['trained_at'][:19]:<22} {m['age_days']:<5}天 "
            f"{m['ic']:<+8.3f} {m['sharpe']:<+8.2f} {m['signal']:<+8.3f}")
    return models


def run_phase2_identify_candidates(models, args):
    """阶段二: 识别需要重训的模型"""
    log("\n>>> 阶段二: 识别重训候选 <<<")
    symbols = [s.strip() for s in args.symbols.split(",")] if args.symbols else None
    to_retrain, to_skip = identify_retrain_candidates(models, force=args.force, symbols=symbols)

    log(f"  需重训: {len(to_retrain)} 个")
    for m in to_retrain:
        reasons = ", ".join(m.get("retrain_reasons", ["强制重训"]))
        log(f"    🔴 {m['symbol']}: {reasons}")

    log(f"  跳过:   {len(to_skip)} 个")

    if not to_retrain:
        log("\n  ✅ 所有模型均无需重训, 退出")
        # 仍生成空报告
        if not args.dry_run:
            report_path = generate_retrain_report(args.date or datetime.now().strftime("%Y-%m-%d"), [], models, [], [])
            log(f"\n📋 重训报告: {report_path}")
        sys.exit(0)

    if args.dry_run:
        log("\n>>> 试运行模式, 不实际执行重训 <<<")
        log(f"  将重训 {len(to_retrain)} 个模型:")
        for m in to_retrain:
            log(f"    - {m['symbol']} (年龄{m['age_days']}天, IC={m['ic']:.3f})")
        sys.exit(0)

    return to_retrain, to_skip


def run_phase3_retrain_models(to_retrain, args):
    """阶段三: 执行重训"""
    log(f"\n>>> 阶段三: 执行重训 ({len(to_retrain)} 个模型) <<<")
    retrain_results = []
    verifications = []

    for i, m in enumerate(to_retrain, 1):
        symbol = m["symbol"]
        log(f"\n  [{i}/{len(to_retrain)}] 重训 {symbol}...")

        # C13 修复: 备份失败时跳过此模型, 防止原始模型被覆盖且无备份 (模型永久丢失)
        backup_path = None
        try:
            backup_path = backup_model(m)
            if backup_path:
                log(f"    📦 已备份旧模型: {backup_path.name}")
            elif RETRAIN_CONFIG["backup_old_models"]:
                log(f"    ⚠️ {symbol}: 模型备份失败, 跳过重训以防覆盖原始模型", "ERROR")
                retrain_results.append({
                    "symbol": symbol,
                    "success": False,
                    "started_at": datetime.now().isoformat(),
                    "error": "backup failed, abort to protect original model",
                })
                verifications.append({"verified": False, "symbol": symbol, "reason": "backup failed"})
                continue
        except Exception as e:
            log(f"    ⚠️ {symbol}: 模型备份异常, 跳过重训以防覆盖原始模型: {e}", "ERROR")
            retrain_results.append({
                "symbol": symbol,
                "success": False,
                "started_at": datetime.now().isoformat(),
                "error": f"backup exception: {e}",
            })
            verifications.append({"verified": False, "symbol": symbol, "reason": "backup exception"})
            continue

        # 执行重训
        success, info = retrain_model(
            symbol,
            no_news=args.no_news,
            timeout_min=RETRAIN_CONFIG["training_timeout_min"],
        )

        result = {
            "symbol": symbol,
            "success": success,
            "started_at": datetime.now().isoformat(),
            **info,
        }
        retrain_results.append(result)

        if success:
            log(f"    ✅ {symbol} 重训成功")
            # 验证新模型
            verification = verify_retrained_model(symbol, m)
            verification["symbol"] = symbol
            verifications.append(verification)

            if verification.get("verified"):
                ic_imp = verification.get("ic_improvement", 0)
                sh_imp = verification.get("sharpe_improvement", 0)
                log(f"    📊 IC: {verification['old_ic']:+.3f} → {verification['new_ic']:+.3f} ({ic_imp:+.3f})")
                log(f"    📊 Sharpe: {verification['old_sharpe']:+.2f} → {verification['new_sharpe']:+.2f} ({sh_imp:+.2f})")
        else:
            log(f"    ❌ {symbol} 重训失败: {info.get('error', info.get('stderr_tail', '')[:200])}", "ERROR")
            verifications.append({"verified": False, "symbol": symbol, "reason": "training failed"})

    return retrain_results, verifications


def run_phase4_report(report_date, to_retrain, to_skip, retrain_results, verifications):
    """阶段四: 生成重训报告"""
    log("\n>>> 阶段四: 生成重训报告并归档 <<<")
    return generate_retrain_report(report_date, to_retrain, to_skip, retrain_results, verifications)


def print_retrain_summary(to_retrain, retrain_results, report_path):
    """打印重训总结"""
    success_count = sum(1 for r in retrain_results if r["success"])
    fail_count = sum(1 for r in retrain_results if not r["success"])
    log("\n" + "=" * 60)
    log("║  ML 模型自动重训完成                                  ║")
    log(f"║  重训: {len(to_retrain)} | 成功: {success_count} | 失败: {fail_count}     ║")
    log(f"║  报告: {report_path.name}  ║")
    log("=" * 60)
    return success_count, fail_count


def main():
    args = parse_retrain_args()
    report_date = args.date or datetime.now().strftime("%Y-%m-%d")
    print_retrain_banner(args, report_date)

    symbols = [s.strip() for s in args.symbols.split(",") if s.strip()] if args.symbols else None
    if symbols:
        log(f"  指定标的: {symbols}")

    models = run_phase1_scan_models()
    to_retrain, to_skip = run_phase2_identify_candidates(models, args)

    if args.dry_run:
        return

    retrain_results, verifications = run_phase3_retrain_models(to_retrain, args)
    report_path = run_phase4_report(report_date, to_retrain, to_skip, retrain_results, verifications)

    success_count, fail_count = print_retrain_summary(to_retrain, retrain_results, report_path)

    if fail_count == 0:
        sys.exit(0)
    elif success_count > 0:
        sys.exit(1)
    else:
        sys.exit(2)


if __name__ == "__main__":
    main()
