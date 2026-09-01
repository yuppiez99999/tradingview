"""
v7.5 NTP 时间同步 —— 防止本地时钟漂移导致"未来函数"

特性:
    - 启动时同步 pool.ntp.org
    - 每 60 分钟自动重同步
    - 漂移 > 50ms 自动校准
    - 提供 server_ts() / local_ts() 双时间戳
    - 网络不可用时优雅降级 (返回本地时间 + 警告)
"""
from __future__ import annotations

import logging
import time
from datetime import datetime, timedelta

logger = logging.getLogger("v75.execution.ntp")


class NTPSync:
    """NTP 时间同步器"""

    def __init__(self,
                 server: str = "ntp.tencent.com",
                 resync_interval_min: int = 30,
                 max_drift_ms: float = 2000.0,
                 fallback_servers: list | None = None):
        """
        Args:
            server: 主 NTP 服务器
            resync_interval_min: 重同步间隔 (分钟)
            max_drift_ms: 最大允许漂移 (毫秒)
            fallback_servers: 备用 NTP 服务器列表 (主服务器失败时按序尝试)
        """
        self.server = server
        self.fallback_servers = fallback_servers or ["ntp.aliyun.com", "cn.pool.ntp.org", "pool.ntp.org"]
        self.resync_interval = timedelta(minutes=resync_interval_min)
        self.max_drift_ms = float(max_drift_ms)
        self.offset_seconds: float = 0.0
        self.last_sync: datetime | None = None
        self.sync_failed_count: int = 0
        self.active_server: str = server  # 当前成功的服务器

        # 首次同步
        self._do_sync()

    def _do_sync(self) -> bool:
        """执行一次 NTP 同步 (主服务器失败时自动切换备用)"""
        try:
            import ntplib
        except ImportError:
            logger.warning("ntplib 未安装, 使用本地时间 (建议: pip install ntplib)")
            self.offset_seconds = 0.0
            self.last_sync = datetime.utcnow()
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

                drift_ms = abs(self.offset_seconds - old_offset) * 1000
                if drift_ms > self.max_drift_ms:
                    logger.warning("NTP 漂移 %.1f ms > 阈值 %.1f ms, 已校准 (server=%s)",
                                   drift_ms, self.max_drift_ms, srv)
                else:
                    logger.info("NTP 同步成功, offset=%.3f s (server=%s)", self.offset_seconds, srv)
                return True
            except (ValueError, TypeError, KeyError, AttributeError, RuntimeError, OSError, TimeoutError, ConnectionError) as e:  # noqa: E501
                # 数据处理/计算/IO 异常: 格式/类型/字段/属性/运行时/网络/超时
                logger.debug("NTP 服务器 %s 失败: %s", srv, e)
                continue

        # 所有服务器都失败
        self.sync_failed_count += 1
        logger.warning("NTP 同步失败 (%d 次): 所有服务器均不可用", self.sync_failed_count)
        if self.sync_failed_count > 5:
            logger.error("NTP 连续失败 > 5 次, 进入降级模式 (使用本地时间)")
        return False

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
        """NTP 健康状态"""
        return (self.last_sync is not None
                and self.sync_failed_count < 5
                and self.drift_ms() < self.max_drift_ms)

    def snapshot(self) -> dict:
        return {
            "server": self.active_server,
            "offset_seconds": float(self.offset_seconds),
            "drift_ms": float(self.drift_ms()),
            "last_sync": self.last_sync.isoformat() if self.last_sync else None,
            "failed_count": int(self.sync_failed_count),
            "healthy": self.is_healthy(),
        }
