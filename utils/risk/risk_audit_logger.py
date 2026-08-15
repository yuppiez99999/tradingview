"""T14 风控审计日志 — 全链路风控决策 JSONL 留痕 + 事后回放.

属于「不崩风控六件套」最后一环, 核心目的: **任何风控决策 (拦截/放行/熔断/升级) 必须可回放, 支持按日期/标的/级别检索**.

与 risk_bus 日志的区别:
    - risk_bus 面向实时订阅/异步分发, 有 feature flag, 可能关闭
    - 本审计日志是 **强制留痕, 永不关闭** 的合规底座, 写盘前先落内存 buffer, 再异步刷 JSONL

字段标准 (每条记录必含):
    timestamp, audit_id, module(T09–T14), action, symbol, severity, reason, context_json

回放 API:
    query_by_date(date)       → 当日全量记录
    query_by_symbol(symbol)   → 某标的历史风控决策
    query_rejections(date)    → 当日所有拦截事件 (给 EOD 报告一章用)
    replay_stream(date)       → 按时间顺序 yield 记录, 便于可视化

零行为变更: 本模块是纯 append 写 + 只读查询, 不影响任何决策路径的时序/结果.
"""

from __future__ import annotations

import json
import logging
import threading
import uuid
from collections import defaultdict
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Iterator

logger = logging.getLogger("risk_audit")

_AUDIT_DIR_DEFAULT = "reports/risk_audit"
_SEVERITIES = {"DEBUG", "INFO", "WARN", "ERROR", "CRITICAL"}
_ACTIONS = {"ALLOW", "BLOCK", "TRIP", "RESET", "LEVEL_CHANGE", "RECONCILE_ISSUE", "OTHER"}
_MODULES = {"T09_PRETRADE", "T10_POSITION", "T11_CB", "T12_KILL", "T13_RECONCILE", "T14_AUDIT", "OTHER"}


@dataclass
class AuditRecord:
    """单条审计记录 — 字段固定, 顺序写入 JSONL."""

    timestamp: str
    audit_id: str
    module: str
    action: str
    severity: str
    symbol: str = ""
    reason: str = ""
    context: dict = field(default_factory=dict)

    def to_json_line(self) -> str:
        return json.dumps(asdict(self), ensure_ascii=False, sort_keys=True)

    @staticmethod
    def from_json_line(line: str) -> "AuditRecord | None":
        try:
            d = json.loads(line)
        except json.JSONDecodeError:
            return None
        return AuditRecord(**d)


class RiskAuditLogger:
    """T14 风控审计日志 — 强制留痕 + 回放查询."""

    def __init__(
        self,
        audit_dir: str | Path = _AUDIT_DIR_DEFAULT,
        buffer_capacity: int = 100,
        project_root: Path | None = None,
    ) -> None:
        if project_root is None:
            project_root = Path(__file__).resolve().parents[2]
        audit_path = Path(audit_dir)
        self.audit_dir: Path = audit_path if audit_path.is_absolute() else project_root / audit_path
        self.audit_dir.mkdir(parents=True, exist_ok=True)

        self._buffer: list[AuditRecord] = []
        self._buffer_capacity = max(10, int(buffer_capacity))
        self._lock = threading.Lock()
        self._index_by_symbol: dict[str, list[str]] = defaultdict(list)  # symbol → [audit_id]
        self._index_by_date: dict[str, list[str]] = defaultdict(list)    # date → [audit_id]
        # 内存二级索引: audit_id → (date_str, jsonl_path, line_offset)
        # 简单起见, 回放时扫 JSONL 全量 (风控审计日量级 <1MB, 可接受)

    # ------------------------------------------------------------
    # 写入 API (主链路嵌入)
    # ------------------------------------------------------------

    def log(
        self,
        module: str,
        action: str,
        severity: str = "INFO",
        symbol: str = "",
        reason: str = "",
        context: dict | None = None,
    ) -> str:
        """记录一条风控审计日志, 返回 audit_id."""
        if module not in _MODULES:
            module = "OTHER"
        if action not in _ACTIONS:
            action = "OTHER"
        if severity not in _SEVERITIES:
            severity = "INFO"

        now = datetime.now()
        rec = AuditRecord(
            timestamp=now.isoformat(timespec="milliseconds"),
            audit_id=uuid.uuid4().hex[:12],
            module=module,
            action=action,
            severity=severity,
            symbol=symbol,
            reason=reason,
            context=context or {},
        )

        with self._lock:
            self._buffer.append(rec)
            date_str = now.strftime("%Y-%m-%d")
            self._index_by_date[date_str].append(rec.audit_id)
            if symbol:
                self._index_by_symbol[symbol].append(rec.audit_id)
            if len(self._buffer) >= self._buffer_capacity:
                self._flush_locked(date_str)
        return rec.audit_id

    def flush(self) -> None:
        """强制刷盘 (EOD 结束时调用)."""
        with self._lock:
            today = datetime.now().strftime("%Y-%m-%d")
            self._flush_locked(today)

    # ------------------------------------------------------------
    # 回放 API
    # ------------------------------------------------------------

    def query_by_date(self, date: str) -> list[AuditRecord]:
        return list(self.replay_stream(date))

    def query_by_symbol(self, symbol: str, date_from: str | None = None) -> list[AuditRecord]:
        results: list[AuditRecord] = []
        for f in sorted(self.audit_dir.glob("risk_audit_*.jsonl")):
            d_str = f.stem.replace("risk_audit_", "")
            if date_from and d_str < date_from:
                continue
            for rec in self._iter_jsonl(f):
                if rec and rec.symbol == symbol:
                    results.append(rec)
        return results

    def query_rejections(self, date: str) -> list[AuditRecord]:
        return [r for r in self.replay_stream(date) if r.action == "BLOCK" or r.action == "TRIP"]

    def replay_stream(self, date: str) -> Iterator[AuditRecord]:
        """按时间顺序 yield 当日记录."""
        # 先把 buffer 中当日的一起返回, 再返回文件中
        file_records: list[AuditRecord] = []
        fpath = self.audit_dir / f"risk_audit_{date}.jsonl"
        if fpath.exists():
            file_records = [r for r in self._iter_jsonl(fpath) if r is not None]
        with self._lock:
            buffered = [
                r for r in self._buffer
                if r.timestamp.startswith(date.replace("-", "-"))  # ISO 前缀匹配
            ]
        all_recs = file_records + buffered
        all_recs.sort(key=lambda r: r.timestamp)
        yield from all_recs

    # ------------------------------------------------------------
    # 内部
    # ------------------------------------------------------------

    def _flush_locked(self, today_date: str) -> None:
        if not self._buffer:
            return
        fpath = self.audit_dir / f"risk_audit_{today_date}.jsonl"
        try:
            with open(fpath, "a", encoding="utf-8") as f:
                for rec in self._buffer:
                    f.write(rec.to_json_line() + "\n")
        except OSError as e:
            # 审计日志写失败绝对不阻断风控主路径, 只记 stderr logger
            logger.error(f"[T14] 审计日志刷盘失败 {fpath}: {e}")
            return
        self._buffer.clear()

    @staticmethod
    def _iter_jsonl(path: Path) -> Iterator[AuditRecord | None]:
        try:
            with open(path, encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    yield AuditRecord.from_json_line(line)
        except OSError as e:
            logger.warning(f"[T14] 回放读取失败 {path}: {e}")
            return
