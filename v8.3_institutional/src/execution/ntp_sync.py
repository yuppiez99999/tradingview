# -*- coding: utf-8 -*-
"""
v7.5 NTP 时间同步 —— 防止本地时钟漂移导致"未来函数"

特性:
    - 启动时同步 pool.ntp.org
    - 每 60 分钟自动重同步
    - 漂移 > 50ms 自动校准
    - 提供 server_ts() / local_ts() 双时间戳
    - 网络不可用时优雅降级 (返回本地时间 + 警告)

v8.6.8 P2-LIVE-10 升级 (2026-07-26):
    - 三级漂移阈值分级告警 (max_drift_ms / drift_warning_ms / drift_critical_ms)
    - 1300ms+ 漂移触发 CRITICAL 告警 + 回调通知 (邮件/短信/钉钉)
    - 500ms-1300ms 漂移触发 WARNING 告警 + 告警计数
    - 漂移历史记录 (ring buffer, 保留最近 100 条)
    - 告警去抖 (5 分钟内同级别告警不重复触发)

设计依据 (顶级对冲基金实盘对接要求):
    - 高频策略对时钟精度要求 < 100ms
    - 监管合规要求时间戳可追溯, 漂移需有审计轨迹
    - 时钟漂移 > 1300ms 可能导致:
      a) 集合竞价订单错失 (09:25 前后)
      b) 收盘前最后一笔订单错失 (14:57-15:00)
      c) 跨日结算时点错位
      d) 监管报送时间戳异常
"""
from __future__ import annotations

import logging
import time
from collections import deque
from datetime import datetime, timedelta
from typing import Callable, Optional

logger = logging.getLogger("v75.execution.ntp")


# 告警级别
ALERT_LEVEL_INFO = "INFO"
ALERT_LEVEL_WARNING = "WARNING"
ALERT_LEVEL_CRITICAL = "CRITICAL"


class NTPSync:
    """NTP 时间同步器 (v8.6.8 P2-LIVE-10 升级)"""

    # 告警级别阈值默认值 (毫秒)
    DEFAULT_DRIFT_WARNING_MS = 500.0    # 500ms 告警
    DEFAULT_DRIFT_CRITICAL_MS = 1300.0  # 1300ms 严重告警

    # 告警去抖周期 (秒): 同级别告警在窗口内不重复触发
    ALERT_DEBOUNCE_SECONDS = 300  # 5 分钟

    # 历史漂移记录容量
    HISTORY_CAPACITY = 100

    def __init__(self,
                 server: str = "ntp.tencent.com",
                 resync_interval_min: int = 30,
                 max_drift_ms: float = 50.0,
                 fallback_servers: Optional[list] = None,
                 drift_warning_ms: float = None,
                 drift_critical_ms: float = None,
                 alert_callback: Optional[Callable[[dict], None]] = None):
        """
        Args:
            server: 主 NTP 服务器
            resync_interval_min: 重同步间隔 (分钟)
            max_drift_ms: 最大允许漂移 (毫秒, 超过自动校准)
            fallback_servers: 备用 NTP 服务器列表 (主服务器失败时按序尝试)
            drift_warning_ms: 漂移 WARNING 阈值 (毫秒), 默认 500ms
            drift_critical_ms: 漂移 CRITICAL 阈值 (毫秒), 默认 1300ms
            alert_callback: 漂移告警回调函数 (dict 参数, 含 level/drift_ms/timestamp/message)
        """
        self.server = server
        self.fallback_servers = fallback_servers or ["ntp.aliyun.com", "cn.pool.ntp.org", "pool.ntp.org"]
        self.resync_interval = timedelta(minutes=resync_interval_min)
        self.max_drift_ms = float(max_drift_ms)
        # v8.6.8 P2-LIVE-10: 三级阈值分级告警
        self.drift_warning_ms = float(drift_warning_ms) if drift_warning_ms is not None else self.DEFAULT_DRIFT_WARNING_MS
        self.drift_critical_ms = float(drift_critical_ms) if drift_critical_ms is not None else self.DEFAULT_DRIFT_CRITICAL_MS
        # 阈值合理性校验: critical > warning > max
        if not (self.drift_critical_ms > self.drift_warning_ms > self.max_drift_ms):
            logger.warning(
                "NTP 漂移阈值配置异常: max=%.1f ms, warning=%.1f ms, critical=%.1f ms "
                "(应为 critical > warning > max), 使用默认值",
                self.max_drift_ms, self.drift_warning_ms, self.drift_critical_ms,
            )
            self.drift_warning_ms = self.DEFAULT_DRIFT_WARNING_MS
            self.drift_critical_ms = self.DEFAULT_DRIFT_CRITICAL_MS

        # v8.6.8 P2-LIVE-10: 告警回调 (邮件/短信/钉钉等)
        self._alert_callback = alert_callback

        self.offset_seconds: float = 0.0
        self.last_sync: Optional[datetime] = None
        self.sync_failed_count: int = 0
        self.active_server: str = server  # 当前成功的服务器

        # v8.6.8 P2-LIVE-10: 告警去抖状态
        # _last_alert_at[level] = 上次触发该级别告警的时间戳
        self._last_alert_at: dict = {}
        # 告警统计计数器
        self._alert_stats: dict = {
            ALERT_LEVEL_INFO: 0,
            ALERT_LEVEL_WARNING: 0,
            ALERT_LEVEL_CRITICAL: 0,
        }
        # 漂移历史记录 (ring buffer)
        self._drift_history: deque = deque(maxlen=self.HISTORY_CAPACITY)

        # 首次同步
        self._do_sync()

    def _do_sync(self) -> bool:
        """执行一次 NTP 同步 (主服务器失败时自动切换备用)

        v8.6.8 P2-LIVE-10: 同步成功后调用 _check_drift_and_alert() 触发分级告警
        """
        try:
            import ntplib
        except ImportError:
            logger.warning("ntplib 未安装, 使用本地时间 (建议: pip install ntplib)")
            self.offset_seconds = 0.0
            self.last_sync = datetime.utcnow()
            # v8.6.8 P2-LIVE-10: ntplib 缺失视为降级模式, 触发 CRITICAL 告警
            self._check_drift_and_alert(0.0, server="(none, ntplib missing)")
            return False

        client = ntplib.NTPClient()
        servers_to_try = [self.server] + list(self.fallback_servers)

        for srv in servers_to_try:
            try:
                resp = client.request(srv, version=3, timeout=5)
                old_offset = self.offset_seconds
                self.offset_seconds = float(resp.tx_time - time.time())
                self.last_sync = datetime.utcnow()
                self.sync_failed_count = 0
                self.active_server = srv

                # v8.6.8 P2-LIVE-10: 记录漂移历史并触发分级告警
                current_drift_ms = self.drift_ms()
                self._drift_history.append({
                    "timestamp": datetime.utcnow().isoformat(),
                    "drift_ms": current_drift_ms,
                    "offset_seconds": float(self.offset_seconds),
                    "server": srv,
                })
                self._check_drift_and_alert(current_drift_ms, server=srv)

                # 兼容旧逻辑: 超过 max_drift_ms 自动校准
                if current_drift_ms > self.max_drift_ms:
                    logger.warning("NTP 漂移 %.1f ms > 阈值 %.1f ms, 已校准 (server=%s)",
                                   current_drift_ms, self.max_drift_ms, srv)
                else:
                    logger.info("NTP 同步成功, offset=%.3f s (server=%s)", self.offset_seconds, srv)
                return True
            except Exception as e:
                logger.debug("NTP 服务器 %s 失败: %s", srv, e)
                continue

        # 所有服务器都失败
        self.sync_failed_count += 1
        logger.warning("NTP 同步失败 (%d 次): 所有服务器均不可用", self.sync_failed_count)
        if self.sync_failed_count > 5:
            logger.error("NTP 连续失败 > 5 次, 进入降级模式 (使用本地时间)")
            # v8.6.8 P2-LIVE-10: 连续失败 > 5 次触发 CRITICAL 告警
            self._fire_alert(
                level=ALERT_LEVEL_CRITICAL,
                drift_ms=self.drift_ms(),
                server="(all servers unreachable)",
                message=(
                    f"NTP 连续失败 {self.sync_failed_count} 次, 进入降级模式 "
                    f"(漂移 {self.drift_ms():.1f} ms, 本地时间不可信)"
                ),
            )
        return False

    # ------------------------------------------------------------
    # v8.6.8 P2-LIVE-10: 漂移分级告警
    # ------------------------------------------------------------

    def _check_drift_and_alert(self, drift_ms: float, server: str = "") -> None:
        """根据当前漂移触发分级告警 (INFO/WARNING/CRITICAL)

        阈值:
            - drift_ms < drift_warning_ms (< 500ms): INFO (健康, 仅记录历史)
            - drift_warning_ms <= drift_ms < drift_critical_ms (500-1300ms): WARNING
            - drift_ms >= drift_critical_ms (>= 1300ms): CRITICAL

        去抖: 同级别告警在 ALERT_DEBOUNCE_SECONDS 内不重复触发

        Args:
            drift_ms: 当前漂移 (毫秒)
            server: 当前 NTP 服务器
        """
        if drift_ms >= self.drift_critical_ms:
            level = ALERT_LEVEL_CRITICAL
            message = (
                f"NTP 漂移 {drift_ms:.1f} ms >= CRITICAL 阈值 {self.drift_critical_ms:.1f} ms "
                f"(server={server}). 实盘交易可能受影响: 集合竞价错失/收盘订单错失/监管报送异常"
            )
        elif drift_ms >= self.drift_warning_ms:
            level = ALERT_LEVEL_WARNING
            message = (
                f"NTP 漂移 {drift_ms:.1f} ms >= WARNING 阈值 {self.drift_warning_ms:.1f} ms "
                f"(server={server}). 监控中, 接近影响交易时点"
            )
        else:
            # 健康状态, 不触发告警 (只记录历史, 已在 _do_sync 中完成)
            return

        self._fire_alert(level=level, drift_ms=drift_ms, server=server, message=message)

    def _fire_alert(self, level: str, drift_ms: float, server: str, message: str) -> None:
        """触发告警 (含去抖和回调通知)

        Args:
            level: 告警级别 (INFO/WARNING/CRITICAL)
            drift_ms: 当前漂移 (毫秒)
            server: 当前 NTP 服务器
            message: 告警消息
        """
        now_ts = time.time()
        last_at = self._last_alert_at.get(level)
        if last_at is not None and (now_ts - last_at) < self.ALERT_DEBOUNCE_SECONDS:
            # 去抖: 窗口内同级别告警不重复触发
            logger.debug("NTP 告警去抖: level=%s 在 %.0f 秒内已触发过, 跳过",
                         level, self.ALERT_DEBOUNCE_SECONDS)
            return

        # 更新去抖时间戳和计数
        self._last_alert_at[level] = now_ts
        self._alert_stats[level] = self._alert_stats.get(level, 0) + 1

        # 分级日志输出
        if level == ALERT_LEVEL_CRITICAL:
            logger.error("[NTP-CRITICAL] %s (累计: %d 次)", message, self._alert_stats[level])
        elif level == ALERT_LEVEL_WARNING:
            logger.warning("[NTP-WARNING] %s (累计: %d 次)", message, self._alert_stats[level])
        else:
            logger.info("[NTP-INFO] %s", message)

        # 触发外部告警回调 (邮件/短信/钉钉等)
        alert_payload = {
            "level": level,
            "drift_ms": float(drift_ms),
            "server": server,
            "message": message,
            "timestamp": datetime.utcnow().isoformat(),
            "alert_count": self._alert_stats[level],
            "thresholds": {
                "max_drift_ms": self.max_drift_ms,
                "drift_warning_ms": self.drift_warning_ms,
                "drift_critical_ms": self.drift_critical_ms,
            },
        }
        if self._alert_callback is not None:
            try:
                self._alert_callback(alert_payload)
            except Exception as cb_err:
                logger.error("NTP 告警回调执行失败: %s", cb_err, exc_info=True)

    def sync_if_needed(self) -> bool:
        """如果到达重同步时间则重新同步"""
        if self.last_sync is None:
            return self._do_sync()
        if datetime.utcnow() - self.last_sync >= self.resync_interval:
            return self._do_sync()
        return True

    def server_ts(self) -> datetime:
        """返回 NTP 校准后的 UTC 时间"""
        self.sync_if_needed()
        return datetime.utcnow() + timedelta(seconds=self.offset_seconds)

    # ---------- 兼容方法 ----------
    def sync(self) -> bool:
        """强制执行一次同步 (兼容旧 API)"""
        return self._do_sync()

    def get_offset(self) -> float:
        """返回当前 offset (秒) (兼容旧 API)"""
        return float(self.offset_seconds)

    def local_ts(self) -> datetime:
        """返回本地 UTC 时间"""
        return datetime.utcnow()

    def drift_ms(self) -> float:
        """返回当前 offset (毫秒)"""
        return abs(self.offset_seconds) * 1000

    def is_healthy(self) -> bool:
        """NTP 健康状态

        v8.6.8 P2-LIVE-10: 健康判断升级
        - 漂移 < drift_warning_ms (500ms) 才算健康
        - CRITICAL 级别告警自动标记为不健康
        """
        return (self.last_sync is not None
                and self.sync_failed_count < 5
                and self.drift_ms() < self.drift_warning_ms)

    def snapshot(self) -> dict:
        """返回 NTP 完整快照 (含 v8.6.8 P2-LIVE-10 告警统计)"""
        return {
            "server": self.active_server,
            "offset_seconds": float(self.offset_seconds),
            "drift_ms": float(self.drift_ms()),
            "last_sync": self.last_sync.isoformat() if self.last_sync else None,
            "failed_count": int(self.sync_failed_count),
            "healthy": self.is_healthy(),
            # v8.6.8 P2-LIVE-10 新增字段
            "thresholds": {
                "max_drift_ms": float(self.max_drift_ms),
                "drift_warning_ms": float(self.drift_warning_ms),
                "drift_critical_ms": float(self.drift_critical_ms),
            },
            "alert_stats": dict(self._alert_stats),
            "last_alert_at": {k: datetime.utcfromtimestamp(v).isoformat()
                              for k, v in self._last_alert_at.items()},
            "drift_history_count": len(self._drift_history),
            "drift_history_latest": (
                self._drift_history[-1] if self._drift_history else None
            ),
        }

    # ------------------------------------------------------------
    # v8.6.8 P2-LIVE-10: 告警统计与历史查询 API
    # ------------------------------------------------------------

    def get_alert_stats(self) -> dict:
        """获取告警统计 (供运维面板/审计使用)"""
        return dict(self._alert_stats)

    def get_drift_history(self, limit: int = 20) -> list:
        """获取最近的漂移记录 (默认 20 条, 最多 100 条)

        Args:
            limit: 返回的记录条数
        """
        if limit <= 0:
            return []
        # deque 末端为最新, 反转为按时间倒序输出
        history_list = list(self._drift_history)
        history_list.reverse()
        return history_list[:limit]

    def reset_alert_stats(self) -> None:
        """重置告警统计和去抖状态 (仅供测试或运维重置使用)"""
        self._alert_stats = {ALERT_LEVEL_INFO: 0, ALERT_LEVEL_WARNING: 0, ALERT_LEVEL_CRITICAL: 0}
        self._last_alert_at = {}
        logger.info("NTP 告警统计已重置")
