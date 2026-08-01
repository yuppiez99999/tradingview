# -*- coding: utf-8 -*-
"""
NTP 漂移告警回调处理器 (NTP Alert Callback Handler)
====================================================
创建日期: 2026-07-26
创建原因: v8.6.8 P2-LIVE-10 — NTP 漂移 1300ms+ 告警需外发通知

设计原则:
    - 多渠道通知 (日志/文件/钉钉/邮件)
    - 渠道独立失败: 单一渠道失败不影响其他渠道
    - 配置驱动: 通过环境变量或 config 文件控制渠道启用
    - 审计留痕: 所有告警写入 reports/ntp_alerts/ 便于追溯

渠道优先级:
    1. 日志 (默认启用, 总是写入)
    2. 文件审计 (默认启用, 写入 reports/ntp_alerts/YYYY-MM-DD.jsonl)
    3. 钉钉机器人 (NTF_DINGTALK_WEBHOOK 配置后启用)
    4. 邮件 (NTF_SMTP_HOST 配置后启用)
    5. 控制台 stderr (CRITICAL 级别强制输出)

用法:
    from v8.3_institutional.src.execution.ntp_alert_callback import NTPAlertCallback
    from v8.3_institutional.src.execution.ntp_sync import NTPSync

    callback = NTPAlertCallback()
    ntp = NTPSync(alert_callback=callback.handle_alert)

通知负载结构 (alert_payload):
    {
        "level": "WARNING" | "CRITICAL",
        "drift_ms": float,
        "server": str,
        "message": str,
        "timestamp": ISO 时间戳,
        "alert_count": int,
        "thresholds": {max_drift_ms, drift_warning_ms, drift_critical_ms}
    }
"""

from __future__ import annotations

import json
import logging
import os
import sys
from datetime import datetime
from pathlib import Path
from typing import Optional

logger = logging.getLogger("ntp_alert_callback")

# 默认告警文件目录 (相对于项目根)
BASE_DIR = Path(__file__).resolve().parents[4]
DEFAULT_ALERT_DIR = BASE_DIR / "reports" / "ntp_alerts"


class NTPAlertCallback:
    """NTP 漂移告警多渠道分发器"""

    def __init__(
        self,
        alert_dir: Optional[Path] = None,
        enable_dingtalk: Optional[bool] = None,
        enable_email: Optional[bool] = None,
        enable_stderr: bool = True,
    ):
        """
        Args:
            alert_dir: 告警文件目录, 默认 reports/ntp_alerts/
            enable_dingtalk: 是否启用钉钉, None=自动检测 (有 webhook 就启用)
            enable_email: 是否启用邮件, None=自动检测 (有 SMTP 配置就启用)
            enable_stderr: 是否在 CRITICAL 时强制写 stderr
        """
        self.alert_dir = Path(alert_dir) if alert_dir else DEFAULT_ALERT_DIR
        self.alert_dir.mkdir(parents=True, exist_ok=True)

        # 自动检测渠道启用
        self._dingtalk_webhook = os.getenv("NTF_DINGTALK_WEBHOOK", "")
        self._enable_dingtalk = enable_dingtalk if enable_dingtalk is not None else bool(self._dingtalk_webhook)

        # SMTP 邮件配置
        self._smtp_host = os.getenv("NTF_SMTP_HOST", "")
        self._smtp_port = int(os.getenv("NTF_SMTP_PORT", "465"))
        self._smtp_user = os.getenv("NTF_SMTP_USER", "")
        self._smtp_password = os.getenv("NTF_SMTP_PASSWORD", "")
        self._smtp_from = os.getenv("NTF_SMTP_FROM", self._smtp_user)
        self._smtp_to = os.getenv("NTF_SMTP_TO", "")
        self._enable_email = enable_email if enable_email is not None else bool(self._smtp_host and self._smtp_to)

        self._enable_stderr = bool(enable_stderr)

        logger.info(
            "NTPAlertCallback 初始化: dingtalk=%s, email=%s, stderr=%s, alert_dir=%s",
            self._enable_dingtalk,
            self._enable_email,
            self._enable_stderr,
            self.alert_dir,
        )

    # ------------------------------------------------------------
    # 主入口 (符合 NTPSync.alert_callback 签名)
    # ------------------------------------------------------------

    def handle_alert(self, alert_payload: dict) -> None:
        """处理 NTP 漂移告警 (多渠道分发)

        Args:
            alert_payload: 告警负载 (含 level/drift_ms/server/message/timestamp 等)
        """
        level = alert_payload.get("level", "INFO")
        alert_payload.get("message", "")

        # 渠道 1: 文件审计 (始终写入)
        try:
            self._write_to_file(alert_payload)
        except Exception as e:
            logger.error("NTP 告警写入文件失败: %s", e, exc_info=True)

        # 渠道 2: stderr (仅 CRITICAL)
        if level == "CRITICAL" and self._enable_stderr:
            try:
                self._write_to_stderr(alert_payload)
            except Exception as e:
                logger.error("NTP 告警写入 stderr 失败: %s", e, exc_info=True)

        # 渠道 3: 钉钉机器人
        if self._enable_dingtalk and level in ("WARNING", "CRITICAL"):
            try:
                self._send_dingtalk(alert_payload)
            except Exception as e:
                logger.error("NTP 告警钉钉发送失败: %s", e, exc_info=True)

        # 渠道 4: 邮件
        if self._enable_email and level == "CRITICAL":
            try:
                self._send_email(alert_payload)
            except Exception as e:
                logger.error("NTP 告警邮件发送失败: %s", e, exc_info=True)

    # ------------------------------------------------------------
    # 渠道实现
    # ------------------------------------------------------------

    def _write_to_file(self, alert_payload: dict) -> None:
        """将告警写入 JSONL 文件 (每日一个文件)"""
        today = datetime.now().strftime("%Y-%m-%d")
        alert_file = self.alert_dir / f"{today}.jsonl"
        with open(alert_file, "a", encoding="utf-8") as f:
            f.write(json.dumps(alert_payload, ensure_ascii=False) + "\n")

    def _write_to_stderr(self, alert_payload: dict) -> None:
        """CRITICAL 告警强制写 stderr (确保运维可见)"""
        msg = (
            f"\n{'=' * 60}\n"
            f"🚨 NTP CRITICAL ALERT 🚨\n"
            f"{'=' * 60}\n"
            f"Level:    {alert_payload.get('level', 'CRITICAL')}\n"
            f"Drift:    {alert_payload.get('drift_ms', 0):.1f} ms\n"
            f"Server:   {alert_payload.get('server', 'unknown')}\n"
            f"Time:     {alert_payload.get('timestamp', '')}\n"
            f"Message:  {alert_payload.get('message', '')}\n"
            f"Count:    {alert_payload.get('alert_count', 0)}\n"
            f"{'=' * 60}\n"
        )
        print(msg, file=sys.stderr, flush=True)

    def _send_dingtalk(self, alert_payload: dict) -> None:
        """发送钉钉机器人通知

        需要: NTF_DINGTALK_WEBHOOK 环境变量
        """
        if not self._dingtalk_webhook:
            return

        level = alert_payload.get("level", "INFO")
        emoji = "🔴" if level == "CRITICAL" else "🟡"

        text = (
            f"{emoji} NTP 漂移告警\n"
            f"---\n"
            f"**级别**: {level}\n"
            f"**漂移**: {alert_payload.get('drift_ms', 0):.1f} ms\n"
            f"**服务器**: {alert_payload.get('server', 'unknown')}\n"
            f"**时间**: {alert_payload.get('timestamp', '')}\n"
            f"**累计**: {alert_payload.get('alert_count', 0)} 次\n"
            f"**详情**: {alert_payload.get('message', '')}\n"
            f"---\n"
            f"⚠️ 实盘交易可能受影响, 请立即检查 NTP 服务"
        )

        # 钉钉机器人 API
        payload = {
            "msgtype": "markdown",
            "markdown": {"title": f"NTP 漂移告警 - {level}", "text": text},
        }

        try:
            import urllib.request
            import json as _json

            # B310 防护: 校验 webhook URL 必须为 http/https 协议
            webhook_url = self._dingtalk_webhook
            if not webhook_url or not str(webhook_url).startswith(("http://", "https://")):
                logger.warning("钉钉 webhook URL 协议非法或为空, 跳过发送")
                return
            req = urllib.request.Request(
                webhook_url,
                data=_json.dumps(payload).encode("utf-8"),
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=5) as resp:  # nosec B310  URL已校验为http/https
                result = _json.loads(resp.read().decode("utf-8"))
                if result.get("errcode") != 0:
                    logger.warning("钉钉返回非零错误码: %s", result)
        except Exception as e:
            logger.warning("钉钉发送异常 (网络/配置问题): %s", e)

    def _send_email(self, alert_payload: dict) -> None:
        """发送邮件通知

        需要: NTF_SMTP_HOST, NTF_SMTP_USER, NTF_SMTP_PASSWORD, NTF_SMTP_TO 环境变量
        """
        if not (self._smtp_host and self._smtp_to and self._smtp_user):
            return

        try:
            import smtplib
            from email.mime.text import MIMEText
            from email.mime.multipart import MIMEMultipart

            subject = f"[NTP-CRITICAL] 漂移 {alert_payload.get('drift_ms', 0):.1f} ms 超阈值"
            body = (
                f"NTP 漂移告警\n"
                f"{'=' * 60}\n"
                f"级别: {alert_payload.get('level')}\n"
                f"漂移: {alert_payload.get('drift_ms', 0):.1f} ms\n"
                f"服务器: {alert_payload.get('server', 'unknown')}\n"
                f"时间: {alert_payload.get('timestamp', '')}\n"
                f"累计: {alert_payload.get('alert_count', 0)} 次\n"
                f"{'=' * 60}\n"
                f"详情:\n{alert_payload.get('message', '')}\n"
                f"{'=' * 60}\n"
                f"阈值配置:\n"
                f"  max_drift_ms: {alert_payload.get('thresholds', {}).get('max_drift_ms')}\n"
                f"  drift_warning_ms: {alert_payload.get('thresholds', {}).get('drift_warning_ms')}\n"
                f"  drift_critical_ms: {alert_payload.get('thresholds', {}).get('drift_critical_ms')}\n"
            )

            msg = MIMEMultipart()
            msg["From"] = self._smtp_from
            msg["To"] = self._smtp_to
            msg["Subject"] = subject
            msg.attach(MIMEText(body, "plain", "utf-8"))

            # SMTP 连接
            if self._smtp_port == 465:
                server = smtplib.SMTP_SSL(self._smtp_host, self._smtp_port, timeout=10)
            else:
                server = smtplib.SMTP(self._smtp_host, self._smtp_port, timeout=10)
                server.starttls()

            server.login(self._smtp_user, self._smtp_password)
            server.sendmail(self._smtp_from, self._smtp_to.split(","), msg.as_string())
            server.quit()
            logger.info("NTP 告警邮件已发送至 %s", self._smtp_to)
        except Exception as e:
            logger.warning("邮件发送异常 (网络/配置问题): %s", e)


# ------------------------------------------------------------
# 便捷工厂函数
# ------------------------------------------------------------


def create_default_callback() -> NTPAlertCallback:
    """创建默认的 NTP 告警回调 (自动检测环境变量配置)"""
    return NTPAlertCallback()


def create_silent_callback() -> NTPAlertCallback:
    """创建静默 NTP 告警回调 (仅写文件, 不发钉钉/邮件, 用于测试)"""
    return NTPAlertCallback(
        enable_dingtalk=False,
        enable_email=False,
        enable_stderr=False,
    )
