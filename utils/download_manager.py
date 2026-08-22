"""统一下载管理器 — Motrix 风格脚手架 (Wave 9 / W9-A)

灵感来源: https://github.com/agalwood/Motrix (53,651 stars, 2026-08-19 GitHub Trending)
Motrix 是 Electron 桌面应用 (TypeScript), 本模块仅借鉴其设计理念:
  - 任务队列 + 断点续传
  - 多协议支持 (HTTP/HTTPS/FTP)
  - 并发控制 + 限速
  - 任务状态机 (pending/downloading/paused/completed/failed)

设计原则:
  - 边缘安全: 不动 data_collect/ 现有脚本, 仅提供新下载 API
  - 复用 http_session: 通过 make_no_proxy_session 绕过系统代理
  - 09-05 前仅脚手架 + POC, 不接入 15_每日工作流/ 主链路
  - 09-06 起进入实质对接 (W9-A Sprint)

接入点:
  - data/downloads/ (现有下载目录)
  - utils/http_session.py (无代理 Session)
  - data_collect/ 各脚本 (09-06 后逐步迁移)

关联文档:
  - docs/高价值项目集成排期计划_20260811.md §8.2 (W9-A Sprint)
  - cairn/github-trending-wave9-20260819.md (待创建)
"""
from __future__ import annotations

import logging
import os
import time
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Optional

from utils.http_session import make_no_proxy_session

try:
    from utils.logging_manager import get_logger
    logger = get_logger("download_manager")
except ImportError:
    logger = logging.getLogger("download_manager")


# ============================================================
# 任务状态机
# ============================================================

class DownloadStatus(StrEnum):
    PENDING = "pending"
    DOWNLOADING = "downloading"
    PAUSED = "paused"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELED = "canceled"


@dataclass
class DownloadTask:
    """下载任务 (Motrix 风格)

    Attributes:
        url: 资源 URL
        dest: 本地保存路径 (绝对路径)
        filename: 文件名 (None 时从 URL 推断)
        status: 当前状态
        progress: 进度 0.0~1.0
        size_total: 总字节数 (未知为 -1)
        size_downloaded: 已下载字节数
        speed: 当前速度 (bytes/s)
        retry_count: 已重试次数
        max_retries: 最大重试次数
        created_at: 任务创建时间戳
        updated_at: 最近更新时间戳
        error: 失败原因 (status=FAILED 时填)
        checksum: 期望校验和 (md5/sha256, 可选)
    """
    url: str
    dest: str
    filename: Optional[str] = None
    status: DownloadStatus = DownloadStatus.PENDING
    progress: float = 0.0
    size_total: int = -1
    size_downloaded: int = 0
    speed: float = 0.0
    retry_count: int = 0
    max_retries: int = 3
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    error: Optional[str] = None
    checksum: Optional[str] = None

    @property
    def dest_path(self) -> Path:
        return Path(self.dest) / (self.filename or self._infer_filename())

    def _infer_filename(self) -> str:
        from urllib.parse import unquote, urlparse
        name = unquote(os.path.basename(urlparse(self.url).path))
        return name or f"download_{int(self.created_at)}.bin"


# ============================================================
# 下载管理器
# ============================================================

class DownloadManager:
    """统一下载管理器 (脚手架)

    W9-A 阶段仅实现单任务同步下载 + 断点续传;
    W9-A 后续迭代加入并发队列 + 限速 + 多协议。

    Usage:
        mgr = DownloadManager()
        task = mgr.submit("https://example.com/data.csv", "data/downloads")
        mgr.run(task)  # 同步执行
        print(task.status, task.progress)
    """

    def __init__(
        self,
        default_dest: Optional[str] = None,
        max_retries: int = 3,
        chunk_size: int = 1 << 16,  # 64 KB
        timeout: int = 60,
    ) -> None:
        self.default_dest = default_dest or str(
            Path(__file__).resolve().parent.parent / "data" / "downloads"
        )
        self.max_retries = max_retries
        self.chunk_size = chunk_size
        self.timeout = timeout
        self._session = make_no_proxy_session("download_manager")
        self._tasks: list[DownloadTask] = []

    def submit(
        self,
        url: str,
        dest: Optional[str] = None,
        filename: Optional[str] = None,
        checksum: Optional[str] = None,
    ) -> DownloadTask:
        """提交下载任务 (不入队执行, 需调用 run)"""
        task = DownloadTask(
            url=url,
            dest=dest or self.default_dest,
            filename=filename,
            max_retries=self.max_retries,
            checksum=checksum,
        )
        self._tasks.append(task)
        logger.info("submit task: %s -> %s", url, task.dest_path)
        return task

    def run(self, task: DownloadTask) -> DownloadTask:
        """同步执行单个下载任务 (含断点续传 + 重试)

        Returns:
            更新后的 task (status=COMPLETED 或 FAILED)
        """
        for attempt in range(task.max_retries + 1):
            task.retry_count = attempt
            try:
                self._download_with_resume(task)
                if task.checksum:
                    self._verify_checksum(task)
                task.status = DownloadStatus.COMPLETED
                task.progress = 1.0
                task.updated_at = time.time()
                logger.info("completed: %s (%d bytes)", task.url, task.size_downloaded)
                return task
            except Exception as exc:
                task.error = str(exc)
                task.updated_at = time.time()
                logger.warning("attempt %d failed: %s (%s)", attempt + 1, task.url, exc)
                if attempt < task.max_retries:
                    time.sleep(2 ** attempt)  # 指数退避

        task.status = DownloadStatus.FAILED
        logger.error("failed after %d retries: %s", task.max_retries + 1, task.url)
        return task

    def _download_with_resume(self, task: DownloadTask) -> None:
        """断点续传下载核心"""
        dest_path = task.dest_path
        dest_path.parent.mkdir(parents=True, exist_ok=True)

        resume_offset = 0
        if dest_path.exists():
            resume_offset = dest_path.stat().st_size
            task.size_downloaded = resume_offset

        headers = {"Range": f"bytes={resume_offset}-"} if resume_offset else {}
        resp = self._session.get(
            task.url, headers=headers, stream=True, timeout=self.timeout
        )
        resp.raise_for_status()

        total = int(resp.headers.get("Content-Length", -1))
        if total > 0:
            task.size_total = total + resume_offset

        task.status = DownloadStatus.DOWNLOADING
        task.updated_at = time.time()
        mode = "ab" if resume_offset else "wb"
        t0 = time.time()

        with open(dest_path, mode) as fp:
            for chunk in resp.iter_content(chunk_size=self.chunk_size):
                if not chunk:
                    continue
                fp.write(chunk)
                task.size_downloaded += len(chunk)
                if task.size_total > 0:
                    task.progress = task.size_downloaded / task.size_total
                task.speed = task.size_downloaded / max(time.time() - t0, 1e-6)
                task.updated_at = time.time()

    def _verify_checksum(self, task: DownloadTask) -> None:
        """校验和验证 (占位, W9-A 后续实现)"""
        # TODO(W9-A): 实现 md5/sha256 校验
        logger.debug("checksum verify skipped (TODO): %s", task.dest_path)

    def list_tasks(self) -> list[DownloadTask]:
        return list(self._tasks)

    def cancel(self, task: DownloadTask) -> None:
        task.status = DownloadStatus.CANCELED
        task.updated_at = time.time()
        logger.info("canceled: %s", task.url)


# ============================================================
# 便捷函数
# ============================================================

def download(url: str, dest: Optional[str] = None, filename: Optional[str] = None) -> DownloadTask:
    """一次性下载便捷函数"""
    mgr = DownloadManager()
    task = mgr.submit(url, dest, filename)
    return mgr.run(task)
