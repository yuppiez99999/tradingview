# -*- coding: utf-8 -*-
"""
并发安全工具模块 (Phase 2 并发专项, 2026-07-29)

提供:
1. atomic_write_json / atomic_write_text — 临时文件 + os.replace 原子写,
   防止并发读写/进程崩溃导致 JSON 状态文件(positions.json 等)损坏。
2. get_path_lock — 按文件路径归一化的进程内锁注册表,
   同一路径的读写方在进程内互斥。
3. process_lock — 跨进程锁文件 (Windows 计划任务重入防护),
   基于 O_CREAT|O_EXCL 锁文件 + PID + 过期时间, 上下文管理器用法。

锁顺序约定 (防死锁):
- 只允许先取 path 锁再做 IO, 严禁在持有 path 锁时再取其它 path 锁;
- process_lock 必须在任何 path 锁之外获取 (最外层)。
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Callable, Dict, Iterator, List, Optional, TypeVar, Union

logger = logging.getLogger(__name__)

T = TypeVar("T")
R = TypeVar("R")

# 哨兵: 区分 "未传 fail_default" 与 "fail_default=None"
_UNSET = object()

_PATH_LOCKS: Dict[str, threading.Lock] = {}
_REGISTRY_LOCK = threading.Lock()

PathLike = Union[str, Path]


def _normalize(path: PathLike) -> str:
    return os.path.normcase(os.path.abspath(str(path)))


def get_path_lock(path: PathLike) -> threading.Lock:
    """获取与指定文件路径绑定的进程内锁 (同路径全局唯一)。"""
    key = _normalize(path)
    with _REGISTRY_LOCK:
        lock = _PATH_LOCKS.get(key)
        if lock is None:
            lock = threading.Lock()
            _PATH_LOCKS[key] = lock
        return lock


def _replace_with_retry(src: str, dst: PathLike, retries: int = 50, delay: float = 0.02) -> None:
    """Windows 下 os.replace 在目标文件被其它进程打开时抛 PermissionError
    (共享违规)。短暂重试直到读方释放句柄, 保持替换原子性。"""
    last_err: Optional[BaseException] = None
    for _ in range(retries):
        try:
            os.replace(src, dst)
            return
        except PermissionError as e:
            last_err = e
            time.sleep(delay)
    raise last_err  # type: ignore[misc]


def atomic_write_text(path: PathLike, text: str, encoding: str = "utf-8") -> None:
    """原子写文本: 临时文件写入 + os.replace 覆盖, 进程内同路径互斥。

    崩溃/并发场景下目标文件要么是旧的完整内容, 要么是新的完整内容,
    绝不会出现半截文件。Windows 共享违规自动重试 (最长约 1 秒)。
    """
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    lock = get_path_lock(target)
    with lock:
        fd, tmp_path = tempfile.mkstemp(dir=str(target.parent), suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding=encoding) as f:
                f.write(text)
                f.flush()
                os.fsync(f.fileno())
            _replace_with_retry(tmp_path, target)
        except BaseException:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
            raise


def atomic_write_json(
    path: PathLike, obj: Any, *, ensure_ascii: bool = False, indent: int = 2, default: Any = str
) -> None:
    """原子写 JSON (positions.json / 状态缓存等关键文件专用)。"""
    payload = json.dumps(obj, ensure_ascii=ensure_ascii, indent=indent, default=default)
    atomic_write_text(path, payload)


def read_json_locked(path: PathLike, default: Any = None) -> Any:
    """带进程内锁的 JSON 读取, 与 atomic_write_json 配对使用。"""
    target = Path(path)
    lock = get_path_lock(target)
    with lock:
        try:
            with open(target, "r", encoding="utf-8") as f:
                return json.load(f)
        except FileNotFoundError:
            return default
        except (json.JSONDecodeError, OSError, ValueError) as e:
            logger.warning(f"读取 JSON 失败 ({target.name}): {e}")
            return default


@contextmanager
def process_lock(
    name: str, timeout: float = 0.0, stale_seconds: float = 3600.0, lock_dir: Optional[PathLike] = None
) -> Iterator[bool]:
    """跨进程互斥锁 (Windows 计划任务重入防护)。

    用法::

        with process_lock("daily_trade_executor") as acquired:
            if not acquired:
                logger.warning("已有实例在运行, 退出")
                return
            ...

    - timeout=0: 非阻塞, 拿不到立即返回 acquired=False
    - stale_seconds: 锁文件超过该秒数视为遗留死锁, 自动清理
    """
    directory = Path(lock_dir) if lock_dir else Path(tempfile.gettempdir())
    directory.mkdir(parents=True, exist_ok=True)
    lock_file = directory / f"quant84_{name}.lock"

    acquired = False
    deadline = time.time() + timeout
    try:
        while True:
            try:
                fd = os.open(str(lock_file), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                with os.fdopen(fd, "w", encoding="utf-8") as f:
                    f.write(f"{os.getpid()}\n{time.time()}\n")
                acquired = True
                break
            except FileExistsError:
                # 检查是否为遗留死锁
                try:
                    age = time.time() - lock_file.stat().st_mtime
                    if age > stale_seconds:
                        logger.warning(f"清理过期进程锁 {lock_file.name} (age={age:.0f}s)")
                        lock_file.unlink()
                        continue
                except OSError:
                    pass
                if time.time() >= deadline:
                    break
                time.sleep(0.2)
        yield acquired
    finally:
        if acquired:
            try:
                lock_file.unlink()
            except OSError:
                pass


# ============================================================
# 通用并发 IO 批量执行 (B2.1)
# ============================================================
def run_io_batch(
    items: List[T],
    fn: Callable[[T], R],
    *,
    max_workers: int = 8,
    timeout: Optional[float] = 30.0,
    fail_default: Any = _UNSET,
    progress_cb: Optional[Callable[[int, int], None]] = None,
    desc: str = "",
) -> List[Any]:
    """通用并发 IO 批量执行 helper (B2.1)

    用 ThreadPoolExecutor 并发执行 ``fn(item)``, 支持:
        - 单项超时控制 (per-item ``future.result(timeout)``)
        - 失败降级 (异常/超时返回 ``fail_default``)
        - 进度回调 (``progress_cb(completed, total)``)
        - 结果顺序与 ``items`` 一致

    Args:
        items: 待处理项列表
        fn: 处理函数 (接受单个 item, 返回结果)
        max_workers: 最大并发数 (默认 8)
        timeout: 单项超时秒数; ``None`` 表示不限制 (默认 30s)
        fail_default: 失败时的默认返回值;
            ``_UNSET`` (默认) 表示用 ``None``
        progress_cb: 进度回调 ``progress_cb(completed_count, total_count)``
        desc: 日志描述 (用于日志标识)

    Returns:
        结果列表, 顺序与 ``items`` 一致; 失败项为 ``fail_default`` (或 ``None``)

    Example::

        # 并发拉取多标的 OHLCV
        results = run_io_batch(
            symbols,
            lambda s: fetch_ohlcv(s),
            max_workers=8,
            timeout=30,
            desc="OHLCV预加载",
        )
    """
    if not items:
        return []

    fallback = None if fail_default is _UNSET else fail_default
    total = len(items)
    results: List[Any] = [fallback] * total
    completed = 0

    # 限制 max_workers 不超过 items 数量 (避免空线程)
    actual_workers = min(max_workers, total)
    tag = f"[{desc}]" if desc else ""

    # 按提交顺序遍历, per-future timeout 控制
    # (as_completed 只返回已完成 future, future.result(timeout) 不生效)
    with ThreadPoolExecutor(max_workers=actual_workers, thread_name_prefix="io_batch") as pool:
        futures = [pool.submit(fn, item) for item in items]

        for idx, future in enumerate(futures):
            try:
                if timeout is not None:
                    results[idx] = future.result(timeout=timeout)
                else:
                    results[idx] = future.result()
            except TimeoutError:
                logger.warning(f"{tag} 第 {idx + 1}/{total} 项超时 (>{timeout}s), 使用降级值")
                results[idx] = fallback
                future.cancel()  # best-effort 取消
            except Exception as e:
                logger.warning(f"{tag} 第 {idx + 1}/{total} 项失败: {e}", exc_info=False)
                results[idx] = fallback
            finally:
                completed += 1
                if progress_cb is not None:
                    try:
                        progress_cb(completed, total)
                    except Exception as e:
                        pass  # 进度回调失败不影响主流程

    return results

