"""28 侧 TrendCast Pro 预测客户端（fail-open）。

职责：
  - 健康检查
  - 拉取组合预测摘要（优先 /api/v1/portfolio/summary，失败回退 /api/v1/predict/batch 组合）
  - 归一化为固定契约（predictions/horizons/direction/probability/model）
  - 读取 28 实际持仓清单（config/positions.json）

设计纪律：
  - 所有网络调用异常均返回 {"error": ...} 而非抛异常（观测路径 fail-open）
  - 28 主流程不因此客户端而中断
"""

from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

try:
    import requests
except Exception:  # noqa: BLE001  # 允许在无 requests 环境导入（测试可 mock）
    requests = None  # type: ignore[assignment]


class TrendCastClient:
    def __init__(
        self,
        base_url: str = "http://127.0.0.1:8800",
        timeout: float = 10.0,
        retries: int = 2,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.retries = retries
        self.session = requests.Session() if requests else None

    # ----------------------------------------------------------------
    # 底层 HTTP（带重试/超时，fail-open）
    # ----------------------------------------------------------------
    def _get(self, path: str, params: dict | None = None) -> dict:
        if requests is None:
            return {"error": "requests 库不可用"}
        url = f"{self.base_url}{path}"
        last_err = "未知错误"
        for attempt in range(self.retries + 1):
            try:
                resp = self.session.get(url, params=params, timeout=self.timeout)
                if resp.status_code == 404:
                    return {"error": f"端点不存在: {path}", "_status": 404}
                resp.raise_for_status()
                return resp.json()
            except Exception as e:  # noqa: BLE001
                last_err = str(e)
                logger.warning(f"[TrendCast] GET {path} 失败(第{attempt + 1}次): {e}")
                time.sleep(0.3 * (attempt + 1))
        return {"error": last_err}

    # ----------------------------------------------------------------
    # 公共 API
    # ----------------------------------------------------------------
    def health_check(self) -> dict:
        res = self._get("/health")
        if isinstance(res, dict) and "error" not in res:
            return res
        return {"error": (res or {}).get("error", "服务不可达")}

    def get_portfolio_summary(self, symbols: list[str] | None = None) -> dict:
        """组合级预测摘要。优先 /api/v1/portfolio/summary，否则回退 batch 组合。"""
        params: dict[str, str] = {}
        if symbols:
            params["symbols"] = ",".join(symbols)
        res = self._get("/api/v1/portfolio/summary", params)
        if isinstance(res, dict) and "error" not in res and "predictions" in res:
            return res
        logger.info("[TrendCast] /portfolio/summary 不可用，回退 /predict/batch 组合")
        return self._fallback_summary(symbols)

    def _fallback_summary(self, symbols: list[str] | None) -> dict:
        if symbols is None:
            mkt = self._get("/api/v1/config/markets")
            if isinstance(mkt, dict) and "markets" in mkt:
                symbols = [s for lst in mkt["markets"].values() for s in lst]
            else:
                return {
                    "error": "无法获取标的列表",
                    "predictions": [],
                    "meta": {"error_symbols": []},
                }
        if requests is None:
            return {
                "error": "requests 库不可用",
                "predictions": [],
                "meta": {"error_symbols": symbols or []},
            }
        try:
            resp = self.session.post(
                f"{self.base_url}/api/v1/predict/batch",
                json={"symbols": symbols, "horizon": "all"},
                timeout=self.timeout,
            )
            resp.raise_for_status()
            raw = resp.json()
        except Exception as e:  # noqa: BLE001
            return {
                "error": str(e),
                "predictions": [],
                "meta": {"error_symbols": symbols or []},
            }
        predictions: list[dict[str, Any]] = []
        error_symbols: list[str] = []
        for item in raw.get("results", []):
            sym = item.get("symbol")
            per = item.get("predictions", {})
            if not per or all("error" in p for p in per.values()):
                error_symbols.append(sym)
                continue
            horizons_out: dict[str, Any] = {}
            for h, p in per.items():
                if not p or "error" in p:
                    continue
                horizons_out[h] = {
                    "direction": p.get("direction", "未知"),
                    "probability": p.get("probability", 0.0),
                    "model": f"lightgbm_{h}",
                }
            predictions.append({"symbol": sym, "sector": "", "horizons": horizons_out})
        return {
            "generated_at": "",
            "model_type": "lightgbm",
            "predictions": predictions,
            "meta": {"models_loaded": 0, "error_symbols": error_symbols},
        }

    # ----------------------------------------------------------------
    # 28 持仓清单（fail-safe）
    # ----------------------------------------------------------------
    @staticmethod
    def load_position_symbols(config_dir: str | None = None) -> list[str]:
        """读取 28 持仓清单，返回去重标的列表。

        兼容 positions.json 两种结构：
          - {positions: {code: info}}   （autolearn_trainer 约定）
          - {positions: [ {code: ...} ]}（部分脚本约定）
        """
        root = Path(config_dir) if config_dir else Path(__file__).resolve().parent
        pos_file = root / "config" / "positions.json"
        if not pos_file.exists():
            logger.warning(f"[TrendCast] 持仓文件不存在: {pos_file}")
            return []
        try:
            data = json.loads(pos_file.read_text(encoding="utf-8"))
        except Exception as e:  # noqa: BLE001
            logger.warning(f"[TrendCast] 读取持仓失败: {e}")
            return []
        positions = data.get("positions", {})
        codes: list[str] = []
        if isinstance(positions, dict):
            for code in positions.keys():
                codes.append(code)
        elif isinstance(positions, list):
            for item in positions:
                if isinstance(item, dict) and item.get("code"):
                    codes.append(item["code"])
        # 去重保序
        seen: set[str] = set()
        out: list[str] = []
        for c in codes:
            if c and c not in seen:
                seen.add(c)
                out.append(c)
        return out
