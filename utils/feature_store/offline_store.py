"""FeatureStore 离线层 — 回测对齐 + 增量更新 + 批量写入.

后端:
    - parquet: 分区 Parquet 文件 (默认, 零依赖, pandas 内置)
    - duckdb:  DuckDB 嵌入式列存 (可选, 高性能聚合)

设计原则:
    - fail-closed: 后端不可用时返回空 + WARNING, 不抛异常
    - 分区策略: feature_name/date_key 二级分区
    - 增量更新: upsert 语义 (同 key 覆盖)
    - 批量写入: batch_size 控制内存占用
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import pandas as pd

from utils.feature_store.config import FeatureStoreConfig

logger = logging.getLogger("FeatureStore")


class OfflineStore:
    """离线特征存储 — 回测对齐 + 增量更新.

    用法:
        store = OfflineStore(config)
        store.write_batch("momentum_v1", [
            {"date": "2026-08-14", "value": 0.85, "rank": 1},
            {"date": "2026-08-15", "value": 0.72, "rank": 3},
        ])
        df = store.read_range("momentum_v1", "2026-08-01", "2026-08-31")
    """

    def __init__(self, config: FeatureStoreConfig | None = None):
        self._config = config or FeatureStoreConfig()
        self._root = Path(self._config.offline_parquet_dir)
        self._root.mkdir(parents=True, exist_ok=True)
        self._duckdb = None
        if self._config.offline_backend == "duckdb":
            self._init_duckdb()

    def _init_duckdb(self) -> None:
        try:
            import duckdb
            db_path = self._config.offline_duckdb_path
            Path(db_path).parent.mkdir(parents=True, exist_ok=True)
            self._duckdb = duckdb.connect(db_path, read_only=False)
            self._duckdb.execute("""
                CREATE TABLE IF NOT EXISTS features (
                    feature_name VARCHAR,
                    date VARCHAR,
                    data JSON,
                    PRIMARY KEY (feature_name, date)
                )
            """)
            logger.info("[FeatureStore] OfflineStore duckdb connected: %s", db_path)
        except Exception as e:
            logger.warning("[FeatureStore] OfflineStore duckdb init failed (%s), falling back to parquet", e)
            self._duckdb = None

    def _feature_dir(self, feature_name: str) -> Path:
        safe_name = feature_name.replace("/", "_").replace("\\", "_")
        return self._root / safe_name

    def _parquet_path(self, feature_name: str, date_key: str) -> Path:
        return self._feature_dir(feature_name) / f"{date_key}.parquet"

    def write_batch(self, feature_name: str, records: list[dict[str, Any]]) -> int:
        """批量写入特征记录. 返回成功写入数."""
        if not feature_name or not records:
            return 0
        if self._config.reject_nan:
            records = [r for r in records if self._validate_no_nan(r)]
        if not records:
            logger.warning("[FeatureStore] OfflineStore write_batch all rejected (NaN/inf): %s", feature_name)
            return 0
        if self._duckdb is not None:
            return self._write_duckdb(feature_name, records)
        return self._write_parquet(feature_name, records)

    def _write_parquet(self, feature_name: str, records: list[dict[str, Any]]) -> int:
        feature_dir = self._feature_dir(feature_name)
        feature_dir.mkdir(parents=True, exist_ok=True)
        count = 0
        batch_size = self._config.batch_size
        for i in range(0, len(records), batch_size):
            batch = records[i:i + batch_size]
            df = pd.DataFrame(batch)
            if "date" in df.columns:
                for _, row in df.iterrows():
                    date_key = str(row["date"])
                    path = self._parquet_path(feature_name, date_key)
                    row_df = pd.DataFrame([row.to_dict()])
                    row_df.to_parquet(path, index=False, engine="pyarrow")
                    count += 1
            else:
                path = feature_dir / f"batch_{i}.parquet"
                df.to_parquet(path, index=False, engine="pyarrow")
                count += len(df)
        return count

    def _write_duckdb(self, feature_name: str, records: list[dict[str, Any]]) -> int:
        import json
        count = 0
        for record in records:
            date_key = str(record.get("date", ""))
            if not date_key:
                continue
            try:
                self._duckdb.execute(
                    "INSERT OR REPLACE INTO features VALUES (?, ?, ?)",
                    [feature_name, date_key, json.dumps(record, default=str)],
                )
                count += 1
            except Exception as e:
                logger.warning("[FeatureStore] OfflineStore duckdb write failed: %s", e)
        return count

    def read_range(self, feature_name: str, start_date: str, end_date: str) -> pd.DataFrame:
        """读取日期范围内的特征数据. 返回空 DataFrame 表示未找到."""
        if not feature_name:
            return pd.DataFrame()
        if self._duckdb is not None:
            df = self._read_duckdb(feature_name, start_date, end_date)
            if not df.empty:
                return df
        return self._read_parquet(feature_name, start_date, end_date)

    def _read_parquet(self, feature_name: str, start_date: str, end_date: str) -> pd.DataFrame:
        feature_dir = self._feature_dir(feature_name)
        if not feature_dir.exists():
            return pd.DataFrame()
        frames = []
        for parquet_file in sorted(feature_dir.glob("*.parquet")):
            date_key = parquet_file.stem
            if start_date <= date_key <= end_date:
                try:
                    frames.append(pd.read_parquet(parquet_file))
                except Exception as e:
                    logger.warning("[FeatureStore] OfflineStore parquet read failed %s: %s", parquet_file, e)
        if not frames:
            return pd.DataFrame()
        return pd.concat(frames, ignore_index=True)

    def _read_duckdb(self, feature_name: str, start_date: str, end_date: str) -> pd.DataFrame:
        import json
        try:
            result = self._duckdb.execute(
                "SELECT date, data FROM features WHERE feature_name = ? AND date >= ? AND date <= ? ORDER BY date",
                [feature_name, start_date, end_date],
            ).fetchall()
            if not result:
                return pd.DataFrame()
            records = []
            for date_key, data_json in result:
                record = json.loads(data_json) if isinstance(data_json, str) else data_json
                if not isinstance(record, dict):
                    record = {"data": record}
                record.setdefault("date", date_key)
                records.append(record)
            return pd.DataFrame(records)
        except Exception as e:
            logger.warning("[FeatureStore] OfflineStore duckdb read failed: %s", e)
            return pd.DataFrame()

    def read_latest(self, feature_name: str) -> pd.DataFrame:
        """读取最新一天的特征数据."""
        if self._duckdb is not None:
            try:
                import json
                result = self._duckdb.execute(
                    "SELECT date, data FROM features WHERE feature_name = ? ORDER BY date DESC LIMIT 1",
                    [feature_name],
                ).fetchall()
                if result:
                    date_key, data_json = result[0]
                    record = json.loads(data_json) if isinstance(data_json, str) else data_json
                    if not isinstance(record, dict):
                        record = {"data": record}
                    record.setdefault("date", date_key)
                    return pd.DataFrame([record])
            except Exception as e:
                logger.warning("[FeatureStore] OfflineStore duckdb read_latest failed: %s", e)
        feature_dir = self._feature_dir(feature_name)
        if not feature_dir.exists():
            return pd.DataFrame()
        files = sorted(feature_dir.glob("*.parquet"), reverse=True)
        if not files:
            return pd.DataFrame()
        try:
            return pd.read_parquet(files[0])
        except Exception as e:
            logger.warning("[FeatureStore] OfflineStore parquet read_latest failed: %s", e)
            return pd.DataFrame()

    def delete_feature(self, feature_name: str) -> int:
        """删除整个特征的所有数据. 返回删除文件数."""
        count = 0
        if self._duckdb is not None:
            try:
                result = self._duckdb.execute(
                    "DELETE FROM features WHERE feature_name = ?", [feature_name]
                )
                count += result.fetchone()[0] if result else 0
            except Exception as e:
                logger.warning("[FeatureStore] OfflineStore duckdb delete failed: %s", e)
        feature_dir = self._feature_dir(feature_name)
        if feature_dir.exists():
            for f in feature_dir.glob("*.parquet"):
                f.unlink()
                count += 1
            try:
                feature_dir.rmdir()
            except OSError:
                pass
        return count

    def list_features(self) -> list[str]:
        """列出所有已存储的特征名."""
        features = set()
        if self._duckdb is not None:
            try:
                result = self._duckdb.execute("SELECT DISTINCT feature_name FROM features").fetchall()
                features.update(r[0] for r in result)
            except Exception as e:
                logger.warning("[FeatureStore] OfflineStore duckdb list failed: %s", e)
        if self._root.exists():
            for d in self._root.iterdir():
                if d.is_dir() and any(d.glob("*.parquet")):
                    features.add(d.name)
        return sorted(features)

    @staticmethod
    def _validate_no_nan(record: dict[str, Any]) -> bool:
        import math
        for v in record.values():
            if isinstance(v, float) and (math.isnan(v) or math.isinf(v)):
                return False
        return True
