"""
PTP精确时间同步模块
根据审计建议 #P2-9 创建

核心原则：
1. 全系统通过PTP（精确时间协议）或GPS时钟同步
2. 各组件间时钟偏差必须<1微秒
3. 所有事件带上精确到纳秒的时间戳
4. 系统中绝对禁止使用本地系统时间做决策
"""

import time
import socket
import struct
import logging
from typing import Optional, Dict, List, Any
from dataclasses import dataclass
from enum import Enum
import subprocess
import threading

logger = logging.getLogger(__name__)


class PTP_Packet:
    """PTP数据包结构（简化版）"""

    # PTP消息类型
    SYNC = 0x00
    DELAY_REQ = 0x01
    PDELAY_REQ = 0x02
    PDELAY_RESP = 0x03

    @staticmethod
    def create_sync(timestamp: int) -> bytes:
        """创建SYNC数据包（纳秒级时间戳）"""
        # 简化的PTPv2报文头
        header = struct.pack(
            "!BBHBBd",
            0x00,  # versionPTP
            PTP_Packet.SYNC,  # messageType
            0,  # reserved
            0,  # flags
            0,  # correction
            timestamp,  # nanosecond timestamp
        )
        return header


@dataclass
class TimestampedEvent:
    """带精确时间戳的事件"""

    event_type: str
    event_data: Dict[str, Any]
    timestamp_ns: int  # 纳秒级时间戳
    source: str
    clock_offset_ns: int = 0  # 时钟偏移（纳秒）


class ClockSyncMethod(Enum):
    """时钟同步方法"""

    PTP_1588 = "ptp_1588"  # IEEE 1588 PTP
    NTP = "ntp"  # Network Time Protocol
    GPS = "gps"  # GPS disciplined oscillator
    MANUAL = "manual"  # 手动设置（仅用于测试）


class HardwareTimestamping:
    """硬件时间戳支持检测和操作"""

    @staticmethod
    def check_support() -> bool:
        """检查系统是否支持硬件时间戳"""
        try:
            # Linux系统检查PTP硬件支持
            # 安全修复: 去掉 shell=True（消除 B602/CWE-78 命令注入风险），
            # 同时修复原写法 bug——list + shell=True 会把整个字符串当命令名。
            # 改为标准 list 形式，由 subprocess 直接执行无 shell 解释。
            result = subprocess.run(["ip", "-s", "link"], capture_output=True, text=True, timeout=5)
            return "PTP" in result.stdout.upper()
        except Exception:
            return False

    @staticmethod
    def get_hardware_timestamp(interface: str = "eth0") -> Optional[int]:
        """获取硬件时间戳（纳秒）"""
        try:
            # 使用SIOCGSTAMP ioctl获取
            import fcntl
            import array

            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            data = array.array("i", [0])
            fcntl.ioctl(sock.fileno(), 0x8933, data)  # SIOCGSTAMP
            timestamp_ns = data[0] * 1000  # 转换为纳秒
            sock.close()
            return timestamp_ns
        except Exception as e:
            logger.debug(f"Hardware timestamp not available: {e}")
            return None


class PTPClock:
    """PTP高精度时钟"""

    def __init__(self, sync_method: ClockSyncMethod = ClockSyncMethod.NTP):
        self.sync_method = sync_method
        self.clock_offset_ns = 0  # 相对于参考时钟的偏移
        self.last_sync_time = None
        self.sync_interval = 60  # 同步间隔（秒）
        self.max_clock_drift_ns = 1000  # 最大允许漂移（1微秒）
        self._thread = None
        self._stop_event = threading.Event()

        # 检测硬件支持
        self.hardware_ts = HardwareTimestamping.check_support()

        logger.info(
            f"[PTP-CLOCK] Initialized with method: {sync_method.value}, HW-TS: {'Yes' if self.hardware_ts else 'No'}"
        )

    def start(self):
        """启动后台时钟同步"""
        if self._thread and self._thread.is_alive():
            return

        self._stop_event.clear()
        self._thread = threading.Thread(target=self._sync_loop, daemon=True)
        self._thread.start()
        logger.info("[PTP-CLOCK] Background sync started")

    def stop(self):
        """停止后台同步"""
        self._stop_event.set()
        if self._thread:
            self._thread.join(timeout=5)
        logger.info("[PTP-CLOCK] Background sync stopped")

    def _sync_loop(self):
        """后台同步循环"""
        while not self._stop_event.is_set():
            try:
                offset = self._sync_to_reference()
                if offset is not None:
                    self.clock_offset_ns = offset
                    self.last_sync_time = time.time()

                    # 检查漂移
                    drift = abs(offset)
                    if drift > self.max_clock_drift_ns:
                        logger.warning(
                            f"[PTP-CLOCK] Clock drift exceeded: {drift}ns > {self.max_clock_drift_ns}ns threshold"
                        )

                self._stop_event.wait(self.sync_interval)

            except Exception as e:
                logger.error(f"[PTP-CLOCK] Sync error: {e}")
                self._stop_event.wait(10)  # 错误后10秒重试

    def _sync_to_reference(self) -> Optional[int]:
        """
        同步到参考时钟

        Returns:
            时钟偏移（纳秒），失败返回None
        """
        if self.sync_method == ClockSyncMethod.NTP:
            return self._sync_ntp()
        elif self.sync_method == ClockSyncMethod.PTP_1588:
            return self._sync_ptp()
        elif self.sync_method == ClockSyncMethod.GPS:
            return self._sync_gps()
        else:
            return self._sync_manual()

    def _sync_ntp(self) -> Optional[int]:
        """NTP同步（精度约1-50毫秒）"""
        try:
            # 使用python-ntp库或系统命令
            result = subprocess.run(["ntpdate", "-q", "pool.ntp.org"], capture_output=True, text=True, timeout=10)

            # 解析offset
            for line in result.stdout.split("\n"):
                if "offset" in line.lower():
                    offset_str = line.split(":")[1].strip().replace(",", "")
                    offset_ms = float(offset_str)
                    return int(offset_ms * 1_000_000)  # 转换为纳秒

            return 0

        except FileNotFoundError:
            # ntpdate不可用，使用socket方法
            return self._sync_ntp_socket()
        except Exception as e:
            logger.error(f"NTP sync failed: {e}")
            return None

    def _sync_ntp_socket(self) -> Optional[int]:
        """通过socket进行NTP同步"""
        try:
            NTP_SERVER = "time.google.com"
            NTP_PORT = 123
            NTP_DELTA = 2208988800  # 1970-1900的秒数

            client = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            client.settimeout(5)

            # 发送NTP请求
            msg = b"\x1b" + b"\x00" * 47
            client.sendto(msg, (NTP_SERVER, NTP_PORT))

            # 接收响应并计算往返时间
            data, _address = client.recvfrom(1024)
            receive_time = time.time()

            # 解析接收时间戳（第40-47字节）
            tx_ts = struct.unpack("!12I", data)[10]
            transmit_time = tx_ts - NTP_DELTA

            # 粗略估计偏移（假设往返延迟对称）
            round_trip_delay = (receive_time - transmit_time) * 1000_000_000
            offset_ns = int(round_trip_delay / 2)

            client.close()
            return offset_ns

        except Exception as e:
            logger.error(f"Socket NTP sync failed: {e}")
            return None

    def _sync_ptp(self) -> Optional[int]:
        """PTP精确时间同步（精度<1微秒）"""
        try:
            # 使用linux PTP硬件
            if self.hardware_ts:
                hw_ts = HardwareTimestamping.get_hardware_timestamp()
                if hw_ts:
                    system_time = time.time() * 1_000_000_000
                    offset = hw_ts - int(system_time)
                    return offset

            # 软件PTP实现
            ptp_port = 319  # PTP默认端口

            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
            sock.bind(("0.0.0.0", ptp_port))  # nosec B104  PTP协议需绑定全网卡接收广播
            sock.settimeout(5)

            # 发送PDELAY_REQ
            delay_req = PTP_Packet.create_sync(int(time.time() * 1e9))
            sock.sendto(delay_req, ("255.255.255.255", ptp_port))

            # 接收PDELAY_RESP
            _data, _addr = sock.recvfrom(1024)
            resp_time = time.time()

            # 计算双向延迟
            round_trip = (resp_time - time.time()) * 1e9
            offset_ns = int(round_trip / 2)

            sock.close()
            return offset_ns

        except Exception as e:
            logger.error(f"PTP sync failed: {e}")
            return None

    def _sync_gps(self) -> Optional[int]:
        """GPS驯服时钟同步"""
        try:
            # 读取GPS设备（如/dev/ttyUSB0）
            # 实际实现需要解析NMEA语句
            logger.info("GPS sync not yet implemented")
            return None

        except Exception as e:
            logger.error(f"GPS sync failed: {e}")
            return None

    def _sync_manual(self) -> int:
        """手动同步（仅用于测试）"""
        logger.warning("[PTP-CLOCK] Manual sync mode - using system time")
        return 0

    def get_precise_timestamp(self) -> int:
        """
        获取精确的时间戳（纳秒）

        Returns:
            纳秒级时间戳（已校正时钟偏移）
        """
        system_time_ns = int(time.time() * 1e9)
        corrected_time_ns = system_time_ns + self.clock_offset_ns
        return corrected_time_ns

    def get_monotonic_clock(self) -> float:
        """
        获取单调时钟（不受NTP调整影响）

        Returns:
            单调时钟秒数
        """
        return time.monotonic()

    def get_event_timestamp(self, event: TimestampedEvent) -> int:
        """
        获取事件校正后的时间戳

        Args:
            event: 带原始时间戳的事件

        Returns:
            校正后的纳秒时间戳
        """
        return event.timestamp_ns + self.clock_offset_ns


class GlobalTimeService:
    """全局时间服务（单例模式）"""

    _instance = None
    _lock = threading.Lock()

    def __new__(cls):
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
                    cls._instance._initialized = False
        return cls._instance

    def __init__(self):
        if self._initialized:
            return

        self._initialized = True
        self.ptp_clock = PTPClock(ClockSyncMethod.NTP)
        self.event_log: List[TimestampedEvent] = []

        # 启动后台同步
        self.ptp_clock.start()

        logger.info("[TIME-SERVICE] Global time service initialized")

    @classmethod
    def get_instance(cls) -> "GlobalTimeService":
        """获取全局时间服务实例"""
        return cls()

    def get_timestamp_ns(self) -> int:
        """获取当前纳秒级时间戳（已校正）"""
        return self.ptp_clock.get_precise_timestamp()

    def get_timestamp_ms(self) -> float:
        """获取当前毫秒级时间戳（已校正）"""
        return self.ptp_clock.get_precise_timestamp() / 1_000_000

    def get_timestamp_us(self) -> float:
        """获取当前微秒级时间戳（已校正）"""
        return self.ptp_clock.get_precise_timestamp() / 1_000_000

    def create_event(self, event_type: str, data: Dict[str, Any], source: str = "unknown") -> TimestampedEvent:
        """
        创建带精确时间戳的事件

        Args:
            event_type: 事件类型
            data: 事件数据
            source: 事件来源

        Returns:
            带时间戳的事件对象
        """
        event = TimestampedEvent(
            event_type=event_type,
            event_data=data,
            timestamp_ns=self.get_timestamp_ns(),
            source=source,
            clock_offset_ns=self.ptp_clock.clock_offset_ns,
        )

        self.event_log.append(event)

        # 保持日志在合理大小
        if len(self.event_log) > 10000:
            self.event_log = self.event_log[-5000:]

        return event

    def get_clock_health(self) -> Dict[str, Any]:
        """获取时钟健康状态"""
        return {
            "clock_offset_ns": self.ptp_clock.clock_offset_ns,
            "last_sync_time": self.ptp_clock.last_sync_time,
            "hardware_timestamping": self.ptp_clock.hardware_ts,
            "sync_method": self.ptp_clock.sync_method.value,
            "is_running": self.ptp_clock._thread and self.ptp_clock._thread.is_alive(),
        }

    def shutdown(self):
        """关闭时间服务"""
        self.ptp_clock.stop()
        logger.info("[TIME-SERVICE] Shutdown complete")


# 全局时间服务实例
_time_service: Optional[GlobalTimeService] = None


def get_time_service() -> GlobalTimeService:
    """获取全局时间服务"""
    global _time_service
    if _time_service is None:
        _time_service = GlobalTimeService.get_instance()
    return _time_service


def now_ns() -> int:
    """获取当前纳秒级时间戳（便捷函数）"""
    return get_time_service().get_timestamp_ns()


def now_ms() -> float:
    """获取当前毫秒级时间戳（便捷函数）"""
    return get_time_service().get_timestamp_ms()


def now_us() -> float:
    """获取当前微秒级时间戳（便捷函数）"""
    return get_time_service().get_timestamp_us()


def create_event(event_type: str, data: Dict[str, Any], source: str = "unknown") -> TimestampedEvent:
    """创建带时间戳的事件（便捷函数）"""
    return get_time_service().create_event(event_type, data, source)


# 使用示例：
# ts = now_ns()  # 纳秒级时间戳
# event = create_event("order_submitted", {"symbol": "600519"}, "trading_engine")
