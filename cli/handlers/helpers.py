"""统一入口通用辅助函数 (报告落盘/归档/股票名/ML 信号段/ETF 资金流/执行日志/实盘闸门)。

自 量化策略系统_统一入口_v8.6.py 字节级迁出 (2026-09-10, 审计 item 11 结构拆解)。
零行为变更: 迁出代码与原文逐字节一致, 仅裸 datetime.now() 按项目时区规范改为 now_bj()。
"""

from __future__ import annotations

import glob
import json
import os
import sys

from cli.handlers.support import (
    BASE_DIR,
    ML_ENHANCED_PREDICTOR_AVAILABLE,
    ML_PREDICTOR_AVAILABLE,
    EnhancedPredictor,
    ETFFundFlowMonitor,
    connector_manager,
    data_provider,
    do_archive_report,
    event_tracker,
    load_portfolio_config,
    logger,
    pd,
    run_ml_signal_scan,
)
from utils.datetime_utils import now_bj


# ============================================================
# 通用辅助函数 — 消除各 run_* 模式中的重复样板
# ============================================================
def write_report_file(report: str, filename: str | None) -> None:
    """可选：将报告写入 BASE_DIR/reports/<filename>（filename 为空则跳过）。"""
    if not filename:
        return
    report_dir = os.path.join(BASE_DIR, "reports")
    os.makedirs(report_dir, exist_ok=True)
    report_path = os.path.join(report_dir, filename)
    with open(report_path, "w", encoding="utf-8") as f:
        f.write(report)
    logger.info(f"\n✅ 报告已保存: {report_path}")


def archive_report(report: str, name: str, ext: str = ".md") -> str:
    """归档报告到「每日报告归档」目录，返回归档路径。"""
    archive_name = f'{name}_{now_bj().strftime("%Y%m%d")}{ext}'
    archive_path = do_archive_report(BASE_DIR, archive_name, report)
    logger.info(f"✅ 报告已归档: {archive_path}")
    return archive_path


def get_stock_name(code: str) -> str:
    """解析标的名称，优先从 names.py 查找，备用纯代码。"""
    try:
        from ui.components.names import STOCK_NAME_MAP

        # 去掉后缀 .SZ/.SH
        pure = code.split(".")[0] if "." in code else code
        return STOCK_NAME_MAP.get(pure, code)
    except ImportError:
        return code


def get_ml_signal_section(
    external_signals: dict | None = None, return_raw: bool = False, use_enhanced: bool = True
) -> str | None:
    """
    运行 ML 模型信号扫描，返回 Markdown 格式的信号报告段落。
    若 ML 模块不可用或扫描失败，返回 None。

    v5.9 增强:
    - use_enhanced=True 时优先使用四维优化模型 (EnhancedPredictor)
    - 包含预测窗口和过滤震荡信息
    """
    if not ML_PREDICTOR_AVAILABLE:
        return None

    result = {}
    try:
        model_dir = os.path.join(BASE_DIR, "models")
        data_dir = os.path.join(BASE_DIR, "data", "cache")

        # v5.9: 优先使用增强预测器
        enhanced_info = {}
        if (
            use_enhanced
            and ML_ENHANCED_PREDICTOR_AVAILABLE
            and EnhancedPredictor is not None
        ):
            try:
                ep = EnhancedPredictor(model_dir=model_dir, weight_method="f1_weighted")
                if ep.auto_discover_and_load(prefer_enhanced=True):
                    kline_dict = {}
                    for f in glob.glob(os.path.join(data_dir, "kline_*.parquet")):
                        code = (
                            os.path.basename(f)
                            .replace("kline_", "")
                            .replace("_daily.parquet", "")
                        )
                        try:
                            kline_dict[code] = pd.read_parquet(f)
                        except Exception:
                            continue  # noqa: BLE001  # fail-safe, 待后续精确化
                    if kline_dict:
                        signals = ep.generate_trading_signals(kline_dict)
                        info = ep.get_model_info()
                        enhanced_info = {
                            "horizon": info.get("horizon", 1),
                            "filter_oscillation": info.get("filter_oscillation", True),
                            "model_count": info.get("model_count", 0),
                            "feature_count": info.get("feature_count", 0),
                        }
                        result = {
                            "model_info": info,
                            "signals": signals,
                            "scanned_count": len(kline_dict),
                            "enhanced": True,
                        }
                        logger.info(f"增强预测器就绪: T+{enhanced_info['horizon']}")
                    else:
                        enhanced_info = {}
            except Exception as e:  # noqa: BLE001  # fail-safe, 待后续精确化
                logger.debug(f"增强预测器跳过: {e}")
                enhanced_info = {}

        if not enhanced_info and run_ml_signal_scan is not None:
            result = run_ml_signal_scan(
                data_dir=data_dir, model_dir=model_dir, threshold=0.55
            )

        if not result or "error" in result:
            logger.warning(f"ML信号扫描失败: {result.get('error', '无数据')}")
            return None

        model_info = result.get("model_info", {})
        signals = result.get("signals", {})
        is_enhanced = result.get("enhanced", False)

        lines = []
        lines.append("")
        lines.append("---")
        lines.append("")
        if is_enhanced:
            lines.append(
                f"## ML增强预测信号 v2.0 (四维优化, T+{enhanced_info.get('horizon', 1)})"
            )
        else:
            lines.append("## ML模型预测信号 (LightGBM)")
        lines.append("")
        lines.append(f"- **模型**: {model_info.get('best_model', 'N/A')}")
        lines.append(
            f"- **准确率**: {model_info.get('accuracy', 0):.2%} "
            f"| F1: {model_info.get('f1', 0):.4f} "
            f"| AUC: {model_info.get('auc', 0):.4f}"
        )
        if is_enhanced:
            lines.append(
                f"- **预测窗口**: T+{enhanced_info.get('horizon', 1)} "
                f"| 过滤震荡: {enhanced_info.get('filter_oscillation', True)} "
                f"| 模型数: {enhanced_info.get('model_count', 0)}"
            )
        lines.append(
            f"- **信号阈值**: 55% | 扫描标的: {result.get('scanned_count', 0)} 只"
        )
        ds_label = getattr(
            connector_manager, "get_data_source_label", lambda: "Unknown"
        )()
        lines.append(f"- **数据源**: {ds_label}")
        lines.append("")

        buy_signals = signals.get("buy", [])
        sell_signals = signals.get("sell", [])
        hold_signals = signals.get("hold", [])
        lines.append(
            f"买入: {len(buy_signals)} | 卖出: {len(sell_signals)} | 持有/震荡: {len(hold_signals)}"
        )
        lines.append("")

        if buy_signals:
            lines.append("### 买入信号")
            lines.append("")
            if external_signals:
                lines.append("| 代码 | 名称 | 上涨概率 | 置信度 | 外部信号 |")
                lines.append("|------|------|----------|--------|----------|")
                for s in sorted(
                    buy_signals, key=lambda x: x["probability"], reverse=True
                ):
                    name = get_stock_name(s["code"])
                    ext = external_signals.get(s["code"], {})
                    ext_label = (
                        f"{ext.get('source', '?')}: {ext.get('action', '?')}"
                        if ext
                        else ""
                    )
                    lines.append(
                        f"| {s['code']} | {name} | {s['probability']:.2%} | {s['confidence']:.2%} | {ext_label} |"
                    )
            else:
                lines.append("| 代码 | 名称 | 上涨概率 | 置信度 |")
                lines.append("|------|------|----------|--------|")
                for s in sorted(
                    buy_signals, key=lambda x: x["probability"], reverse=True
                ):
                    name = get_stock_name(s["code"])
                    lines.append(
                        f"| {s['code']} | {name} | {s['probability']:.2%} | {s['confidence']:.2%} |"
                    )
            lines.append("")

        if sell_signals:
            lines.append("### 卖出信号")
            lines.append("")
            if external_signals:
                lines.append("| 代码 | 名称 | 下跌概率 | 置信度 | 外部信号 |")
                lines.append("|------|------|----------|--------|----------|")
                for s in sorted(sell_signals, key=lambda x: x["probability"]):
                    name = get_stock_name(s["code"])
                    ext = external_signals.get(s["code"], {})
                    ext_label = (
                        f"{ext.get('source', '?')}: {ext.get('action', '?')}"
                        if ext
                        else ""
                    )
                    lines.append(
                        f"| {s['code']} | {name} | {1 - s['probability']:.2%} | {s['confidence']:.2%} | {ext_label} |"
                    )
            else:
                lines.append("| 代码 | 名称 | 下跌概率 | 置信度 |")
                lines.append("|------|------|----------|--------|")
                for s in sorted(sell_signals, key=lambda x: x["probability"]):
                    name = get_stock_name(s["code"])
                    lines.append(
                        f"| {s['code']} | {name} | {1 - s['probability']:.2%} | {s['confidence']:.2%} |"
                    )
            lines.append("")

        # v5.7 Phase 3 增强：多信号一致性分析（含AI Hedge Fund + GLM-5 + ML对比）
        if external_signals and (buy_signals or sell_signals):
            lines.append("### 多信号一致性分析")
            lines.append("")
            lines.append(
                "*三信号源融合决策：ML模型 + AI Hedge Fund + GLM-5。一致性越高，置信度越高。*"
            )
            lines.append("")

            all_ml_codes = {
                s["code"]: s for s in (buy_signals + sell_signals + hold_signals)
            }

            agree_count = 0
            conflict_count = 0
            undefined_count = 0
            consistency_rows = []

            for code, ext in external_signals.items():
                ml_sig = all_ml_codes.get(code)
                if not ml_sig:
                    undefined_count += 1
                    continue

                ml_prob = ml_sig.get("probability", 0.5)
                ml_action = (
                    "BUY" if ml_prob >= 0.55 else "SELL" if ml_prob <= 0.45 else "HOLD"
                )
                ext_action = ext.get("action", "HOLD")
                ext_source = ext.get("source", "?")

                # 判断一致性
                all_actions = [ml_action, ext_action]
                glm5_action = ext.get("glm5_action", "")
                if glm5_action:
                    all_actions.append(glm5_action)

                if all(a == "BUY" for a in all_actions if a):
                    status = "✅ 强烈一致买入"
                    agree_count += 1
                elif all(a == "SELL" for a in all_actions if a):
                    status = "🔴 强烈一致卖出"
                    agree_count += 1
                elif any(a == "BUY" and "SELL" in all_actions for a in all_actions):
                    status = "⚠️ 分歧"
                    conflict_count += 1
                else:
                    status = "🟡 中性/混合"

                # 综合投票
                buy_votes = sum(1 for a in all_actions if a == "BUY")
                sell_votes = sum(1 for a in all_actions if a == "SELL")
                hold_votes = sum(1 for a in all_actions if a == "HOLD")
                if buy_votes > sell_votes and buy_votes > hold_votes:
                    combined = "买入"
                elif sell_votes > buy_votes and sell_votes > hold_votes:
                    combined = "卖出"
                else:
                    combined = "观望"

                consistency_rows.append(
                    {
                        "code": code,
                        "name": get_stock_name(code),
                        "ml_prob": ml_prob,
                        "ml_action": ml_action,
                        "ext_source": ext_source,
                        "ext_action": ext_action,
                        "glm5": glm5_action,
                        "status": status,
                        "combined": combined,
                        "confidence": ml_sig.get("confidence", 0),
                    }
                )

            # 按一致性优先级排序：一致买入 > 一致卖出 > 其余
            consistency_rows.sort(
                key=lambda r: (
                    (
                        0
                        if "一致买入" in r["status"]
                        else 1 if "一致卖出" in r["status"] else 2
                    ),
                    -r["confidence"],
                )
            )

            # 汇总统计
            lines.append("| 指标 | 数值 |")
            lines.append("|------|------|")
            lines.append(f"| ML预测标的总数 | {len(all_ml_codes)} |")
            lines.append(f"| 多信号对照标的 | {len(consistency_rows)} |")
            lines.append(f"| 信号一致 | {agree_count} |")
            lines.append(f"| 信号冲突 | {conflict_count} |")
            lines.append(
                f"| 一致率 | {agree_count / max(agree_count + conflict_count, 1):.1%} |"
            )
            lines.append("")

            # 详细一致性表格
            if consistency_rows:
                lines.append(
                    "| 代码 | 名称 | ML概率 | ML | 外部信号 | 外部 | GLM-5 | 状态 | 综合 |"
                )
                lines.append(
                    "|------|------|--------|----|----------|------|-------|------|------|"
                )
                for r in consistency_rows:
                    ml_label = (
                        "🟢"
                        if r["ml_action"] == "BUY"
                        else "🔴" if r["ml_action"] == "SELL" else "🟡"
                    )
                    ext_label = (
                        "🟢"
                        if r["ext_action"] == "BUY"
                        else "🔴" if r["ext_action"] == "SELL" else "🟡"
                    )
                    glm5_label = (
                        "🟢"
                        if r["glm5"] == "BUY"
                        else (
                            "🔴"
                            if r["glm5"] == "SELL"
                            else "🟡" if r["glm5"] == "HOLD" else "-"
                        )
                    )
                    lines.append(
                        f"| {r['code']} | {r['name']} | {r['ml_prob']:.2%} | "
                        f"{ml_label} | {r['ext_source']} | {ext_label} | "
                        f"{glm5_label} | {r['status']} | {r['combined']} |"
                    )
                lines.append("")

                # 置信度最高的一致信号
                consensus_buys = [
                    r for r in consistency_rows if "一致买入" in r["status"]
                ]
                consensus_sells = [
                    r for r in consistency_rows if "一致卖出" in r["status"]
                ]
                conflicts = [r for r in consistency_rows if "分歧" in r["status"]]

                if consensus_buys:
                    top_buys = sorted(
                        consensus_buys, key=lambda r: r["confidence"], reverse=True
                    )[:3]
                    items = [f"{r['name']}({r['confidence']:.1%})" for r in top_buys]
                    lines.append(f"**高置信一致买入**: {', '.join(items)}")
                if consensus_sells:
                    top_sells = sorted(
                        consensus_sells, key=lambda r: r["confidence"], reverse=True
                    )[:3]
                    items = [f"{r['name']}({r['confidence']:.1%})" for r in top_sells]
                    lines.append(f"**高置信一致卖出**: {', '.join(items)}")
                if conflicts:
                    lines.append(
                        f"**信号冲突需关注**: {', '.join(r['name'] for r in conflicts)}"
                    )
                    lines.append("")

            lines.append("")

        lines.append("> 信号仅供参考，不构成投资建议。请结合基本面与技术面综合判断。")
        report = "\n".join(lines)
        return (report, result) if return_raw else report

    except Exception as e:  # noqa: BLE001  # fail-safe, 待后续精确化
        logger.warning(f"ML信号扫描异常: {e}")
        return None


def _get_portfolio_quotes() -> dict[str, dict[str, float]]:
    """加载持仓配置并批量获取行情，返回 {code: {'price': p}}（行情不可用时返回空字典）。"""
    get_quotes_batch = data_provider.get("get_quotes_batch")
    config = load_portfolio_config()
    if not get_quotes_batch or not config:
        return {}

    assets = config.get("assets", [])
    stocks = [a["code"] for a in assets if a.get("asset_type") != "etf"]
    funds = [a["code"] for a in assets if a.get("asset_type") == "etf"]
    prices = get_quotes_batch(stocks, funds)
    result = {k: {"price": v["price"]} for k, v in prices.items() if v["price"] > 0}

    fallback_prices = config.get("fallback_prices", {})
    if fallback_prices:
        last_updated = fallback_prices.get("last_updated", "")
        fallback_map = fallback_prices.get("prices", {})
        for code in [a["code"] for a in assets]:
            if code not in result and code in fallback_map:
                result[code] = {"price": fallback_map[code]}
                logger.warning(
                    f"使用兜底价格 {code}: {fallback_map[code]} (最后更新: {last_updated})"
                )

    return result


def _build_etf_flow_data(flow_monitor: object) -> dict | None:
    """将 ETFFundFlowMonitor.flow_data 转为 SocialSecurityETFTracker 需要的格式（无数据返回 None）。"""
    if not flow_monitor.flow_data:
        return None
    return {
        code: {
            "name": data.get("name", code),
            "net_flow_yi": data.get("net_flow_yi", 0),
            "trend": data.get("trend", "中性"),
            "category": data.get("category", "未知"),
            # SC-10: 透传降级标记, SocialSecurityETFTracker 据此跳过信号检测
            "mock_degraded": bool(data.get("mock_degraded")),
        }
        for code, data in flow_monitor.flow_data.items()
    }


def get_etf_flow_data(connector_manager: object = None) -> dict | None:
    """获取ETF资金流数据（集成版: 优先 utils.etf_fund_tracker, 回退旧监控器）。"""
    try:
        tracker = ETFFundFlowMonitor(days=5, source="auto")  # 集成版兼容别名
        tracker.analyze_fund_flow()
        return _build_etf_flow_data(tracker)
    except Exception as e:  # noqa: BLE001  # fail-safe, 待后续精确化
        logger.debug(f"集成版ETF资金流获取失败（尝试回退旧监控器）: {e}")
    if connector_manager is None:
        connector_manager = globals().get("connector_manager")
    try:
        flow_monitor = ETFFundFlowMonitor(data_connector_manager=connector_manager)
        flow_monitor.analyze_fund_flow()
        return _build_etf_flow_data(flow_monitor)
    except Exception as e:  # noqa: BLE001  # fail-safe, 待后续精确化
        logger.debug(f"获取ETF资金流数据失败（将使用静态分析）: {e}")
        return None

def _check_commodity_module() -> bool:
    """检查大宗商品基本面模块是否可用"""
    try:
        sys.path.insert(0, os.path.join(BASE_DIR, "..", "03_投研与策略生成"))
        from 大宗商品基本面综合 import get_copper_fundamentals

        return callable(get_copper_fundamentals)
    except Exception:  # noqa: BLE001  # fail-safe, 待后续精确化
        return False

def _log_execution_summary(
    mode_name: str, duration_sec: float, success: bool, result: dict | None = None
) -> None:
    """记录每个CLI模式执行的统一结构化日志。

    v5.7 Phase 1: 为所有22个CLI模式提供一致的可观测性。
    """
    metrics = {}
    if isinstance(result, dict):
        # 从结果中提取关键指标
        for key in (
            "accuracy",
            "signals_count",
            "scanned_count",
            "alerts_count",
            "buy_count",
            "sell_count",
            "hold_count",
            "total_assets",
        ):
            if key in result:
                metrics[key] = result[key]

    status_icon = "✅" if success else "❌"
    summary = f"[EXEC] {mode_name} | 耗时={duration_sec:.1f}s | {status_icon}"
    if metrics:
        summary += f" | {json.dumps(metrics, ensure_ascii=False)}"

    # 写入统一日志文件
    try:
        exec_log_dir = os.path.join(BASE_DIR, "logs", "executions")
        os.makedirs(exec_log_dir, exist_ok=True)
        exec_log_file = os.path.join(exec_log_dir, f"exec_{now_bj():%Y%m}.jsonl")
        with open(exec_log_file, "a", encoding="utf-8") as f:
            f.write(
                json.dumps(
                    {
                        "mode": mode_name,
                        "duration_sec": round(duration_sec, 2),
                        "success": success,
                        "timestamp": now_bj().isoformat(),
                        "metrics": metrics,
                    },
                    ensure_ascii=False,
                )
                + "\n"
            )
    except Exception:  # noqa: BLE001  # fail-safe, 待后续精确化
        pass

    logger.info(summary)

    # 如果失败且有时长异常(>60s)，发送告警
    if not success and duration_sec > 60:
        event_tracker.track(
            "execution_timeout",
            {
                "mode": mode_name,
                "duration_sec": duration_sec,
                "timestamp": now_bj().isoformat(),
            },
        )

def _enforce_live_gate(
    dry_run: bool, confirm_only: bool, action: str, confirm: bool = False
) -> bool:
    """UE-1 统一实盘门控 (2026-08-24).

    当系统 broker 配置为"真实下单就绪" (broker.enabled=true 且 TRADING_ENV=production)
    时, 非 dry_run/非 confirm_only 的撮合/下单动作必须显式 --yes 确认, 否则阻断。

    当前系统 broker.enabled=false (模拟盘), 门控直接放行, 不影响现有模拟流程;
    未来接 QMT 置 enabled=true 后此门控自动激活, 防止裸实盘 (memory 23032726 双签模式).

    Returns:
        True=放行; False=已阻断 (告警)
    """
    # 模拟/演练路径: 直接放行
    if dry_run or confirm_only:
        return True

    # 读取 broker 配置
    try:
        from utils.execution.broker_factory import _load_broker_config

        broker_cfg = _load_broker_config()
        broker_enabled = broker_cfg.get("enabled", False)
    except Exception:  # noqa: BLE001  # 配置读取失败按未启用处理 (安全默认)
        broker_enabled = False

    if not broker_enabled:
        # 模拟盘: 放行 (当前状态)
        return True

    # 真实 broker 已启用: 需 TRADING_ENV=production + 显式 --yes
    env = os.environ.get("TRADING_ENV", "sim").lower()
    if env != "production":
        msg = f"UE-1 阻断: broker.enabled=true 但 TRADING_ENV≠production ({env}), {action} 已禁止 (防裸实盘)"
        logger.error(f"[BLOCK] {msg}")
        try:
            from utils.notify import send_alert

            send_alert(title=f"[UE-1][BLOCK] {action}", content=msg, level="critical")
        except (ImportError, AttributeError):
            pass
        return False

    if not confirm:
        msg = (
            f"UE-1 阻断: {action} 将真实下单 (TRADING_ENV=production + broker.enabled=true), "
            f"必须加 --yes 显式确认"
        )
        logger.error(f"[BLOCK] {msg}")
        return False

    return True
