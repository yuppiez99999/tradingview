# -*- coding: utf-8 -*-
"""延迟标签追踪器 — GAP-6 交付物.

ECC mle-workflow MLE-10 修复:
    Monitoring covers system health, feature drift, prediction drift, and delayed labels

设计原则:
    1. 延迟标签 (delayed label) 是 quant ML 的核心痛点:
       t 日预测 t+5 日收益, 标签要等 5 天后才能观测
    2. 在标签观测前, 无法计算真实 IC / IC_IR, 只能用代理指标
    3. 本模块追踪预测记录, 在标签观测后计算真实 IC 指标
    4. 持久化到 reports/delayed_labels/ 目录

硬约束:
    - HC-1: 默认不阻断生产 (warn_only), 与 drift_monitor 一致
    - HC-5: ConfigManager 不受影响 (本模块独立)

用法:
    tracker = DelayedLabelTracker(model_name="v9_lgb", label_delay_days=5)
    # 盘后记录当日预测
    tracker.record_prediction(
        date="2026-07-29",
        symbol="588080.SH",
        predicted_score=0.0235,
        model_version="lgbm_factor_mining_vabc12345_d20260729",
    )
    # 5 天后, 检查哪些预测的 label 已可观测
    observable = tracker.check_label_observability(current_date="2026-08-05")
    # 计算延迟指标
    metrics = tracker.compute_delayed_metrics(model_version="lgbm_factor_mining_vabc12345_d20260729")
    # metrics = {"ic": 0.05, "ic_ir": 0.42, "rank_ic": 0.04, ...}
"""

from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional

import numpy as np
import pandas as pd

logger = logging.getLogger("delayed_label_tracker")

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent


# ============================================================
# 异常定义
# ============================================================
class DelayedLabelTrackerError(Exception):
    """延迟标签追踪器基础异常."""


# ============================================================
# 数据类 (frozen=True)
# ============================================================
@dataclass(frozen=True)
class PredictionRecord:
    """单条预测记录 (不可变).

    Attributes:
        date: 预测日期 (t 日, 如 "2026-07-29")
        symbol: 标的代码 (如 "588080.SH")
        predicted_score: 模型预测分数 (如 forward_return_5d 的预测值)
        model_name: 模型名 (如 "v9_lgb")
        model_version: 模型版本 (artifact_name)
        recorded_at: 记录时间 (ISO)
        label_date: 标签可观测日期 (t + label_delay_days, 如 "2026-08-05")
        actual_label: 实际标签 (None 表示未观测, 观测后填入实际收益)
        label_observed_at: 标签观测时间 (None 表示未观测)
    """

    date: str
    symbol: str
    predicted_score: float
    model_name: str
    model_version: str
    recorded_at: str
    label_date: str
    actual_label: Optional[float] = None
    label_observed_at: Optional[str] = None

    @property
    def is_observed(self) -> bool:
        """标签是否已观测."""
        return self.actual_label is not None

    def to_dict(self) -> Dict[str, Any]:
        """转为 dict."""
        return asdict(self)


@dataclass(frozen=True)
class DelayedMetrics:
    """延迟指标 (不可变).

    Attributes:
        model_version: 模型版本
        n_predictions: 预测总数
        n_observed: 已观测标签数
        n_pending: 待观测标签数
        observation_rate: 观测率 (n_observed / n_predictions)
        ic: Information Coefficient (Pearson 相关)
        rank_ic: Rank IC (Spearman 相关)
        ic_ir: IC Information Ratio (IC 均值 / IC 标准差, 按日聚合)
        mean_predicted: 预测分数均值
        mean_actual: 实际标签均值
        std_predicted: 预测分数标准差
        std_actual: 实际标签标准差
        timestamp: 计算时间
    """

    model_version: str
    n_predictions: int = 0
    n_observed: int = 0
    n_pending: int = 0
    observation_rate: float = 0.0
    ic: float = 0.0
    rank_ic: float = 0.0
    ic_ir: float = 0.0
    mean_predicted: float = 0.0
    mean_actual: float = 0.0
    std_predicted: float = 0.0
    std_actual: float = 0.0
    timestamp: str = ""

    def to_dict(self) -> Dict[str, Any]:
        """转为 dict."""
        return asdict(self)


# ============================================================
# 延迟标签追踪器
# ============================================================
class DelayedLabelTracker:
    """延迟标签追踪器 (GAP-6 核心交付物).

    追踪 t 日预测的 t+N 日 label 是否已观测 (N = label_delay_days).
    在标签观测后, 计算真实 IC / IC_IR / RankIC 指标.

    Attributes:
        model_name: 模型名
        label_delay_days: 标签延迟天数 (默认 5, 对齐 V9 LGB forward_return_5d)
        storage_dir: 持久化目录 (默认 reports/delayed_labels)
    """

    def __init__(
        self,
        model_name: str = "v9_lgb",
        label_delay_days: int = 5,
        storage_dir: Optional[str] = None,
    ) -> None:
        """初始化.

        Args:
            model_name: 模型名
            label_delay_days: 标签延迟天数 (默认 5)
            storage_dir: 持久化目录 (None 则用 reports/delayed_labels)
        """
        self.model_name = model_name
        self.label_delay_days = int(label_delay_days)
        if storage_dir:
            self.storage_dir = Path(storage_dir)
            if not self.storage_dir.is_absolute():
                self.storage_dir = _PROJECT_ROOT / storage_dir
        else:
            self.storage_dir = _PROJECT_ROOT / "reports" / "delayed_labels"
        self.storage_dir.mkdir(parents=True, exist_ok=True)
        # 内存缓存 (从磁盘加载)
        self._records: List[PredictionRecord] = []
        self._loaded = False

    # ============================================================
    # 持久化
    # ============================================================
    def _storage_file(self, date: Optional[str] = None) -> Path:
        """获取存储文件路径.

        按 date 分文件存储 (便于按日加载):
            reports/delayed_labels/{model_name}_predictions_{date}.jsonl
        """
        if date is None:
            date = datetime.utcnow().strftime("%Y-%m-%d")
        return self.storage_dir / f"{self.model_name}_predictions_{date}.jsonl"

    def _metrics_file(self, model_version: Optional[str] = None) -> Path:
        """获取指标文件路径."""
        suffix = f"_{model_version}" if model_version else ""
        return self.storage_dir / f"{self.model_name}_metrics{suffix}.json"

    def _load_records_for_date(self, date: str) -> List[PredictionRecord]:
        """加载某日的预测记录."""
        file = self._storage_file(date)
        if not file.exists():
            return []
        records: List[PredictionRecord] = []
        try:
            with open(file, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        data = json.loads(line)
                        records.append(PredictionRecord(**data))
                    except (json.JSONDecodeError, TypeError) as e:
                        logger.warning("解析预测记录失败 (file=%s): %s", file, e)
                        continue
        except OSError as e:
            logger.warning("加载预测记录失败 (file=%s): %s", file, e)
        return records

    def _load_all_records(self) -> List[PredictionRecord]:
        """加载所有日期的预测记录."""
        if self._loaded:
            return self._records
        records: List[PredictionRecord] = []
        for file in self.storage_dir.glob(f"{self.model_name}_predictions_*.jsonl"):
            try:
                with open(file, "r", encoding="utf-8") as f:
                    for line in f:
                        line = line.strip()
                        if not line:
                            continue
                        try:
                            data = json.loads(line)
                            records.append(PredictionRecord(**data))
                        except (json.JSONDecodeError, TypeError):
                            continue
            except OSError:
                continue
        self._records = records
        self._loaded = True
        return records

    def _persist_record(self, record: PredictionRecord) -> None:
        """持久化单条记录 (追加 JSONL)."""
        file = self._storage_file(record.date)
        try:
            with open(file, "a", encoding="utf-8") as f:
                f.write(json.dumps(record.to_dict(), ensure_ascii=False) + "\n")
        except OSError as e:
            logger.warning("持久化预测记录失败: %s", e)

    def _update_record_label(self, date: str, symbol: str, actual_label: float, observed_at: str) -> None:
        """更新记录的实际标签 (重写当日文件)."""
        file = self._storage_file(date)
        if not file.exists():
            return
        # 读全部行
        lines: List[str] = []
        try:
            with open(file, "r", encoding="utf-8") as f:
                lines = f.readlines()
        except OSError:
            return
        # 更新匹配的记录
        updated_lines: List[str] = []
        updated = False
        for line in lines:
            line = line.strip()
            if not line:
                continue
            try:
                data = json.loads(line)
                if data.get("date") == date and data.get("symbol") == symbol:
                    data["actual_label"] = float(actual_label)
                    data["label_observed_at"] = observed_at
                    updated = True
                updated_lines.append(json.dumps(data, ensure_ascii=False))
            except json.JSONDecodeError:
                continue
        if updated:
            try:
                with open(file, "w", encoding="utf-8") as f:
                    for ul in updated_lines:
                        f.write(ul + "\n")
            except OSError as e:
                logger.warning("更新预测记录标签失败: %s", e)

    # ============================================================
    # 公共 API
    # ============================================================
    def record_prediction(
        self,
        date: str,
        symbol: str,
        predicted_score: float,
        model_version: str,
    ) -> PredictionRecord:
        """记录单条预测.

        Args:
            date: 预测日期 (t 日, 如 "2026-07-29")
            symbol: 标的代码 (如 "588080.SH")
            predicted_score: 模型预测分数
            model_version: 模型版本 (artifact_name)

        Returns:
            PredictionRecord 已记录的预测
        """
        # 计算 label_date (t + label_delay_days)
        try:
            pred_date = datetime.strptime(date, "%Y-%m-%d")
            label_date = pred_date + timedelta(days=self.label_delay_days)
            label_date_str = label_date.strftime("%Y-%m-%d")
        except ValueError:
            # date 格式不规范, 用原始字符串
            label_date_str = date

        record = PredictionRecord(
            date=date,
            symbol=symbol,
            predicted_score=float(predicted_score),
            model_name=self.model_name,
            model_version=model_version,
            recorded_at=datetime.utcnow().isoformat() + "Z",
            label_date=label_date_str,
        )
        self._persist_record(record)
        self._records.append(record)
        return record

    def record_predictions_batch(
        self,
        date: str,
        predictions: Dict[str, float],
        model_version: str,
    ) -> List[PredictionRecord]:
        """批量记录当日预测.

        Args:
            date: 预测日期
            predictions: {symbol: predicted_score}
            model_version: 模型版本

        Returns:
            List[PredictionRecord] 已记录的预测列表
        """
        records: List[PredictionRecord] = []
        for symbol, score in predictions.items():
            record = self.record_prediction(
                date=date,
                symbol=symbol,
                predicted_score=score,
                model_version=model_version,
            )
            records.append(record)
        logger.info(
            "批量记录预测: date=%s, n=%d, model=%s",
            date,
            len(records),
            model_version,
        )
        return records

    def check_label_observability(self, current_date: str) -> List[PredictionRecord]:
        """检查哪些预测的 label 已可观测 (label_date <= current_date).

        Args:
            current_date: 当前日期 (如 "2026-08-05")

        Returns:
            List[PredictionRecord] label 已可观测但尚未填入 actual_label 的记录
        """
        all_records = self._load_all_records()
        pending: List[PredictionRecord] = []
        for record in all_records:
            if record.actual_label is None and record.label_date <= current_date:
                pending.append(record)
        logger.info(
            "标签可观测检查: current_date=%s, pending=%d",
            current_date,
            len(pending),
        )
        return pending

    def update_actual_label(
        self,
        date: str,
        symbol: str,
        actual_label: float,
    ) -> bool:
        """更新实际标签 (标签观测后调用).

        Args:
            date: 预测日期 (t 日)
            symbol: 标的代码
            actual_label: 实际标签值 (如真实 5 日收益)

        Returns:
            True = 更新成功, False = 记录未找到
        """
        observed_at = datetime.utcnow().isoformat() + "Z"
        # 更新磁盘
        self._update_record_label(date, symbol, actual_label, observed_at)
        # 更新内存
        updated = False
        for i, record in enumerate(self._records):
            if record.date == date and record.symbol == symbol:
                self._records[i] = PredictionRecord(
                    date=record.date,
                    symbol=record.symbol,
                    predicted_score=record.predicted_score,
                    model_name=record.model_name,
                    model_version=record.model_version,
                    recorded_at=record.recorded_at,
                    label_date=record.label_date,
                    actual_label=float(actual_label),
                    label_observed_at=observed_at,
                )
                updated = True
                break
        return updated

    def update_actual_labels_batch(
        self,
        labels: Dict[str, Dict[str, float]],
    ) -> int:
        """批量更新实际标签.

        Args:
            labels: {date: {symbol: actual_label}}

        Returns:
            更新成功的记录数
        """
        count = 0
        for date, symbol_labels in labels.items():
            for symbol, actual in symbol_labels.items():
                if self.update_actual_label(date, symbol, actual):
                    count += 1
        logger.info("批量更新实际标签: updated=%d", count)
        return count

    # ============================================================
    # 指标计算
    # ============================================================
    def compute_delayed_metrics(self, model_version: Optional[str] = None) -> DelayedMetrics:
        """计算延迟指标 (IC / IC_IR / RankIC).

        基于 label 已观测的记录, 计算预测分数与实际标签的相关性.

        Args:
            model_version: 模型版本 (None 则用所有版本)

        Returns:
            DelayedMetrics 延迟指标
        """
        all_records = self._load_all_records()
        # 过滤: model_version + label 已观测
        observed_records = [
            r
            for r in all_records
            if r.actual_label is not None and (model_version is None or r.model_version == model_version)
        ]

        if len(observed_records) < 2:
            return DelayedMetrics(
                model_version=model_version or "all",
                n_predictions=len(all_records),
                n_observed=len(observed_records),
                n_pending=len(all_records) - len(observed_records),
                observation_rate=(len(observed_records) / len(all_records) if len(all_records) > 0 else 0.0),
                timestamp=datetime.utcnow().isoformat() + "Z",
            )

        predicted = np.array([r.predicted_score for r in observed_records])
        actual = np.array([r.actual_label for r in observed_records])

        # IC (Pearson)
        try:
            if np.std(predicted) > 0 and np.std(actual) > 0:
                ic = float(np.corrcoef(predicted, actual)[0, 1])
            else:
                ic = 0.0
        except Exception as e:
            ic = 0.0

        # Rank IC (Spearman)
        try:
            from scipy import stats as _stats

            if len(predicted) >= 2:
                rank_ic = float(_stats.spearmanr(predicted, actual)[0])
            else:
                rank_ic = 0.0
        except (ImportError, Exception):
            rank_ic = 0.0

        # IC IR (按日聚合 IC, 然后计算 IC 均值 / IC 标准差)
        ic_ir = self._compute_ic_ir(observed_records)

        return DelayedMetrics(
            model_version=model_version or "all",
            n_predictions=len(all_records),
            n_observed=len(observed_records),
            n_pending=len(all_records) - len(observed_records),
            observation_rate=len(observed_records) / max(len(all_records), 1),
            ic=ic,
            rank_ic=rank_ic,
            ic_ir=ic_ir,
            mean_predicted=float(np.mean(predicted)),
            mean_actual=float(np.mean(actual)),
            std_predicted=float(np.std(predicted)),
            std_actual=float(np.std(actual)),
            timestamp=datetime.utcnow().isoformat() + "Z",
        )

    def _compute_ic_ir(self, records: List[PredictionRecord]) -> float:
        """计算 IC IR (按日聚合 IC 序列, IC 均值 / IC 标准差).

        IC_IR 是 quant ML 的核心指标:
            - IC > 0.05 且 IC_IR > 0.3 视为有效因子
            - IC_IR < 0 视为反向因子 (反向使用)

        Args:
            records: 已观测标签的记录列表

        Returns:
            IC IR 值
        """
        if len(records) < 2:
            return 0.0
        # 按日期聚合
        daily_ic: List[float] = []
        df_records: List[Dict[str, Any]] = [r.to_dict() for r in records]
        try:
            df = pd.DataFrame(df_records)
            if "date" not in df.columns:
                return 0.0
            for _date, group in df.groupby("date"):
                if len(group) < 2:
                    continue
                pred = group["predicted_score"].values
                act = group["actual_label"].values
                if np.std(pred) > 0 and np.std(act) > 0:
                    ic_day = float(np.corrcoef(pred, act)[0, 1])
                    if np.isfinite(ic_day):
                        daily_ic.append(ic_day)
        except Exception as e:
            return 0.0

        if len(daily_ic) < 2:
            return 0.0
        ic_mean = float(np.mean(daily_ic))
        ic_std = float(np.std(daily_ic))
        if ic_std < 1e-10:
            return 0.0
        ic_ir = ic_mean / ic_std
        if not np.isfinite(ic_ir):
            return 0.0
        return ic_ir

    # ============================================================
    # 查询
    # ============================================================
    def get_records(
        self,
        date: Optional[str] = None,
        model_version: Optional[str] = None,
        only_observed: bool = False,
    ) -> List[PredictionRecord]:
        """查询预测记录.

        Args:
            date: 过滤日期 (None 则所有)
            model_version: 过滤模型版本 (None 则所有)
            only_observed: 是否只返回标签已观测的

        Returns:
            List[PredictionRecord]
        """
        records = self._load_all_records()
        result = records
        if date is not None:
            result = [r for r in result if r.date == date]
        if model_version is not None:
            result = [r for r in result if r.model_version == model_version]
        if only_observed:
            result = [r for r in result if r.is_observed]
        return result

    def get_summary(self) -> Dict[str, Any]:
        """获取追踪器汇总."""
        all_records = self._load_all_records()
        observed = [r for r in all_records if r.is_observed]
        pending = [r for r in all_records if not r.is_observed]
        # 按模型版本分组
        by_version: Dict[str, int] = {}
        for r in all_records:
            by_version[r.model_version] = by_version.get(r.model_version, 0) + 1
        return {
            "model_name": self.model_name,
            "label_delay_days": self.label_delay_days,
            "storage_dir": str(self.storage_dir),
            "total_records": len(all_records),
            "observed": len(observed),
            "pending": len(pending),
            "observation_rate": len(observed) / max(len(all_records), 1),
            "by_version": by_version,
            "timestamp": datetime.utcnow().isoformat() + "Z",
        }
