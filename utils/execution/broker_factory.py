"""
broker_factory — 统一券商接口装配点 (G1 QMT 真实下单接线, 2026-08-09)
                (云上桥接扩展, 2026-08-17: 新增 RemoteQmtBroker 分支)

设计原则 (遵循项目铁律):
- 永不裸实盘: 真实下单需同时满足 ①enabled=true ②dry_run=false ③TRADING_ENV=production
- 非实盘路径 fail-open 降级: 环境缺失/异常 → 降级 SimulatedBroker, 不阻断执行链路
- 实盘就绪路径 fail-closed (2026-09-10, 批次三 · 报告项 15): 三重条件全满足却装配不出
  真实 broker → 抛 `LiveBrokerUnavailableError`, **绝不降级模拟**. 理由: 静默降级会让
  订单被"模拟成交"而真实账户无仓位, 属"以为在下单、实则空转"的资金管理事故; 调用方
  必须向上抛出并拒绝启动 (决策路径 fail-close).
- 观测路径 fail-open: 装配失败仅告警, 不静默 (send_alert 通道, 缺失则 logger.warning)

装配策略:
1. config.broker.enabled=false        → SimulatedBroker (模拟, 默认)
2. xtquant 未安装                     → SimulatedBroker (降级, 告警)
3. enabled=true 但 dry_run=true       → SimulatedBroker (影子/演练, 不真实下单)
4. enabled=true 且 dry_run=false 且
   TRADING_ENV=production 且 QMT_RPC_URL 配置 → RemoteQmtBroker (云端 RPC 桥接, 调 Win 实盘机)
5. enabled=true 且 dry_run=false 且
   TRADING_ENV=production 且 QMT connect 成功 → QmtBrokerAPI (本地直连, Win 实盘机自跑)

并行体系 (v8.3 适配器体系, 2026-09-03 新增):
   get_broker_adapter() 返回 broker_adapters 注册表里的适配器 (带风控前置 + JSONL 审计),
   与 get_broker() 的 v8.4 BrokerAPI 体系并存. QMT 适配器插件
   (utils.execution.qmt_broker_adapter) 在此完成注册挂接, 使 QMT 真实下单能力
   可复用 v8.3 的风控外壳 (日限额 / 熔断 / 审计日志).
"""

from __future__ import annotations

import json
import logging
import os
import sys
from typing import Any

logger = logging.getLogger(__name__)

_PROJECT_ROOT = os.path.dirname(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
)
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)


class LiveBrokerUnavailableError(RuntimeError):
    """真实下单就绪但真实 broker 装配失败 — fail-closed 专用异常 (2026-09-10).

    触发条件: broker.enabled=true 且 dry_run=false 且 TRADING_ENV=production
    (即 `is_live_intent()` 为真) 时, 真实 broker 因 xtquant 未装 / RPC 未配 /
    connect 失败 / 构造异常等任一原因不可用.

    语义: **绝不降级为 SimulatedBroker**. 调用方 (执行器入口 / 主链路装配点) 必须
    让该异常向上传播并拒绝启动, 而不是带 `broker=None` 继续跑 — 否则系统会以
    "模拟成交"冒充实盘成交, 造成真实账户与本地账本的隐形背离.
    """


# 模拟/影子 broker 类名白名单 (用于 is_live_broker 反向判定, 覆盖 v8.4/v8.3 两套体系)
_SIMULATED_BROKER_NAMES = frozenset(
    {"SimulatedBroker", "MockBroker", "ShadowBroker", "PaperBroker"}
)


def _live_intent_from_cfg(cfg: dict) -> bool:
    """基于已加载配置判定"真实下单就绪"三重条件 (不读盘)."""
    if not cfg.get("enabled", False):
        return False
    if cfg.get("dry_run", True):
        return False
    return os.environ.get("TRADING_ENV", "sim").strip().lower() == "production"


def is_live_intent(cfg: dict | None = None) -> bool:
    """当前配置是否为"真实下单就绪" (①enabled ②dry_run=false ③TRADING_ENV=production).

    供执行器入口做 fail-closed 前置校验. 配置读取失败按 False 处理 (安全默认:
    视为未启用, 与既有 `_enforce_live_gate` 口径一致).
    """
    try:
        cfg = cfg if cfg is not None else _load_broker_config()
    except (OSError, ValueError, TypeError, KeyError, AttributeError, RuntimeError):
        # 配置读取异常 → 视为未启用 (安全默认, 绝不误判为实盘)
        return False
    return _live_intent_from_cfg(cfg)


def is_live_broker(broker: Any) -> bool:
    """broker 实例是否为真实券商通道 (非模拟/影子).

    以类名白名单反向判定, 避免对 v8.4 BrokerAPI / v8.3 适配器两套体系的硬依赖.
    """
    if broker is None:
        return False
    return type(broker).__name__ not in _SIMULATED_BROKER_NAMES


def _safe_send_alert(message: str, level: str = "WARNING") -> None:
    """观测路径 fail-open: 告警通道缺失则降级 logger, 绝不静默."""
    try:
        from utils.notify import send_alert

        send_alert(title=f"[broker] {level}", content=message, level=level.lower())
    except (ImportError, AttributeError, TypeError):
        logger.warning("[broker_factory] %s: %s", level, message)


def _load_broker_config() -> dict:
    """读取 system_config.json 的 broker 段, 失败返回安全默认."""
    default = {
        "type": "qmt",
        "enabled": False,
        "dry_run": True,
        "account_id": "",
        "session_id": 0,
        "account_type": "STOCK",
        "qmt_path": "",
        "connect_timeout": 10,
    }
    try:
        # 2026-09-07: 单一事实源 = 根 system_config.json
        # (原 config/system_config.json 已合并至根文件并删除, 勿再指向 config/ 子目录)
        cfg_path = os.path.join(_PROJECT_ROOT, "system_config.json")
        with open(cfg_path, encoding="utf-8") as f:
            cfg = json.load(f)
        broker_cfg = cfg.get("broker", {})
        default.update(broker_cfg)
    except (
        ValueError,
        KeyError,
        TypeError,
        AttributeError,
        OSError,
        RuntimeError,
    ) as exc:
        _safe_send_alert(f"broker 配置读取失败, 使用安全默认: {exc}", "WARNING")
    return default


def get_broker(config: dict | None = None) -> Any:
    """
    统一 broker 装配入口.

    门控矩阵 (三重条件 ①enabled ②dry_run=false ③TRADING_ENV=production):
        ①②③ 全满足 → 走真实通道 (RemoteQmtBroker / QmtBrokerAPI);
                       装配失败 **抛 LiveBrokerUnavailableError** (fail-closed, 不降级)
        ① 满足但 ③ 不满足 → SimulatedBroker (防裸实盘, 告警)
        ① 满足但 ② 不满足 (dry_run) → SimulatedBroker (影子/演练)
        ① 不满足 (默认) → SimulatedBroker (模拟盘)

    Returns:
        BrokerAPI 实例 (RemoteQmtBroker / QmtBrokerAPI / SimulatedBroker)

    Raises:
        LiveBrokerUnavailableError: 真实下单就绪但真实 broker 装不出来 (fail-closed)
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
    if not _live_intent_from_cfg(cfg):
        _safe_send_alert(
            "broker enabled 但 TRADING_ENV≠production, 降级模拟", "WARNING"
        )
        return _build_simulated(cfg)

    # 4. 真实下单就绪: 优先云端 RPC 桥接 (QMT_RPC_URL 配置时), 否则本地 QMT 直连.
    #    live=True → 任何装配失败都抛 LiveBrokerUnavailableError, 绝不降级模拟.
    if os.environ.get("QMT_RPC_URL", "").strip():
        return _build_remote_qmt(cfg, live=True)
    return _build_qmt(cfg, live=True)


def _degrade_or_raise(
    cfg: dict, reason: str, level: str = "CRITICAL", live: bool = False
) -> Any:
    """统一的"降级模拟 or fail-closed 抛出"分支 (2026-09-10).

    观测路径 (live=False): 告警 + 降级 SimulatedBroker (不阻断链路);
    决策路径 (live=True):  告警 + 抛 LiveBrokerUnavailableError (绝不降级).
    """
    if live:
        msg = f"{reason} — 实盘就绪, fail-closed 拒绝降级模拟"
        _safe_send_alert(msg, "CRITICAL")
        raise LiveBrokerUnavailableError(msg)
    _safe_send_alert(f"{reason}, 降级 SimulatedBroker", level)
    return _build_simulated(cfg)


def _build_simulated(cfg: dict, shadow: bool = False, live: bool = False) -> Any:
    """构造模拟 broker (降级/影子).

    live=True 时拒绝构造: 真实下单就绪场景下模拟盘不可作为兜底 (fail-closed, 防御未来误用).
    """
    if live:
        msg = "真实下单就绪, 拒绝构造 SimulatedBroker (fail-closed)"
        _safe_send_alert(msg, "CRITICAL")
        raise LiveBrokerUnavailableError(msg)
    try:
        from ms_strategy.src.execution.broker_api import SimulatedBroker

        capital = float(os.environ.get("TOTAL_CAPITAL", "5000000"))
        broker = SimulatedBroker(initial_capital=capital)
        mode = "shadow" if shadow else "sim"
        logger.info("[broker_factory] 使用 SimulatedBroker (%s 模式)", mode)
        return broker
    except (
        ValueError,
        KeyError,
        TypeError,
        AttributeError,
        OSError,
        RuntimeError,
    ) as exc:
        _safe_send_alert(f"SimulatedBroker 构造失败: {exc}", "CRITICAL")
        raise


def _build_remote_qmt(cfg: dict, live: bool = False) -> Any:
    """构造远程 QMT broker (云端 → Win 实盘机 RPC 网关).

    live=False (观测路径): 依赖缺失/未配置/connect 失败 → 降级 SimulatedBroker + 告警;
    live=True  (决策路径): 上述任一情况 → 抛 LiveBrokerUnavailableError (fail-closed).

    环境变量:
        QMT_RPC_URL     — Win 网关地址
        QMT_RPC_TOKEN   — 鉴权 token
        QMT_RPC_TIMEOUT — HTTP 超时 (默认 10s)
    """
    try:
        from utils.execution.remote_qmt_broker import HTTPX_AVAILABLE, RemoteQmtBroker

        if not HTTPX_AVAILABLE:
            return _degrade_or_raise(
                cfg, "httpx 未安装, RemoteQmtBroker 不可用", "WARNING", live
            )

        rpc_url = os.environ.get("QMT_RPC_URL", "").strip()
        token = os.environ.get("QMT_RPC_TOKEN", "").strip()
        timeout = float(os.environ.get("QMT_RPC_TIMEOUT", "10"))

        if not rpc_url or not token:
            return _degrade_or_raise(
                cfg, "QMT_RPC_URL/QMT_RPC_TOKEN 未配置", "WARNING", live
            )

        broker = RemoteQmtBroker(rpc_url=rpc_url, token=token, timeout=timeout)
        if not broker.connect():
            return _degrade_or_raise(
                cfg, f"RemoteQmtBroker connect() 失败 ({rpc_url})", "CRITICAL", live
            )
        logger.info(
            "[broker_factory] RemoteQmtBroker 已连接 (云端桥接模式): %s", rpc_url
        )
        return broker
    except LiveBrokerUnavailableError:
        raise  # fail-closed 信号透传, 勿被下方宽捕获吞掉
    except (
        ValueError,
        KeyError,
        TypeError,
        AttributeError,
        OSError,
        RuntimeError,
        ImportError,
    ) as exc:
        return _degrade_or_raise(
            cfg, f"RemoteQmtBroker 构造失败: {exc}", "CRITICAL", live
        )


def _build_qmt(cfg: dict, live: bool = False) -> Any:
    """构造 QMT 实盘 broker (本地直连).

    live=False (观测路径): xtquant 缺失/connect 失败 → 降级 SimulatedBroker + 告警;
    live=True  (决策路径): 上述任一情况 → 抛 LiveBrokerUnavailableError (fail-closed).
    """
    try:
        from ms_strategy.src.execution.qmt_broker import XTQUANT_AVAILABLE, QmtBrokerAPI

        if not XTQUANT_AVAILABLE:
            return _degrade_or_raise(
                cfg, "xtquant 未安装, QMT 不可用", "WARNING", live
            )
        broker = QmtBrokerAPI(
            account_id=cfg.get("account_id", ""),
            session_id=int(cfg.get("session_id", 0)),
            account_type=cfg.get("account_type", "STOCK"),
            path=cfg.get("qmt_path", ""),
        )
        if not broker.connect():
            return _degrade_or_raise(cfg, "QMT connect() 失败", "CRITICAL", live)
        logger.info("[broker_factory] QmtBrokerAPI 已连接 (实盘模式)")
        return broker
    except LiveBrokerUnavailableError:
        raise  # fail-closed 信号透传, 勿被下方宽捕获吞掉
    except (
        ValueError,
        KeyError,
        TypeError,
        AttributeError,
        OSError,
        RuntimeError,
    ) as exc:
        return _degrade_or_raise(cfg, f"QmtBrokerAPI 构造失败: {exc}", "CRITICAL", live)


def _register_adapter_plugins() -> list[str]:
    """装配 v8.3 适配器体系插件 (导入即自注册), fail-open.

    QMT 适配器插件在 import 时把自己挂进 _ADAPTER_REGISTRY; 插件缺失/导入失败
    只告警, 绝不阻断本装配点 (观测路径 fail-open).
    """
    try:
        import utils.execution.qmt_broker_adapter  # noqa: F401  # 注册副作用
    except (ImportError, ValueError, OSError, RuntimeError) as exc:
        _safe_send_alert(f"QMT adapter 插件注册失败: {exc}", "WARNING")
        return []

    try:
        from utils.execution.broker_adapters import list_supported_brokers

        return list(list_supported_brokers())
    except (ImportError, AttributeError, ValueError, OSError, RuntimeError) as exc:
        _safe_send_alert(f"适配器注册表不可用: {exc}", "WARNING")
        return []


def get_broker_adapter(broker_type: str = "qmt", config: dict | None = None) -> Any:
    """v8.3 适配器体系入口 (与 get_broker() 的 v8.4 BrokerAPI 体系并行).

    适用于需要「风控前置 + JSONL 审计 + 日交易限额 + 熔断」外壳的下单场景.
    注意: 返回的适配器默认 dry-run (config.live=False), 真实下单须额外满足
    TRADING_ENV=production 且 xtquant 已安装 (见 qmt_broker_adapter 三重门控).

    Args:
        broker_type: 适配器类型 (qmt / ths / xueqiu / ctp)
        config: 适配器配置 (缺省读 system_config.json 的 broker 段)

    Returns:
        适配器实例 (未 connect, 由调用方显式 connect)
    """
    _register_adapter_plugins()
    try:
        from utils.execution.broker_adapters import create_broker_adapter
    except (ImportError, AttributeError, ValueError, OSError, RuntimeError) as exc:
        _safe_send_alert(f"broker_adapters 不可用: {exc}", "CRITICAL")
        raise

    cfg = dict(config if config is not None else _load_broker_config())
    # v8.3 体系用 live 表达实盘; v8.4 配置用 enabled+dry_run → 语义转换 (默认安全)
    if "live" not in cfg:
        cfg["live"] = bool(cfg.get("enabled", False)) and not bool(
            cfg.get("dry_run", True)
        )
    # 影子/演练模式: 未开 live 时明确不连真实券商
    if not cfg["live"]:
        _safe_send_alert(
            f"broker adapter ({broker_type}) 以 dry-run 装配, 不真实下单", "INFO"
        )
    return create_broker_adapter(broker_type, cfg)


if __name__ == "__main__":
    import logging as _logging

    _logging.basicConfig(level=_logging.INFO)
    b = get_broker()
    logger.info("broker type: %s", type(b).__name__)
