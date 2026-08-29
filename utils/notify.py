#!/usr/bin/env python
"""
统一监控告警模块 (Unified Notification Module)
================================================
创建: 2026-08-06 工程地基修复 v9.0 (UPGRADE_PLAN_v9.0)
创建原因: live_scheduler.py / hedge_rebalance_integrator 等多处引用
    `from utils.notify import send_sms_alert`, 但该模块从未实现,
    导致 ImportError 被 fail-open 吞掉 (只 log warning "告警未发送"),
    系统运行异常无法外部通知运维人员。

设计原则:
    1. 无配置时降级到控制台日志 (不崩溃, 不阻断主流程)
    2. 单次告警超时 5 秒 (避免阻塞交易主路径)
    3. 告警失败只 log warning (fail-open, 绝不阻断交易)
    4. 基于环境变量配置 webhook (无硬编码密钥)

支持通道:
    - 钉钉 webhook (DINGTALK_WEBHOOK_URL)
    - 飞书 webhook (FEISHU_WEBHOOK_URL)
    - 控制台日志 (兜底, 无需配置)

用法:
    from utils.notify import send_alert, send_sms_alert

    # 基本告警
    send_alert("止损触发", "组合回撤超过 15%, 已触发 L2 熔断", level="critical")

    # 向后兼容 (live_scheduler.py 调用的接口)
    send_sms_alert("组合回撤超阈值")

环境变量:
    DINGTALK_WEBHOOK_URL  钉钉机器人 webhook URL
    FEISHU_WEBHOOK_URL    飞书机器人 webhook URL
    NOTIFY_TIMEOUT        告警超时秒数 (默认 5)
    NOTIFY_ENABLED        是否启用外部告警 (默认 true, false 时只打日志)
"""

from __future__ import annotations

import json
import logging
import os
import threading
from typing import Optional

logger = logging.getLogger(__name__)

# ============================================================
# 配置 (从环境变量读取, 无硬编码)
# ============================================================

_DINGTALK_URL = os.environ.get("DINGTALK_WEBHOOK_URL", "")
_FEISHU_URL = os.environ.get("FEISHU_WEBHOOK_URL", "")
_TIMEOUT = float(os.environ.get("NOTIFY_TIMEOUT", "5"))
_ENABLED = os.environ.get("NOTIFY_ENABLED", "true").lower() not in ("false", "0", "no")

# 告警级别到emoji/markdown标记的映射 (用于消息格式化)
_LEVEL_MARKER = {
    "info": "[INFO]",
    "warning": "[WARN]",
    "error": "[ERROR]",
    "critical": "[CRITICAL]",
}


def _send_dingtalk(title: str, content: str, level: str = "warning") -> bool:
    """发送钉钉 webhook 告警.

    Args:
        title: 告警标题
        content: 告警内容
        level: 告警级别 (info/warning/error/critical)

    Returns:
        True 发送成功, False 发送失败或未配置
    """
    if not _DINGTALK_URL:
        return False

    try:
        import urllib.error
        import urllib.request

        marker = _LEVEL_MARKER.get(level, "[WARN]")
        text = f"{marker} {title}\n\n{content}"
        payload = json.dumps(
            {
                "msgtype": "text",
                "text": {"content": text},
            },
            ensure_ascii=False,
        ).encode("utf-8")

        req = urllib.request.Request(
            _DINGTALK_URL,
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(
            req, timeout=_TIMEOUT
        ) as resp:  # nosec B310  # 钉钉 webhook 合法 URL
            body = resp.read().decode("utf-8", errors="replace")
            if '"errcode":0' in body or '"success"' in body.lower():
                logger.info("钉钉告警发送成功: %s", title)
                return True
            logger.warning("钉钉告警响应异常: %s", body[:200])
            return False
    except (OSError, ValueError, TypeError) as e:
        logger.warning("钉钉告警发送失败 (fail-open): %s", e)
        return False


def _send_feishu(title: str, content: str, level: str = "warning") -> bool:
    """发送飞书 webhook 告警.

    Args:
        title: 告警标题
        content: 告警内容
        level: 告警级别

    Returns:
        True 发送成功, False 发送失败或未配置
    """
    if not _FEISHU_URL:
        return False

    try:
        import urllib.error
        import urllib.request

        marker = _LEVEL_MARKER.get(level, "[WARN]")
        text = f"{marker} {title}\n{content}"
        payload = json.dumps(
            {
                "msg_type": "text",
                "content": {"text": text},
            },
            ensure_ascii=False,
        ).encode("utf-8")

        req = urllib.request.Request(
            _FEISHU_URL,
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(
            req, timeout=_TIMEOUT
        ) as resp:  # nosec B310  # 飞书 webhook 合法 URL
            body = resp.read().decode("utf-8", errors="replace")
            if "0" in body and "StatusCode" not in body:
                # 飞书成功响应通常是 {"StatusCode": 0, "StatusMessage": "success", "code": 0}
                pass
            if '"StatusCode":0' in body or '"code":0' in body:
                logger.info("飞书告警发送成功: %s", title)
                return True
            logger.warning("飞书告警响应异常: %s", body[:200])
            return False
    except (OSError, ValueError, TypeError) as e:
        logger.warning("飞书告警发送失败 (fail-open): %s", e)
        return False


def _log_alert(title: str, content: str, level: str = "warning") -> None:
    """控制台日志告警 (兜底通道, 永远可用)."""
    marker = _LEVEL_MARKER.get(level, "[WARN]")
    msg = f"告警 {marker} {title}: {content}"
    if level == "critical":
        logger.critical(msg)
    elif level == "error":
        logger.error(msg)
    elif level == "warning":
        logger.warning(msg)
    else:
        logger.info(msg)


# ============================================================
# 公共 API
# ============================================================


def send_alert(
    title: str,
    content: str,
    level: str = "warning",
    channels: Optional[list] = None,
) -> dict:
    """发送告警到所有已配置的通道.

    Args:
        title: 告警标题 (简短, <50字符)
        content: 告警内容 (详细描述)
        level: 告警级别, 可选 info/warning/error/critical
        channels: 指定通道列表, None=自动发送到所有已配置通道

    Returns:
        各通道发送结果, 如 {"dingtalk": True, "feishu": False, "log": True}

    示例:
        >>> send_alert("止损触发", "600519 跌破止损线 -3%", level="critical")
        {"dingtalk": True, "feishu": False, "log": True}
    """
    results = {"log": True}  # 日志通道永远成功

    # 控制台日志 (兜底, 永远执行)
    _log_alert(title, content, level)

    if not _ENABLED:
        logger.debug("NOTIFY_ENABLED=false, 跳过外部告警通道")
        return results

    # 在子线程中发送网络告警, 避免阻塞主流程
    # 但为了简单和可预测, 这里同步发送 (有超时保护)
    target_channels = channels or []

    if not target_channels:
        # 自动发送到所有已配置的通道
        if _DINGTALK_URL:
            target_channels.append("dingtalk")
        if _FEISHU_URL:
            target_channels.append("feishu")

    if "dingtalk" in target_channels:
        results["dingtalk"] = _send_dingtalk(title, content, level)
    if "feishu" in target_channels:
        results["feishu"] = _send_feishu(title, content, level)

    return results


def send_sms_alert(message: str, level: str = "warning") -> bool:
    """发送短信告警 (向后兼容接口).

    live_scheduler.py 等历史代码调用 `from utils.notify import send_sms_alert`。
    此函数将消息通过 webhook 通道发送 (无真实短信网关时降级为日志)。

    Args:
        message: 告警消息内容
        level: 告警级别

    Returns:
        True 至少一个通道发送成功, False 全部失败或未配置
    """
    results = send_alert("系统告警", message, level=level)
    # 排除 log 通道, 检查是否有外部通道成功
    external_ok = any(v for k, v in results.items() if k != "log")
    return external_ok


def send_async_alert(
    title: str, content: str, level: str = "warning"
) -> threading.Thread:
    """异步发送告警 (不阻塞主流程).

    用于交易主路径中, 告警发送不应影响交易延迟。

    Args:
        title: 告警标题
        content: 告警内容
        level: 告警级别

    Returns:
        已启动的告警线程 (daemon=True, 主进程退出时自动结束)
    """
    t = threading.Thread(
        target=send_alert,
        args=(title, content, level),
        daemon=True,
        name=f"notify-{level}",
    )
    t.start()
    return t


# ============================================================
# 自检入口
# ============================================================


def _self_check() -> int:
    """模块自检 (python utils/notify.py)."""
    print("=== utils/notify 自检 ===")
    print(f"NOTIFY_ENABLED: {_ENABLED}")
    print(f"DINGTALK_WEBHOOK_URL: {'已配置' if _DINGTALK_URL else '未配置 (降级日志)'}")
    print(f"FEISHU_WEBHOOK_URL: {'已配置' if _FEISHU_URL else '未配置 (降级日志)'}")
    print(f"NOTIFY_TIMEOUT: {_TIMEOUT}s")
    print()
    print("发送测试告警...")
    results = send_alert("自检测试", "utils/notify 模块自检告警, 请忽略", level="info")
    print(f"发送结果: {results}")
    print()
    print("自检完成 (无 ImportError 即模块可用)")
    return 0


if __name__ == "__main__":
    import sys

    sys.exit(_self_check())
