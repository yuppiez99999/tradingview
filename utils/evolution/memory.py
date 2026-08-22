"""进化记忆 — 全量审计留痕, 自我进化框架的"学习"基础.

模块整合 8.4 — ARCHITECTURE_自我进化框架 §6.7 (v2.0 合并版)
任务编号: T1.2 (Phase 1 防御层加固)

职责:
    记录所有进化动作及其结果, 作为未来进化决策的参考, 实现"学习"能力.
    所有进化组件 (Guard / AutoFixEngine / FeedbackLoop / Orchestrator) 的
    审计留痕都通过本模块持久化.

关键约束 (TASK T1.2):
    - Memory 写入失败 → 拒绝所有进化 (审计优先)
    - 写入失败时抛异常, 不静默吞错
    - 全量写入 reports/evolution/memory.jsonl (JSONL 格式, 每行一条记录)

存储结构 (ARCHITECTURE §6.7):
    {
      "proposal_id": "EVO-20260802-001",
      "timestamp": "2026-08-02T14:30:00",
      "level": "L2",
      "action_type": "retrain",
      "trigger_reason": "LGB模型 IC 从 0.08 衰减至 0.02,触发漂移",
      "target_module": "lgb_enhanced_trainer",
      "status": "executed",
      "score_report": {...},
      "executed_at": "2026-08-02T15:00:00",
      "result": {...},
      "rollback_plan": "回滚至 v8.6.14 基线模型",
      "learned": "夏季低波动期 IC 衰减是季节性现象,重训有效",
      "metadata": {...}
    }

核心 API:
    Memory.record(proposal) -> str          # 写入提案, 返回 proposal_id
    Memory.query(filter) -> list[MemoryRecord]  # 按条件查询
    Memory.learn(proposal_id, lesson) -> bool   # 追加学习总结
    Memory.update_status(proposal_id, status, result) -> bool  # 更新执行状态

用法:
    from utils.evolution.memory import EvolutionMemory, MemoryRecord

    memory = EvolutionMemory()
    pid = memory.record({
        "level": "L2",
        "action_type": "retrain",
        "trigger_reason": "IC 衰减",
        "target_module": "lgb_enhanced_trainer",
        "rollback_plan": "回滚至基线",
    })
    memory.update_status(pid, status="executed", result={"new_ic": 0.075})
    memory.learn(pid, "夏季 IC 衰减是季节性现象")

硬约束:
    - HC-2: 所有进化动作 100% 审计留痕 (本模块是 HC-2 的实现基础)
    - 写入失败抛 MemoryWriteError, 调用方必须处理 (不可静默吞错)
"""

from __future__ import annotations

import json
import logging
import os
import sys
import tempfile
from collections.abc import Iterable
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# 项目根目录 (用于默认路径解析)
_PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))


# ============================================================
# 常量
# ============================================================

# 默认存储路径 (ARCHITECTURE §6.7)
DEFAULT_MEMORY_PATH = _PROJECT_ROOT / "reports" / "evolution" / "memory.jsonl"

# 状态枚举
STATUS_PENDING = "pending"          # 已记录, 待执行
STATUS_EXECUTED = "executed"        # 已执行成功
STATUS_FAILED = "failed"            # 执行失败
STATUS_ROLLED_BACK = "rolled_back"  # 已回滚
STATUS_REJECTED = "rejected"        # 被 Guard 拒绝
STATUS_LEARNED = "learned"          # 已追加学习总结 (终态)

# 进化层级枚举 (ARCHITECTURE §4.2)
LEVEL_L1 = "L1"  # 防御层: AutoFixEngine 自动修复
LEVEL_L2 = "L2"  # 优化层: DriftMonitor + AutoRetrain + A/B Test
LEVEL_L3 = "L3"  # 进化层: AutoFactorFactory + FeedbackLoop

# 动作类型枚举 (常见值, 不穷举)
ACTION_RETRAIN = "retrain"                  # 模型重训
ACTION_WEIGHT_ADJUST = "weight_adjust"      # 因子权重调整
ACTION_FACTOR_DEPLOY = "factor_deploy"      # 新因子部署
ACTION_FACTOR_RETIRE = "factor_retire"      # 因子下线
ACTION_FIX = "fix"                          # 自动修复
ACTION_ROLLBACK = "rollback"                # 回滚
ACTION_PROMOTE = "promote"                  # 模型/因子晋升
ACTION_EVALUATE = "evaluate"                # 仅评估 (观察期)


# ============================================================
# 异常
# ============================================================


class MemoryError(Exception):
    """进化记忆基础异常."""


class MemoryWriteError(MemoryError):
    """记忆写入失败 (调用方必须处理, 不可静默吞错)."""


class MemoryNotFoundError(MemoryError):
    """查询的 proposal_id 不存在."""


class MemoryValidationError(MemoryError):
    """记录字段校验失败."""


# ============================================================
# 数据类
# ============================================================


@dataclass
class MemoryRecord:
    """进化记忆单条记录 (对应 memory.jsonl 的一行).

    字段对应 ARCHITECTURE §6.7 的存储结构.
    """

    proposal_id: str  # EVO-YYYYMMDD-NNN
    timestamp: str  # ISO8601 创建时间
    level: str  # L1 / L2 / L3
    action_type: str  # retrain / weight_adjust / factor_deploy / fix / ...
    trigger_reason: str  # 触发原因
    target_module: str  # 目标模块
    status: str = STATUS_PENDING  # 状态枚举
    score_report: dict[str, Any] = field(default_factory=dict)  # 评估报告 (可选)
    executed_at: str = ""  # 执行时间 (ISO8601, 可选)
    result: dict[str, Any] = field(default_factory=dict)  # 执行结果 (可选)
    rollback_plan: str = ""  # 回滚方案 (L2/L3 必填)
    learned: str = ""  # 学习总结 (由 learn() 填充)
    metadata: dict[str, Any] = field(default_factory=dict)  # 额外元数据

    def to_dict(self) -> dict[str, Any]:
        """转换为字典 (用于 JSONL 序列化)."""
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "MemoryRecord":
        """从字典构造 (用于 JSONL 反序列化).

        容忍缺失字段 (向后兼容), 但 proposal_id/level/action_type 必须存在.
        """
        # 必填字段校验
        required = ["proposal_id", "level", "action_type"]
        for k in required:
            if k not in data:
                raise MemoryValidationError(f"记录缺少必填字段: {k}")

        return cls(
            proposal_id=data["proposal_id"],
            timestamp=data.get("timestamp", ""),
            level=data["level"],
            action_type=data["action_type"],
            trigger_reason=data.get("trigger_reason", ""),
            target_module=data.get("target_module", ""),
            status=data.get("status", STATUS_PENDING),
            score_report=data.get("score_report", {}) or {},
            executed_at=data.get("executed_at", ""),
            result=data.get("result", {}) or {},
            rollback_plan=data.get("rollback_plan", ""),
            learned=data.get("learned", ""),
            metadata=data.get("metadata", {}) or {},
        )

    def validate(self) -> None:
        """校验记录字段, 失败抛 MemoryValidationError."""
        if not self.proposal_id:
            raise MemoryValidationError("proposal_id 不能为空")

        if self.level not in (LEVEL_L1, LEVEL_L2, LEVEL_L3):
            raise MemoryValidationError(
                f"level 必须是 {LEVEL_L1}/{LEVEL_L2}/{LEVEL_L3}, 实际: {self.level}"
            )

        if not self.action_type:
            raise MemoryValidationError("action_type 不能为空")

        if not self.target_module:
            raise MemoryValidationError("target_module 不能为空")

        # L2/L3 必须有回滚方案 (HC-3: 所有进化动作可一键回滚)
        # 例外: STATUS_REJECTED 状态的提案未执行, 无需回滚方案.
        # 设计权衡: HC-2 (审计完整性) 要求记录所有提案含被拒的,
        # 而 HC-3 (可回滚) 仅针对已执行的进化动作, 两者冲突时对 rejected 豁免.
        if (
            self.level in (LEVEL_L2, LEVEL_L3)
            and not self.rollback_plan
            and self.status != STATUS_REJECTED
        ):
            raise MemoryValidationError(
                f"{self.level} 级进化必须提供 rollback_plan (HC-3)"
            )


# ============================================================
# 进化记忆
# ============================================================


class EvolutionMemory:
    """进化记忆 — 全量审计留痕 + 查询 + 学习.

    所有进化动作必须先 record() 再执行, 执行后 update_status(), 最后 learn().
    任何写入失败都抛 MemoryWriteError, 调用方必须处理 (HC-2 审计优先).

    线程安全: 文件级原子写入 (临时文件 + rename), 不加进程锁.
    多进程并发写入需调用方自行加锁 (本模块不引入 fcntl 等平台依赖).
    """

    def __init__(
        self,
        memory_path: Path | str | None = None,
        auto_create_dir: bool = True,
    ) -> None:
        """初始化进化记忆.

        Args:
            memory_path: memory.jsonl 路径 (None=默认 reports/evolution/memory.jsonl)
            auto_create_dir: 是否自动创建父目录 (默认 True)
        """
        self.memory_path: Path = (
            Path(memory_path) if memory_path else DEFAULT_MEMORY_PATH
        )

        if auto_create_dir:
            try:
                self.memory_path.parent.mkdir(parents=True, exist_ok=True)
            except OSError as e:
                logger.warning("创建记忆目录失败 (容错): %s (%s)", self.memory_path.parent, e)

        logger.debug("EvolutionMemory 初始化: path=%s", self.memory_path)

    # ============================================================
    # 核心方法: record / query / learn / update_status
    # ============================================================

    def record(self, proposal: "MemoryRecord | dict[str, Any]") -> str:
        """记录一条进化提案, 返回 proposal_id.

        Args:
            proposal: MemoryRecord 或字典 (至少含 level/action_type/target_module)

        Returns:
            proposal_id (EVO-YYYYMMDD-NNN)

        Raises:
            MemoryValidationError: 字段校验失败
            MemoryWriteError: 写入失败 (调用方必须处理, 不可静默吞错)
        """
        # 统一转换为 MemoryRecord
        if isinstance(proposal, dict):
            # 浅拷贝, 避免污染调用方字典 (ARCHITECTURE §5.1 不可变性原则)
            proposal = dict(proposal)
            # 自动补全 proposal_id / timestamp (如果未提供)
            if not proposal.get("proposal_id"):
                proposal["proposal_id"] = self._generate_proposal_id()
            if not proposal.get("timestamp"):
                proposal["timestamp"] = self._now_iso()
            record = MemoryRecord.from_dict(proposal)
        elif isinstance(proposal, MemoryRecord):
            # 创建新对象, 避免修改调用方传入的 MemoryRecord (不可变性)
            if proposal.timestamp:
                record = proposal
            else:
                record = MemoryRecord(
                    proposal_id=proposal.proposal_id,
                    timestamp=self._now_iso(),
                    level=proposal.level,
                    action_type=proposal.action_type,
                    trigger_reason=proposal.trigger_reason,
                    target_module=proposal.target_module,
                    status=proposal.status,
                    score_report=proposal.score_report,
                    executed_at=proposal.executed_at,
                    result=proposal.result,
                    rollback_plan=proposal.rollback_plan,
                    learned=proposal.learned,
                    metadata=proposal.metadata,
                )
        else:
            raise MemoryValidationError(
                f"proposal 必须是 dict 或 MemoryRecord, 实际: {type(proposal).__name__}"
            )

        # 字段校验
        record.validate()

        # 原子写入 (HC-2: 审计优先, 写入失败必须抛异常)
        self._append_record(record)

        logger.info(
            "进化记忆已记录: pid=%s level=%s action=%s module=%s",
            record.proposal_id,
            record.level,
            record.action_type,
            record.target_module,
        )
        return record.proposal_id

    def query(
        self,
        level: str | None = None,
        action_type: str | None = None,
        status: str | None = None,
        proposal_id: str | None = None,
        target_module: str | None = None,
        since: str | None = None,  # ISO8601, 含当天
        until: str | None = None,
        limit: int | None = None,
    ) -> list[MemoryRecord]:
        """按条件查询记忆记录.

        所有参数都是可选的, None 表示不过滤该维度.

        Args:
            level: 进化层级 (L1/L2/L3)
            action_type: 动作类型
            status: 状态
            proposal_id: 提案 ID (精确匹配)
            target_module: 目标模块
            since: 起始时间 (ISO8601, 含当天, 按 timestamp 字符串比较)
            until: 结束时间 (ISO8601, 含当天)
            limit: 最多返回条数 (None=不限)

        Returns:
            匹配的记录列表 (按 timestamp 升序)

        Note:
            查询失败不抛异常 (查询不影响审计), 返回空列表 + 记录 warning.
        """
        try:
            all_records = self._read_all()
        except (ValueError, TypeError, KeyError, AttributeError, RuntimeError, OSError, TimeoutError, ConnectionError) as e:
            # 数值计算/数据处理异常: 格式/类型/字段/属性/运行时/IO/超时/网络
            logger.warning("查询读取失败 (容错返回空列表): %s", e)
            return []

        filtered: list[MemoryRecord] = []
        for rec in all_records:
            if level is not None and rec.level != level:
                continue
            if action_type is not None and rec.action_type != action_type:
                continue
            if status is not None and rec.status != status:
                continue
            if proposal_id is not None and rec.proposal_id != proposal_id:
                continue
            if target_module is not None and rec.target_module != target_module:
                continue
            if since is not None and rec.timestamp < since:
                continue
            if until is not None and rec.timestamp > until:
                continue
            filtered.append(rec)

        # 升序 (旧 → 新)
        filtered.sort(key=lambda r: r.timestamp)

        if limit is not None and limit >= 0:
            filtered = filtered[:limit]

        return filtered

    def learn(self, proposal_id: str, lesson: str) -> bool:
        """对指定提案追加学习总结.

        将 lesson 写入该记录的 learned 字段, 并将 status 置为 STATUS_LEARNED.
        实现方式: 全量重写 memory.jsonl (保证一致性).

        Args:
            proposal_id: 提案 ID
            lesson: 学习总结文本

        Returns:
            True 表示成功

        Raises:
            MemoryNotFoundError: proposal_id 不存在
            MemoryWriteError: 重写失败
        """
        if not lesson.strip():
            raise MemoryValidationError("lesson 不能为空")

        all_records = self._read_all()
        target_idx: int | None = None
        for i, rec in enumerate(all_records):
            if rec.proposal_id == proposal_id:
                target_idx = i
                break

        if target_idx is None:
            raise MemoryNotFoundError(f"proposal_id 不存在: {proposal_id}")

        # 不可变更新 (ARCHITECTURE §5.1 不可变性原则)
        old_rec = all_records[target_idx]
        new_rec = MemoryRecord(
            proposal_id=old_rec.proposal_id,
            timestamp=old_rec.timestamp,
            level=old_rec.level,
            action_type=old_rec.action_type,
            trigger_reason=old_rec.trigger_reason,
            target_module=old_rec.target_module,
            status=STATUS_LEARNED,
            score_report=old_rec.score_report,
            executed_at=old_rec.executed_at,
            result=old_rec.result,
            rollback_plan=old_rec.rollback_plan,
            learned=lesson,
            metadata=old_rec.metadata,
        )
        all_records[target_idx] = new_rec

        self._rewrite_all(all_records)

        logger.info("学习总结已追加: pid=%s lesson_len=%d", proposal_id, len(lesson))
        return True

    def update_status(
        self,
        proposal_id: str,
        status: str,
        result: dict[str, Any] | None = None,
        executed_at: str | None = None,
    ) -> bool:
        """更新提案的执行状态.

        Args:
            proposal_id: 提案 ID
            status: 新状态 (STATUS_EXECUTED / STATUS_FAILED / STATUS_ROLLED_BACK / ...)
            result: 执行结果 (可选)
            executed_at: 执行时间 (None=自动 now)

        Returns:
            True 表示成功

        Raises:
            MemoryNotFoundError: proposal_id 不存在
            MemoryWriteError: 重写失败
        """
        if not status:
            raise MemoryValidationError("status 不能为空")

        all_records = self._read_all()
        target_idx: int | None = None
        for i, rec in enumerate(all_records):
            if rec.proposal_id == proposal_id:
                target_idx = i
                break

        if target_idx is None:
            raise MemoryNotFoundError(f"proposal_id 不存在: {proposal_id}")

        old_rec = all_records[target_idx]
        new_rec = MemoryRecord(
            proposal_id=old_rec.proposal_id,
            timestamp=old_rec.timestamp,
            level=old_rec.level,
            action_type=old_rec.action_type,
            trigger_reason=old_rec.trigger_reason,
            target_module=old_rec.target_module,
            status=status,
            score_report=old_rec.score_report,
            executed_at=executed_at or self._now_iso(),
            result=result if result is not None else old_rec.result,
            rollback_plan=old_rec.rollback_plan,
            learned=old_rec.learned,
            metadata=old_rec.metadata,
        )
        all_records[target_idx] = new_rec

        self._rewrite_all(all_records)

        logger.info("状态已更新: pid=%s status=%s", proposal_id, status)
        return True

    # ============================================================
    # 辅助方法
    # ============================================================

    def count(self) -> int:
        """返回总记录数 (容错, 失败返回 0)."""
        try:
            return len(self._read_all())
        except (ValueError, TypeError, KeyError, AttributeError, RuntimeError, OSError, TimeoutError, ConnectionError):
            # 数值计算/数据处理异常: 格式/类型/字段/属性/运行时/IO/超时/网络
            return 0

    def get_latest_proposal_id(self) -> str | None:
        """获取最新的 proposal_id (None=无记录)."""
        try:
            all_records = self._read_all()
        except (ValueError, TypeError, KeyError, AttributeError, RuntimeError, OSError, TimeoutError, ConnectionError):
            # 数值计算/数据处理异常: 格式/类型/字段/属性/运行时/IO/超时/网络
            return None

        if not all_records:
            return None

        # 按 timestamp 降序取第一条
        all_records.sort(key=lambda r: r.timestamp, reverse=True)
        return all_records[0].proposal_id

    # ============================================================
    # 内部方法: IO + ID 生成
    # ============================================================

    def _generate_proposal_id(self) -> str:
        """生成新的 proposal_id (EVO-YYYYMMDD-NNN, 按日递增).

        读取当天已有记录数 +1 作为序号.
        如果读取失败, 退化为基于时间戳的序号 (容错).
        """
        today = datetime.now(self._tz()).strftime("%Y%m%d")
        prefix = f"EVO-{today}-"

        try:
            all_records = self._read_all()
        except (ValueError, TypeError, KeyError, AttributeError, RuntimeError, OSError, TimeoutError, ConnectionError):
            # 数值计算/数据处理异常: 格式/类型/字段/属性/运行时/IO/超时/网络
            # 读取失败时, 用秒数作为序号 (容错, 避免阻塞记录)
            seq = int(datetime.now(self._tz()).strftime("%H%M%S"))
            return f"{prefix}{seq:03d}"

        # 统计当天前缀数量
        today_count = sum(1 for r in all_records if r.proposal_id.startswith(prefix))
        seq = today_count + 1
        return f"{prefix}{seq:03d}"

    def _append_record(self, record: MemoryRecord) -> None:
        """原子追加一条记录到 JSONL.

        Raises:
            MemoryWriteError: 写入失败
        """
        line = json.dumps(record.to_dict(), ensure_ascii=False, sort_keys=False)
        # 原子写入: 先写临时文件再 append+rename 不适用于追加场景,
        # 这里采用 "打开追加 + flush + fsync" 模式保证 durability.
        try:
            with self.memory_path.open("a", encoding="utf-8") as f:
                f.write(line + "\n")
                f.flush()
                try:
                    os.fsync(f.fileno())
                except OSError:
                    # 某些文件系统不支持 fsync, 容忍
                    pass
        except OSError as e:
            raise MemoryWriteError(
                f"追加写入失败: {self.memory_path} ({type(e).__name__}: {e})"
            ) from e

    def _read_all(self) -> list[MemoryRecord]:
        """全量读取 memory.jsonl.

        Returns:
            记录列表 (按文件顺序)

        Raises:
            MemoryError: 文件解析失败 (单行解析失败跳过 + warning)
        """
        if not self.memory_path.exists():
            return []

        records: list[MemoryRecord] = []
        with self.memory_path.open("r", encoding="utf-8") as f:
            for line_num, line in enumerate(f, 1):
                line = line.strip()
                if not line:
                    continue
                try:
                    data = json.loads(line)
                    rec = MemoryRecord.from_dict(data)
                    records.append(rec)
                except (json.JSONDecodeError, MemoryValidationError) as e:
                    logger.warning(
                        "memory.jsonl 第 %d 行解析失败 (跳过): %s (line=%s)",
                        line_num,
                        e,
                        line[:100],
                    )
                    continue
        return records

    def _rewrite_all(self, records: Iterable[MemoryRecord]) -> None:
        """全量重写 memory.jsonl (原子: 临时文件 + rename).

        用于 learn() 和 update_status() 修改已有记录.

        Raises:
            MemoryWriteError: 重写失败
        """
        records_list = list(records)

        # 原子写入: 先写临时文件, 成功后 rename 覆盖原文件
        try:
            # 在同目录创建临时文件 (保证同文件系统, rename 原子)
            tmp_fd, tmp_path = tempfile.mkstemp(
                prefix=".memory.tmp.",
                suffix=".jsonl",
                dir=str(self.memory_path.parent),
            )
            try:
                with os.fdopen(tmp_fd, "w", encoding="utf-8") as f:
                    for rec in records_list:
                        line = json.dumps(rec.to_dict(), ensure_ascii=False)
                        f.write(line + "\n")
                    f.flush()
                    try:
                        os.fsync(f.fileno())
                    except OSError:
                        pass
                # 原子 rename
                os.replace(tmp_path, str(self.memory_path))
            except (ValueError, TypeError, KeyError, AttributeError, RuntimeError, OSError, TimeoutError, ConnectionError):
                # 数值计算/数据处理异常: 格式/类型/字段/属性/运行时/IO/超时/网络
                # 清理临时文件
                try:
                    os.unlink(tmp_path)
                except OSError:
                    pass
                raise
        except OSError as e:
            raise MemoryWriteError(
                f"全量重写失败: {self.memory_path} ({type(e).__name__}: {e})"
            ) from e

    @staticmethod
    def _now_iso() -> str:
        """当前时间 ISO8601 (UTC, 带 Z 后缀)."""
        return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%S.%fZ")

    @staticmethod
    def _tz() -> timezone:
        """返回 UTC 时区."""
        return UTC
