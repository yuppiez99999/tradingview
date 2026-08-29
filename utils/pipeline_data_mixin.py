"""
机构管道数据加载 Mixin
=====================

从 institutional_pipeline_runner.py 抽取的数据加载与快照方法:
- 历史数据加载 (_load_base_cache, _load_and_truncate, _preload_historical_data)
- 数据快照 (_real_snapshot, _mock_snapshot)
- Alpha 信号构建 (_build_alpha_signals, _real_alpha_signals, _save_alpha_signals_report)
- 统一历史数据 helper (_get_sym_lock, _get_or_load_historical, _truncate_history_by_date)
"""

from __future__ import annotations

import logging
import math
import threading
from datetime import datetime
from typing import Any

import pandas as pd

from utils.concurrency import run_io_batch
from utils.path_config import get_historical_base_file

logger = logging.getLogger("institutional_pipeline")

# === LGB 特性标志 (与主模块一致) ===
try:
    from lgb_enhanced_trainer import POSITION_SYMBOLS

    _HAS_LGB = True
except Exception:  # noqa: BLE001
    _HAS_LGB = False
    POSITION_SYMBOLS = []

# 跨市场代理标的（特征工程依赖）
_CROSS_MARKET_PROXY_SYMBOLS = ["518880", "600036", "588000", "515180"]


class DataMixin:
    """数据加载 Mixin — 历史数据 + 快照 + Alpha 信号构建。"""

    def _load_base_cache(self, symbol: str) -> pd.DataFrame | None:
        """读取预下载的 5y 基础缓存（_base.parquet），绕过 data_provider 的 24h TTL。

        由 _download_base_data.py 预先生成，覆盖 2021~2026 约 1260 个交易日，
        截断到回测日期后仍有 ~640 行，远超 LGB min_samples=150。
        """
        base_file = get_historical_base_file(symbol)
        if not base_file.exists():
            return None
        try:
            df = pd.read_parquet(base_file)
            if df is not None and not df.empty:
                return df
        except Exception as e:
            logger.debug("[Pipeline] 读取 _base 缓存失败 %s: %s", symbol, e)
        return None

    def _load_and_truncate(
        self, symbol: str, period: str, cutoff: pd.Timestamp
    ) -> pd.DataFrame | None:
        """加载历史数据并截断到回测日期（杜绝前视偏差）。

        优先级: _base.parquet 预下载缓存 > data_provider.get_historical_data(5y)
        """
        # P1: 优先读预下载的 5y 基础缓存（无 TTL 限制，秒级读取）
        df = self._load_base_cache(symbol)
        # P2: 回退到 data_provider 在线拉取
        if (df is None or df.empty) and self.data_provider is not None:
            try:
                df = self.data_provider.get_historical_data(symbol, period=period)
            except Exception as e:
                logger.debug("[Pipeline] 在线拉取失败 %s: %s", symbol, e)
                df = None
        if df is None or df.empty:
            return None

        # BUG 修复 (2026-08-01 顶级对冲基金重跑验证发现, V2 增强):
        # V1 修复仅对 df.index 做 tz_localize(None), 但部分 pandas 版本下
        # DatetimeIndex.tz_localize(None) 不改元素 tz, 仍抛
        # "Cannot compare tz-naive and tz-aware timestamps"。
        # V2 修复: 元素级强制 tz-naive (与 backtest_runner._to_naive_idx 一致)。
        def _to_naive_idx(idx: pd.DatetimeIndex) -> pd.DatetimeIndex:
            """将 DatetimeIndex 强制转为 tz-naive, 元素也 tz-naive。"""
            try:
                if hasattr(idx, "tz") and idx.tz is not None:
                    idx = idx.tz_localize(None)
                # 元素级规范化 (部分 pandas 版本下 idx.tz_localize(None) 不改元素 tz)
                return pd.DatetimeIndex(
                    [
                        (
                            pd.Timestamp(d).tz_localize(None)
                            if pd.Timestamp(d).tzinfo
                            else pd.Timestamp(d)
                        )
                        for d in idx
                    ]
                )
            except Exception as e:  # noqa: BLE001
                logger.exception(f"DatetimeIndex 时区规范化失败, 已降级返回原 idx: {e}")
                return idx

        try:
            df.index = _to_naive_idx(df.index)
        except Exception as e:  # noqa: BLE001
            logger.exception(f"索引时区转换失败, 已降级跳过 (后续比较仍处理): {e}")

        cutoff_naive = pd.Timestamp(cutoff)
        if hasattr(cutoff_naive, "tz") and cutoff_naive.tz is not None:
            cutoff_naive = cutoff_naive.tz_localize(None)
        cutoff_naive = cutoff_naive.normalize()
        df = df[df.index <= cutoff_naive]
        return df if not df.empty else None

    def _preload_historical_data(self) -> None:
        if self.data_provider is None:
            return
        logger.info(
            "[Pipeline] 回测预加载历史数据: symbols=%s as_of=%s",
            self.ctx.symbols,
            self.ctx.report_date,
        )
        # 顶级对冲基金标准: 回测预加载必须截断到 as_of_date, 杜绝未来数据泄漏
        # period='5y' 确保截断到 2024-01-01 后仍有 ~640 行（> min_samples=150）
        cutoff = pd.Timestamp(self.ctx.report_date).normalize()

        # B2.3: 合并 4 个 symbol 来源去重 (原实现 4 个串行 for 循环, 重复检查 cache)
        etf_candidates = [
            "510300",
            "510500",
            "510050",
            "159915",
            "512100",
            "512010",
            "512480",
            "512760",
            "515030",
            "515790",
        ]
        # 用 dict 保留插入顺序 (Python 3.7+ dict 有序), 同时去重
        merged: dict[str, None] = {}
        for s in self.ctx.symbols:
            merged.setdefault(s, None)
        if _HAS_LGB:
            for code, _suffix, _stype, _name, _style in POSITION_SYMBOLS:
                merged.setdefault(code, None)
        for proxy in _CROSS_MARKET_PROXY_SYMBOLS:
            merged.setdefault(proxy, None)
        for etf in etf_candidates:
            merged.setdefault(etf, None)

        all_symbols = list(merged.keys())
        logger.info(
            "[Pipeline] B2.3 预加载符号合并去重: ctx=%d → 合并=%d (POSITION=%d, proxy=%d, etf=%d)",
            len(self.ctx.symbols),
            len(all_symbols),
            len(POSITION_SYMBOLS) if _HAS_LGB else 0,
            len(_CROSS_MARKET_PROXY_SYMBOLS),
            len(etf_candidates),
        )

        # B2.3: 并发拉取 (替代串行 for 循环)
        def _worker(symbol: str) -> tuple[str, pd.DataFrame | None]:
            try:
                df = self._load_and_truncate(symbol, "5y", cutoff)
                return (symbol, df)
            except Exception as e:
                if symbol in self.ctx.symbols:
                    logger.warning("[Pipeline] 预加载失败 %s: %s", symbol, e)
                else:
                    logger.debug("[Pipeline] 预加载失败 %s: %s", symbol, e)
                return (symbol, None)

        pairs = run_io_batch(
            all_symbols,
            _worker,
            max_workers=8,
            timeout=60,
            desc="preload_historical",
        )

        loaded_count = 0
        for symbol, df in pairs:
            if symbol is None or df is None:
                continue
            self._historical_cache[symbol] = df.copy()
            loaded_count += 1
            logger.debug(
                "[Pipeline] 预加载 %s: %d 行 (截止 %s)", symbol, len(df), cutoff.date()
            )

        logger.info(
            "[Pipeline] 预加载完成: %d 个标的 (cache=%d)",
            loaded_count,
            len(self._historical_cache),
        )

    def _real_snapshot(self, symbol: str) -> dict[str, Any]:
        if self.data_provider is None:
            return self._mock_snapshot(symbol)
        try:
            data = self.data_provider.get_market_data(symbol)
            if not data or not isinstance(data, dict):
                return self._mock_snapshot(symbol)
            price = data.get("index_price") or data.get("close") or data.get("price")
            try:
                price = float(price)
            except Exception:
                logger.debug("[Pipeline] 价格转换失败 symbol=%s raw=%r", symbol, price)
                price = None
            if not price or not math.isfinite(price) or price <= 0:
                return self._mock_snapshot(symbol)
            return {
                "price": price,
                "quality_score": 95.0,
                "timestamp": data.get("timestamp") or datetime.now().isoformat(),
                "source": data.get("source") or "data_provider",
            }
        except Exception as e:
            logger.warning("[Pipeline] 获取真实数据失败: %s", e)
            return self._mock_snapshot(symbol)

    def _mock_snapshot(self, symbol: str) -> dict[str, Any]:
        return {
            "price": 10.0,
            "quality_score": 95.0,
            "timestamp": datetime.now().isoformat(),
            "source": "mock",
        }

    def _build_alpha_signals(self, alpha_report: Any) -> dict[str, dict[str, Any]]:
        evaluations = []
        if isinstance(alpha_report, dict):
            evaluations = alpha_report.get("evaluations", [])
        elif hasattr(alpha_report, "evaluations"):
            evaluations = [
                e.to_dict() if hasattr(e, "to_dict") else e
                for e in getattr(alpha_report, "evaluations", [])
            ]
        signals = {}
        for ev in evaluations:
            name = ev.get("factor_name", "")
            ic = float(ev.get("ic_1d", 0.0))
            ic_ir = float(ev.get("ic_ir", 0.0))
            strength = max(-1.0, min(1.0, ic * 10.0))
            confidence = min(1.0, abs(ic_ir) + 0.2)
            # 修复 BUG: 原代码将每个 evaluation 应用到所有 symbols（最后一个覆盖全部）
            # 现在使用 evaluation 中的 symbol 字段正确映射
            symbol = ev.get("symbol", "")
            if not symbol:
                # 兼容旧格式 mom60_{symbol} / lgb_{symbol}
                if name.startswith("mom60_"):
                    symbol = name[len("mom60_") :]
                elif name.startswith("lgb_"):
                    symbol = name[len("lgb_") :]
            if symbol and symbol in self.ctx.symbols:
                signals[symbol] = {"strength": strength, "confidence": confidence}
        return signals

    def _get_sym_lock(self, symbol: str) -> threading.Lock:
        """获取 per-symbol 锁 (双检: 同一 symbol 全局唯一锁实例).

        B2.2: 保证 run_io_batch 多线程并发拉取同一 symbol 时, 只有一个线程真正拉取.
        """
        # 双检锁: 避免每次都取 _sym_locks_lock
        with self._cache_lock:
            lock = self._sym_locks.get(symbol)
        if lock is not None:
            return lock
        with self._sym_locks_lock:
            lock = self._sym_locks.get(symbol)
            if lock is None:
                lock = threading.Lock()
                self._sym_locks[symbol] = lock
            return lock

    def _get_or_load_historical(self, symbol: str) -> pd.DataFrame | None:
        """获取历史数据 (cache 优先 + 回填 + 日期截断)

        B2.2: 替代散落在 4 个 _real_*_signals 方法中的重复 cache 检查逻辑。
        统一拉取 "5y" 数据 (其他 period "1y"/"1m" 都是 "5y" 的子集,
        无需分别拉取; 且 _preload_historical_data 预加载的也是 "5y")。

        特性:
        - cache 命中直接返回 (避免重复网络 IO)
        - cache 未命中时拉取并回填 (后续方法调用直接命中, 消除 4× 重复拉取)
        - 双检锁 + per-symbol 锁: 多线程并发拉取同一 symbol 时,
          只有一个线程真正拉取, 其他线程在锁内等待后命中 cache
        - 回测模式按 report_date 截断 (防前视偏差)

        Returns:
            截断到 self.ctx.report_date 的 DataFrame; 失败返回 None
        """
        if self.data_provider is None:
            return None

        # 1) 快速路径: cache 命中 (持锁读, 避免读到半写入状态)
        with self._cache_lock:
            df = self._historical_cache.get(symbol)
        if df is not None:
            return self._truncate_history_by_date(df, symbol)

        # 2) cache miss → per-symbol 锁内双检 + 拉取 + 回填
        sym_lock = self._get_sym_lock(symbol)
        with sym_lock:
            # 双检: 持有 sym_lock 期间其他线程可能已写入 cache
            with self._cache_lock:
                df = self._historical_cache.get(symbol)
            if df is not None:
                return self._truncate_history_by_date(df, symbol)

            # 真正拉取 (只一个线程执行此代码块)
            try:
                df = self.data_provider.get_historical_data(symbol, period="5y")
            except Exception as e:
                logger.debug("[Pipeline] 拉取历史数据失败 %s: %s", symbol, e)
                return None
            if df is None or df.empty:
                return None

            # 回填 cache (双检: 再次确认未被其他线程写入)
            with self._cache_lock:
                if symbol not in self._historical_cache:
                    self._historical_cache[symbol] = df.copy()
                    logger.debug("[Pipeline] cache 回填: %s (rows=%d)", symbol, len(df))

        return self._truncate_history_by_date(df, symbol)

    def _truncate_history_by_date(
        self, df: pd.DataFrame, symbol: str
    ) -> pd.DataFrame | None:
        """按 report_date 截断历史数据 (回测模式防前视偏差).

        B2.2: 从 _get_or_load_historical 抽取, 保持单一职责.
        df 切片返回新对象, 不污染 cache.
        """
        if df is None or df.empty:
            return None
        try:
            cutoff = pd.Timestamp(self.ctx.report_date).normalize()
            df_trunc = df
            if hasattr(df_trunc.index, "tz") and df_trunc.index.tz is not None:
                df_trunc = df_trunc.copy()
                df_trunc.index = df_trunc.index.tz_localize(None)
            if hasattr(cutoff, "tz") and cutoff.tz is not None:
                cutoff = cutoff.tz_localize(None)
            df_trunc = df_trunc[df_trunc.index <= cutoff]
            return df_trunc if not df_trunc.empty else None
        except Exception as e:
            logger.debug("[Pipeline] 日期截断失败 %s: %s", symbol, e)
            return df

    def _real_alpha_signals(self) -> dict[str, dict[str, Any]]:
        """B2.2: 并发拉取多 symbol 历史数据 + 计算技术指标 (替代串行 for 循环)"""
        if self.data_provider is None:
            return {s: {"strength": 0.0, "confidence": 0.2} for s in self.ctx.symbols}

        def _worker(symbol: str) -> tuple[str, dict[str, Any]]:
            try:
                close, volume = self._load_symbol_close_volume(symbol)
                components = self._compute_alpha_components(close, volume)
                mom_strength, mom_confidence = self._compose_momentum_signal(
                    close, components
                )
                strength, confidence = self._fuse_with_lgb_signal(
                    symbol, mom_strength, mom_confidence
                )
                return (symbol, {"strength": strength, "confidence": confidence})
            except Exception as e:
                logger.warning("[Pipeline] 真实alpha信号获取失败 %s: %s", symbol, e)
                return (symbol, {"strength": 0.0, "confidence": 0.2})

        # B2.2: 并发执行 (替代串行 for 循环)
        pairs = run_io_batch(
            self.ctx.symbols,
            _worker,
            max_workers=8,
            timeout=60,
            desc="alpha_signals",
        )
        return {s: sig for s, sig in pairs if s is not None}

    def _save_alpha_signals_report(
        self, alpha_signals: dict[str, dict[str, Any]]
    ) -> None:
        """保存 alpha 信号报告到 reports/pipeline/alpha_signals_{timestamp}.json

        供 DriftShadowIntegrator._load_latest_predictions() 读取，
        补齐 EOD 管道不产出 alpha_signals 文件导致 observed=0/0 的数据断链。
        """
        try:
            import json as _json
            from datetime import datetime as _dt
            from pathlib import Path as _Path

            report_dir = (
                _Path(self.ctx.output_root).parent / "reports" / "pipeline"
                if hasattr(self.ctx, "output_root")
                else _Path("reports") / "pipeline"
            )
            report_dir.mkdir(parents=True, exist_ok=True)

            timestamp = _dt.now().strftime("%Y%m%d_%H%M%S")
            signals_flat = {
                sym: float(sig.get("strength", 0.0))
                for sym, sig in alpha_signals.items()
            }
            confidence_flat = {
                sym: float(sig.get("confidence", 0.0))
                for sym, sig in alpha_signals.items()
            }

            report = {
                "model": "institutional_pipeline_v2",
                "training_date": _dt.now().strftime("%Y-%m-%d"),
                "n_stocks": len(signals_flat),
                "model_metrics": {"status": "ok", "source": "real_alpha_signals"},
                "signals": signals_flat,
                "confidence": confidence_flat,
            }

            path = report_dir / f"alpha_signals_{timestamp}.json"
            with open(path, "w", encoding="utf-8") as f:
                _json.dump(report, f, ensure_ascii=False, indent=2)
            logger.info(
                "[Pipeline] Alpha 信号报告已保存: %s (n=%d)", path, len(signals_flat)
            )
        except (
            ValueError,
            TypeError,
            KeyError,
            AttributeError,
            RuntimeError,
            OSError,
        ) as e:
            logger.warning("[Pipeline] 保存 alpha 信号报告失败: %s", e)
