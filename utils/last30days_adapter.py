"""last30days 全球社交舆情适配器.

模块整合 8.4 — GitHub 热门项目集成 §3.3
Flag: USE_LAST30DAYS_SENTIMENT (默认 False, 双签启用)

将 mvanhorn/last30days-skill 的全球社交舆情信号接入本系统:
  - Reddit / X (Twitter) / YouTube / TikTok / Hacker News / Polymarket / GitHub
  - 补充 Wind/iFinD/东方财富 传统舆情源无法覆盖的海外社交信号

设计原则:
    1. 失败安全: CLI 不可用时返回空列表, 不阻断 news_sentiment_engine
    2. 缓存: 复用 external_data_source 模式 (TTL=600s)
    3. Flag 透传 (HC-1): USE_LAST30DAYS_SENTIMENT=False 时返回空列表
    4. 审计: 查询结果写入 reports/last30days/
    5. 不可变性: 所有返回均为新对象

调用方式:
    - 优先: subprocess 调用 npx skills-mcp CLI (需 Node.js + npx)
    - 降级: 返回空列表, 不阻断主流程

API:
    from utils.last30days_adapter import Last30DaysAdapter, Last30DaysSignal

    adapter = Last30DaysAdapter()
    signals = adapter.search_topic("gold price", platforms=["reddit", "x"], days=7)
    sentiment = adapter.aggregate_sentiment(signals)
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import subprocess
import threading
import traceback
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from utils.infra.feature_flags import is_enabled

logger = logging.getLogger("last30days_adapter")

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_CACHE_DIR = _PROJECT_ROOT / "data" / "external_cache"
_CACHE_DIR.mkdir(parents=True, exist_ok=True)
_AUDIT_DIR = _PROJECT_ROOT / "reports" / "last30days"
_AUDIT_DIR.mkdir(parents=True, exist_ok=True)

_CACHE_TTL = 600  # 10 分钟
_CACHE_LOCK = threading.Lock()
_CLI_TIMEOUT = 30  # subprocess 超时 (秒)


# ============================================================
# 数据结构
# ============================================================
@dataclass
class Last30DaysSignal:
    """last30days 舆情信号.

    Attributes:
        topic: 搜索主题
        platform: 平台 (reddit/x/youtube/hn/tiktok/polymarket/github)
        sentiment_score: 情感得分 [-1, 1]
        mention_count: 提及次数
        engagement_score: 互动得分 (点赞+评论+转发)
        first_seen: 首次出现时间
        last_seen: 最后出现时间
        source_urls: 来源 URL 列表
        title: 标题 (可选)
        summary: 摘要 (可选)
    """

    topic: str
    platform: str
    sentiment_score: float = 0.0
    mention_count: int = 0
    engagement_score: float = 0.0
    first_seen: str = ""
    last_seen: str = ""
    source_urls: list[str] = field(default_factory=list)
    title: str = ""
    summary: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


# ============================================================
# last30days 适配器
# ============================================================
class Last30DaysAdapter:
    """last30days-skill Python 适配器.

    调用方式: subprocess 调用 npx skills-mcp CLI
    失败安全: CLI 不可用时返回空列表, 不阻断 news_sentiment_engine
    缓存: TTL=600s (news 类)
    """

    SUPPORTED_PLATFORMS = {"reddit", "x", "youtube", "hn", "tiktok", "polymarket", "github"}

    def __init__(self, cache_ttl: int = _CACHE_TTL, cli_timeout: int = _CLI_TIMEOUT) -> None:
        self.cache_ttl = int(cache_ttl)
        self.cli_timeout = int(cli_timeout)
        self._cli_available: bool | None = None

    @property
    def cli_available(self) -> bool:
        """检查 npx CLI 是否可用 (惰性检查, 缓存结果)."""
        if self._cli_available is None:
            self._cli_available = self._check_cli()
        return self._cli_available

    def _check_cli(self) -> bool:
        """检查 npx 是否可执行."""
        try:
            result = subprocess.run(
                ["npx", "--version"],
                capture_output=True,
                timeout=5,
                shell=False,
            )
            return result.returncode == 0
        except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
            return False

    # ============================================================
    # 搜索接口
    # ============================================================
    def search_topic(
        self,
        topic: str,
        platforms: list[str] | None = None,
        days: int = 30,
    ) -> list[Last30DaysSignal]:
        """搜索主题的全球社交舆情信号.

        Args:
            topic: 搜索主题 (如 "gold price", "AI chip shortage")
            platforms: 平台列表 (None=全部支持的平台)
            days: 时间窗口 (1-30 天)

        Returns:
            Last30DaysSignal 列表, 失败时返回空列表
        """
        if not is_enabled("USE_LAST30DAYS_SENTIMENT"):
            logger.debug("USE_LAST30DAYS_SENTIMENT=False, 返回空列表")
            return []

        if not topic or not topic.strip():
            return []

        if platforms is None:
            platforms = list(self.SUPPORTED_PLATFORMS)
        platforms = [p for p in platforms if p in self.SUPPORTED_PLATFORMS]
        if not platforms:
            logger.warning("无有效平台, 返回空列表")
            return []

        days = max(1, min(int(days), 30))

        # 检查缓存
        cache_key = self._cache_key(topic, platforms, days)
        cached = self._load_cache(cache_key)
        if cached is not None:
            logger.debug("[%s] 命中缓存: %d 条信号", topic, len(cached))
            return cached

        # 调用 CLI
        signals = self._query_cli(topic, platforms, days)

        # 写缓存 + 审计
        if signals:
            self._save_cache(cache_key, signals)
            self._write_audit(topic, platforms, days, signals)

        return signals

    def _query_cli(
        self,
        topic: str,
        platforms: list[str],
        days: int,
    ) -> list[Last30DaysSignal]:
        """通过 subprocess 调用 last30days CLI.

        失败安全: 任何异常返回空列表.
        """
        if not self.cli_available:
            logger.info("npx CLI 不可用, 跳过 last30days 查询 (返回空列表)")
            return []

        try:
            # 构建 CLI 命令 (npx last30days-skill)
            cmd = [
                "npx",
                "-y",
                "last30days-skill",
                "--topic", topic,
                "--platforms", ",".join(platforms),
                "--days", str(days),
                "--format", "json",
            ]

            # 清空代理环境变量 (避免代理干扰)
            env = {k: v for k, v in os.environ.items() if k.lower() not in ("http_proxy", "https_proxy")}
            env["NO_PROXY"] = "*"

            result = subprocess.run(
                cmd,
                capture_output=True,
                timeout=self.cli_timeout,
                env=env,
                shell=False,
            )

            if result.returncode != 0:
                logger.warning(
                    "last30days CLI 返回非零: %s, stderr: %s",
                    result.returncode,
                    result.stderr.decode("utf-8", errors="replace")[:200],
                )
                return []

            return self._parse_cli_output(result.stdout, topic, platforms)

        except subprocess.TimeoutExpired:
            logger.warning("last30days CLI 超时 (%ds)", self.cli_timeout)
            return []
        except (OSError, json.JSONDecodeError) as e:
            logger.warning("last30days CLI 调用失败: %s", e)
            logger.debug(traceback.format_exc())
            return []
        except (ValueError, TypeError, KeyError, AttributeError, RuntimeError, TimeoutError, ConnectionError) as e: # noqa: BLE001  # 模块 fail-safe, 不阻断主流程
            logger.error("last30days 查询异常: %s", e)
            logger.debug(traceback.format_exc())
            return []

    def _parse_cli_output(
        self,
        stdout: bytes,
        topic: str,
        platforms: list[str],
    ) -> list[Last30DaysSignal]:
        """解析 CLI JSON 输出为 Last30DaysSignal 列表."""
        try:
            data = json.loads(stdout.decode("utf-8", errors="replace"))
        except json.JSONDecodeError:
            logger.warning("last30days CLI 输出非 JSON")
            return []

        if not isinstance(data, list):
            if isinstance(data, dict) and "results" in data:
                data = data["results"]
            else:
                return []

        signals: list[Last30DaysSignal] = []
        for item in data:
            if not isinstance(item, dict):
                continue
            try:
                platform = str(item.get("platform", "")).lower()
                if platform not in self.SUPPORTED_PLATFORMS:
                    continue
                signals.append(
                    Last30DaysSignal(
                        topic=topic,
                        platform=platform,
                        sentiment_score=float(item.get("sentiment_score", 0.0)),
                        mention_count=int(item.get("mention_count", 0)),
                        engagement_score=float(item.get("engagement_score", 0.0)),
                        first_seen=str(item.get("first_seen", "")),
                        last_seen=str(item.get("last_seen", "")),
                        source_urls=list(item.get("source_urls", [])),
                        title=str(item.get("title", "")),
                        summary=str(item.get("summary", "")),
                    )
                )
            except (ValueError, TypeError) as e:
                logger.debug("解析信号项失败: %s", e)
                continue

        return signals

    # ============================================================
    # 情感聚合
    # ============================================================
    @staticmethod
    def aggregate_sentiment(signals: list[Last30DaysSignal]) -> dict[str, float]:
        """聚合多平台信号为综合情感得分.

        Returns:
            {
                "composite_sentiment": float,  # [-1, 1] 加权平均
                "total_mentions": int,
                "total_engagement": float,
                "platform_count": int,
                "positive_ratio": float,  # 正面信号占比
                "negative_ratio": float,  # 负面信号占比
            }
        """
        if not signals:
            return {
                "composite_sentiment": 0.0,
                "total_mentions": 0,
                "total_engagement": 0.0,
                "platform_count": 0,
                "positive_ratio": 0.0,
                "negative_ratio": 0.0,
            }

        total_mentions = sum(s.mention_count for s in signals)
        total_engagement = sum(s.engagement_score for s in signals)

        # 加权情感 (按互动量加权)
        if total_engagement > 0:
            composite = sum(s.sentiment_score * s.engagement_score for s in signals) / total_engagement
        else:
            composite = sum(s.sentiment_score for s in signals) / len(signals)

        positive = sum(1 for s in signals if s.sentiment_score > 0.1)
        negative = sum(1 for s in signals if s.sentiment_score < -0.1)

        return {
            "composite_sentiment": round(composite, 4),
            "total_mentions": total_mentions,
            "total_engagement": round(total_engagement, 2),
            "platform_count": len({s.platform for s in signals}),
            "positive_ratio": round(positive / len(signals), 4),
            "negative_ratio": round(negative / len(signals), 4),
        }

    # ============================================================
    # 缓存
    # ============================================================
    def _cache_key(self, topic: str, platforms: list[str], days: int) -> str:
        """生成缓存键 (MD5 哈希)."""
        raw = f"{topic}|{','.join(sorted(platforms))}|{days}"
        return hashlib.md5(raw.encode("utf-8")).hexdigest()  # nosec B324 — 非安全用途, 仅作缓存键哈希

    def _load_cache(self, key: str) -> list[Last30DaysSignal] | None:
        """加载缓存 (过期返回 None)."""
        cache_file = _CACHE_DIR / f"last30days_{key}.json"
        if not cache_file.exists():
            return None
        try:
            mtime = datetime.fromtimestamp(cache_file.stat().st_mtime)
            if datetime.now() - mtime > timedelta(seconds=self.cache_ttl):
                return None
            with _CACHE_LOCK:
                with open(cache_file, encoding="utf-8") as f:
                    data = json.load(f)
            return [Last30DaysSignal(**item) for item in data]
        except (OSError, json.JSONDecodeError, TypeError) as e:
            logger.debug("加载缓存失败: %s", e)
            return None

    def _save_cache(self, key: str, signals: list[Last30DaysSignal]) -> None:
        """保存缓存 (原子写入)."""
        cache_file = _CACHE_DIR / f"last30days_{key}.json"
        tmp_file = cache_file.with_suffix(".json.tmp")
        try:
            with _CACHE_LOCK:
                with open(tmp_file, "w", encoding="utf-8") as f:
                    json.dump([s.to_dict() for s in signals], f, ensure_ascii=False)
                tmp_file.replace(cache_file)
        except OSError as e:
            logger.warning("保存缓存失败: %s", e)
            if tmp_file.exists():
                tmp_file.unlink(missing_ok=True)

    # ============================================================
    # 审计
    # ============================================================
    def _write_audit(
        self,
        topic: str,
        platforms: list[str],
        days: int,
        signals: list[Last30DaysSignal],
    ) -> None:
        """写审计日志 (JSONL 格式)."""
        try:
            date_str = datetime.utcnow().strftime("%Y%m%d")
            audit_file = _AUDIT_DIR / f"query_{date_str}.jsonl"
            record = {
                "timestamp": datetime.utcnow().isoformat() + "Z",
                "topic": topic,
                "platforms": platforms,
                "days": days,
                "signal_count": len(signals),
                "aggregated": self.aggregate_sentiment(signals),
            }
            with open(audit_file, "a", encoding="utf-8") as f:
                f.write(json.dumps(record, ensure_ascii=False) + "\n")
        except OSError as e:
            logger.warning("写审计日志失败: %s", e)

    # ============================================================
    # 健康检查
    # ============================================================
    def get_health(self) -> dict[str, Any]:
        """返回适配器健康状态."""
        return {
            "flag_enabled": is_enabled("USE_LAST30DAYS_SENTIMENT"),
            "cli_available": self.cli_available if self._cli_available is not None else False,
            "supported_platforms": list(self.SUPPORTED_PLATFORMS),
            "cache_ttl": self.cache_ttl,
        }


# ============================================================
# 模块级快捷函数
# ============================================================
_adapter: Last30DaysAdapter | None = None
_adapter_lock = threading.Lock()


def get_adapter() -> Last30DaysAdapter:
    """获取全局 Last30DaysAdapter 单例."""
    global _adapter
    with _adapter_lock:
        if _adapter is None:
            _adapter = Last30DaysAdapter()
        return _adapter


def search_topic(
    topic: str,
    platforms: list[str] | None = None,
    days: int = 30,
) -> list[Last30DaysSignal]:
    """快捷函数: 搜索主题舆情."""
    return get_adapter().search_topic(topic, platforms, days)
