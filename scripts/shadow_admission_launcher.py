"""Shadow 准入流程启动器 — 模块整合 8.4 (T2.4).

任务: T2.4
硬约束: HC-3 (risk_managed=True) + HC-4 (14 天观察期阻塞 Stage 2)

功能:
    1. 加载 shadow_admission.yaml 配置 (走 ConfigManager 4 级优先级, HC-5)
    2. 初始化 14 天观察期状态文件 (reports/shadow/admission_state.json)
    3. 生成每日 DSR 占位报告 (reports/shadow/{date}_dsr.json)
    4. 校验 Stage 2 推进条件 (HC-4 阻塞)
    5. 集成 FailFastMonitor (单日>3% / 3日>5% 立即终止)

用法:
    # 启动 14 天观察期
    python scripts/shadow_admission_launcher.py start

    # 生成今日 DSR 报告 (每日运行)
    python scripts/shadow_admission_launcher.py daily

    # 查看当前状态
    python scripts/shadow_admission_launcher.py status

    # 14 天后评估是否可推进 Stage 2
    python scripts/shadow_admission_launcher.py evaluate

设计原则:
    - 启动后即不可逆 (HC-4: 14 天观察期内不可推进 Stage 2)
    - fail-fast 触发立即终止 + 回滚 (用户硬约束)
    - 所有输出走 ConfigManager + reports/shadow/ (HC-5)
"""
from __future__ import annotations

import json
import logging
import sys
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

# 项目根目录
_PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from utils.config_manager import get_config  # noqa: E402

logger = logging.getLogger("shadow_admission_launcher")

# 常量
DEFAULT_CONFIG_NAME = "shadow_admission"
DEFAULT_REPORT_DIR = _PROJECT_ROOT / "reports" / "shadow"
DEFAULT_STATE_FILE = DEFAULT_REPORT_DIR / "admission_state.json"

# 可选配置名 (通过 --config 参数指定, 用于 P3 任务 T5.7/T5.8 的独立观察期)
_OVERRIDE_CONFIG_NAME: str | None = None


def set_config_name(name: str | None) -> None:
    """设置覆盖配置名 (用于 P3 等多观察期场景).

    Args:
        name: 配置名 (如 "shadow_p3_admission"), None=恢复默认
    """
    global _OVERRIDE_CONFIG_NAME
    _OVERRIDE_CONFIG_NAME = name
DEFAULT_OBSERVATION_DAYS = 14
DATETIME_FMT = "%Y-%m-%dT%H:%M:%S"
DATE_FMT = "%Y-%m-%d"


def _utcnow_iso() -> str:
    """当前 UTC 时间 ISO 格式 (带 Z 后缀)."""
    return datetime.utcnow().strftime(DATETIME_FMT) + "Z"


def _today_str() -> str:
    """今日日期字符串 (本地时区)."""
    return datetime.now().strftime(DATE_FMT)


def _load_shadow_config() -> dict[str, Any]:
    """加载 shadow 配置 (走 ConfigManager, HC-5).

    优先使用 _OVERRIDE_CONFIG_NAME (通过 --config 参数设置),
    否则回退到 DEFAULT_CONFIG_NAME ("shadow_admission").

    Returns:
        配置字典
    Raises:
        RuntimeError: 配置加载失败
    """
    config_name = _OVERRIDE_CONFIG_NAME or DEFAULT_CONFIG_NAME
    cfg = get_config(config_name, default={}) or {}
    if not cfg:
        raise RuntimeError(
            f"配置未找到: {config_name} (搜索路径见 ConfigManager 4 级优先级)"
        )
    return cfg


def _ensure_report_dir(report_dir: Path) -> Path:
    """确保报告目录存在."""
    report_dir.mkdir(parents=True, exist_ok=True)
    return report_dir


def _load_state(state_file: Path) -> dict[str, Any] | None:
    """加载状态文件 (已存在时)."""
    if not state_file.exists():
        return None
    try:
        with open(state_file, encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError) as e:
        logger.warning("状态文件读取失败: %s (%s)", state_file, e)
        return None


def _save_state(state_file: Path, state: dict[str, Any]) -> None:
    """保存状态文件."""
    state_file.parent.mkdir(parents=True, exist_ok=True)
    with open(state_file, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)


def _compute_observation_progress(started_at: str, observation_days: int) -> dict[str, Any]:
    """计算观察期进度.

    Args:
        started_at: 启动时间 ISO 字符串
        observation_days: 目标观察天数

    Returns:
        {days_elapsed, days_remaining, progress_pct, is_complete}
    """
    try:
        # 兼容带 Z 后缀的 ISO 字符串
        started_dt = datetime.fromisoformat(started_at.rstrip("Z"))
    except ValueError:
        started_dt = datetime.now()

    now = datetime.utcnow()
    days_elapsed = max(0, (now - started_dt).days)
    days_remaining = max(0, observation_days - days_elapsed)
    progress_pct = min(100.0, days_elapsed / observation_days * 100.0)
    return {
        "days_elapsed": days_elapsed,
        "days_remaining": days_remaining,
        "progress_pct": round(progress_pct, 2),
        "is_complete": days_elapsed >= observation_days,
    }


def _check_stage_2_blockers(state: dict[str, Any], criteria: dict[str, Any]) -> dict[str, Any]:
    """检查 Stage 2 推进条件 (HC-4 阻塞).

    Args:
        state: 当前状态
        criteria: 准入标准 (min_dsr / min_annual_return / max_drawdown / max_sharpe_cv)

    Returns:
        {can_promote, blockers, promoters}
    """
    blockers: list[str] = []
    promoters: list[str] = []

    # 条件 1: 观察期 >= 14 天
    progress = _compute_observation_progress(
        state.get("started_at", _utcnow_iso()),
        state.get("observation_days", DEFAULT_OBSERVATION_DAYS),
    )
    if progress["is_complete"]:
        promoters.append(f"observation_days>={state.get('observation_days', 14)}")
    else:
        blockers.append(
            f"observation_days<{state.get('observation_days', 14)} "
            f"(elapsed={progress['days_elapsed']})"
        )

    # 条件 2: fail-fast 未触发
    if state.get("fail_fast_triggered", False):
        blockers.append(
            f"fail_fast_triggered=true (reason={state.get('fail_fast_reason', 'unknown')})"
        )
    else:
        promoters.append("fail_fast_triggered=false")

    # 条件 3-6: 仅在观察期完成后才评估绩效指标
    if progress["is_complete"] and not state.get("fail_fast_triggered", False):
        metrics = state.get("latest_metrics", {}) or {}
        dsr = float(metrics.get("dsr", 0.0))
        annual_return = float(metrics.get("annual_return", 0.0))
        max_drawdown = float(metrics.get("max_drawdown", 1.0))
        sharpe_cv = float(metrics.get("sharpe_cv", float("inf")))

        min_dsr = float(criteria.get("min_dsr", 5))
        min_ar = float(criteria.get("min_annual_return", 0.15))
        max_dd = float(criteria.get("max_drawdown", 0.10))
        max_cv = float(criteria.get("max_sharpe_cv", 1.0))

        if dsr >= min_dsr:
            promoters.append(f"dsr={dsr:.2f}>={min_dsr}")
        else:
            blockers.append(f"dsr={dsr:.2f}<{min_dsr}")

        if annual_return >= min_ar:
            promoters.append(f"annual_return={annual_return:.2%}>={min_ar:.2%}")
        else:
            blockers.append(f"annual_return={annual_return:.2%}<{min_ar:.2%}")

        if max_drawdown <= max_dd:
            promoters.append(f"max_drawdown={max_drawdown:.2%}<={max_dd:.2%}")
        else:
            blockers.append(f"max_drawdown={max_drawdown:.2%}>{max_dd:.2%}")

        if sharpe_cv <= max_cv:
            promoters.append(f"sharpe_cv={sharpe_cv:.2f}<={max_cv}")
        else:
            blockers.append(f"sharpe_cv={sharpe_cv:.2f}>{max_cv}")

    return {
        "can_promote": len(blockers) == 0,
        "blockers": blockers,
        "promoters": promoters,
    }


def cmd_start() -> int:
    """启动 14 天观察期.

    Returns:
        退出码 (0=成功, 1=失败)
    """
    logger.info("=" * 70)
    logger.info("Shadow 准入流程 — 启动 14 天观察期 (T2.4, HC-3/HC-4)")
    logger.info("=" * 70)

    try:
        cfg = _load_shadow_config()
    except RuntimeError as e:
        logger.info(f"[FAIL] {e}")
        return 1

    settings = cfg.get("settings", {}) or {}
    state_file = _PROJECT_ROOT / settings.get("state_file", "reports/shadow/admission_state.json")
    report_dir = _PROJECT_ROOT / settings.get("report_dir", "reports/shadow")
    observation_days = int(settings.get("observation_days", DEFAULT_OBSERVATION_DAYS))

    # 检查是否已启动
    existing_state = _load_state(state_file)
    if existing_state:
        logger.info(f"[WARN] 观察期已启动于 {existing_state.get('started_at')}")
        logger.info(f"       状态文件: {state_file}")
        logger.info("       如需重新启动, 请先删除状态文件")
        return 1

    # 确保目录存在
    _ensure_report_dir(report_dir)

    # 初始化状态
    modules = cfg.get("modules", []) or []
    state = {
        "version": "1.0",
        "task_id": "T2.4",
        "started_at": _utcnow_iso(),
        "observation_days": observation_days,
        "min_observation_days": int(settings.get("min_observation_days", observation_days)),
        "modules": [
            {
                "name": m.get("name", ""),
                "task_id": m.get("task_id", ""),
                "feature_flag": m.get("feature_flag", ""),
                "fallback": m.get("fallback", ""),
                "status": "running",
            }
            for m in modules
        ],
        "fail_fast_triggered": False,
        "fail_fast_reason": None,
        "fail_fast_triggered_at": None,
        "latest_metrics": None,
        "daily_reports": [],
        "stage_2_promoted": False,
        "stage_2_blocked_reason": "observation_in_progress",
    }

    _save_state(state_file, state)

    logger.info("[OK] 14 天观察期已启动")
    logger.info(f"     启动时间 (UTC): {state['started_at']}")
    logger.info(f"     观察期天数: {observation_days}")
    logger.info(f"     预计完成 (UTC): {(datetime.utcnow() + timedelta(days=observation_days)).strftime(DATETIME_FMT)}Z")
    logger.info(f"     状态文件: {state_file}")
    logger.info(f"     报告目录: {report_dir}")
    print()
    logger.info("待准入模块:")
    for m in state["modules"]:
        logger.info(f"  - {m['name']} ({m['task_id']})")
        logger.info(f"    Feature Flag: {m['feature_flag']} (默认 False, 观察期后启用)")
        logger.info(f"    Fallback: {m['fallback']}")
    print()
    logger.info("Fail-Fast 触发器 (用户硬约束):")
    ff = cfg.get("fail_fast", {}) or {}
    logger.info(f"  - 单日回撤 > {ff.get('daily_drawdown_threshold', 0.03):.0%} → 立即终止")
    logger.info(f"  - 3日累计回撤 > {ff.get('cumulative_3d_drawdown_threshold', 0.05):.0%} → 立即终止")
    print()
    logger.info("HC-4 阻塞: 14 天观察期内不可推进 Stage 2")
    print()
    logger.info("下一步:")
    logger.info("  1. 每日运行: python scripts/shadow_admission_launcher.py daily")
    logger.info("  2. 查看状态: python scripts/shadow_admission_launcher.py status")
    logger.info("  3. 14 天后: python scripts/shadow_admission_launcher.py evaluate")
    return 0


def _load_daily_returns(report_dir: Path) -> tuple[list[float], list[str]]:
    """加载历史每日收益率序列 (从 daily_returns.jsonl).

    数据源格式 (JSONL, 每行一条):
        {"date": "2026-07-27", "daily_return": 0.012, "source": "real_backtest"}
        {"date": "2026-07-28", "daily_return": -0.005, "source": "real_backtest"}

    Args:
        report_dir: 报告目录

    Returns:
        (daily_returns, dates) 两个列表
    """
    jsonl_path = report_dir / "daily_returns.jsonl"
    if not jsonl_path.exists():
        return [], []

    returns: list[float] = []
    dates: list[str] = []
    try:
        with open(jsonl_path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    record = json.loads(line)
                    ret = float(record.get("daily_return", 0.0))
                    date = str(record.get("date", ""))
                    if date:
                        returns.append(ret)
                        dates.append(date)
                except (json.JSONDecodeError, ValueError, TypeError):
                    continue
    except OSError as e:
        logger.warning("读取 daily_returns.jsonl 失败: %s", e)
        return [], []

    return returns, dates


def _compute_real_metrics(
    cfg: dict[str, Any],
    daily_returns: list[float],
    dates: list[str],
    is_real_data: bool = True,
) -> tuple[dict[str, Any] | None, str | None]:
    """调用 ShadowAccountAdapter 计算真实指标.

    Args:
        cfg: Shadow 配置字典
        daily_returns: 每日收益率序列
        dates: 对应日期列表
        is_real_data: 是否为真实数据

    Returns:
        (metrics_dict, error_msg)
        metrics_dict 为 None 时表示计算失败, error_msg 含错误信息
    """
    if not daily_returns:
        return None, "no_daily_returns_data"

    try:
        from utils.alpha.shadow_account_adapter import (
            MIN_SAMPLES_FOR_DSR,
            FailFastTriggeredError,
            InsufficientReturnsError,
            ShadowAccountAdapter,
        )

        ff_cfg = cfg.get("fail_fast", {}) or {}
        adapter = ShadowAccountAdapter(
            account_id=f"shadow_T2.4_{dates[0] if dates else 'unknown'}",
            strategy_id="T2.4_modules_admission",
            initial_capital=float(cfg.get("settings", {}).get("initial_capital", 1_000_000)),
            daily_dd_threshold=float(ff_cfg.get("daily_drawdown_threshold", 0.03)),
            cumulative_3d_threshold=float(ff_cfg.get("cumulative_3d_drawdown_threshold", 0.05)),
        )

        # 重放历史收益率
        run_result = adapter.run_shadow(
            daily_returns=daily_returns,
            dates=dates,
            is_real_data=is_real_data,
        )

        # 尝试获取完整指标 (样本不足时返回降级指标)
        try:
            metrics = adapter.get_metrics()
            metrics_dict = {
                "dsr": round(metrics.dsr, 6),
                "annual_return": round(metrics.annual_return, 6),
                "max_drawdown": round(metrics.max_drawdown, 6),
                "sharpe_cv": round(metrics.sharpe_cv, 6),
                "sharpe_ratio": round(metrics.sharpe_ratio, 6),
                "total_return": round(metrics.total_return, 6),
                "final_nav": round(metrics.final_nav, 6),
                "days_tracked": metrics.days_tracked,
                "samples_for_dsr": metrics.samples_for_dsr,
                "samples_for_sharpe_cv": metrics.samples_for_sharpe_cv,
                "is_real_data": metrics.is_real_data,
                "fail_fast_triggered": metrics.fail_fast_triggered,
                "fail_fast_reason": metrics.fail_fast_reason,
                "status": "real_data" if metrics.is_real_data else "simulated_data",
            }
            if run_result.fail_fast_triggered:
                metrics_dict["status"] = "fail_fast_triggered"
                metrics_dict["termination_date"] = run_result.termination_date
            return metrics_dict, None
        except InsufficientReturnsError as e:
            # 样本不足: 返回降级指标 (仅基础字段)
            return {
                "dsr": None,
                "annual_return": None,
                "max_drawdown": None,
                "sharpe_cv": None,
                "status": "insufficient_samples",
                "note": str(e),
                "days_tracked": len(daily_returns),
                "min_samples_required": MIN_SAMPLES_FOR_DSR,
                "is_real_data": is_real_data,
                "fail_fast_triggered": run_result.fail_fast_triggered,
                "fail_fast_reason": run_result.fail_fast_reason,
            }, None

    except FailFastTriggeredError as e:
        return {
            "dsr": None,
            "annual_return": None,
            "max_drawdown": None,
            "sharpe_cv": None,
            "status": "fail_fast_triggered",
            "note": str(e),
            "is_real_data": is_real_data,
            "fail_fast_triggered": True,
        }, None
    except (ValueError, TypeError, KeyError, AttributeError, RuntimeError, OSError, TimeoutError, ConnectionError) as e:
        # 数据处理/计算/IO 异常: 格式/类型/字段/属性/运行时/网络/超时
        logger.exception("ShadowAccountAdapter 调用失败")
        return None, f"adapter_error: {type(e).__name__}: {e}"


def cmd_daily() -> int:
    """生成每日 DSR 报告 (接入 ShadowAccountAdapter 真实数据).

    数据源优先级:
        1. reports/shadow/daily_returns.jsonl (真实历史收益率序列, JSONL 格式)
        2. 配置 shadow_admission.yaml 的 simulated_returns (测试用模拟数据)
        3. 占位模式 (无数据时返回 pending_real_data)

    Returns:
        退出码 (0=成功, 1=失败)
    """
    logger.info("=" * 70)
    logger.info(f"Shadow 准入流程 — 每日 DSR 报告生成 ({_today_str()})")
    logger.info("=" * 70)

    try:
        cfg = _load_shadow_config()
    except RuntimeError as e:
        logger.info(f"[FAIL] {e}")
        return 1

    settings = cfg.get("settings", {}) or {}
    state_file = _PROJECT_ROOT / settings.get("state_file", "reports/shadow/admission_state.json")
    report_dir = _PROJECT_ROOT / settings.get("report_dir", "reports/shadow")

    state = _load_state(state_file)
    if not state:
        logger.info("[FAIL] 观察期未启动, 请先运行: python scripts/shadow_admission_launcher.py start")
        return 1

    if state.get("fail_fast_triggered", False):
        logger.info("[FAIL] fail-fast 已触发, 观察期已终止")
        logger.info(f"       原因: {state.get('fail_fast_reason')}")
        logger.info(f"       时间: {state.get('fail_fast_triggered_at')}")
        return 1

    # 计算观察期进度
    progress = _compute_observation_progress(
        state["started_at"], state["observation_days"]
    )

    # === 数据源加载 ===
    daily_returns, dates = _load_daily_returns(report_dir)
    is_real_data = bool(daily_returns)  # 真实数据源
    data_source = "daily_returns.jsonl"

    if not daily_returns:
        # 回退到配置中的模拟数据 (用于测试)
        simulated = cfg.get("simulated_returns", []) or []
        if simulated:
            daily_returns = [float(r) for r in simulated]
            # 生成对应日期 (基于观察期启动时间)
            try:
                start_dt = datetime.fromisoformat(state["started_at"].rstrip("Z"))
            except ValueError:
                start_dt = datetime.now()
            dates = [(start_dt + timedelta(days=i)).strftime("%Y-%m-%d")
                     for i in range(len(daily_returns))]
            is_real_data = False
            data_source = "simulated_returns (config)"
            logger.info(f"[INFO] 使用配置中的模拟收益率 (测试模式, {len(daily_returns)} 天)")
        else:
            # 无数据: 占位模式
            data_source = "none (placeholder)"

    logger.info(f"[INFO] 数据源: {data_source}")
    logger.info(f"[INFO] 样本数: {len(daily_returns)} 天, is_real_data={is_real_data}")

    # === 计算真实指标 ===
    if daily_returns:
        metrics, err = _compute_real_metrics(cfg, daily_returns, dates, is_real_data)
        if err:
            logger.info(f"[WARN] 真实指标计算失败: {err}, 回退到占位模式")
            metrics = {
                "dsr": None,
                "annual_return": None,
                "max_drawdown": None,
                "sharpe_cv": None,
                "status": "pending_real_data",
                "note": f"指标计算失败: {err}",
                "is_real_data": is_real_data,
            }
        else:
            logger.info(f"[OK] 真实指标已计算 (status={metrics.get('status', 'unknown')})")
            if metrics.get("dsr") is not None:
                logger.info(f"     DSR={metrics['dsr']:.4f}")
                logger.info(f"     年化={metrics['annual_return']:.2%}")
                logger.info(f"     回撤={metrics['max_drawdown']:.2%}")
                logger.info(f"     Sharpe CV={metrics['sharpe_cv']:.4f}")
    else:
        metrics = {
            "dsr": None,
            "annual_return": None,
            "max_drawdown": None,
            "sharpe_cv": None,
            "status": "pending_real_data",
            "note": "无收益率数据, 等待 daily_returns.jsonl 或 simulated_returns 配置",
            "is_real_data": False,
        }

    # === 检查 fail-fast 触发 ===
    ff_triggered = bool(metrics.get("fail_fast_triggered", False))
    ff_reason = metrics.get("fail_fast_reason")

    # 若指标计算检测到 fail-fast, 同步到状态文件
    if ff_triggered and not state.get("fail_fast_triggered", False):
        state["fail_fast_triggered"] = True
        state["fail_fast_reason"] = ff_reason
        state["fail_fast_triggered_at"] = _utcnow_iso()
        logger.info(f"[CRITICAL] fail-fast 触发! reason={ff_reason}")
        logger.info("           观察期已终止, 不可推进 Stage 2")

    # 同步 latest_metrics 到状态文件
    if metrics.get("dsr") is not None:
        state["latest_metrics"] = {
            "dsr": metrics["dsr"],
            "annual_return": metrics["annual_return"],
            "max_drawdown": metrics["max_drawdown"],
            "sharpe_cv": metrics["sharpe_cv"],
        }

    # === 生成今日 DSR 报告 ===
    today = _today_str()
    dsr_report = {
        "date": today,
        "generated_at": _utcnow_iso(),
        "task_id": "T2.4",
        "observation_progress": progress,
        "modules": state["modules"],
        "metrics": metrics,
        "data_source": {
            "type": data_source,
            "samples": len(daily_returns),
            "is_real_data": is_real_data,
            "date_range": {
                "start": dates[0] if dates else None,
                "end": dates[-1] if dates else None,
            },
        },
        "fail_fast_check": {
            "triggered": ff_triggered,
            "reason": ff_reason,
            "thresholds": {
                "daily_drawdown": cfg.get("fail_fast", {}).get("daily_drawdown_threshold", 0.03),
                "cumulative_3d": cfg.get("fail_fast", {}).get("cumulative_3d_drawdown_threshold", 0.05),
            },
        },
        "config_snapshot": {
            "single_factor": cfg.get("single_factor", {}).get("config_name", "Config_E"),
            "factor_combination": cfg.get("factor_combination", {}).get("config_name", "Config_E_plus1"),
            "risk_managed": True,  # HC-3
        },
    }

    # 保存每日报告
    report_file = report_dir / f"{today}_dsr.json"
    _ensure_report_dir(report_dir)
    with open(report_file, "w", encoding="utf-8") as f:
        json.dump(dsr_report, f, ensure_ascii=False, indent=2)

    # 更新状态文件
    daily_reports = state.get("daily_reports", []) or []
    daily_reports.append({
        "date": today,
        "file": str(report_file.relative_to(_PROJECT_ROOT)),
        "generated_at": dsr_report["generated_at"],
        "metrics_status": metrics.get("status", "unknown"),
    })
    state["daily_reports"] = daily_reports
    _save_state(state_file, state)

    print()
    logger.info("[OK] 每日 DSR 报告已生成")
    logger.info(f"     日期: {today}")
    logger.info(f"     报告文件: {report_file}")
    logger.info(f"     观察期进度: {progress['days_elapsed']}/{state['observation_days']} 天 ({progress['progress_pct']:.1f}%)")
    logger.info(f"     剩余天数: {progress['days_remaining']}")
    logger.info(f"     指标状态: {metrics.get('status', 'unknown')}")
    print()
    if ff_triggered:
        logger.info("[CRITICAL] fail-fast 已触发, 观察期终止")
    elif not progress["is_complete"]:
        logger.info("HC-4 阻塞: 观察期未完成, 不可推进 Stage 2")
    else:
        logger.info("观察期已完成, 可运行 evaluate 评估 Stage 2 推进条件")
    return 0


def cmd_status() -> int:
    """查看当前观察期状态.

    Returns:
        退出码 (0=成功, 1=失败)
    """
    logger.info("=" * 70)
    logger.info("Shadow 准入流程 — 当前状态")
    logger.info("=" * 70)

    try:
        cfg = _load_shadow_config()
    except RuntimeError as e:
        logger.info(f"[FAIL] {e}")
        return 1

    settings = cfg.get("settings", {}) or {}
    state_file = _PROJECT_ROOT / settings.get("state_file", "reports/shadow/admission_state.json")

    state = _load_state(state_file)
    if not state:
        logger.info("[INFO] 观察期未启动")
        logger.info("       运行: python scripts/shadow_admission_launcher.py start")
        return 0

    progress = _compute_observation_progress(
        state["started_at"], state["observation_days"]
    )

    logger.info(f"启动时间 (UTC): {state['started_at']}")
    logger.info(f"观察期天数: {state['observation_days']}")
    logger.info(f"已过天数: {progress['days_elapsed']}")
    logger.info(f"剩余天数: {progress['days_remaining']}")
    logger.info(f"进度: {progress['progress_pct']:.1f}%")
    logger.info(f"状态: {'已完成' if progress['is_complete'] else '进行中'}")
    print()

    logger.info("待准入模块:")
    for m in state.get("modules", []):
        logger.info(f"  - {m['name']} ({m['task_id']}) | flag={m['feature_flag']} | status={m['status']}")
    print()

    if state.get("fail_fast_triggered", False):
        logger.info("[CRITICAL] fail-fast 已触发!")
        logger.info(f"           原因: {state.get('fail_fast_reason')}")
        logger.info(f"           时间: {state.get('fail_fast_triggered_at')}")
        logger.info("           观察期已终止, 不可推进 Stage 2")
    else:
        logger.info("fail-fast: 未触发")

    print()
    daily_reports = state.get("daily_reports", []) or []
    logger.info(f"每日报告数: {len(daily_reports)}")
    if daily_reports:
        logger.info(f"最近报告: {daily_reports[-1]['date']} ({daily_reports[-1]['file']})")

    print()
    logger.info(f"状态文件: {state_file}")
    return 0


def cmd_evaluate() -> int:
    """14 天后评估是否可推进 Stage 2.

    Returns:
        退出码 (0=可推进, 1=不可推进, 2=失败)
    """
    logger.info("=" * 70)
    logger.info("Shadow 准入流程 — Stage 2 推进评估")
    logger.info("=" * 70)

    try:
        cfg = _load_shadow_config()
    except RuntimeError as e:
        logger.info(f"[FAIL] {e}")
        return 2

    settings = cfg.get("settings", {}) or {}
    state_file = _PROJECT_ROOT / settings.get("state_file", "reports/shadow/admission_state.json")

    state = _load_state(state_file)
    if not state:
        logger.info("[FAIL] 观察期未启动")
        return 2

    progress = _compute_observation_progress(
        state["started_at"], state["observation_days"]
    )

    if not progress["is_complete"]:
        logger.info("[BLOCKED] 观察期未完成 (HC-4)")
        logger.info(f"          已过: {progress['days_elapsed']}/{state['observation_days']} 天")
        logger.info(f"          剩余: {progress['days_remaining']} 天")
        return 1

    if state.get("fail_fast_triggered", False):
        logger.info("[BLOCKED] fail-fast 已触发, 不可推进 Stage 2")
        logger.info(f"          原因: {state.get('fail_fast_reason')}")
        return 1

    # 评估准入标准
    criteria = cfg.get("admission_criteria", {}) or {}
    result = _check_stage_2_blockers(state, criteria)

    logger.info(f"观察期: 已完成 ({progress['days_elapsed']}/{state['observation_days']} 天)")
    print()
    logger.info("Stage 2 推进条件评估:")
    print()
    logger.info("已满足条件 (promoters):")
    for p in result["promoters"]:
        logger.info(f"  [OK] {p}")
    print()
    logger.info("阻塞条件 (blockers):")
    if result["blockers"]:
        for b in result["blockers"]:
            logger.info(f"  [BLOCK] {b}")
    else:
        logger.info("  (无)")
    print()

    if result["can_promote"]:
        logger.info("[PASS] 所有条件已满足, 可推进 Stage 2")
        logger.info("       下一步: 双签启用 Feature Flag (USE_LLM_REPORT_ANALYZER /")
        logger.info("              USE_DECISION_THEORIES_FUSION / USE_MULTI_FACTOR_SIGNAL)")
        state["stage_2_promoted"] = True
        state["stage_2_blocked_reason"] = None
        _save_state(state_file, state)
        return 0
    else:
        logger.info("[BLOCKED] 仍有阻塞条件未满足, 不可推进 Stage 2 (HC-4)")
        state["stage_2_blocked_reason"] = "; ".join(result["blockers"])
        _save_state(state_file, state)
        return 1


def main() -> int:
    """主入口.

    支持可选的 --config 参数指定配置名 (用于 P3 等多观察期场景):
        python scripts/shadow_admission_launcher.py start --config shadow_p3_admission
        python scripts/shadow_admission_launcher.py daily --config shadow_p3_admission
        python scripts/shadow_admission_launcher.py status --config shadow_p3_admission
        python scripts/shadow_admission_launcher.py evaluate --config shadow_p3_admission
    """
    # 配置日志
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    args = sys.argv[1:]
    if not args:
        logger.info("用法: python scripts/shadow_admission_launcher.py {start|daily|status|evaluate} [--config <name>]")
        logger.info("  默认配置: shadow_admission (T2.4)")
        logger.info("  P3 配置:  shadow_p3_admission (T5.7/T5.8)")
        return 2

    cmd = args[0].lower()
    config_name: str | None = None

    # 解析 --config 参数
    if len(args) >= 3 and args[1] == "--config":
        config_name = args[2]
    elif "--config" in args:
        idx = args.index("--config")
        if idx + 1 < len(args):
            config_name = args[idx + 1]

    # P0 启动自检 (v8.6.14) - 默认每次启动都自检,--skip-system-check 可跳过
    if "--skip-system-check" not in args:
        try:
            from utils.system_check import assert_system_ready
            assert_system_ready()  # 失败时 sys.exit(1)
        except SystemExit:
            raise
        except (ValueError, TypeError, KeyError, AttributeError, RuntimeError, OSError, TimeoutError, ConnectionError) as e:
            # 数据处理/计算/IO 异常: 格式/类型/字段/属性/运行时/网络/超时
            logger.warning(f"[P0 自检] 异常 (容错通过): {e}")
    else:
        logger.info("[P0 自检] 已通过 --skip-system-check 跳过")

    if config_name:
        set_config_name(config_name)
        logger.info(f"[INFO] 使用配置: {config_name}")

    if cmd == "start":
        return cmd_start()
    elif cmd == "daily":
        return cmd_daily()
    elif cmd == "status":
        return cmd_status()
    elif cmd == "evaluate":
        return cmd_evaluate()
    else:
        logger.info(f"未知命令: {cmd}")
        logger.info("可用命令: start | daily | status | evaluate")
        logger.info("可选参数: --config <name> (默认: shadow_admission)")
        return 2


if __name__ == "__main__":
    sys.exit(main())
