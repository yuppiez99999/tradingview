"""28 侧 TrendCast 预测审计（JSONL 落盘 + 真实行情回溯验证 + 漂移告警）。

关键纪律：
  - 命中率判据必须用 28 本地真实行情计算，严禁使用 16_ 自身 simulation 审计。
  - price_source(symbol, predicted_at:str, horizon_days:int) -> Optional[float]
      返回预测日后第 horizon_days 个交易日的真实收益率（小数，如 0.03）；
      无法获取返回 None（该记录保持未验证，不误判命中）。
  - 默认 _default_price_source 为 best-effort（尝试读 data/cache 下 parquet），
    生产应注入 28 规范行情源（如 DataProvider 扩展方法）。
"""

from __future__ import annotations

import json
import logging
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

_HORIZON_DAYS = {"short_term": 5, "mid_term": 10, "long_term": 20}


class TrendCastAudit:
    def __init__(self, audit_dir: str | None = None) -> None:
        if audit_dir is None:
            audit_dir = Path(__file__).resolve().parent / "logs" / "trendcast" / "audit"
        self.audit_dir = Path(audit_dir)
        self.audit_dir.mkdir(parents=True, exist_ok=True)
        self.audit_file = self.audit_dir / "predictions.jsonl"

    # ----------------------------------------------------------------
    # 记录
    # ----------------------------------------------------------------
    def record_prediction(
        self,
        symbol: str,
        horizon: str,
        direction: str,
        probability: float,
        source: str = "trendcast_pro",
        predicted_at: str | None = None,
    ) -> None:
        rec = {
            "symbol": symbol,
            "horizon": horizon,
            "direction": direction,
            "probability": probability,
            "source": source,
            "predicted_at": predicted_at
            or datetime.now().isoformat(timespec="seconds"),
            "verify_date": None,
            "verified": False,
            "hit": None,
        }
        self._append(rec)

    def _append(self, rec: dict) -> None:
        with open(self.audit_file, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")

    # ----------------------------------------------------------------
    # 读取
    # ----------------------------------------------------------------
    def _load_records(self) -> list[dict]:
        if not self.audit_file.exists():
            return []
        out: list[dict] = []
        with open(self.audit_file, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    out.append(json.loads(line))
                except Exception:  # noqa: BLE001
                    continue
        return out

    # ----------------------------------------------------------------
    # 验证（用 28 真实行情）
    # ----------------------------------------------------------------
    def verify_predictions(
        self,
        price_source: Callable[[str, str, int], Optional[float]] | None = None,
        asof: str | None = None,
    ) -> dict:
        if price_source is None:
            price_source = _default_price_source
        asof = asof or datetime.now().isoformat(timespec="seconds")
        try:
            asof_ord = datetime.fromisoformat(asof).date().toordinal()
        except Exception:  # noqa: BLE001
            asof_ord = datetime.now().date().toordinal()

        records = self._load_records()
        updated = 0
        for rec in records:
            if rec.get("verified"):
                continue
            sym = rec.get("symbol")
            h = rec.get("horizon")
            hd = _HORIZON_DAYS.get(h, 5)
            try:
                pred_ord = (
                    datetime.fromisoformat(rec["predicted_at"]).date().toordinal()
                )
            except Exception:  # noqa: BLE001
                continue
            # 是否到期：预测日 + horizon_days <= asof
            if pred_ord + hd > asof_ord:
                continue
            ret = price_source(sym, rec["predicted_at"], hd)
            if ret is None:
                continue  # 无真实行情 → 保持未验证，绝不误判
            hit = (rec["direction"] == "看涨" and ret > 0) or (
                rec["direction"] == "看跌" and ret < 0
            )
            rec["verified"] = True
            rec["verify_date"] = asof
            rec["hit"] = bool(hit)
            rec["real_return"] = ret
            updated += 1
        if updated:
            self._rewrite(records)
        return {"checked": len(records), "updated": updated}

    def _rewrite(self, records: list[dict]) -> None:
        tmp = self.audit_file.with_suffix(".jsonl.tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            for rec in records:
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        tmp.replace(self.audit_file)

    # ----------------------------------------------------------------
    # 统计
    # ----------------------------------------------------------------
    def get_stats(self) -> dict:
        records = self._load_records()
        verified = [r for r in records if r.get("verified")]
        hits = sum(1 for r in verified if r.get("hit"))
        return {
            "total_predictions": len(records),
            "verified": len(verified),
            "hits": hits,
            "hit_rate": (hits / len(verified)) if verified else 0.0,
        }


def _default_price_source(
    symbol: str, predicted_at: str, horizon_days: int
) -> Optional[float]:
    """best-effort 真实行情回取：尝试 28 data/cache 下 parquet；失败返回 None。

    生产环境应在构造 TrendCastAudit 时注入 28 规范行情源。
    绝不依赖 16_ 自身 simulation 行情。
    """
    try:
        import pandas as pd
    except Exception:  # noqa: BLE001
        return None
    cache_root = Path(__file__).resolve().parent / "data" / "cache"
    if not cache_root.exists():
        return None
    candidates = list(cache_root.rglob(f"*{symbol}*.parquet"))
    if not candidates:
        return None
    try:
        df = pd.read_parquet(candidates[0])
        date_col = next(
            (
                c
                for c in df.columns
                if str(c).lower() in ("date", "datetime", "trade_date", "时间")
            ),
            None,
        )
        close_col = next(
            (
                c
                for c in df.columns
                if str(c).lower() in ("close", "收盘", "收盘价", "adj_close")
            ),
            None,
        )
        if date_col is None or close_col is None:
            return None
        df[date_col] = pd.to_datetime(df[date_col])
        df = df.sort_values(date_col).reset_index(drop=True)
        after = df[df[date_col] > pd.to_datetime(predicted_at)]
        if len(after) < horizon_days + 1:
            return None
        p0 = float(after[close_col].iloc[0])
        p1 = float(after[close_col].iloc[horizon_days])
        if p0 == 0:
            return None
        return p1 / p0 - 1.0
    except Exception as e:  # noqa: BLE001
        logger.warning(f"[TrendCast Audit] 行情回取失败 {symbol}: {e}")
        return None
