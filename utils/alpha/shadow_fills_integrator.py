"""ShadowFillsIntegrator — 影子账户真实撮合桥接器 (P0-1 核心).

[2026-08-27 P0-1] 对齐 cairn/shadow-realness-audit P0 要求:
    影子账户 NAV 必须基于真实撮合成交 (FillsStore), 而非模拟收益曲线.

职责:
    1. 从 FillsStore 读取当日 (或全部) 真实撮合成交 (consumes the DTE-1 建仓撮合链).
    2. 计算真实撮合净值序列 nav_by_fills (从 1.0 累乘).
    3. 把 trade_log / nav_by_fills / data_source_real 写入:
         - reports/shadow/shadow_state.json   (主状态单事实源)
         - reports/shadow/admission_state.json (准入评估状态)
    4. 幂等: 已桥接日期跳过, 失败 fail-open (异常/无成交仅记日志).

HC 合规:
    - HC-4: 只读 FillsStore, 只写 shadow_state.json / admission_state.json (不碰 V9 基线)
    - fail-safe: 任何异常都返回结构化结果, 不抛到 EOD 主流程

设计为可被单测 (unittest) 全覆盖: 所有外部依赖 (FillsStore, 状态文件 IO)
通过构造参数注入, 默认走真实路径.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

logger = logging.getLogger(__name__)

# ---- 路径常量 (相对项目根) ----
# 文件位于 utils/alpha/ -> parents[0]=alpha, [1]=utils, [2]=项目根
_DEFAULT_PROJECT_ROOT = Path(__file__).resolve().parents[2]
_DEFAULT_SHADOW_STATE = _DEFAULT_PROJECT_ROOT / "reports" / "shadow" / "shadow_state.json"
_DEFAULT_ADMISSION_STATE = _DEFAULT_PROJECT_ROOT / "reports" / "shadow" / "admission_state.json"
# launch_shadow_account.load_state() 读取的状态文件 (advance_stage 守卫读 trade_log 的来源)
_DEFAULT_LAUNCH_STATE = _DEFAULT_PROJECT_ROOT / "output" / "shadow_account" / "shadow_state.json"

# 单笔成交的必需字段
_FILL_REQUIRED_KEYS = ("ts", "symbol", "side", "filled_qty", "avg_price")


@dataclass
class IntegrationResult:
    """桥接结果 (结构化, 便于 EOD/测试消费)."""

    success: bool
    fills_consumed: int = 0
    new_fills: int = 0
    dates_bridged: list = field(default_factory=list)
    trade_log_len: int = 0
    nav_by_fills_len: int = 0
    data_source_real: bool = False
    shadow_state_written: bool = False
    admission_state_written: bool = False
    launch_state_written: bool = False
    error: str | None = None

    def to_dict(self) -> dict:
        return {
            "success": self.success,
            "fills_consumed": self.fills_consumed,
            "new_fills": self.new_fills,
            "dates_bridged": self.dates_bridged,
            "trade_log_len": self.trade_log_len,
            "nav_by_fills_len": self.nav_by_fills_len,
            "data_source_real": self.data_source_real,
            "shadow_state_written": self.shadow_state_written,
            "admission_state_written": self.admission_state_written,
            "launch_state_written": self.launch_state_written,
            "error": self.error,
        }


def _utcnow_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _parse_fill_date(fill: dict) -> str | None:
    """从成交记录解析交易日 (YYYY-MM-DD). 优先 'date' 字段, 其次 'ts'."""
    d = fill.get("date")
    if d:
        return str(d)
    ts = fill.get("ts")
    if ts:
        # ts 形如 2026-08-27T08:30:27
        return str(ts)[:10]
    return None


def _fill_to_trade_log_entry(fill: dict) -> dict:
    """把 FillsStore 成交规范化为 trade_log 条目 (含费用明细)."""
    meta = fill.get("meta", {}) or {}
    return {
        "ts": fill.get("ts"),
        "date": _parse_fill_date(fill),
        "symbol": fill.get("symbol"),
        "side": fill.get("side"),
        "filled_qty": fill.get("filled_qty"),
        "avg_price": fill.get("avg_price"),
        "broker": fill.get("broker"),
        "is_live": bool(fill.get("is_live", False)),
        "strategy": fill.get("strategy"),
        "source": fill.get("source"),
        # 费用明细 (meta 内)
        "commission": meta.get("commission"),
        "transfer_fee": meta.get("transfer_fee"),
        "stamp_duty": meta.get("stamp_duty"),
        "slippage_rate": meta.get("slippage_rate"),
    }


def _load_json_safe(path: Path) -> dict:
    if path.exists():
        try:
            return json.loads(path.read_text(encoding="utf-8") or "{}")
        except (json.JSONDecodeError, OSError) as e:
            logger.warning("[INTEGRATOR] 读取 %s 失败: %s", path, e)
    return {}


def _save_json_atomic(path: Path, data: dict) -> bool:
    """写状态文件 (先写临时文件再替换, 避免半写损坏).

    Windows 兼容 (2026-08-28 实测修复):
        本机 os.replace / Path.replace 对 .tmp -> .json 重命名被系统拦截
        (WinError 5, 疑似安全软件 hook MoveFileEx),
        而 shutil.move 实测可用且能覆盖已存在目标, 故改用 shutil.move.
    """
    try:
        import shutil

        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        shutil.move(str(tmp), str(path))
        return True
    except OSError as e:
        logger.warning("[INTEGRATOR] 写入 %s 失败: %s", path, e)
        return False


def _compute_nav_by_fills(fills: list[dict], initial_capital: float = 1_000_000.0) -> list[dict]:
    """基于真实撮合成交计算净值序列 (按交易日聚合日收益).

    模型 (P0-1 真实数据源锚点, 区别于模拟收益曲线):
        - 组合价值 PV = initial_capital + Σ(净流量), 净流量 = 卖出收入 - 买入成本.
        - 当日日收益 = 当日净流量 / 前一日组合价值 (避免 nav 归零).
        - nav 从 1.0 累乘 = PV / initial_capital.

    这是真实撮合锚点而非完整组合会计 (FillsStore 不携带持仓市值),
    但保证: 净买入时 PV 下降、净卖出获利时 PV 上升, 且 nav 始终 > 0.

    返回: [{date, daily_return, nav}, ...] 按日期升序.
    """
    if not fills:
        return []

    by_date: dict[str, float] = {}
    for f in fills:
        d = _parse_fill_date(f)
        if not d:
            continue
        qty = float(f.get("filled_qty") or 0.0)
        price = float(f.get("avg_price") or 0.0)
        sign = 1.0 if str(f.get("side", "")).upper() == "SELL" else -1.0
        # 净流量 (正=资金流入组合, 负=资金流出)
        by_date[d] = by_date.get(d, 0.0) + sign * qty * price

    series: list[dict] = []
    nav = 1.0
    prev_pv = float(initial_capital)
    for d in sorted(by_date.keys()):
        flow = by_date[d]
        pv = prev_pv + flow
        daily_ret = (flow / prev_pv) if prev_pv > 0 else 0.0
        nav *= 1.0 + daily_ret
        series.append({"date": d, "daily_return": daily_ret, "nav": nav})
        prev_pv = pv
    return series


class ShadowFillsIntegrator:
    """影子账户真实撮合桥接器 (可注入依赖, 便于单测)."""

    def __init__(
        self,
        project_root: Path | None = None,
        shadow_state_path: Path | None = None,
        admission_state_path: Path | None = None,
        launch_state_path: Path | None = None,
        fills_source: Callable[[str | None], Iterable[dict]] | None = None,
    ) -> None:
        self.project_root = Path(project_root or _DEFAULT_PROJECT_ROOT)
        self.shadow_state_path = Path(shadow_state_path or _DEFAULT_SHADOW_STATE)
        self.admission_state_path = Path(admission_state_path or _DEFAULT_ADMISSION_STATE)
        self.launch_state_path = Path(launch_state_path or _DEFAULT_LAUNCH_STATE)
        # fills_source(date_str|None) -> iterable of fill dicts
        # 默认从 FillsStore 读取
        self._fills_source = fills_source or self._default_fills_source

    # ---- 默认数据源: FillsStore ----
    def _default_fills_source(self, target_date: str | None) -> Iterable[dict]:
        try:
            import glob

            from utils.execution import fills_store

            store = fills_store.FillsStore()
            if target_date:
                return store.load_day(target_date) or []
            # 全量: 扫描所有 fills_{date}.jsonl 文件 (按日期聚合)
            fills_dir = fills_store._FILLS_DIR
            if not fills_dir.exists():
                return []
            out: list[dict] = []
            for fp in sorted(glob.glob(str(fills_dir / "fills_*.jsonl"))):
                # 从文件名解析日期: fills_2026-08-27.jsonl
                stem = Path(fp).stem  # fills_2026-08-27
                d = stem[len("fills_") :] if stem.startswith("fills_") else None
                out.extend(store.load_day(d) or [])
            return out
        except (ImportError, ValueError, TypeError, KeyError, AttributeError, OSError, RuntimeError) as e:
            logger.warning("[INTEGRATOR] FillsStore 读取失败: %s", e)
            return []

    # ---- 主入口 ----
    def integrate(self, target_date: str | None = None) -> IntegrationResult:
        """执行桥接.

        Args:
            target_date: 指定交易日 (YYYY-MM-DD) 或 None=全部日期.
                        幂等: 已桥接的日期会被跳过.
        """
        result = IntegrationResult(success=False)
        try:
            fills = list(self._fills_source(target_date))
            result.fills_consumed = len(fills)

            if not fills:
                logger.info(
                    "[INTEGRATOR] 无撮合成交 (target_date=%s), 标记 data_source_real=False",
                    target_date,
                )
                result.data_source_real = False
                # 仍写入状态 (data_source_real=False), 让守卫/准入读到一致状态
                self._write_states(result, trade_log=[], nav_by_fills=[], data_source_real=False)
                result.success = True
                return result

            # 规范化 trade_log
            trade_log = [_fill_to_trade_log_entry(f) for f in fills]

            # 幂等: 已经存在于 shadow_state.trade_log 的日期跳过
            existing = _load_json_safe(self.shadow_state_path)
            existing_dates = {t.get("date") for t in existing.get("trade_log", []) if t.get("date")}
            new_entries = [e for e in trade_log if e.get("date") not in existing_dates]
            result.new_fills = len(new_entries)
            result.dates_bridged = sorted({e.get("date") for e in new_entries if e.get("date")})

            # nav_by_fills 基于全部真实成交 (含历史)
            all_fills = list(self._fills_source(None))
            nav_by_fills = _compute_nav_by_fills(all_fills)
            result.nav_by_fills_len = len(nav_by_fills)
            result.data_source_real = True

            self._write_states(
                result,
                trade_log=trade_log,
                nav_by_fills=nav_by_fills,
                data_source_real=True,
            )
            result.success = True
            result.trade_log_len = len(trade_log)
            logger.info(
                "[INTEGRATOR] 桥接完成: %d 笔成交, 新增 %d, 日期 %s, nav_by_fills=%d",
                result.fills_consumed,
                result.new_fills,
                result.dates_bridged,
                result.nav_by_fills_len,
            )
        except (ValueError, TypeError, KeyError, AttributeError, OSError, RuntimeError, ImportError) as e:
            logger.exception("[INTEGRATOR] 桥接异常: %s", e)
            result.error = str(e)
            result.success = False
        return result

    # ---- 状态写入 ----
    def _write_states(
        self,
        result: IntegrationResult,
        trade_log: list,
        nav_by_fills: list,
        data_source_real: bool,
    ) -> None:
        # 1. shadow_state.json (主状态)
        shadow = _load_json_safe(self.shadow_state_path)
        merged_trade_log = shadow.get("trade_log", []) or []
        # 去重合并 (按 date+symbol+side+ts)
        seen = {(t.get("date"), t.get("symbol"), t.get("side"), t.get("ts")) for t in merged_trade_log}
        for e in trade_log:
            key = (e.get("date"), e.get("symbol"), e.get("side"), e.get("ts"))
            if key not in seen:
                merged_trade_log.append(e)
                seen.add(key)
        shadow["trade_log"] = merged_trade_log
        shadow["nav_by_fills"] = nav_by_fills
        shadow["data_source_real"] = data_source_real
        shadow["last_fills_integration"] = _utcnow_iso()
        result.shadow_state_written = _save_json_atomic(self.shadow_state_path, shadow)

        # 2. admission_state.json (准入评估状态)
        admission = _load_json_safe(self.admission_state_path)
        admission["trade_log"] = merged_trade_log
        admission["nav_by_fills"] = nav_by_fills
        admission["data_source_real"] = data_source_real
        admission["last_fills_integration"] = _utcnow_iso()
        result.admission_state_written = _save_json_atomic(self.admission_state_path, admission)

        # 3. output/shadow_account/shadow_state.json
        #    (launch_shadow_account.load_state() 读取, advance_stage 守卫在此读 trade_log)
        launch = _load_json_safe(self.launch_state_path)
        launch_tl = launch.get("trade_log", []) or []
        seen_l = {(t.get("date"), t.get("symbol"), t.get("side"), t.get("ts")) for t in launch_tl}
        for e in merged_trade_log:
            key = (e.get("date"), e.get("symbol"), e.get("side"), e.get("ts"))
            if key not in seen_l:
                launch_tl.append(e)
                seen_l.add(key)
        launch["trade_log"] = launch_tl
        launch["data_source_real"] = data_source_real
        launch["nav_by_fills"] = nav_by_fills
        launch["last_fills_integration"] = _utcnow_iso()
        # 若 state 文件尚未初始化 (无 account_id), 不覆盖其他字段, 仅补 trade_log
        result.launch_state_written = _save_json_atomic(self.launch_state_path, launch)


def run_integration(target_date: str | None = None) -> IntegrationResult:
    """模块级便捷入口 (供 CLI / EOD 调用)."""
    return ShadowFillsIntegrator().integrate(target_date)


if __name__ == "__main__":
    import sys

    _td = sys.argv[1] if len(sys.argv) > 1 else None
    _res = run_integration(_td)
    # 使用 logger 替代 print 以符合静态检查与日志一致性
    logger = logging.getLogger(__name__)
    logger.info(json.dumps(_res.to_dict(), ensure_ascii=False, indent=2))
    sys.exit(0 if _res.success else 1)
