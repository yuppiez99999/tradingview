"""
broker_factory — 统一券商接口装配点 (G1 QMT 真实下单接线, 2026-08-09)

设计原则 (遵循项目铁律):
- fail-open 降级: 任何异常/环境缺失 → 降级 SimulatedBroker, 不阻断执行链路
- 永不裸实盘: 真实下单需同时满足 ①enabled=true ②dry_run=false ③TRADING_ENV=production
- 观测路径 fail-open: 装配失败仅告警, 不静默 (send_alert 通道, 缺失则 logger.warning)

装配策略:
1. config.broker.enabled=false        → SimulatedBroker (模拟, 默认)
2. xtquant 未安装                     → SimulatedBroker (降级, 告警)
3. enabled=true 但 dry_run=true       → SimulatedBroker (影子/演练, 不真实下单)
4. enabled=true 且 dry_run=false 且
   TRADING_ENV=production 且 QMT connect 成功 → QmtBrokerAPI (真实下单)
"""

import json
import logging
import os
import sys
from typing import Any, Optional

logger = logging.getLogger(__name__)

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)


def _safe_send_alert(message: str, level: str = "WARNING") -> None:
    """观测路径 fail-open: 告警通道缺失则降级 logger, 绝不静默."""
    try:
        from utils.notify import send_alert
        send_alert(content=message, level=level)
    except (ImportError, AttributeError):
        logger.warning("[broker_factory] %s: %s", level, message)


def _load_broker_config() -> dict:
    """读取 system_config.json 的 broker 段, 失败返回安全默认."""
    default = {"type": "qmt", "enabled": False, "dry_run": True,
               "account_id": "", "session_id": 0, "account_type": "STOCK",
               "qmt_path": "", "connect_timeout": 10}
    try:
        cfg_path = os.path.join(_PROJECT_ROOT, "config", "system_config.json")
        with open(cfg_path, "r", encoding="utf-8") as f:
            cfg = json.load(f)
        broker_cfg = cfg.get("broker", {})
        default.update(broker_cfg)
    except (ValueError, KeyError, TypeError, AttributeError, OSError, RuntimeError) as exc:
        _safe_send_alert(f"broker 配置读取失败, 使用安全默认: {exc}", "WARNING")
    return default


def get_broker(config: Optional[dict] = None) -> Any:
    """
    统一 broker 装配入口.

    Returns:
        BrokerAPI 实例 (QmtBrokerAPI 或 SimulatedBroker)
    """
    cfg = config or _load_broker_config()

    # 1. 未启用 → 模拟盘
    if not cfg.get("enabled", False):
        return _build_simulated(cfg)

    # 2. dry_run → 影子/演练 (不真实下单)
    if cfg.get("dry_run", True):
        _safe_send_alert("broker 已启用但 dry_run=true, 走影子模拟, 不真实下单", "INFO")
        return _build_simulated(cfg, shadow=True)

    # 3. 真实下单硬条件: TRADING_ENV=production
    if os.environ.get("TRADING_ENV", "sim").lower() != "production":
        _safe_send_alert("broker enabled 但 TRADING_ENV≠production, 降级模拟", "WARNING")
        return _build_simulated(cfg)

    # 4. 真实下单: 尝试 QMT
    return _build_qmt(cfg)


def _build_simulated(cfg: dict, shadow: bool = False) -> Any:
    """构造模拟 broker (降级/影子)."""
    try:
        from ms_strategy.src.execution.broker_api import SimulatedBroker
        capital = float(os.environ.get("TOTAL_CAPITAL", "5000000"))
        broker = SimulatedBroker(initial_capital=capital)
        mode = "shadow" if shadow else "sim"
        logger.info("[broker_factory] 使用 SimulatedBroker (%s 模式)", mode)
        return broker
    except (ValueError, KeyError, TypeError, AttributeError, OSError, RuntimeError) as exc:
        _safe_send_alert(f"SimulatedBroker 构造失败: {exc}", "CRITICAL")
        raise


def _build_qmt(cfg: dict) -> Any:
    """构造 QMT 实盘 broker, connect 失败则降级模拟 + 告警."""
    try:
        from ms_strategy.src.execution.qmt_broker import QmtBrokerAPI, XTQUANT_AVAILABLE
        if not XTQUANT_AVAILABLE:
            _safe_send_alert("xtquant 未安装, QMT 不可用, 降级 SimulatedBroker", "WARNING")
            return _build_simulated(cfg)
        broker = QmtBrokerAPI(
            account_id=cfg.get("account_id", ""),
            session_id=int(cfg.get("session_id", 0)),
            account_type=cfg.get("account_type", "STOCK"),
            path=cfg.get("qmt_path", ""),
        )
        if not broker.connect():
            _safe_send_alert("QMT connect() 失败, 降级 SimulatedBroker", "CRITICAL")
            return _build_simulated(cfg)
        logger.info("[broker_factory] QmtBrokerAPI 已连接 (实盘模式)")
        return broker
    except (ValueError, KeyError, TypeError, AttributeError, OSError, RuntimeError) as exc:
        _safe_send_alert(f"QmtBrokerAPI 构造失败, 降级模拟: {exc}", "CRITICAL")
        return _build_simulated(cfg)


if __name__ == "__main__":
    import logging as _logging
    _logging.basicConfig(level=_logging.INFO)
    b = get_broker()
    print("broker type:", type(b).__name__)