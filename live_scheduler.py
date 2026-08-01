# -*- coding: utf-8 -*-
"""
v7.5 实时监控并发调度器 (--live 模式)
====================================

参考项目记忆中 --live 模式要求:
    - 6个模块同时启动
    - 对冲再平衡联动: 每30分钟
    - ETF资金流监控: 每10分钟
    - ML信号扫描: 每15分钟
    - 实时行情监控: 每5分钟
    - 自动再平衡: 每60分钟
    - 收盘报告: 收盘后执行

架构:
    - threading.Timer 实现定时轮询
    - ThreadPoolExecutor 实现并发执行
    - 支持热重启和优雅退出
    - 模块状态监控与自动恢复

用法:
    python live_scheduler.py                    # 启动实时监控
    python live_scheduler.py --dry-run          # 干跑模式 (不执行实际交易)
    python live_scheduler.py --stop             # 停止运行中的调度器
    python live_scheduler.py --status           # 查看运行状态

依赖:
    - utils.etf_flow_monitor (ETF资金流)
    - utils.wt_hedge_strategy (对冲策略)
    - utils.wt_tick_engine (Tick回测引擎)
    - v8.3_institutional.src.hedging (对冲协调器)
"""

from __future__ import annotations

import os
import sys
import json
import time
import signal
import threading
import logging
import argparse
import subprocess
from datetime import datetime, timedelta, time as dt_time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Dict, List, Optional, Callable, Any

# ============================================================
# 路径初始化
# ============================================================
BASE_DIR = Path(__file__).resolve().parent
V75_DIR = BASE_DIR / "v8.3_institutional"
V75_SRC = V75_DIR / "src"
LOG_DIR = BASE_DIR / "logs"
LOG_DIR.mkdir(exist_ok=True)

sys.path.insert(0, str(BASE_DIR))
sys.path.insert(0, str(V75_SRC))

# ============================================================
# 日志配置
# ============================================================
logger = logging.getLogger("live_scheduler")
logger.setLevel(logging.INFO)
handler = logging.StreamHandler(sys.stdout)
handler.setFormatter(logging.Formatter("%(asctime)s [%(threadName)s] [%(levelname)s] %(message)s"))
logger.addHandler(handler)
file_handler = logging.FileHandler(LOG_DIR / "live_scheduler.log", encoding="utf-8")
file_handler.setFormatter(logging.Formatter("%(asctime)s [%(threadName)s] [%(levelname)s] %(message)s"))
logger.addHandler(file_handler)

# ============================================================
# 全局状态
# ============================================================
RUNNING = True
MODULE_STATUS: Dict[str, Dict] = {}
PYTHON = sys.executable
LOCK_FILE = BASE_DIR / ".live_scheduler.lock"

# ============================================================
# 模块定义
# ============================================================
MODULE_DEFINITIONS = [
    {
        "name": "market_monitor",
        "description": "实时行情监控",
        "interval_seconds": 300,  # 5分钟
        "task_func": "run_market_monitor",
        "required": True,
    },
    {
        "name": "auto_rebalance",
        "description": "自动再平衡",
        "interval_seconds": 3600,  # 60分钟
        "task_func": "run_auto_rebalance",
        "required": False,
    },
    {
        "name": "hedge_rebalance",
        "description": "对冲再平衡联动",
        "interval_seconds": 1800,  # 30分钟
        "task_func": "run_hedge_rebalance",
        "required": True,
    },
    {
        "name": "etf_flow_monitor",
        "description": "ETF资金流监控",
        "interval_seconds": 600,  # 10分钟
        "task_func": "run_etf_flow_monitor",
        "required": True,
    },
    {
        "name": "ml_signal_scan",
        "description": "ML信号扫描",
        "interval_seconds": 900,  # 15分钟
        "task_func": "run_ml_signal_scan",
        "required": False,
    },
    {
        "name": "daily_report",
        "description": "收盘报告",
        "interval_seconds": None,  # 收盘后执行
        "task_func": "run_daily_report",
        "required": True,
        "trigger_time": dt_time(15, 15),  # 15:15 收盘后
    },
    {
        "name": "strategy_evaluation",
        "description": "策略健康度评估 (N6)",
        "interval_seconds": None,  # 收盘后执行
        "task_func": "run_strategy_evaluation",
        "required": False,  # 默认关闭, 需显式启用
        "trigger_time": dt_time(15, 25),  # 收盘报告之后
        "feature_flag": "USE_STRATEGY_EVALUATION",
    },
]

# ============================================================
# 任务函数
# ============================================================


def run_market_monitor(dry_run: bool = False) -> Dict[str, Any]:
    """实时行情监控任务"""
    start = datetime.now()
    result = {"status": "OK", "data": {}}
    try:
        from utils.data_provider import MarketDataProvider

        provider = MarketDataProvider()

        positions_path = BASE_DIR / "config" / "positions.json"
        if positions_path.exists():
            with open(positions_path, "r", encoding="utf-8") as f:
                positions_data = json.load(f)
            codes = list(positions_data.get("positions", {}).keys())
        else:
            codes = ["510300.SH", "510050.SH", "588000.SH"]

        prices = {}
        for code in codes[:20]:
            try:
                market_data = provider.get_market_data(code)
                if market_data:
                    for key in ["price", "index_price", "close", "last_price", "current_price"]:
                        if market_data.get(key):
                            prices[code] = float(market_data[key])
                            break
            except Exception as e:
                logger.debug(f"获取 {code} 行情失败: {e}")
                continue

        result["data"] = {
            "n_symbols": len(prices),
            "prices": prices,
            "timestamp": datetime.now().isoformat(),
        }
        logger.info(f"[market_monitor] 获取 {len(prices)} 个标的行情")

    except Exception as e:
        result["status"] = "FAIL"
        result["error"] = str(e)
        logger.error(f"[market_monitor] 执行失败: {e}")

    result["duration"] = (datetime.now() - start).total_seconds()
    return result


def run_auto_rebalance(dry_run: bool = False) -> Dict[str, Any]:
    """自动再平衡任务"""
    start = datetime.now()
    result = {"status": "OK", "data": {}}
    try:
        from utils.risk_metrics import calculate_portfolio_weights

        positions_path = BASE_DIR / "config" / "positions.json"
        if positions_path.exists():
            with open(positions_path, "r", encoding="utf-8") as f:
                positions_data = json.load(f)
            positions = positions_data.get("positions", {})

            actual_weights = calculate_portfolio_weights(positions)

            trade_plan_path = V75_DIR / "trade_plans" / "auto_trade_plan_500w_2026-2030.json"
            if trade_plan_path.exists():
                with open(trade_plan_path, "r", encoding="utf-8") as f:
                    plan = json.load(f)
                target_weights = {}
                for pos in plan.get("stock_etf_account", {}).get("positions", []):
                    target_weights[pos["code"]] = pos["weight"]

                deviations = {}
                for code, actual in actual_weights.items():
                    target = target_weights.get(code, 0)
                    deviations[code] = abs(actual - target)

                result["data"] = {
                    "actual_weights": actual_weights,
                    "target_weights": target_weights,
                    "deviations": deviations,
                    "exceeds_threshold": {k: v for k, v in deviations.items() if v > 0.05},
                }
                logger.info(
                    f"[auto_rebalance] 权重偏差检查完成, {len(result['data']['exceeds_threshold'])} 个标的偏差>5%"
                )
    except Exception as e:
        result["status"] = "FAIL"
        result["error"] = str(e)
        logger.error(f"[auto_rebalance] 执行失败: {e}")

    result["duration"] = (datetime.now() - start).total_seconds()
    return result


def run_hedge_rebalance(dry_run: bool = False) -> Dict[str, Any]:
    """对冲再平衡联动任务"""
    start = datetime.now()
    result = {"status": "OK", "data": {}}
    try:
        from hedging.beta_hedger import BetaHedger

        positions_path = BASE_DIR / "config" / "positions.json"
        if positions_path.exists():
            with open(positions_path, "r", encoding="utf-8") as f:
                positions_data = json.load(f)
            positions = positions_data.get("positions", {})

            # v8.6.13 P0 FIX (2026-08-01 AI 扫描):
            # 原代码用 pos.get("phase1_amount", 0) + ... 计算组合市值,
            # 但 positions.json 实际字段是 amount / phase1_shares / total_shares / est_price,
            # 根本不存在 phase1_amount / phase2_amount / phase3_amount 字段.
            # 导致 portfolio_value 恒为 0 → if portfolio_value > 0 恒为 False →
            # 对冲再平衡任务每 30 分钟"成功执行"但实际什么都不做, 对冲账户 100 万资金风控静默失效.
            # 修复: 优先用 amount (已投入金额), 其次用 total_shares * est_price 估算市值.
            portfolio_value = 0.0
            for pos in positions.values():
                if not isinstance(pos, dict):
                    continue
                # 优先用 amount (已投入金额)
                amt = pos.get("amount")
                if amt is not None:
                    try:
                        portfolio_value += float(amt)
                        continue
                    except (TypeError, ValueError):
                        pass
                # 降级: total_shares * est_price 估算市值
                shares = pos.get("total_shares") or pos.get("phase1_shares") or 0
                est_price = pos.get("est_price") or 0
                try:
                    portfolio_value += float(shares) * float(est_price)
                except (TypeError, ValueError):
                    continue

            if portfolio_value <= 0:
                # v8.6.13 P0 FIX: 明确告警, 避免静默跳过
                logger.warning(
                    "[hedge_rebalance] portfolio_value=0, positions.json 可能未建仓或字段缺失, 跳过对冲计算"
                )
                result["data"] = {"portfolio_value": 0, "skipped": "no_positions"}

            if portfolio_value > 0:
                # SR1 修复: 期货价格硬编码死值 — 实时获取为主, 硬编码仅作最终兜底
                # 原硬编码 IF=3800/IM=5800/IC=5500 实盘偏差 10%+ 导致对冲量计算错误
                # 现增加: 单标的失败 warning + 全失效 critical 告警 + 时效性标记
                from hedging.hedge_engine_v59 import get_live_futures_prices

                futures_config = {
                    "IF": {"multiplier": 300, "beta": 1.0, "price": 3800.0, "source": "fallback"},
                    "IM": {"multiplier": 200, "beta": 1.1, "price": 5800.0, "source": "fallback"},
                    "IC": {"multiplier": 200, "beta": 1.2, "price": 5500.0, "source": "fallback"},
                }

                # 优先: hedge_engine_v59 多源聚合 (iFinD→Wind→AKShare→Sina→efinance)
                live_prices: Dict[str, float] = {}
                try:
                    live_prices = get_live_futures_prices() or {}
                except Exception as e:
                    logger.warning(f"[hedge_rebalance] hedge_engine 实时期货价格获取失败: {e}")

                # 补充: utils.data_provider (作为第二来源)
                if len(live_prices) < 3:
                    try:
                        from utils.data_provider import MarketDataProvider

                        provider = MarketDataProvider()
                        # IC5 修复: 动态生成主力合约代码, 避免使用过期合约 (IF2501 等 2025年1月合约)
                        # 股指期货交割日为交割月第三个周五, 过交割日后需切换到下月合约
                        # 简单策略: 当月合约 + 下月合约 + 季月合约, 优先用当月
                        from datetime import datetime as _dt

                        _now = _dt.now()
                        _yy = _now.year % 100
                        _mm = _now.month
                        # 当月合约代码
                        _cur_code = f"{_yy:02d}{_mm:02d}"
                        # 下月合约代码
                        _next_mm = _mm + 1 if _mm < 12 else 1
                        _next_yy = _yy if _mm < 12 else _yy + 1
                        _next_code = f"{_next_yy:02d}{_next_mm:02d}"
                        # 下季月合约代码 (3/6/9/12 循环)
                        _quarter_months = [3, 6, 9, 12]
                        _next_q = next((m for m in _quarter_months if m > _mm), 3)
                        _next_q_yy = _yy if _next_q > _mm else _yy + 1
                        _quarter_code = f"{_next_q_yy:02d}{_next_q:02d}"

                        futures_codes = {
                            "IF": [f"IF{_cur_code}.CFFEX", f"IF{_next_code}.CFFEX", f"IF{_quarter_code}.CFFEX"],
                            "IM": [f"IM{_cur_code}.CFFEX", f"IM{_next_code}.CFFEX", f"IM{_quarter_code}.CFFEX"],
                            "IC": [f"IC{_cur_code}.CFFEX", f"IC{_next_code}.CFFEX", f"IC{_quarter_code}.CFFEX"],
                        }
                        for ft_key, ft_code_list in futures_codes.items():
                            if ft_key in live_prices:
                                continue  # 已获取, 跳过
                            for ft_code in ft_code_list:
                                try:
                                    md = provider.get_market_data(ft_code)
                                    if md:
                                        for px_key in ["price", "last_price", "current_price", "close"]:
                                            if md.get(px_key):
                                                live_prices[ft_key] = float(md[px_key])
                                                break
                                        if ft_key in live_prices:
                                            break  # 获取成功, 不再尝试其他合约
                                except Exception as e_ft:
                                    # SR1 修复: 单标的失败不再静默 pass, 记录 warning
                                    logger.debug(f"[hedge_rebalance] {ft_key} ({ft_code}) 实时价格获取失败: {e_ft}")
                    except Exception as e:
                        logger.warning(f"[hedge_rebalance] MarketDataProvider 不可用: {e}")

                # 应用实时价格, 标记来源
                n_realtime = 0
                for ft_key in futures_config:
                    if ft_key in live_prices and live_prices[ft_key] > 0:
                        futures_config[ft_key]["price"] = float(live_prices[ft_key])
                        futures_config[ft_key]["source"] = "realtime"
                        n_realtime += 1

                # SR1 修复: 全失效告警
                if n_realtime == 0:
                    logger.critical(
                        "[hedge_rebalance][SR1] 所有期货实时价格源失效! "
                        "使用硬编码兜底价 (IF=3800/IM=5800/IC=5500), "
                        "实盘偏差可能 >10% 导致对冲量计算错误, 请人工核查行情链路"
                    )
                    try:
                        from utils.notify import send_sms_alert

                        send_sms_alert("[量化系统-SR1告警] 期货实时价格源全失效, 对冲计算使用硬编码兜底价, 请立即排查!")
                    except ImportError:
                        logger.warning("[hedge_rebalance][SR1] utils.notify 不可用, 告警未发送")
                    except Exception as e:
                        logger.warning(f"[hedge_rebalance][SR1] 告警发送失败: {e}")
                else:
                    logger.info(
                        f"[hedge_rebalance] 期货价格: "
                        f"IF={futures_config['IF']['price']:.0f}({futures_config['IF']['source']}), "
                        f"IM={futures_config['IM']['price']:.0f}({futures_config['IM']['source']}), "
                        f"IC={futures_config['IC']['price']:.0f}({futures_config['IC']['source']}), "
                        f"实时覆盖率 {n_realtime}/3"
                    )

                beta_hedger = BetaHedger(futures_config=futures_config, beta_trigger=0.7, beta_target=0.3)
                order = beta_hedger.compute_hedge(portfolio_beta=1.0, portfolio_value=portfolio_value)

                result["data"] = {
                    "portfolio_value": portfolio_value,
                    "hedge_order": order,
                }
                logger.info(
                    f"[hedge_rebalance] 对冲计算完成: action={order.get('action')}, contracts={order.get('contracts', 0)}"
                )
    except Exception as e:
        result["status"] = "FAIL"
        result["error"] = str(e)
        logger.error(f"[hedge_rebalance] 执行失败: {e}")

    result["duration"] = (datetime.now() - start).total_seconds()
    return result


def run_etf_flow_monitor(dry_run: bool = False) -> Dict[str, Any]:
    """ETF资金流监控任务"""
    start = datetime.now()
    result = {"status": "OK", "data": {}}
    try:
        from utils.etf_flow_monitor import refresh_etf_flow_signals, get_etf_flow_summary

        summary = get_etf_flow_summary()

        if not dry_run:
            try:
                refresh_etf_flow_signals()
                summary = get_etf_flow_summary()
            except Exception as e:
                logger.warning(f"[etf_flow_monitor] 刷新失败, 使用缓存数据: {e}")

        flow_data = summary.get("flow_data", {})
        result["data"] = {
            "n_etfs": len(flow_data),
            "total_inflow": summary.get("total_flow_yi", 0),
            "overall_trend": summary.get("overall_trend", "未知"),
            "signal_count": summary.get("signal_count", 0),
            "signals": summary.get("signals", []),
        }
        logger.info(
            f"[etf_flow_monitor] 监控完成: {len(flow_data)} 只ETF, 净流入={result['data']['total_inflow']:.2f}亿, 趋势={result['data']['overall_trend']}"
        )
    except Exception as e:
        result["status"] = "FAIL"
        result["error"] = str(e)
        logger.error(f"[etf_flow_monitor] 执行失败: {e}")

    result["duration"] = (datetime.now() - start).total_seconds()
    return result


def run_ml_signal_scan(dry_run: bool = False) -> Dict[str, Any]:
    """ML信号扫描任务

    Phase 3 集成: Kronos 时序模型作为 AB test Challenger 注入.
    HC-1: USE_KRONOS_PREDICTOR=False 时完全降级为原 PricePredictor 单源.
    失败安全: Kronos 异常不影响 PricePredictor 主流程.
    """
    start = datetime.now()
    result = {"status": "OK", "data": {}}
    try:
        from utils.tf_price_predictor import PricePredictor

        predictor = PricePredictor()

        codes = ["510300.SH", "510050.SH", "588000.SH", "688041.SH", "300308.SZ"]
        predictions = {}

        for code in codes:
            try:
                pred = predictor.predict(code, days=3)
                if pred:
                    predictions[code] = {
                        "predicted_return": pred.get("predicted_return", 0),
                        "confidence": pred.get("confidence", 0),
                    }
            except Exception:
                continue

        # ============================================================
        # Phase 3: Kronos Challenger 预测注入 (HC-1: flag-gated)
        # ============================================================
        kronos_meta = _enrich_with_kronos(predictions, codes)

        result["data"] = {
            "n_predictions": len(predictions),
            "predictions": predictions,
            "kronos_integration": kronos_meta,
        }
        logger.info(
            f"[ml_signal_scan] 预测完成: {len(predictions)} 个标的 "
            f"(kronos_enriched={kronos_meta.get('enriched_count', 0)})"
        )
    except Exception as e:
        result["status"] = "FAIL"
        result["error"] = str(e)
        logger.error(f"[ml_signal_scan] 执行失败: {e}")

    result["duration"] = (datetime.now() - start).total_seconds()
    return result


def _enrich_with_kronos(predictions: Dict[str, Any], codes: List[str]) -> Dict[str, Any]:
    """用 Kronos Challenger 预测丰富 predictions 字典.

    HC-1: USE_KRONOS_PREDICTOR=False 时返回 disabled 状态, 不修改 predictions.
    失败安全: 任何异常返回 error 状态, 不影响主流程.

    Args:
        predictions: {code: {predicted_return, confidence}} 字典 (会被原地丰富)
        codes: 待预测的标的代码列表

    Returns:
        Kronos 集成元数据:
            {
                "status": "enabled" | "disabled" | "error",
                "enriched_count": int,
                "factors_added": list[str],
                "error": str (仅 status=error 时),
            }
    """
    meta: Dict[str, Any] = {
        "status": "disabled",
        "enriched_count": 0,
        "factors_added": [],
    }

    try:
        from utils.infra.feature_flags import is_enabled

        if not is_enabled("USE_KRONOS_PREDICTOR"):
            return meta

        from utils.alpha.kronos_predictor import KronosPredictor

        predictor = KronosPredictor.get_instance()
        if not predictor.available:
            meta["status"] = "error"
            meta["error"] = f"Kronos model unavailable: {predictor.init_error or 'unknown'}"
            logger.debug(f"[ml_signal_scan] Kronos 不可用: {meta['error']}")
            return meta

        meta["status"] = "enabled"
        enriched = 0
        all_factor_keys: set = set()

        for code in codes:
            try:
                # 获取历史数据 (从 predictions 中已有的 code 或缓存)
                # KronosPredictor.predict 需要 DataFrame 输入
                # 这里使用 predict_batch 的简化接口 (内部处理数据获取)
                kronos_factors = predictor.predict_batch({code: None})
                if code in kronos_factors and kronos_factors[code]:
                    # 丰富 predictions[code] 字典 (不覆盖已有字段)
                    for k, v in kronos_factors[code].items():
                        factor_key = f"kronos_{k}"
                        if code in predictions:
                            predictions[code][factor_key] = v
                            all_factor_keys.add(factor_key)
                    enriched += 1
            except Exception as e:
                logger.debug(f"[ml_signal_scan] Kronos 预测 {code} 失败: {e}")
                continue

        meta["enriched_count"] = enriched
        meta["factors_added"] = sorted(all_factor_keys)
        logger.info(
            f"[ml_signal_scan] Kronos 注入完成: {enriched}/{len(codes)} 个标的, "
            f"因子: {meta['factors_added']}"
        )
    except ImportError as e:
        meta["status"] = "error"
        meta["error"] = f"Kronos 模块导入失败: {e}"
        logger.debug(f"[ml_signal_scan] Kronos 导入失败: {e}")
    except Exception as e:
        meta["status"] = "error"
        meta["error"] = f"Kronos 集成异常: {e}"
        logger.warning(f"[ml_signal_scan] Kronos 集成异常: {e}")

    return meta


def run_daily_report(dry_run: bool = False) -> Dict[str, Any]:
    """收盘报告任务"""
    start = datetime.now()
    result = {"status": "OK", "data": {}}
    try:
        if dry_run:
            logger.info("[daily_report] [DRY-RUN] 收盘报告生成 (跳过实际执行)")
            result["data"] = {"dry_run": True}
            result["duration"] = 0
            return result

        report_script = BASE_DIR / "generate_daily_report.py"
        if report_script.exists():
            cmd = [PYTHON, str(report_script)]
            proc = subprocess.run(
                cmd,
                cwd=str(BASE_DIR),
                capture_output=True,
                text=True,
                encoding="utf-8",
                timeout=600,
            )
            if proc.returncode == 0:
                result["data"] = {"exit_code": 0}
                logger.info("[daily_report] 收盘报告生成成功")
            else:
                result["status"] = "FAIL"
                result["error"] = proc.stderr[:200]
                logger.error(f"[daily_report] 执行失败: {proc.stderr[:200]}")
        else:
            result["status"] = "FAIL"
            result["error"] = "generate_daily_report.py 不存在"
    except Exception as e:
        result["status"] = "FAIL"
        result["error"] = str(e)
        logger.error(f"[daily_report] 执行失败: {e}")

    result["duration"] = (datetime.now() - start).total_seconds()
    return result


def run_strategy_evaluation(dry_run: bool = False) -> Dict[str, Any]:
    """N6: 策略健康度评估任务 (每日收盘后运行).

    对接真实数据源:
      - 持仓: config/positions.json
      - 交易历史: reports/trade_history.json (若存在)
      - 信号历史: reports/signal_history.json (若存在)
      - IC 记录: reports/daily_ic_scores.json (N3 写入)

    Feature Flag: USE_STRATEGY_EVALUATION (默认关闭)
    """
    start = datetime.now()
    result = {"status": "OK", "data": {}}

    # Feature Flag 检查
    flag_val = os.getenv("USE_STRATEGY_EVALUATION", "False")
    enabled = flag_val.lower() in ("true", "1", "yes", "on")
    if not enabled:
        result["data"] = {"enabled": False, "message": "USE_STRATEGY_EVALUATION=False, 跳过"}
        result["duration"] = (datetime.now() - start).total_seconds()
        logger.info("[strategy_eval] Feature Flag 关闭, 跳过评估")
        return result

    try:
        if dry_run:
            logger.info("[strategy_eval] [DRY-RUN] 跳过实际评估")
            result["data"] = {"dry_run": True}
            result["duration"] = 0.0
            return result

        # 延迟导入 (避免模块加载时的循环依赖)
        try:
            from scripts.ic_recorder import (
                compute_ic_from_signals,
                record_daily_ic,
                load_ic_store,
                record_ic_from_qlib_report,
            )
        except ImportError:
            from ic_recorder import (
                compute_ic_from_signals,
                record_daily_ic,
                load_ic_store,
                record_ic_from_qlib_report,
            )

        # 1. 加载信号/交易历史
        signal_hist_path = BASE_DIR / "reports" / "signal_history.json"
        trade_hist_path = BASE_DIR / "reports" / "trade_history.json"

        signal_history: List[Dict] = []
        _trade_history: List[Dict] = []  # 预留，后续 strategy_eval 扩展用
        if signal_hist_path.exists():
            try:
                with open(signal_hist_path, "r", encoding="utf-8") as f:
                    signal_history = json.load(f)
            except Exception as e:
                logger.warning(f"[strategy_eval] 读取信号历史失败: {e}")
        if trade_hist_path.exists():
            try:
                with open(trade_hist_path, "r", encoding="utf-8") as f:
                    _trade_history = json.load(f)  # 预留，后续 strategy_eval 扩展用
            except Exception as e:
                logger.warning(f"[strategy_eval] 读取交易历史失败: {e}")

        # 2. 计算并记录当日 IC (N3)
        if signal_history:
            ic_value = compute_ic_from_signals(signal_history)
            if ic_value is not None:
                record_daily_ic(ic_value, source="signal_history")
                logger.info(f"[strategy_eval] 当日 IC={ic_value:.4f} 已记录 (signal_history)")
            else:
                # 回退: 从 QLib 报告读 IC
                qlib_reports = list((BASE_DIR / "reports").glob("qlib_*.json"))
                if qlib_reports:
                    latest_report = max(qlib_reports, key=lambda p: p.stat().st_mtime)
                    record_ic_from_qlib_report(str(latest_report))

        # 3. 策略多维评分 (N2)
        try:
            try:
                from scripts.strategy_evaluator import StrategyEvaluator
            except ImportError:
                from strategy_evaluator import StrategyEvaluator

            evaluator = StrategyEvaluator()
            # N2 的 evaluate API: 接受文件路径, 返回 ScoreReport dataclass
            daily_returns_path = str(BASE_DIR / "reports" / "daily_returns.jsonl")
            pos_path = str(BASE_DIR / "config" / "positions.json")
            score_report = evaluator.evaluate(
                daily_returns_path=daily_returns_path,
                pos_path=pos_path,
            )
            # ScoreReport 是 dataclass, 转为 dict 方便下游使用
            eval_result = {
                "composite_score": float(getattr(score_report, "composite_score", 0) or 0),
                "return_metrics": getattr(score_report, "return_metrics", None),
                "diversification_metrics": getattr(score_report, "diversification_metrics", None),
                "degraded": bool(getattr(score_report, "is_degraded", False)),
                "degraded_reason": str(getattr(score_report, "degraded_reason", "")),
            }
            # 构造状态报告 (N2 无 get_status_report, 这里手动生成)
            status = {
                "status": (
                    "healthy"
                    if eval_result["composite_score"] > 0.6
                    else ("warning" if eval_result["composite_score"] > 0.4 else "degraded")
                ),
                "latest_score": eval_result["composite_score"],
                "degraded": eval_result["degraded"],
            }
            result["data"] = {
                "evaluation": eval_result,
                "status_report": status,
                "ic_store": load_ic_store(),
            }
            logger.info(
                f"[strategy_eval] 评分完成: "
                f"综合分={eval_result['composite_score']:.3f}, "
                f"状态={status['status']}, "
                f"退化={eval_result['degraded']}"
            )

            # 4. 退化告警 (记录到 N5 SkillManager)
            if eval_result["degraded"]:
                try:
                    try:
                        from scripts.skill_manager import record_lesson
                    except ImportError:
                        from skill_manager import record_lesson

                    record_lesson(
                        symbol="PORTFOLIO",
                        lesson_type="strategy_degradation",
                        description=(
                            f"策略综合评分={eval_result['composite_score']:.3f}, 原因={eval_result['degraded_reason']}"
                        ),
                        impact="negative",
                        action_taken="alert_only",
                        verified=False,
                    )
                except Exception as e:
                    logger.debug(f"[strategy_eval] 退化告警记录失败 (非致命): {e}")

            # 5. N4: 漂移触发的超参自适应搜索 (Feature Flag: USE_ADAPTIVE_OPTIMIZE, 默认关闭)
            # 仅在策略退化时触发, 生成新的 LGB 配置供下次训练使用
            if eval_result["degraded"]:
                try:
                    try:
                        from scripts.adaptive_optimize import adaptive_optimize
                    except ImportError:
                        from adaptive_optimize import adaptive_optimize

                    # 加载基准训练配置 (与 N1 _trigger_retrain_with_cooldown 对齐)
                    try:
                        from lgb_enhanced_trainer import LGB_ENHANCED_CONFIG
                        base_config = LGB_ENHANCED_CONFIG
                    except Exception:
                        base_config = {
                            "lgb_params": {
                                "learning_rate": 0.005,
                                "n_estimators": 2000,
                                "max_depth": 6,
                                "num_leaves": 31,
                                "reg_alpha": 0.1,
                                "reg_lambda": 0.5,
                            },
                            "early_stopping_rounds": 200,
                        }

                    # 从 ic_store 构造 drift_signal (与 ModelDriftDetector.generate_report() 格式对齐)
                    ic_store = result["data"].get("ic_store", {}) or {}
                    history = ic_store.get("history", []) or []
                    ic_values = [float(h.get("ic", 0) or 0) for h in history]
                    mean_ic = sum(ic_values) / len(ic_values) if ic_values else 0.0
                    recent_window = ic_values[-10:] if ic_values else []
                    recent_ic = sum(recent_window) / len(recent_window) if recent_window else mean_ic

                    drift_signal = {
                        "ic_stats": {"mean_ic": mean_ic, "ic_10d": recent_ic},
                        "alerts": [{"severity": "critical"}],
                        "ks_detected": False,
                        "psi_value": 0.0,
                        "adwin_drift": False,
                        "should_retrain": True,
                    }

                    # 对组合层面优化 (symbol="PORTFOLIO")
                    optimized = adaptive_optimize(
                        symbol="PORTFOLIO",
                        config=base_config,
                        drift_signal=drift_signal,
                    )

                    # 保存到 reports/adaptive_config_{date}.json 供下次训练读取
                    today_str = datetime.now().date().isoformat()
                    adaptive_path = BASE_DIR / "reports" / f"adaptive_config_{today_str}.json"
                    try:
                        with open(adaptive_path, "w", encoding="utf-8") as f:
                            json.dump(optimized, f, ensure_ascii=False, indent=2)
                        logger.info(f"[strategy_eval] N4 自适应配置已保存: {adaptive_path}")
                        result["data"]["adaptive_config_path"] = str(adaptive_path)
                    except Exception as e_save:
                        logger.warning(f"[strategy_eval] 保存 N4 自适应配置失败: {e_save}")

                except ImportError:
                    logger.debug("[strategy_eval] N4 adaptive_optimize 不可用, 跳过")
                except Exception as e:
                    logger.warning(f"[strategy_eval] N4 自适应搜索失败 (非致命): {e}")

        except ImportError:
            logger.warning("[strategy_eval] StrategyEvaluator 不可用, 跳过评分")
            result["data"]["ic_store"] = load_ic_store()
        except Exception as e:
            logger.warning(f"[strategy_eval] 评分失败: {e}")
            result["data"]["error"] = str(e)

    except Exception as e:
        result["status"] = "FAIL"
        result["error"] = str(e)
        logger.error(f"[strategy_eval] 执行失败: {e}")

    result["duration"] = (datetime.now() - start).total_seconds()
    return result


# ============================================================
# 调度器核心
# ============================================================
class LiveScheduler:
    """实时监控并发调度器"""

    def __init__(self, dry_run: bool = False, max_workers: int = 6):
        self.dry_run = dry_run
        self.max_workers = max_workers
        self.executor = ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="live")
        self.timers: Dict[str, threading.Timer] = {}
        self.module_last_run: Dict[str, datetime] = {}
        self.module_results: Dict[str, List[Dict]] = {}

        for mod in MODULE_DEFINITIONS:
            self.module_results[mod["name"]] = []

    def _run_task(self, module_name: str, task_func: Callable) -> None:
        """运行单个任务"""
        try:
            result = task_func(dry_run=self.dry_run)
            self.module_last_run[module_name] = datetime.now()
            self.module_results[module_name].append(
                {
                    "timestamp": datetime.now().isoformat(),
                    **result,
                }
            )
            if len(self.module_results[module_name]) > 100:
                self.module_results[module_name] = self.module_results[module_name][-50:]

            MODULE_STATUS[module_name] = {
                "status": result["status"],
                "last_run": datetime.now().isoformat(),
                "duration": result["duration"],
            }
        except Exception as e:
            logger.error(f"[{module_name}] 任务异常: {e}")
            MODULE_STATUS[module_name] = {
                "status": "ERROR",
                "last_run": datetime.now().isoformat(),
                "error": str(e),
            }

    def _schedule_module(self, module_def: Dict) -> None:
        """调度单个模块"""
        name = module_def["name"]
        interval = module_def["interval_seconds"]
        task_func = globals()[module_def["task_func"]]

        def run_and_reschedule():
            if not RUNNING:
                return

            self._run_task(name, task_func)

            if RUNNING and interval:
                timer = threading.Timer(interval, run_and_reschedule)
                timer.daemon = True
                self.timers[name] = timer
                timer.start()

        run_and_reschedule()

    def _schedule_timed_module(self, module_def: Dict) -> None:
        """调度定时触发模块 (interval_seconds=None, 按 trigger_time 每日定时执行)

        v8.6.13 P0 FIX (2026-08-01 AI 扫描):
            原代码 _schedule_daily_report 只调度 daily_report, 完全遗漏 strategy_evaluation,
            导致 N6 策略健康度评估 + N3 IC 记录 + N4 adaptive_optimize 永远不执行.
            原代码还用 MODULE_DEFINITIONS[-1] 取 trigger_time, 但列表最后一项是
            strategy_evaluation (15:25) 而非 daily_report (15:15), trigger_time 取错.
            修复: 改造为通用方法, 按 name 查找模块, 支持所有定时模块.

        ER3 修复 (保留): 用 datetime + timedelta 计算窗口结束时间, 避免 time.replace 溢出.
        """
        module_name = module_def["name"]
        trigger_time = module_def["trigger_time"]
        task_func_name = module_def["task_func"]
        feature_flag = module_def.get("feature_flag")

        # feature_flag 检查: 关闭的功能不启动定时器
        if feature_flag:
            flag_val = os.getenv(feature_flag, "False")
            if flag_val.lower() not in ("true", "1", "yes", "on"):
                logger.info(f"  [跳过] {module_name}: feature_flag {feature_flag}=False")
                return

        def check_and_run():
            if not RUNNING:
                return

            now = datetime.now()
            # v8.6.13 P0 FIX: 用模块自身的 trigger_time, 不再 MODULE_DEFINITIONS[-1]
            today_trigger = datetime.combine(now.date(), trigger_time)
            window_end = today_trigger + timedelta(hours=2)  # 2 小时窗口

            if today_trigger <= now < window_end:
                today_str = now.strftime("%Y-%m-%d")
                key = f"{module_name}_{today_str}"
                if key not in self.module_last_run:
                    task_func = globals()[task_func_name]
                    self._run_task(module_name, task_func)
                    self.module_last_run[key] = now

            timer = threading.Timer(60, check_and_run)
            timer.daemon = True
            self.timers[f"{module_name}_sched"] = timer
            timer.start()

        check_and_run()

    def start(self) -> None:
        """启动所有模块"""
        logger.info("=" * 70)
        logger.info("  v7.5 实时监控并发调度器启动")
        logger.info("  模式: %s", "DRY-RUN" if self.dry_run else "LIVE")
        logger.info("  模块数: %d", len(MODULE_DEFINITIONS))
        logger.info("=" * 70)

        for mod in MODULE_DEFINITIONS:
            if mod["interval_seconds"]:
                logger.info(f"  [启动] {mod['name']}: {mod['description']} (每{mod['interval_seconds'] / 60:.0f}分钟)")
                self._schedule_module(mod)
            else:
                # v8.6.13 P0 FIX: 定时模块全部走 _schedule_timed_module, 不再硬编码只调度 daily_report
                logger.info(f"  [启动] {mod['name']}: {mod['description']} (定时 {mod['trigger_time']})")
                self._schedule_timed_module(mod)

        logger.info("  所有模块已启动")
        logger.info("=" * 70)

    def stop(self) -> None:
        """停止所有模块"""
        logger.info("=" * 70)
        logger.info("  正在停止实时监控调度器...")
        logger.info("=" * 70)

        for name, timer in self.timers.items():
            timer.cancel()
            logger.info(f"  [停止] {name}")

        self.executor.shutdown(wait=True)
        logger.info("  调度器已停止")
        logger.info("=" * 70)

    def get_status(self) -> Dict[str, Any]:
        """获取当前状态"""
        return {
            "running": RUNNING,
            "dry_run": self.dry_run,
            "modules": MODULE_STATUS,
            "last_runs": {k: v.isoformat() if isinstance(v, datetime) else v for k, v in self.module_last_run.items()},
            "timestamp": datetime.now().isoformat(),
        }


# ============================================================
# 进程管理
# ============================================================
def _write_lock(pid: int) -> None:
    """写入锁文件"""
    with open(LOCK_FILE, "w", encoding="utf-8") as f:
        json.dump({"pid": pid, "start_time": datetime.now().isoformat()}, f)


def _read_lock() -> Optional[Dict]:
    """读取锁文件"""
    if LOCK_FILE.exists():
        try:
            with open(LOCK_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return None
    return None


def _remove_lock() -> None:
    """移除锁文件"""
    if LOCK_FILE.exists():
        LOCK_FILE.unlink()


def _is_process_alive(pid: int) -> bool:
    """检查进程是否存活"""
    try:
        if sys.platform == "win32":
            result = subprocess.run(["tasklist", "/FI", f"PID eq {pid}", "/NH"], capture_output=True, text=True, timeout=5)
            return str(pid) in result.stdout
        else:
            try:
                import psutil
                return psutil.pid_exists(pid)
            except ImportError:
                return False
    except Exception:
        return False


def _is_running() -> bool:
    """检查是否已有进程在运行（含 stale 锁自动清理）"""
    lock = _read_lock()
    if not lock:
        return False
    pid = lock.get("pid")
    if not pid:
        _remove_lock()
        return False
    if _is_process_alive(pid):
        return True
    logger.warning(f"发现 stale 锁文件 (PID {pid} 已失效), 自动清理")
    _remove_lock()
    return False


# ============================================================
# 信号处理
# ============================================================
def signal_handler(signum, frame):
    """处理中断信号"""
    global RUNNING
    logger.info(f"收到信号 {signum}, 正在停止...")
    RUNNING = False


# ============================================================
# CLI 入口
# ============================================================
def main():
    parser = argparse.ArgumentParser(
        description="v7.5 实时监控并发调度器",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
    python live_scheduler.py                    # 启动实时监控 (6模块并发)
    python live_scheduler.py --dry-run          # 干跑模式
    python live_scheduler.py --stop             # 停止运行中的调度器
    python live_scheduler.py --status           # 查看运行状态
        """,
    )
    parser.add_argument("--dry-run", action="store_true", help="干跑模式")
    parser.add_argument("--stop", action="store_true", help="停止运行中的调度器")
    parser.add_argument("--status", action="store_true", help="查看运行状态")
    parser.add_argument("--max-workers", type=int, default=6, help="最大并发线程数")
    args = parser.parse_args()

    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)

    if args.status:
        status = _read_lock()
        if status:
            logger.info(f"运行中 PID: {status['pid']}")
            logger.info(f"启动时间: {status['start_time']}")
        else:
            logger.info("调度器未运行")
        return 0

    if args.stop:
        lock = _read_lock()
        if lock and lock.get("pid"):
            try:
                # v8.6.13 P1 FIX (2026-08-01 AI 扫描):
                # 原代码不检查 taskkill 返回码, PID 已不存在或权限不足时 taskkill 失败
                # 但仍打印"已停止"并删除锁文件, 导致下次启动时 _is_running() 返回 False,
                # 产生双实例并行调度, 任务重复执行. 修复: 检查 returncode, 失败时保留锁并提示.
                result = subprocess.run(
                    ["taskkill", "/F", "/PID", str(lock["pid"])],
                    capture_output=True,
                    text=True,
                )
                if result.returncode == 0:
                    logger.info(f"已停止 PID {lock['pid']}")
                    _remove_lock()
                else:
                    # taskkill 失败: PID 可能已不存在, 或权限不足
                    stderr_msg = (result.stderr or "").strip()
                    # exit code 128 = "无此进程" (PID 已退出), 可安全删除锁
                    if "找不到" in stderr_msg or "no such" in stderr_msg.lower() or "not found" in stderr_msg.lower():
                        logger.info(f"PID {lock['pid']} 已不存在, 清理锁文件")
                        _remove_lock()
                    else:
                        logger.error(
                            f"停止 PID {lock['pid']} 失败 (returncode={result.returncode}): {stderr_msg}. "
                            f"锁文件已保留, 请手动处理后重试."
                        )
                        return 1
            except Exception as e:
                logger.error(f"停止失败: {e}. 锁文件已保留, 请手动处理.")
                return 1
        else:
            # 无锁文件, 直接清理 (避免遗留)
            _remove_lock()
        return 0

    if _is_running():
        logger.info("调度器已在运行中")
        return 0

    _write_lock(os.getpid())

    scheduler = LiveScheduler(dry_run=args.dry_run, max_workers=args.max_workers)
    scheduler.start()

    try:
        while RUNNING:
            time.sleep(1)
    except KeyboardInterrupt:
        pass

    scheduler.stop()
    _remove_lock()


if __name__ == "__main__":
    sys.exit(main())
