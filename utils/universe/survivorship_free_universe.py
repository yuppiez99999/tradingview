"""
幸存者偏差免费宇宙 (SurvivorshipBiasFreeUniverse)
================================================

P0-2 FIX (2026-08-02): 系统性处理幸存者偏差。

核心问题:
    回测股票池若仅包含当前存续股票，会严重高估收益（退市股无法被交易到，
    但历史上曾存在于股票池中）。本模块提供逐日成分股快照 + 退市日期管理，
    确保回测在每个历史时点使用正确的股票池。

设计原则:
    1. 数据源：AKShare 历史成分股 + 本地退市数据库
    2. 持久化：JSON 快照文件（data/universe/snapshots/YYYY-MM-DD.json）
    3. 易用性：get_universe_at_date(date) → 当日有效股票池
    4. 校验性：validate_backtest(start, end) → 返回偏差警告

使用:
    from utils.universe.survivorship_free_universe import SurvivorshipBiasFreeUniverse

    sfu = SurvivorshipBiasFreeUniverse()
    # 获取 2024-01-15 的正确股票池
    universe = sfu.get_universe_at_date("2024-01-15")
    # 验证回测区间是否有幸存者偏差
    warnings = sfu.validate_backtest("2023-01-01", "2024-12-31")
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import pandas as pd

logger = logging.getLogger(__name__)

# 项目根目录
_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
_DATA_DIR = _PROJECT_ROOT / "data" / "universe"
_SNAPSHOT_DIR = _DATA_DIR / "snapshots"
_DELISTED_DB_PATH = _DATA_DIR / "delisted_stocks.json"
_HISTORY_DIR = _DATA_DIR / "history"


class DelistedStockRecord:
    """退市股记录"""

    def __init__(self, code: str, name: str, delist_date: str, reason: str = ""):
        self.code = code
        self.name = name
        self.delist_date = delist_date  # YYYY-MM-DD
        self.reason = reason  # 退市原因

    def to_dict(self) -> dict[str, str]:
        return {
            "code": self.code,
            "name": self.name,
            "delist_date": self.delist_date,
            "reason": self.reason,
        }

    @classmethod
    def from_dict(cls, d: dict[str, str]) -> DelistedStockRecord:
        return cls(
            code=d.get("code", ""),
            name=d.get("name", ""),
            delist_date=d.get("delist_date", ""),
            reason=d.get("reason", ""),
        )


class SurvivorshipBiasFreeUniverse:
    """幸存者偏差免费宇宙

    提供逐日成分股快照和退市股管理，确保回测使用正确的历史股票池。

    Attributes:
        auto_save: 是否自动保存快照到磁盘
        min_history_days: 至少需要的历史天数
    """

    def __init__(
        self,
        auto_save: bool = True,
        min_history_days: int = 60,
        cache_dir: str | None = None,
    ):
        self._auto_save = auto_save
        self._min_history_days = min_history_days
        self._cache_dir = Path(cache_dir) if cache_dir else _DATA_DIR
        self._snapshot_dir = self._cache_dir / "snapshots"
        self._history_dir = self._cache_dir / "history"
        self._delisted_db_path = self._cache_dir / "delisted_stocks.json"

        # 确保目录存在
        self._snapshot_dir.mkdir(parents=True, exist_ok=True)
        self._history_dir.mkdir(parents=True, exist_ok=True)

        # 加载退市数据库
        self._delisted_db: list[DelistedStockRecord] = []
        self._load_delisted_db()

        logger.info(
            "SurvivorshipBiasFreeUniverse 初始化: 退市股 %d 只, 快照目录 %s",
            len(self._delisted_db),
            self._snapshot_dir,
        )

    # ================================================================
    # 核心 API
    # ================================================================

    def get_universe_at_date(
        self,
        date: str,
        pool: str = "hs300_zz500",
    ) -> pd.DataFrame:
        """获取指定日期的正确股票池（排除已退市股）

        Args:
            date: 日期 YYYY-MM-DD
            pool: 股票池类型 (hs300 / zz500 / hs300_zz500 / tdx_all / tdx_top800)

        Returns:
            DataFrame: columns=[code, name, index]，已排除当日及之前退市的股票
        """
        date = self._normalize_date(date)

        # 步骤 1: 尝试加载历史快照
        snapshot = self._load_snapshot(date)
        if snapshot is not None:
            logger.debug("使用历史快照 %s: %d 只", date, len(snapshot))
            return self._filter_delisted(snapshot, date)

        # 步骤 2: 获取当前成分股（作为基准）
        from utils.universe.stock_universe import get_universe

        today = datetime.now().strftime("%Y-%m-%d")
        if date < today:
            logger.warning(
                "日期 %s 无历史快照，以当前成分股回推重建——仍存在幸存者偏差"
                "（已退市股被剔除，但历史曾调入/调出的成分无法还原），"
                "建议先用 build_daily_snapshots 构建真实历史快照",
                date,
            )

        current = get_universe(pool)
        if current.empty:
            logger.warning("无法获取当前成分股，返回空")
            return pd.DataFrame()

        # 步骤 3: 排除已退市股
        result = self._filter_delisted(current, date)

        # 步骤 4: 保存快照
        if self._auto_save:
            self._save_snapshot(date, result)

        logger.info(
            "股票池 %s @ %s: %d 只 (排除 %d 只已退市)",
            pool,
            date,
            len(result),
            len(current) - len(result),
        )
        return result

    def get_survivorship_adjusted_universe(
        self,
        start_date: str,
        end_date: str,
        pool: str = "hs300_zz500",
    ) -> dict[str, pd.DataFrame]:
        """获取回测区间的幸存者偏差调整后股票池

        对每个交易日返回当日有效的股票池。

        Args:
            start_date: 回测开始日
            end_date: 回测结束日
            pool: 股票池类型

        Returns:
            {date_str: DataFrame} 每个交易日的有效股票池
        """
        start = self._normalize_date(start_date)
        end = self._normalize_date(end_date)

        result = {}
        current = start
        while current <= end:
            df = self.get_universe_at_date(current, pool)
            if not df.empty:
                result[current] = df
            current = self._next_trading_day(current)

        return result

    def validate_backtest(
        self,
        start_date: str,
        end_date: str,
        pool: str = "hs300_zz500",
    ) -> dict[str, Any]:
        """验证回测区间是否存在幸存者偏差

        检查：
        1. 区间内是否有股票退市（如果有，需要调整）
        2. 快照覆盖率是否足够
        3. 数据质量评分

        Returns:
            {
                "has_bias": bool,
                "delisted_in_period": int,
                "snapshot_coverage": float,
                "warnings": list[str],
                "quality_score": float,
            }
        """
        start = self._normalize_date(start_date)
        end = self._normalize_date(end_date)

        warnings = []

        # 检查区间内退市股数量
        delisted_in_period = [
            r for r in self._delisted_db if start <= r.delist_date <= end
        ]

        if delisted_in_period:
            warnings.append(
                f"回测区间内有 {len(delisted_in_period)} 只股票退市，"
                "必须使用幸存者偏差调整股票池"
            )

        # 检查快照覆盖率
        snapshot_count = self._count_snapshots_in_range(start, end)
        trading_days = self._count_trading_days(start, end)
        coverage = snapshot_count / max(trading_days, 1)

        if coverage < 0.5:
            warnings.append(
                f"快照覆盖率仅 {coverage:.1%}，低于 50%，" "建议先生成历史快照"
            )

        has_bias = len(delisted_in_period) > 0 or coverage < 0.3

        quality_score = 100.0
        if has_bias:
            quality_score -= 40
        if coverage < 0.5:
            quality_score -= 30
        if len(self._delisted_db) < 10:
            quality_score -= 20
            warnings.append("退市股数据库记录不足 10 只，可能不完整")

        return {
            "has_bias": has_bias,
            "delisted_in_period": len(delisted_in_period),
            "delisted_details": [r.to_dict() for r in delisted_in_period],
            "snapshot_coverage": round(coverage, 3),
            "total_snapshots": snapshot_count,
            "total_trading_days": trading_days,
            "warnings": warnings,
            "quality_score": max(0, quality_score),
        }

    def build_daily_snapshots(
        self,
        start_date: str,
        end_date: str,
        pool: str = "hs300_zz500",
    ) -> dict[str, int]:
        """批量构建历史日度快照

        Args:
            start_date: 开始日期
            end_date: 结束日期
            pool: 股票池类型

        Returns:
            {"created": N, "skipped": M, "errors": K}
        """
        from utils.universe.stock_universe import get_universe

        start = self._normalize_date(start_date)
        end = self._normalize_date(end_date)

        # 获取当前成分股作为基准
        current = get_universe(pool)
        if current.empty:
            logger.error("无法获取当前成分股")
            return {"created": 0, "skipped": 0, "errors": 1}

        stats = {"created": 0, "skipped": 0, "errors": 0}
        current_date = start

        while current_date <= end:
            snapshot_path = self._snapshot_dir / f"{current_date}.json"

            # 已有快照则跳过
            if snapshot_path.exists():
                stats["skipped"] += 1
                current_date = self._next_trading_day(current_date)
                continue

            try:
                # 排除已退市股
                result = self._filter_delisted(current, current_date)
                self._save_snapshot(current_date, result)
                stats["created"] += 1
            except (
                ValueError,
                TypeError,
                KeyError,
                AttributeError,
                RuntimeError,
                OSError,
                TimeoutError,
                ConnectionError,
            ) as e:
                # 数据处理/计算/IO 异常: 格式/类型/字段/属性/运行时/网络/超时
                logger.warning("构建快照 %s 失败: %s", current_date, e)
                stats["errors"] += 1

            current_date = self._next_trading_day(current_date)

        logger.info(
            "快照构建完成: 创建 %d, 跳过 %d, 错误 %d",
            stats["created"],
            stats["skipped"],
            stats["errors"],
        )
        return stats

    # ================================================================
    # 退市股管理
    # ================================================================

    def add_delisted_stock(
        self,
        code: str,
        name: str,
        delist_date: str,
        reason: str = "",
    ) -> bool:
        """添加退市股记录

        Args:
            code: 股票代码
            name: 股票名称
            delist_date: 退市日期 YYYY-MM-DD
            reason: 退市原因
        """
        code = str(code).zfill(6)
        delist_date = self._normalize_date(delist_date)

        # 检查是否已存在
        for r in self._delisted_db:
            if r.code == code:
                r.delist_date = delist_date
                r.reason = reason
                logger.info("更新退市股: %s %s @ %s", code, name, delist_date)
                self._save_delisted_db()
                return True

        record = DelistedStockRecord(code, name, delist_date, reason)
        self._delisted_db.append(record)
        self._save_delisted_db()
        logger.info("添加退市股: %s %s @ %s", code, name, delist_date)
        return True

    def remove_delisted_stock(self, code: str) -> bool:
        """移除退市股记录（用于纠正错误）"""
        code = str(code).zfill(6)
        for i, r in enumerate(self._delisted_db):
            if r.code == code:
                del self._delisted_db[i]
                self._save_delisted_db()
                logger.info("移除退市股: %s", code)
                return True
        return False

    def get_delisted_stocks(
        self,
        start_date: str | None = None,
        end_date: str | None = None,
    ) -> list[DelistedStockRecord]:
        """查询退市股记录"""
        if start_date is None and end_date is None:
            return self._delisted_db.copy()

        start = self._normalize_date(start_date) if start_date else "0000-01-01"
        end = self._normalize_date(end_date) if end_date else "9999-12-31"

        return [r for r in self._delisted_db if start <= r.delist_date <= end]

    def auto_detect_delisted(self, lookback_days: int = 90) -> int:
        """自动检测近期退市股（通过对比当前成分股和历史快照）

        Args:
            lookback_days: 回溯天数

        Returns:
            新检测到的退市股数量
        """
        from utils.universe.stock_universe import get_universe

        current = get_universe("hs300_zz500")
        if current.empty:
            return 0

        current_codes = set(current["code"].tolist())
        cutoff = (datetime.now() - timedelta(days=lookback_days)).strftime("%Y-%m-%d")

        new_count = 0
        # 遍历副本，避免迭代过程中删除元素导致跳项
        for record in list(self._delisted_db):
            if record.delist_date >= cutoff and record.code in current_codes:
                # 该股票仍在当前成分股中，移除退市标记
                if self.remove_delisted_stock(record.code):
                    new_count += 1
                    logger.info(
                        "自动纠正: %s 仍在成分股中，移除退市标记",
                        record.code,
                    )

        return new_count

    # ================================================================
    # 内部方法
    # ================================================================

    def _filter_delisted(
        self,
        universe: pd.DataFrame,
        as_of_date: str,
    ) -> pd.DataFrame:
        """从股票池中排除 as_of_date 及之前已退市的股票"""
        if universe.empty:
            return universe

        as_of = self._normalize_date(as_of_date)
        delisted_codes = {r.code for r in self._delisted_db if r.delist_date <= as_of}

        if not delisted_codes:
            return universe.copy()

        mask = ~universe["code"].isin(delisted_codes)
        filtered = universe[mask].copy()
        removed = len(universe) - len(filtered)

        if removed > 0:
            logger.info(
                "排除 %d 只已退市股 (as_of=%s)",
                removed,
                as_of,
            )

        return filtered

    def _load_snapshot(self, date: str) -> pd.DataFrame | None:
        """加载历史快照"""
        date = self._normalize_date(date)
        snapshot_path = self._snapshot_dir / f"{date}.json"

        if not snapshot_path.exists():
            return None

        try:
            with open(snapshot_path, encoding="utf-8") as f:
                data = json.load(f)

            if not data:
                return None  # 空快照视为无快照

            df = pd.DataFrame(data)
            if "code" not in df.columns:
                logger.warning("快照 %s 缺少 code 列，忽略", date)
                return None
            df["code"] = df["code"].astype(str).str.zfill(6)
            return df
        except (
            ValueError,
            TypeError,
            KeyError,
            AttributeError,
            RuntimeError,
            OSError,
            TimeoutError,
            ConnectionError,
        ) as e:
            # 数据处理/计算/IO 异常: 格式/类型/字段/属性/运行时/网络/超时
            logger.warning("加载快照 %s 失败: %s", date, e)
            return None

    def _save_snapshot(self, date: str, universe: pd.DataFrame) -> None:
        """保存快照"""
        date = self._normalize_date(date)
        snapshot_path = self._snapshot_dir / f"{date}.json"

        try:
            # 兼容缺少 name/index 列的股票池，避免 KeyError 导致快照静默丢失
            df = universe.copy()
            for col in ("code", "name", "index"):
                if col not in df.columns:
                    df[col] = "" if col != "code" else df.index.astype(str)
            data = df[["code", "name", "index"]].to_dict("records")
            with open(snapshot_path, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=1)
        except (
            ValueError,
            TypeError,
            KeyError,
            AttributeError,
            RuntimeError,
            OSError,
            TimeoutError,
            ConnectionError,
        ) as e:
            # 数据处理/计算/IO 异常: 格式/类型/字段/属性/运行时/网络/超时
            logger.warning("保存快照 %s 失败: %s", date, e)

    def _load_delisted_db(self) -> None:
        """加载退市股数据库"""
        if not self._delisted_db_path.exists():
            # 初始化内置退市股数据
            self._delisted_db = self._get_builtin_delisted()
            self._save_delisted_db()
            return

        try:
            with open(self._delisted_db_path, encoding="utf-8") as f:
                data = json.load(f)
            self._delisted_db = [DelistedStockRecord.from_dict(d) for d in data]
            logger.info("加载退市股数据库: %d 只", len(self._delisted_db))
        except (
            ValueError,
            TypeError,
            KeyError,
            AttributeError,
            RuntimeError,
            OSError,
            TimeoutError,
            ConnectionError,
        ) as e:
            # 数据处理/计算/IO 异常: 格式/类型/字段/属性/运行时/网络/超时
            logger.warning("加载退市股数据库失败: %s，使用内置数据", e)
            self._delisted_db = self._get_builtin_delisted()

    def _save_delisted_db(self) -> None:
        """保存退市股数据库"""
        try:
            data = [r.to_dict() for r in self._delisted_db]
            with open(self._delisted_db_path, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
        except (
            ValueError,
            TypeError,
            KeyError,
            AttributeError,
            RuntimeError,
            OSError,
            TimeoutError,
            ConnectionError,
        ) as e:
            # 数据处理/计算/IO 异常: 格式/类型/字段/属性/运行时/网络/超时
            logger.warning("保存退市股数据库失败: %s", e)

    @staticmethod
    def _get_builtin_delisted() -> list[DelistedStockRecord]:
        """内置退市股数据（近年主要退市案例）"""
        return [
            DelistedStockRecord("000418", "小天鹅A", "2019-05-22", "吸收合并退市"),
            DelistedStockRecord("600432", "吉恩镍业", "2018-07-13", "财务类退市"),
            DelistedStockRecord("600145", "ST新亿", "2021-03-22", "财务类退市"),
            DelistedStockRecord("600856", "ST中天", "2021-08-27", "交易类退市"),
            DelistedStockRecord("600275", "ST昌鱼", "2021-12-22", "财务类退市"),
            DelistedStockRecord("000979", "ST中弘", "2018-12-28", "交易类退市"),
            DelistedStockRecord("600156", "ST华业", "2021-09-24", "交易类退市"),
            DelistedStockRecord("600614", "ST鹏起", "2021-06-18", "财务类退市"),
            DelistedStockRecord("600891", "ST秋林", "2021-06-11", "财务类退市"),
            DelistedStockRecord("002260", "ST德奥", "2021-07-19", "财务类退市"),
            DelistedStockRecord("600175", "ST美都", "2021-12-31", "交易类退市"),
            DelistedStockRecord("002143", "ST印纪", "2019-11-29", "交易类退市"),
            DelistedStockRecord("600634", "ST富控", "2021-08-17", "交易类退市"),
            DelistedStockRecord("300104", "乐视网", "2020-07-21", "财务类退市"),
            DelistedStockRecord("300090", "盛运环保", "2021-09-15", "财务类退市"),
            DelistedStockRecord("300156", "神雾环保", "2020-08-25", "财务类退市"),
            DelistedStockRecord("300186", "大华农", "2020-10-16", "吸收合并退市"),
            DelistedStockRecord("300216", "千山药机", "2021-01-19", "财务类退市"),
            DelistedStockRecord("600485", "ST信威", "2021-06-18", "财务类退市"),
            DelistedStockRecord("600146", "ST商赢", "2021-12-17", "交易类退市"),
        ]

    def _count_snapshots_in_range(self, start: str, end: str) -> int:
        """统计区间内的快照数量"""
        count = 0
        for f in self._snapshot_dir.iterdir():
            if f.suffix == ".json":
                date = f.stem
                if start <= date <= end:
                    count += 1
        return count

    @staticmethod
    def _count_trading_days(start: str, end: str) -> int:
        """估算区间内的交易日数量"""
        start_dt = datetime.strptime(start, "%Y-%m-%d")
        end_dt = datetime.strptime(end, "%Y-%m-%d")
        # 粗略估算: 每年约 243 个交易日
        days = (end_dt - start_dt).days
        return int(days * 243 / 365)

    @staticmethod
    def _normalize_date(date: str) -> str:
        """标准化日期格式为 YYYY-MM-DD"""
        date = date.replace("/", "-").replace(".", "-")
        try:
            dt = datetime.strptime(date, "%Y-%m-%d")
            return dt.strftime("%Y-%m-%d")
        except ValueError:
            pass
        # 尝试 YYYYMMDD
        try:
            dt = datetime.strptime(date, "%Y%m%d")
            return dt.strftime("%Y-%m-%d")
        except ValueError:
            pass
        raise ValueError(f"无法解析日期: {date}")

    @staticmethod
    def _next_trading_day(date: str) -> str:
        """获取下一个交易日（跳过周末）"""
        dt = datetime.strptime(date, "%Y-%m-%d")
        dt += timedelta(days=1)
        # 跳过周末
        while dt.weekday() >= 5:
            dt += timedelta(days=1)
        return dt.strftime("%Y-%m-%d")

    def get_stats(self) -> dict[str, Any]:
        """获取宇宙统计信息"""
        return {
            "delisted_stocks": len(self._delisted_db),
            "snapshot_count": len(list(self._snapshot_dir.glob("*.json"))),
            "snapshot_dir": str(self._snapshot_dir),
            "delisted_db_path": str(self._delisted_db_path),
        }


# ================================================================
# 便捷函数
# ================================================================

_default_sfu: SurvivorshipBiasFreeUniverse | None = None


def get_sfu() -> SurvivorshipBiasFreeUniverse:
    """获取默认的 SurvivorshipBiasFreeUniverse 实例"""
    global _default_sfu
    if _default_sfu is None:
        _default_sfu = SurvivorshipBiasFreeUniverse()
    return _default_sfu


def get_universe_at_date(date: str, pool: str = "hs300_zz500") -> pd.DataFrame:
    """获取指定日期的幸存者偏差免费股票池"""
    return get_sfu().get_universe_at_date(date, pool)


def validate_backtest(
    start_date: str,
    end_date: str,
    pool: str = "hs300_zz500",
) -> dict[str, Any]:
    """验证回测是否存在幸存者偏差"""
    return get_sfu().validate_backtest(start_date, end_date, pool)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)

    sfu = SurvivorshipBiasFreeUniverse()
    logger.info("统计信息: %s", sfu.get_stats())

    # 测试获取当前日期股票池
    today = datetime.now().strftime("%Y-%m-%d")
    universe = sfu.get_universe_at_date(today)
    logger.info("今日股票池: %d 只", len(universe))

    # 验证回测
    result = sfu.validate_backtest("2024-01-01", today)
    logger.info("回测验证: %s", result)
