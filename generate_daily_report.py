"""
收盘盈亏报告生成器
- 按照 Bridgewater/Renaissance 等顶级对冲基金视角
- 盘中自主决策支持
- 严谨高效的持仓盈亏明细
"""
from __future__ import annotations

import atexit
import json
import logging
import os
import sys
from datetime import datetime
from pathlib import Path as _Path
from typing import Any

import requests as _requests

from utils.datetime_utils import now_bj

logger = logging.getLogger(__name__)

# Ensure utils path is available early so downstream reporting imports work at top-level
_PROJECT_ROOT = _Path(__file__).resolve().parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))
try:
    from utils.path_config import setup_sys_path

    setup_sys_path()
except Exception:  # noqa: BLE001
    # best-effort: if setup_sys_path is unavailable, continue; downstream imports may fail later
    pass

# Import reporting/AI helpers at module top to satisfy E402 (imports should be at file top)
from ai.recommendation_generator import (  # noqa: E402
    generate_ai_recommendations as _ai_generate_recommendations,
)
from ai.recommendation_generator import (  # noqa: E402
    generate_deepseek_recommendations as _ai_generate_deepseek_recs,
)
from reporting.hedge_analyzer import (  # noqa: E402
    analyze_hedge_position as _hedge_analyze_position,
)
from reporting.hedge_analyzer import (  # noqa: E402
    analyze_hedge_positions_plan as _hedge_analyze_positions_plan,
)
from reporting.hedge_analyzer import (  # noqa: E402
    calculate_hedge_effectiveness as _hedge_calculate_effectiveness,
)
from reporting.markdown_renderer import (  # noqa: E402
    generate_report as _md_generate_report,
)
from reporting.next_day_planner import (  # noqa: E402
    generate_next_day_plan as _plan_generate_next_day,
)
from reporting.pnl_calculator import (  # noqa: E402
    calculate_max_drawdown as _pnl_calculate_max_drawdown,
)
from reporting.pnl_calculator import (  # noqa: E402
    calculate_pnl as _pnl_calc,
)
from reporting.pnl_calculator import (  # noqa: E402
    calculate_volatility as _pnl_calculate_volatility,
)
from reporting.pnl_calculator import (  # noqa: E402
    count_stop_loss_status as _pnl_count_stop_loss_status,
)
from reporting.pnl_calculator import (  # noqa: E402
    get_position_status as _pnl_get_position_status,
)
from reporting.price_fetcher import (  # noqa: E402
    assess_data_source_health as _price_assess_data_source_health,
)
from reporting.price_fetcher import (  # noqa: E402
    fetch_market_prices as _price_fetch_market_prices,
)
from reporting.price_fetcher import (  # noqa: E402
    fetch_sina_realtime as _price_fetch_sina_realtime,
)
from reporting.price_fetcher import (  # noqa: E402
    to_sina_code as _price_to_sina_code,
)


def _load_dotenv() -> None:
    """轻量级 .env 加载器 (无第三方依赖)

    从项目根目录的 .env 文件加载环境变量, 不覆盖已存在的环境变量。
    """
    env_path = _Path(__file__).resolve().parent / ".env"
    if not env_path.exists():
        return
    try:
        with open(env_path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                if "=" not in line:
                    continue
                key, _, value = line.partition("=")
                key = key.strip()
                value = value.strip().strip('"').strip("'")
                if key and key not in os.environ:
                    os.environ[key] = value
    except Exception:  # noqa: BLE001  # fail-safe, 待后续精确化
        pass


# 启动时加载 .env (DeepSeek API Key 等配置)
_load_dotenv()

# 新浪实时行情 Session (绕过系统代理)
_SINA_SESSION = _requests.Session()
_SINA_SESSION.trust_env = False
_SINA_SESSION.proxies = {"http": None, "https": None}


# C5修复: 注册进程退出时清理 HTTPSession 连接池, 避免资源泄漏
@atexit.register
def _cleanup_sina_session() -> None:
    """进程退出时关闭新浪行情 Session, 避免连接池泄漏"""
    try:
        _SINA_SESSION.close()
    except Exception:  # noqa: BLE001  # fail-safe, 待后续精确化
        pass


# Wave 3 第三阶段: 改用 utils.path_config.setup_sys_path() 统一管理
_PROJECT_ROOT = _Path(__file__).resolve().parent
sys.path.insert(0, str(_PROJECT_ROOT))  # bootstrap: 确保 utils 包可导入
from utils.path_config import setup_sys_path  # noqa: E402

setup_sys_path()  # noqa: E402  # 统一注入 v8.3 根 / v8.3 src / utils

# SC-22: 资金口径默认值唯一事实源
from utils.risk_thresholds import get_total_capital as _get_total_capital  # noqa: E402

# 添加 15_每日工作流 到 sys.path, 支持 LLMRouter flag=False 时透传到旧 llm_client
_LLM_WORKFLOW_DIR = _PROJECT_ROOT / "15_每日工作流"
if _LLM_WORKFLOW_DIR.exists() and str(_LLM_WORKFLOW_DIR) not in sys.path:
    sys.path.insert(0, str(_LLM_WORKFLOW_DIR))  # noqa: E402  # 跨项目目录, setup_sys_path 未涵盖

# Constants
REPORT_DATE = now_bj().strftime("%Y-%m-%d")
# C6 修复: IFIND_TOKEN 已从数据源降级链剔除 (2026-08-18), 保留常量名供向后兼容但不再使用
IFIND_TOKEN = ""

# ═══════════════════════════════════════════════════════════════
# LLM 调用层 (B3.4.4: 迁移到统一 LLMRouter)
# - USE_LLM_REPORT_ANALYZER=True: 走新 LLMRouter (多 provider fallback + 审计日志)
# - USE_LLM_REPORT_ANALYZER=False: 透传到旧 llm_client.chat (7 级降级链)
# ═══════════════════════════════════════════════════════════════
try:
    from utils.alpha.llm_router import chat as _llm_chat

    _LLM_ROUTER_AVAILABLE = True
except Exception as _e:  # P2 模块 fail-safe  # noqa: BLE001
    _llm_chat = None  # type: ignore[misc]
    _LLM_ROUTER_AVAILABLE = False

# 保留旧常量名供其他模块引用 (deprecated, 实际调用走 LLMRouter)
DEEPSEEK_API_KEY = os.environ.get("DEEPSEEK_API_KEY", "")
DEEPSEEK_MODEL = os.environ.get("DEEPSEEK_MODEL", "deepseek-chat")


def _call_deepseek(
    system_prompt: str,
    user_prompt: str,
    temperature: float = 0.4,
    max_tokens: int = 1500,
) -> str | None:
    """调用 LLM 生成文本 (B3.4.4: 统一走 LLMRouter)

    保留原函数签名以维持向后兼容。内部委托给 utils.alpha.llm_router.LLMRouter:
      - flag=True: 走新路由 (omniroute → deepseek → glm → siliconflow → ds4 → ollama; doubao 已于 2026-09-07 出局)
      - flag=False: 透传到旧 llm_client.chat (7 级降级链)

    Args:
        system_prompt: 系统提示词
        user_prompt: 用户提示词
        temperature: 温度参数 (0-2)
        max_tokens: 最大生成 token 数

    Returns:
        生成的文本, 失败返回 None
    """
    if not _llm_chat:
        return None
    try:
        result = _llm_chat(
            user_prompt,
            system=system_prompt,
            temperature=temperature,
            max_tokens=max_tokens,
        )
        if result and result.strip():
            return result.strip()
        return None
    except Exception:  # noqa: BLE001  # fail-safe, 待后续精确化
        return None


# ═══════════════════════════════════════════════════════════════
# B3.2 拆分: PortfolioAnalyzer 业务逻辑委托到子模块
# - reporting.pnl_calculator: 盈亏计算 + 风险指标
# - reporting.hedge_analyzer: 对冲分析
# - reporting.price_fetcher: 价格获取
# - reporting.markdown_renderer: 报告生成
# - reporting.next_day_planner: 次日计划
# - ai.recommendation_generator: AI 建议生成
# ═══════════════════════════════════════════════════════════════


def _load_trade_plan_prices(trade_plan_path: str | None) -> dict[str, float]:
    """加载 trade_plan 获取 est_price，返回 {code_num: est_price}"""
    plan_prices = {}
    if not trade_plan_path:
        return plan_prices
    try:
        with open(trade_plan_path, encoding="utf-8") as f:
            plan = json.load(f)
        exec_plan = plan.get("execution_plan", {})
        for order in exec_plan.get("morning_orders", []):
            code = order.get("code", "")
            code_num = code[2:] if code.startswith(("sh", "sz")) else code
            if code_num and order.get("est_price"):
                plan_prices[code_num] = order["est_price"]
        for order in exec_plan.get("afternoon_orders", []):
            code = order.get("code", "")
            code_num = code[2:] if code.startswith(("sh", "sz")) else code
            if code_num and order.get("est_price") and code_num not in plan_prices:
                plan_prices[code_num] = order["est_price"]
    except Exception:  # noqa: BLE001  # fail-safe, 待后续精确化
        pass
    return plan_prices


def _build_snap_index(sim_positions: dict[str, dict]) -> dict[str, dict]:
    """建立快照索引: 去掉 sh/sz/bj 前缀后的代码 -> 快照记录"""
    snap_index = {}
    for sk, sv in sim_positions.items():
        norm = sk.lower()
        for prefix in ("sh", "sz", "bj"):
            if norm.startswith(prefix):
                norm = norm[len(prefix) :]
                break
        snap_index[norm] = sv
    return snap_index


class PortfolioAnalyzer:
    """组合分析器 - 顶级对冲基金视角

    B3.2 重构: 拆分为 thin coordinator, 业务逻辑委托到子模块:
      - reporting.pnl_calculator: 盈亏计算 + 风险指标
      - reporting.hedge_analyzer: 对冲分析
      - reporting.price_fetcher: 价格获取
      - reporting.markdown_renderer: 报告生成
      - reporting.next_day_planner: 次日计划
      - ai.recommendation_generator: AI 建议生成

    本类仅保留: 数据加载 (__init__/_load_json/_init_data_provider/
    _apply_positions_snapshot/_load_return_projection)
    和委托方法 (维持公开 API 不变)
    """

    def __init__(self, positions_file: str, hedge_file: str):
        self.positions_data = self._load_json(positions_file)
        self.hedge_data = self._load_json(hedge_file)
        self.market_prices: dict[str, dict] = {}
        self._data_provider = None

    def _load_json(self, path: str) -> dict:
        try:
            with open(path, encoding="utf-8") as f:
                return json.load(f)
        except Exception:  # noqa: BLE001  # fail-safe, 待后续精确化
            return {}

    def _init_data_provider(self) -> None:
        """初始化数据源 - Wind MCP > 通达信 > AKShare"""
        try:
            from utils.data_provider import MarketDataProvider

            self._data_provider = MarketDataProvider()
        except Exception:  # noqa: BLE001  # fail-safe, 待后续精确化
            pass

    def _merge_position(
        self,
        key: str,
        pos: dict,
        snap: dict,
        plan_prices: dict[str, float],
    ) -> int:
        """合并单个持仓，返回 matched 增量"""
        code_num = key.split(".")[0]
        snap = snap
        qty = snap.get("qty", 0)
        avg_price = snap.get("avg_price") or pos.get("est_price") or 0
        if not avg_price or avg_price <= 0:
            return 0

        first_open_price = plan_prices.get(code_num) or avg_price or pos.get("est_price") or 0
        if not first_open_price or first_open_price <= 0:
            first_open_price = avg_price

        pos["actual_shares"] = qty
        pos["actual_avg_cost"] = avg_price
        pos["est_price"] = first_open_price
        return 1

    def _add_extra_position(self, norm_code: str, sv: dict, positions: dict) -> None:
        """添加快照中有但 positions 未计划的标的"""
        positions.setdefault("positions", {})[norm_code] = {
            "code": norm_code,
            "name": sv.get("name", ""),
            "style": "其他",
            "sector": "其他",
            "phase1_shares": 0,
            "shares": 0,
            "est_price": sv.get("avg_price", 0),
            "avg_cost": sv.get("avg_price", 0),
            "target_weight": 0.0,
            "actual_shares": sv.get("qty", 0),
            "actual_avg_cost": sv.get("avg_price", 0),
        }

    def _apply_positions_snapshot(self, snapshot_path: str, trade_plan_path: str | None = None) -> None:
        """加载 sim_snapshots/positions_{date}.json 并构建实际持仓视图

        注意: 此方法会修改 self.positions_data 中的持仓字段 (actual_shares,
        actual_avg_cost, est_price), 以便后续 calculate_pnl 等方法使用实际持仓数据。
        首次调用时会深拷贝备份原始计划数据到 self._original_positions_data,
        防止 analyzer 实例重用时原始计划数据丢失。
        """
        # 首次调用时备份原始计划数据, 防止重用 analyzer 时丢失原始数据
        import copy as _copy

        if not hasattr(self, "_original_positions_data"):
            self._original_positions_data = _copy.deepcopy(self.positions_data)

        try:
            with open(snapshot_path, encoding="utf-8") as f:
                snapshot = json.load(f)
        except Exception:  # noqa: BLE001  # fail-safe, 待后续精确化
            return

        plan_prices = _load_trade_plan_prices(trade_plan_path)

        sim_positions = snapshot.get("futures", {}).get("positions", {})
        if not sim_positions:
            return

        snap_index = _build_snap_index(sim_positions)
        positions = self.positions_data.get("positions", {})
        matched = 0
        skipped = 0
        extra = 0

        for key, pos in positions.items():
            code_num = key.split(".")[0]
            snap = snap_index.get(code_num)
            if snap is None:
                skipped += 1
                continue
            matched += self._merge_position(key, pos, snap, plan_prices)

        known_codes = {k.split(".")[0] for k in positions.keys()}
        for norm_code, sv in snap_index.items():
            if norm_code in known_codes:
                continue
            extra += 1
            self._add_extra_position(norm_code, sv, positions)

    @staticmethod
    def _to_sina_code(code: str) -> str:
        """[B3.2 委托] 将标准代码转为新浪代码: 688041.SH -> sh688041, 000333.SZ -> sz000333"""
        return _price_to_sina_code(code)

    def _fetch_sina_realtime(self, codes: list[str]) -> dict[str, dict]:
        """[B3.2 委托] 通过新浪财经 API 批量获取实时行情"""
        return _price_fetch_sina_realtime(codes)

    def fetch_market_prices(self) -> dict[str, dict]:
        """[B3.2 委托] 获取所有持仓标的的收盘价格

        价格验证规则详见 reporting.price_fetcher.fetch_market_prices
        """
        prices = _price_fetch_market_prices(
            self.positions_data,
            data_provider=self._data_provider,
            init_data_provider_fn=self._init_data_provider,
        )
        self.market_prices = prices
        return prices

    def calculate_pnl(self) -> dict[str, Any]:
        """[B3.2 委托] 计算持仓盈亏明细

        G4 补齐: 若当日有真实成交回报落盘, 用 fills 成交均价覆盖行情估算的
        close, 使 PnL 以成交为单一事实源 (fail-open, 无 fills 时回退行情估算)。
        """
        try:
            from utils.execution.fills_pnl_bridge import augment_market_prices

            market_prices = augment_market_prices(self.market_prices, REPORT_DATE)
        except Exception as e:  # noqa: BLE001
            logger.warning("[DailyReport] fills 桥接失败, 回退行情估算: %s", e)
            market_prices = self.market_prices
        result = _pnl_calc(self.positions_data, market_prices)
        # 附加当日已实现 PnL (来自 fills), 便于报告区分
        try:
            from utils.execution.fills_pnl_bridge import realized_pnl

            result["realized_pnl_from_fills"] = realized_pnl(REPORT_DATE)
        except Exception:  # noqa: BLE001
            pass
        return result

    def _get_position_status(self, pnl_pct: float, stop_loss: float) -> str:
        """[B3.2 委托] 判断持仓状态 — 止损线由调用方保证为负值（如 -0.15）"""
        return _pnl_get_position_status(pnl_pct, stop_loss)

    def analyze_hedge_position(self, market_prices: dict[str, dict] | None = None) -> dict[str, Any]:
        """[B3.2 委托] 分析对冲头寸

        Args:
            market_prices: 今日行情字典 (用于估算期权对冲当前市值), 默认用 self.market_prices
        """
        mp = market_prices if market_prices is not None else getattr(self, "market_prices", None)
        return _hedge_analyze_position(self.hedge_data, data_provider=self._data_provider, market_prices=mp)

    def _calculate_hedge_effectiveness(self, hedge_details: list) -> float:
        """[B3.2 委托] 计算对冲有效性"""
        return _hedge_calculate_effectiveness(hedge_details, self.hedge_data)

    def analyze_hedge_positions_plan(self) -> dict[str, Any]:
        """[B3.2 委托] 分析期货期权计划头寸 (来自 positions.json 的 hedge_positions)

        覆盖三类对冲工具:
          - IF 期货  (CFFEX, 沪深300股指期货, Beta加权)
          - IM 期货  (CFFEX, 中证1000股指期货, 中小盘对冲)
          - 510050 Put 期权 (SSE, 上证50ETF认沽, 尾部风险保护)
        """
        return _hedge_analyze_positions_plan(self.positions_data)

    def generate_report(self, report_date: str | None = None) -> dict[str, Any]:
        """[B3.2 委托] 生成完整收盘报告

        Args:
            report_date: 报告日期 'YYYY-MM-DD', None 时使用全局 REPORT_DATE (今天)
        """
        # 使用传入的 report_date, 修复历史报告生成时日期不一致的问题
        effective_date = report_date or REPORT_DATE
        self.fetch_market_prices()

        pnl_data = self.calculate_pnl()
        hedge_position_data = self.analyze_hedge_position()
        hedge_plan = self.analyze_hedge_positions_plan()
        return_projection = self._load_return_projection()

        # 计算净盈亏 (用于 AI 建议)
        portfolio_pnl = pnl_data["summary"]["total_pnl"]
        hedge_pnl = sum(h["hedge_pnl"] for h in hedge_position_data["details"])
        hedge_cost = self.hedge_data.get("total_cost", 0)
        net_pnl = portfolio_pnl + hedge_pnl - hedge_cost

        ai_recommendations = self._generate_ai_recommendations(pnl_data, hedge_position_data, net_pnl)
        next_day_plan = self.generate_next_day_plan(effective_date)

        return _md_generate_report(
            report_date=effective_date,
            positions_data=self.positions_data,
            hedge_data=self.hedge_data,
            market_prices=self.market_prices,
            pnl_data=pnl_data,
            hedge_position_data=hedge_position_data,
            hedge_plan=hedge_plan,
            return_projection=return_projection,
            ai_recommendations=ai_recommendations,
            next_day_plan=next_day_plan,
        )

    def _load_return_projection(self) -> dict[str, Any]:
        """加载 portfolio_return_projection.json 收益率预测数据

        读取四场景预测(bull/base/bear/black_swan)、加权期望、风险披露等。
        若文件不存在或读取失败, 返回空字典(不影响主报告生成)。
        """
        from pathlib import Path as _Path

        try:
            proj_file = _Path(__file__).parent / "portfolio_return_projection.json"
            if not proj_file.exists():
                return {"error": f"projection file not found: {proj_file}"}
            with open(proj_file, encoding="utf-8") as f:
                proj = json.load(f)
            # 提取关键字段
            scenarios = proj.get("scenarios", {})
            expected = proj.get("expected", {})
            prob_w = proj.get("probability_weights", {})
            return {
                "version": proj.get("version") or proj.get("calibration", {}).get("version") or "v3",
                "generated_at": proj.get("generated_at", ""),
                "investment_horizon": proj.get("investment_horizon", ""),
                "horizon_years": proj.get("horizon_years", 1.5),
                "initial_capital": proj.get("initial_capital", _get_total_capital()),
                "scenarios": {
                    s: {
                        "label": scenarios[s].get("label", ""),
                        "weighted_annualized": scenarios[s].get("weighted_annualized", 0),
                        "cumulative_return": scenarios[s].get("cumulative_return", 0),
                        "final_amount": scenarios[s].get("final_amount", 0),
                        "total_profit": scenarios[s].get("total_profit", 0),
                    }
                    for s in ["bull", "base", "bear", "black_swan"]
                },
                "probability_weights": prob_w,
                "expected": {
                    "expected_annualized": expected.get("expected_annualized", 0),
                    "expected_cumulative": expected.get("expected_cumulative", 0),
                    "expected_final_amount": expected.get("expected_final_amount", 0),
                    "expected_profit": expected.get("expected_profit", 0),
                },
                "risk_disclosure": proj.get("risk_disclosure") or self._derive_risk_disclosure(),
            }
        except Exception as e:  # noqa: BLE001  # fail-safe, 待后续精确化
            return {"error": f"projection load failed: {e}"}

    def _derive_risk_disclosure(self) -> dict[str, str]:
        """从 positions.json 派生风险披露 (当 projection 文件缺 risk_disclosure 时 fallback)"""
        positions = self.positions_data.get("positions", {})
        hedge = self.positions_data.get("hedge_positions", {})
        if not positions:
            return {}
        total_amount = sum(p.get("amount", 0) for p in positions.values()) or 1
        max_pos = max(positions.values(), key=lambda p: p.get("amount", 0))
        max_weight = max_pos.get("amount", 0) / total_amount
        max_name = max_pos.get("name", "")
        high_vol_codes = ["688041", "300308", "002371", "688017"]
        high_vol_hits = [p.get("name", c) for c, p in positions.items() if any(hv in c for hv in high_vol_codes)]
        ao = hedge.get("active_orders", {})
        put_count = sum(pp.get("contracts", 0) for pp in (ao.get("put_protection", []) or []))
        call_count = sum(cc.get("contracts", 0) for cc in (ao.get("covered_call", []) or []))
        hedge_desc = (
            f"已建仓 Covered Call {call_count} 张 + Put 保护 {put_count} 张, 尾部保护"
            if (put_count or call_count)
            else "对冲头寸待执行"
        )
        return {
            "concentration_risk": f"{max_name} {max_weight:.0%} (最大单标的), 共 {len(positions)} 只持仓",
            "volatility_risk": f"高波动标的 ({'/'.join(high_vol_hits[:3])}) 占比 {sum(positions[c].get('amount', 0) for c in positions if any(hv in c for hv in high_vol_codes)) / total_amount:.0%}",  # noqa: E501
            "hedge_coverage": hedge_desc,
            "policy_risk": "十五五规划落地节奏、半导体出口管制、AI 监管、医保集采",
            "liquidity_risk": "500 万规模对个股冲击成本约 0.1-0.3%",
        }

    def _assess_data_source_health(self, pnl_data: dict) -> dict[str, Any]:
        """[B3.2 委托] 评估当前报告使用的数据源健康状态"""
        return _price_assess_data_source_health(pnl_data)

    def _calculate_volatility(self, returns: list[float]) -> float:
        """[B3.2 委托] 计算波动率"""
        return _pnl_calculate_volatility(returns)

    def _calculate_max_drawdown(self, details: list) -> float:
        """[B3.2 委托] 计算组合层面真实最大回撤

        详见 reporting.pnl_calculator.calculate_max_drawdown
        """
        return _pnl_calculate_max_drawdown(details)

    def _count_stop_loss_status(self, details: list) -> dict:
        """[B3.2 委托] 统计止损状态"""
        return _pnl_count_stop_loss_status(details)

    def _generate_ai_recommendations(self, pnl_data: dict, hedge_data: dict, net_pnl: float) -> list[str]:
        """[B3.2 委托] 生成AI决策建议 (DeepSeek 优先, 降级到规则引擎)"""
        return _ai_generate_recommendations(
            pnl_data,
            hedge_data,
            net_pnl,
            report_date=REPORT_DATE,
            deepseek_model=DEEPSEEK_MODEL,
            call_deepseek_fn=_call_deepseek,
        )

    def _generate_deepseek_recommendations(
        self, pnl_data: dict, hedge_data: dict, net_pnl: float
    ) -> list[str] | None:
        """[B3.2 委托] 调用 DeepSeek 生成结构化交易决策建议

        生成包含具体操作关键词的建议, 以便 apply_llm_decisions_to_plan.py 识别:
        - "IF空头N手" / "增加期货" / "提升Beta对冲效率" → 期货对冲升级
        - "510050 Put" / "510300 Put" / "Put保护" / "买入Put" → Put 尾部保护
        - "建仓顺序" / "优先建仓" / "调整建仓" → 建仓顺序调整
        """
        return _ai_generate_deepseek_recs(
            pnl_data,
            hedge_data,
            net_pnl,
            report_date=REPORT_DATE,
            deepseek_model=DEEPSEEK_MODEL,
            call_deepseek_fn=_call_deepseek,
        )

    def generate_next_day_plan(self, report_date: str | None = None) -> dict[str, Any]:
        """[B3.2 委托] 生成第二天交易计划 (基于 auto_trade_plan_500w_2026-2030.json 的4阶段)

        根据 auto_trade_plan_500w_2026-2030.json 的 4 阶段执行计划,
        推算次日所属阶段、当日预算、目标动作、对冲策略、风控指令。

        Args:
            report_date: 报告日期 'YYYY-MM-DD', None 表示今天

        Returns:
            dict, 含 next_trading_day, phase, daily_actions, hedge_action, risk_controls 等
        """
        return _plan_generate_next_day(
            report_date=report_date,
            project_root=_Path(__file__).resolve().parent,
        )


def save_report(report: dict, output_path: str) -> None:
    """保存报告"""
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2, ensure_ascii=False)


def print_report_summary(report: dict) -> None:
    """打印报告摘要"""

    # 组合盈亏
    report["portfolio_pnl"]["summary"]

    # 对冲明细
    report["hedge_position"]["summary"]
    report["risk_metrics"].get("beta_exposure", 0.3)

    # 净盈亏
    report["net_performance"]

    # 风险指标
    report["risk_metrics"]

    # AI建议
    for _i, _rec in enumerate(report["ai_recommendations"], 1):
        pass


def _build_data_integrity_warning(data_health: dict) -> str:
    """构建数据完整性警告文本"""
    data_status = data_health.get("status", "UNKNOWN")
    no_data_ratio = data_health.get("no_data_ratio", 0)
    no_data_count = data_health.get("no_data_count", 0)

    if data_status == "NOSIGNAL_MAJORITY":
        return f"""
> **⚠️⚠️⚠️ 数据完整性严重警告 ⚠️⚠️⚠️**
> {no_data_count}/{data_health.get("total_positions", 0)} 个持仓标的无实际行情数据（{no_data_ratio * 100:.0f}%）。
> 以下盈亏数据基于计划价格计算，**并非真实交易结果**。
> 请检查 Wind MCP 数据源连接状态后再信任本报告。
>
"""
    if data_status == "NOSIGNAL_PARTIAL":
        return f"""
> **⚠️ 数据完整性警告**
> {no_data_count}/{data_health.get("total_positions", 0)} 个持仓标的无实际行情数据（{no_data_ratio * 100:.0f}%）。
> 无数据标的盈亏不可用，报告中对应的 daily_pnl 和 daily_pnl_pct 显示为 N/A。
>
"""
    if data_status == "FALLBACK_HEAVY":
        fallback_count = data_health.get("fallback_count", 0)
        no_data_cnt = data_health.get("no_data_count", 0)
        total = data_health.get("total_positions", 0)
        unreal_count = fallback_count + no_data_cnt
        return f"""
> **⚠️⚠️ 数据源严重降级警告 ⚠️⚠️**
> {unreal_count}/{total} 个标的未使用实时行情（兜底{fallback_count} + 无数据{no_data_cnt}）。
> 盈亏数据基于兜底/缓存价格计算，**并非真实交易结果**。
> 基于此报告生成的交易计划需人工确认后方可执行。
>
"""
    return ""


def _render_position_details(details: list[dict]) -> str:
    """渲染现货持仓明细表"""
    md = "| 代码 | 名称 | 股数 | 成本价 | 收盘价 | 日涨跌% | 盈亏 | 模式 | 状态 |\n"
    md += "|------|------|------|------|------|------|------|------|------|\n"
    for d in details:
        status_icon = (
            "✅"
            if d["status"] == "NORMAL"
            else ("⚠️" if d["status"] == "WARNING" else ("🔴" if d["status"] == "STOP_LOSS_TRIGGERED" else "🟢"))
        )
        if d.get("calc_mode") == "FALLBACK_NO_DATA":
            mode_label = "⚠️无数据"
        elif d.get("calc_mode") == "snapshot":
            mode_label = "快照"
        else:
            mode_label = "计划"
        pnl_display = f"{d['daily_pnl_pct']:.2f}%" if d["daily_pnl_pct"] is not None else "N/A"
        md += f"| {d['code']} | {d['name']} | {d['shares']} | {d['cost_price']} | {d['close_price']} | {pnl_display} | {d['pnl']:,.0f} | {mode_label} | {status_icon} |\n"  # noqa: E501
    return md


def _render_hedge_details(details: list[dict]) -> str:
    """渲染期货期权对冲盈亏明细"""
    md = ""
    for h in details:
        cost_bd = h.get("cost_breakdown") or {}
        cost_note = cost_bd.get("cost_note", "")
        md += (
            f"""| 合约 | 方向 | 手数 | 开仓价 | 收盘价 | 对冲盈亏 | Beta降低 |
|------|------|------|------|------|------|------|
| {h["instrument"]} | {h["direction"]} | {h["contracts"]} | {h["entry_price"]} | {h["close_price"]} | """
            f"""{h["hedge_pnl"]:,.0f} | {h["beta_reduced"]:.3f} |

"""
        )
        if cost_bd:
            md += f"""| 成本项 | 数值 |
|------|------|
| 预估总成本 | {h.get("cost", 0):,.2f} |
| 佣金费率 | {cost_bd.get("commission_rate", 0):.6f} |
| 滑点费率 | {cost_bd.get("slippage_rate", 0):.6f} |
| 保证金比例 | {cost_bd.get("margin_rate", 0):.2%} |
| 预估佣金 | {cost_bd.get("estimated_commission", 0):,.2f} |
| 预估滑点 | {cost_bd.get("estimated_slippage", 0):,.2f} |
| 预估保证金 | {cost_bd.get("estimated_margin", 0):,.2f} |

> ⚠️ {cost_note}

"""
    return md


def _render_hedge_plan(plan: dict) -> str:
    """渲染期货期权计划头寸"""
    md = ""
    plan_details = plan.get("details", [])
    if not plan_details:
        return "> ⚠️ positions.json 中未配置 hedge_positions\n\n"

    md += "| # | 工具 | 交易所 | 方向 | 目标手数 | 合约乘数 | 保证金率 | 目标Beta降低 | 行权价 | 权利金预算 | 估算名义价值 | 估算成本 | 说明 |\n"  # noqa: E501
    md += "|---|------|--------|------|---------|---------|---------|------------|--------|-----------|------------|---------|------|\n"  # noqa: E501
    for i, p in enumerate(plan_details, 1):
        strike_str = p.get("strike") or "-"
        premium_str = f"¥{p.get('premium_budget', 0):,}" if p.get("is_option") else "¥0"
        md += (
            f"| {i} | {p['instrument']} | {p['exchange']} | {p['direction']} | "
            f"{p['target_contracts']} | {p['multiplier']} | {p['margin_rate']:.2%} | "
            f"{p['target_beta_reduction']:.3f} | {strike_str} | {premium_str} | "
            f"¥{p['estimated_notional']:,.0f} | ¥{p['estimated_cost']:,.0f} | {p['reason']} |\n"
        )
    summary = plan.get("summary", {})
    md += (
        f"| **合计** | - | - | - | - | - | - | **{summary.get('total_beta_reduction', 0):.3f}** | - | "
        f"**¥{summary.get('total_premium_budget', 0):,}** | "
        f"**¥{summary.get('total_estimated_notional', 0):,.0f}** | - | - |\n\n"
    )
    md += f"> 📌 **期货期权对冲工具**: 共 {summary.get('tool_count', 0)} 类工具 | "
    md += f"期货名义价值 ¥{summary.get('total_estimated_notional', 0):,.0f} | "
    md += f"期权权利金预算 ¥{summary.get('total_premium_budget', 0):,} | "
    md += f"目标Beta降低 {summary.get('total_beta_reduction', 0):.3f}\n\n"
    return md


def _render_next_day_stock_plan(stock_acc: dict, next_day_plan: dict) -> str:
    """渲染次日股票ETF账户计划"""
    md = "| 项目 | 数值 |\n"
    md += "|------|------|\n"
    md += f"| 账户资金 | {stock_acc.get('capital', next_day_plan.get('stock_etf_capital', 3000000)):,.0f} |\n"
    md += f"| 目标标的数 | {stock_acc.get('target_positions', 20)} |\n"
    md += f"| 当日预算 | {stock_acc.get('daily_capital', 0):,.2f} |\n\n"
    md += "**当日交易动作**:\n\n"
    for i, action in enumerate(stock_acc.get("daily_actions", []), 1):
        md += f"{i}. {action}\n"
    target_detail = stock_acc.get("target_positions_detail", [])
    if target_detail:
        md += f"\n**次日标的明细 (共 {len(target_detail)} 只)**:\n\n"
        md += "| # | 代码 | 名称 | 类型 | 动作 | 权重 | 目标金额 | 当日金额 | 风格 | ETF信号 | 净流入(亿) | 说明 |\n"
        md += "|---|------|------|------|------|------|---------|---------|------|---------|-----------|------|\n"
        for idx, pos in enumerate(target_detail, 1):
            code = pos.get("code", "")
            name = pos.get("name", "")
            ptype = pos.get("type", "")
            action = pos.get("action", "HOLD")
            weight = pos.get("weight", 0)
            amount = pos.get("amount", 0)
            daily_amt = pos.get("daily_amount", 0)
            style = pos.get("style", "")
            etf_sig = pos.get("etf_flow_signal", "")
            etf_inflow = pos.get("etf_inflow", "")
            reason = pos.get("reason", "")
            inflow_str = f"{etf_inflow:.2f}" if isinstance(etf_inflow, (int, float)) else str(etf_inflow)
            reason_short = reason[:50] + "..." if len(reason) > 50 else reason
            md += f"| {idx} | {code} | {name} | {ptype} | {action} | {weight:.0%} | {amount:,.0f} | {daily_amt:,.0f} | {style} | {etf_sig} | {inflow_str} | {reason_short} |\n"  # noqa: E501
    return md


def _render_next_day_hedge_plan(hedge_acc: dict) -> str:
    """渲染次日对冲账户计划"""
    md = "| 项目 | 数值 |\n"
    md += "|------|------|\n"
    md += f"| 对冲模式 | {hedge_acc.get('mode', 'dynamic')} |\n"
    md += f"| 触发阈值 | {hedge_acc.get('trigger_threshold', 0.05)} |\n"
    md += f"| 再平衡频率 | {hedge_acc.get('rebalance_frequency', '每周五')} |\n\n"
    md += "**对冲工具明细**:\n\n"
    md += "| 工具 | 方向 | 目标手数 | 说明 |\n"
    md += "|------|------|---------|------|\n"
    for inst in hedge_acc.get("instruments", []):
        md += f"| {inst.get('instrument', '')} | {inst.get('direction', '')} | {inst.get('target_contracts', 0)} | {inst.get('reason', '')} |\n"  # noqa: E501

    hedge_positions_detail = hedge_acc.get("hedge_positions_detail", [])
    if hedge_positions_detail:
        md += "\n**期货期权对冲仓位明细** (来自 positions.json):\n\n"
        md += "| # | 工具 | 交易所 | 方向 | 目标手数 | 合约乘数 | 保证金率 | 目标Beta降低 | 行权价 | 权利金预算 | 估算名义价值 | 说明 |\n"  # noqa: E501
        md += "|---|------|--------|------|---------|---------|---------|------------|--------|-----------|------------|------|\n"  # noqa: E501
        for idx, hp in enumerate(hedge_positions_detail, 1):
            instrument = hp.get("instrument", "")
            exchange = hp.get("exchange", "")
            direction = hp.get("direction", "")
            contracts = hp.get("target_contracts", 0)
            multiplier = hp.get("multiplier", 0)
            margin_rate = hp.get("margin_rate", 0)
            beta_red = hp.get("target_beta_reduction", 0)
            strike = hp.get("strike", "-") if hp.get("strike") else "-"
            premium = hp.get("premium_budget", 0)
            notional = hp.get("estimated_notional", 0)
            reason = hp.get("reason", "")[:60]
            md += f"| {idx} | {instrument} | {exchange} | {direction} | {contracts} | {multiplier} | {margin_rate:.2%} | {beta_red:.2f} | {strike} | ¥{premium:,} | ¥{notional:,} | {reason} |\n"  # noqa: E501
        total_notional = sum(hp.get("estimated_notional", 0) for hp in hedge_positions_detail)
        total_beta_red = sum(hp.get("target_beta_reduction", 0) for hp in hedge_positions_detail)
        md += f"| **合计** | - | - | - | - | - | - | **{total_beta_red:.2f}** | - | - | **¥{total_notional:,}** | - |\n"
    return md


def _render_expected_performance(exp_perf: dict, proj: dict) -> str:
    """渲染预期绩效对照"""
    if not exp_perf:
        return ""

    _proj = proj or {}
    _proj_scenarios = _proj.get("scenarios", {})
    _proj_expected = _proj.get("expected", {})
    _base = _proj_scenarios.get("base", {})

    _ann_raw = (
        exp_perf.get("annual_return") or _base.get("weighted_annualized") or _proj_expected.get("expected_annualized")
    )
    if isinstance(_ann_raw, (int, float)) and _ann_raw > 1:
        _ann_raw = _ann_raw / 100.0
    _ann_str = f"{_ann_raw:.2%}" if isinstance(_ann_raw, (int, float)) else "待测算"

    _dd = exp_perf.get("max_drawdown")
    _dd_str = f"{_dd:.2%}" if isinstance(_dd, (int, float)) else "待测算"

    _sr = exp_perf.get("sharpe_ratio")
    _sr_str = f"{_sr:.3f}" if isinstance(_sr, (int, float)) else "待测算"

    _cum_raw = (
        exp_perf.get("4_5_year_total_return")
        or _base.get("cumulative_return")
        or _proj_expected.get("expected_cumulative")
    )
    if isinstance(_cum_raw, (int, float)) and _cum_raw > 1:
        _cum_raw = _cum_raw / 100.0
    _cum_str = f"{_cum_raw:.2%}" if isinstance(_cum_raw, (int, float)) else "待测算"

    _init_cap = _proj.get("initial_capital", _get_total_capital())
    _final_amt = _base.get("final_amount") or _proj_expected.get("expected_final_amount")
    if isinstance(_final_amt, (int, float)) and _final_amt > 0:
        _proj_str = f"¥{_init_cap:,.0f} → ¥{_final_amt:,.0f}"
    else:
        _proj_str = "待测算"

    return f"""### 7.5 预期绩效对照

| 指标 | 目标 | 预期 |
|------|------|------|
| 年化收益 | >8% | {_ann_str} |
| 最大回撤 | <15% | {_dd_str} |
| Sharpe比率 | >0.80 | {_sr_str} |
| 4.5年总收益 | - | {_cum_str} |
| 4.5年终值 | - | {_proj_str} |
"""


def _render_return_projection_section(proj: dict) -> str:
    """渲染收益率预测章节"""
    if not proj or proj.get("error"):
        return ""
    proj_scenarios = proj.get("scenarios", {})
    proj_expected = proj.get("expected", {})
    proj_prob = proj.get("probability_weights", {})
    proj_risk = proj.get("risk_disclosure", {})

    md = f"""
### 7.6 收益率预测 (基于十五五降权后持仓)

**预测版本**: {proj.get("version", "unknown")}
**投资期限**: {proj.get("investment_horizon", "")} ({proj.get("horizon_years", 1.5)} 年)
**初始资本**: ¥{proj.get("initial_capital", _get_total_capital()):,}

#### 四场景预测

| 场景 | 概率 | 加权年化 | 累计收益 | 期末金额 | 盈亏 |
|------|------|---------|---------|---------|------|
"""
    scenario_icons = {"bull": "🐂", "base": "📊", "bear": "🐻", "black_swan": "⚫"}
    for s_key in ["bull", "base", "bear", "black_swan"]:
        sc = proj_scenarios.get(s_key, {})
        icon = scenario_icons.get(s_key, "")
        prob = proj_prob.get(s_key, 0)
        ann = sc.get("weighted_annualized", 0)
        cum = sc.get("cumulative_return", 0)
        fin = sc.get("final_amount", 0)
        profit = sc.get("total_profit", 0)
        profit_str = f"+¥{profit:,.0f}" if profit >= 0 else f"-¥{abs(profit):,.0f}"
        md += (
            f"| {icon} {sc.get('label', s_key)} | {prob:.0%} | {ann:.2f}% | {cum:.2f}% | ¥{fin:,.0f} | {profit_str} |\n"
        )

    md += (
        f"""
#### 加权期望

| 指标 | 数值 |
|------|------|
| **加权期望年化** | **{proj_expected.get("expected_annualized", 0):.2f}%** |
| 加权期望累计 | {proj_expected.get("expected_cumulative", 0):.2f}% |
| 加权期望期末金额 | ¥{proj_expected.get("expected_final_amount", 0):,.0f} |
| 加权期望盈亏 | """
        f"""{"+" if proj_expected.get("expected_profit", 0) >= 0 else ""}¥"""
        f"""{proj_expected.get("expected_profit", 0):,.0f} |

#### 风险披露

| 风险类型 | 说明 |
|---------|------|
| 集中度风险 | {proj_risk.get("concentration_risk", "-")} |
| 波动率风险 | {proj_risk.get("volatility_risk", "-")} |
| 对冲覆盖 | {proj_risk.get("hedge_coverage", "-")} |
| 政策风险 | {proj_risk.get("policy_risk", "-")} |
| 流动性风险 | {proj_risk.get("liquidity_risk", "-")} |
"""
    )
    return md


def generate_markdown_report(report: dict) -> str:
    """生成Markdown格式报告"""
    pnl_summary = report["portfolio_pnl"]["summary"]
    net_perf = report["net_performance"]
    total_cost = pnl_summary["total_cost"]
    total_market_value = pnl_summary["total_market_value"]
    portfolio_pnl = net_perf["portfolio_pnl"]
    hedge_pnl = net_perf["hedge_pnl"]
    hedge_cost = net_perf.get("hedge_cost", 0)
    net_pnl = net_perf["net_pnl"]
    net_pnl_pct = net_perf["net_pnl_pct"]

    current_value = total_cost + net_pnl
    hedge_pnl_pct = (hedge_pnl / total_cost * 100) if total_cost > 0 else 0

    data_health = report.get("meta", {}).get("data_source_health", {})
    data_integrity_warning = _build_data_integrity_warning(data_health)

    md = (f"""# 📊 综合盈亏统计报告（含期货期权对冲）

**日期**: {report["meta"]["report_date"]}
**阶段**: {report["meta"]["phase"]}
**视角**: {report["meta"]["fund_style"]}
**数据状态**: {data_health.get("status", "UNKNOWN")}
{data_integrity_warning}
---

## 一、市场概况

| 指标 | 数值 |
|------|------|
| 市场状态 | {report["market_overview"]["market_regime"]} |
| VIX估计 | {report["market_overview"]["vix_estimate"]} |
| 趋势判断 | {report["market_overview"]["market_trend"]} |

---

## 二、综合盈亏概览

### 2.1 账户汇总

| 账户类型 | 投入资金 | 当前价值 | 浮盈 | 浮盈率 |
|---------|---------|---------|------|--------|
| 现货账户 | ¥{total_cost:,.2f} | ¥{total_market_value:,.2f} | """
    f"""{"-" if portfolio_pnl < 0 else "+"}¥{abs(portfolio_pnl):,.2f} | {pnl_summary["total_pnl_pct"]:+.2f}% |
| 期货对冲 | - | - | {"-" if hedge_pnl < 0 else "+"}¥{abs(hedge_pnl):,.2f} | {hedge_pnl_pct:+.2f}% |
| 期权保护（计划中） | - | - | ¥0.00 | - |
| 对冲成本 | - | - | -¥{hedge_cost:,.2f} | - |
| **综合净盈亏** | **¥{total_cost:,.2f}** | **¥{current_value:,.2f}** | """
    f"""**{"-" if net_pnl < 0 else "+"}¥{abs(net_pnl):,.2f}** | **{net_pnl_pct:+.2f}%** |

### 2.2 盈亏计算公式

```
综合净盈亏 = 现货盈亏 + 期货盈亏 - 对冲成本
          = {portfolio_pnl:,.2f} + {hedge_pnl:,.2f} - {hedge_cost:,.2f}
          = {"-" if net_pnl < 0 else "+"}{abs(net_pnl):,.2f} 元
          = {net_pnl_pct:+.2f}%

当前价值 = 投入资金 + 净盈亏
        = {total_cost:,.2f} + {net_pnl:,.2f}
        = {current_value:,.2f} 元
```

---

## 三、现货持仓盈亏明细

### 3.1 概览

| 项目 | 金额 |
|------|------|
| 总成本 | ¥{total_cost:,.2f} |
| 总市值 | ¥{total_market_value:,.2f} |
| 总盈亏 | **{"-" if portfolio_pnl < 0 else "+"}¥{abs(portfolio_pnl):,.2f}** ({pnl_summary["total_pnl_pct"]:+.2f}%) |
| 持仓数 | {pnl_summary["position_count"]} |

### 3.2 持仓明细

""")
    md += _render_position_details(report["portfolio_pnl"]["details"])

    md += """
---

## 四、期货期权对冲盈亏

"""
    md += _render_hedge_details(report["hedge_position"]["details"])

    _beta_gap = abs(
        report["hedge_position"]["summary"]["current_portfolio_beta"]
        - report["risk_metrics"]["beta_exposure"]
    )
    md += (f"""### 4.2 对冲效果评估

| 指标 | 数值 |
|------|------|
| 现货组合 Beta | {report["hedge_position"]["summary"]["current_portfolio_beta"]:.3f} |
| 目标 Beta | {report["hedge_position"]["summary"]["target_beta"]:.3f} |
| 已实现 Beta 降低 | ~{_beta_gap:.2f} |
| 现货亏损 | {"-" if portfolio_pnl < 0 else "+"}¥{abs(portfolio_pnl):,.2f} |
| 期货盈亏 | {"-" if hedge_pnl < 0 else "+"}¥{abs(hedge_pnl):,.2f} |
| **净对冲收益** | **{"-" if (hedge_pnl - portfolio_pnl) < 0 else "+"}¥{abs(hedge_pnl - abs(portfolio_pnl)):,.2f}** |
| 对冲成本 | ¥{hedge_cost:,.2f}/日 |

### 4.3 期货期权计划头寸 (来自 positions.json)

""")
    md += _render_hedge_plan(report.get("hedge_position_plan") or {})

    _mdd = report["risk_metrics"]["max_drawdown_pct"]
    _mdd_val = f"{_mdd:.2f}% (组合层面)" if _mdd is not None else "N/A (历史数据不足)"
    _mdd_rating = "安全" if _mdd is not None and _mdd > -5 else "N/A" if _mdd is None else "关注"
    md += (f"""---

## 五、风险指标

| 指标 | 数值 | 评级 |
|------|------|------|
| 日均收益 | {report["risk_metrics"]["avg_daily_return_pct"]:.2f}% | """
    f"""{("良好" if report["risk_metrics"]["avg_daily_return_pct"] > 0.3 else "中性")} |
| 波动率 | {report["risk_metrics"]["portfolio_volatility_pct"]:.2f}% | """
    f"""{("可控" if report["risk_metrics"]["portfolio_volatility_pct"] < 1.0 else "偏高")} |
| 最大回撤 | {_mdd_val} | {_mdd_rating} |
| Beta敞口 | {report["risk_metrics"]["beta_exposure"]:.3f} | """
    f"""{("达标" if report["risk_metrics"]["beta_exposure"] < 0.5 else "偏高")} |

---

## 六、关键分析与建议

### 6.1 对冲效果总结

""")
    _hedge_plan = report.get("hedge_position_plan") or {}
    _hedge_summary = _hedge_plan.get("summary", {})
    _beta_reduced = _hedge_summary.get("total_beta_reduction", 0) or 0
    _current_beta = report["hedge_position"]["summary"].get("current_portfolio_beta", 0) or 0
    _target_beta = 0.3
    if net_pnl > 0 and hedge_pnl > 0:
        md += (f"""✅ **对冲策略运行良好**：期货空头在市场下跌时有效保护了组合
- 现货{"亏损" if portfolio_pnl < 0 else "盈利"} {"-" if portfolio_pnl < 0 else "+"}¥{abs(portfolio_pnl):,.0f} """
            f"""被期货盈利 +¥{abs(hedge_pnl):,.0f} {"完全覆盖" if hedge_pnl > abs(portfolio_pnl) else "部分覆盖"}
- 净收益达 +¥{abs(net_pnl):,.0f}，综合净收益率 {net_pnl_pct:+.2f}%
""")
    elif net_pnl > 0 and hedge_pnl == 0:
        md += f"""ℹ️ **现货盈利，对冲头寸待执行**：当前期货/期权对冲未实际触发盈亏
- 现货盈利 +¥{abs(portfolio_pnl):,.0f}，期货对冲盈亏 ¥0
- 净收益 +¥{abs(net_pnl):,.0f}，综合净收益率 {net_pnl_pct:+.2f}%
"""
    else:
        md += (f"""⚠️ **今日市场波动**：
- 现货{"亏损" if portfolio_pnl < 0 else "盈利"} {"-" if portfolio_pnl < 0 else "+"}¥{abs(portfolio_pnl):,.0f}，"""
            f"""期货{"盈利" if hedge_pnl > 0 else "亏损"} {"+" if hedge_pnl > 0 else ""}¥{abs(hedge_pnl):,.0f}
- 净{"亏损" if net_pnl < 0 else "收益"} {"-" if net_pnl < 0 else "+"}¥{abs(net_pnl):,.0f}，"""
            f"""综合净收益率 {net_pnl_pct:+.2f}%
""")

    if _beta_reduced > 0.01:
        md += (f"""
- Beta 从 {_current_beta:.3f} 降低 {_beta_reduced:.3f} 至 ~{_current_beta - _beta_reduced:.3f}"""
            f"""（目标 {_target_beta:.3f}）
""")
    else:
        md += f"""
- Beta 当前 {_current_beta:.3f}，对冲未生效（目标降至 {_target_beta:.3f}，已配置头寸待执行）
"""

    md += """
### 6.2 风险提示

"""

    max_dd = report["risk_metrics"]["max_drawdown_pct"]
    if max_dd is not None and max_dd < -10:
        md += f"""⚠️ **高风险标的需关注**：
- 组合最大回撤 {max_dd:.2f}%（需关注）
- 建议密切监控，必要时调整仓位或增加对冲

"""
    elif max_dd is None:
        md += """⚠️ **风险指标不完整**：
- 组合最大回撤: N/A（历史报告数据不足5天，无法计算）
- 建议积累至少5个交易日的数据后再评估

"""
    else:
        md += """✅ **风险状态正常**：
- 所有标的止损状态正常，无触发止损标的

"""

    md += """### 6.3 期权建仓建议

"""

    plan = report.get("hedge_position_plan") or {}
    premium_total = plan.get("summary", {}).get("total_premium_budget", 0)
    if premium_total > 0:
        _vix_est = report["market_overview"]["vix_estimate"]
        _vix_zone = "偏低" if _vix_est < 13 else "偏高" if _vix_est >= 22 else "正常"
        md += (f"""⏳ **期权保护待启动**：
- 当前 VIX {_vix_est}，处于{_vix_zone}区间
- 预留 ¥{premium_total:,} 预算，VIX >= 18 触发轻量建仓 (PUT_SPREAD)
- 可优先建仓上证50ETF Put，覆盖宽基尾部风险

""")
    else:
        md += "> ⚠️ 期权保护未配置，建议根据市场风险评估配置 Put 期权\n\n"

    md += """### 6.4 资金分配

"""

    # SC-22 (2026-09-12, capital_base 消费点复验): 默认值改走唯一事实源。
    #   原 5_000_000 为 P1-2 资金口径统一时漏改的残留; 此处分母用于「现货/期货/
    #   期权占净值百分比」, 口径错会同步放大三项占比。(同源修复见 next_day_planner)
    total_capital = report.get("next_day_plan", {}).get(
        "total_capital", _get_total_capital()
    )
    # 除零保护: total_capital=0 或负值时使用默认值
    if total_capital <= 0:
        total_capital = _get_total_capital()
    futures_margin = (
        report["hedge_position"]["summary"].get("total_hedge_notional", 0) * 0.12
        if report["hedge_position"]["summary"].get("total_hedge_notional", 0) > 0
        else 0
    )

    md += (f"""| 类别 | 金额 | 占比 |
|------|------|------|
| 现货持仓 | ¥{total_market_value:,.0f} | {total_market_value / total_capital * 100:.1f}% |
| 期货保证金 | ¥{futures_margin:,.0f} | {futures_margin / total_capital * 100:.1f}% |
| 期权预算 | ¥{premium_total:,.0f} | {premium_total / total_capital * 100:.1f}% |
| 剩余现金 | ¥{(total_capital - total_market_value - futures_margin - premium_total):,.0f} | """
    f"""{(total_capital - total_market_value - futures_margin - premium_total) / total_capital * 100:.1f}% |
| **合计** | **¥{total_capital:,.0f}** | **100%** |

---

## 七、AI决策建议

""")
    for i, rec in enumerate(report["ai_recommendations"], 1):
        md += f"{i}. {rec}\n"

    next_day_plan = report.get("next_day_plan", {})
    if next_day_plan and not next_day_plan.get("error"):
        nd = next_day_plan.get("next_trading_day", "")
        wd = next_day_plan.get("weekday", "")
        phase = next_day_plan.get("phase", {})
        stock_acc = next_day_plan.get("stock_etf_account", {})
        hedge_acc = next_day_plan.get("hedge_account", {})
        etf_mon = next_day_plan.get("etf_flow_monitoring", {})
        risk_ctrl = next_day_plan.get("risk_controls", {})
        exp_perf = next_day_plan.get("expected_performance", {})

        md += f"""
---

## 八、次日交易计划

**下一交易日**: {nd} ({wd})
**所属阶段**: {phase.get("name_cn", "")} (第 {phase.get("day_index", 0)} 天 / {phase.get("period", "")})
**阶段策略**: {phase.get("strategy", "")}

### 7.1 股票ETF账户计划

"""
        md += _render_next_day_stock_plan(stock_acc, next_day_plan)

        md += """
### 7.2 对冲账户计划

"""
        md += _render_next_day_hedge_plan(hedge_acc)

        if etf_mon:
            md += f"""
### 7.3 ETF资金流监控

| 项目 | 数值 |
|------|------|
| 数据源 | {etf_mon.get("source", "实时ETF资金流向监控")} |
| 监控频率 | {etf_mon.get("frequency", "每日3次")} |
| 强信号阈值 | {etf_mon.get("strong_signal_threshold", 20)} 亿 |
| 中信号阈值 | {etf_mon.get("medium_signal_threshold", 10)} 亿 |
| 反转阈值 | {etf_mon.get("reversal_threshold", -5)} 亿 |
| 自动调整 | {("是" if etf_mon.get("auto_adjust", False) else "否")} |
"""

        if risk_ctrl:
            md += f"""
### 7.4 风控指令

| 指标 | 数值 |
|------|------|
| 单标的止损 | {risk_ctrl.get("stop_loss_single", -0.15):.0%} |
| 组合止损 | {risk_ctrl.get("stop_loss_portfolio", -0.12):.0%} |
| 单标的止盈 | {risk_ctrl.get("take_profit_single", 0.50):.0%} |
| 单标的权重上限 | {risk_ctrl.get("max_single_position", 0.10):.0%} |
| 单板块权重上限 | {risk_ctrl.get("max_sector_exposure", 0.30):.0%} |
"""

        md += _render_expected_performance(exp_perf, report.get("return_projection", {}))

    proj = report.get("return_projection", {})
    md += _render_return_projection_section(proj)

    md += f"""
---

**报告生成时间**: {report["meta"]["generated_at"]}
**数据源**: Wind MCP > 通达信 > AKShare
"""
    return md


def main() -> None:
    """主函数 — argparse 契约: --help 只展示用法, 不生成报告"""
    import argparse
    from pathlib import Path

    from utils.runtime_mode import env_flag, set_mode

    parser = argparse.ArgumentParser(
        prog="generate_daily_report",
        description=(
            "每日盈亏报告生成器: 读取持仓/对冲成交/快照, 拉取行情并生成 JSON+Markdown 报告。"
            "注意: 默认会发起网络行情请求并写入报告文件。"
        ),
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument(
        "date",
        nargs="?",
        default=None,
        help="报告日期 (YYYY-MM-DD, 默认今天)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        default=env_flag("QUANT_DRY_RUN"),
        help="干跑模式: 生成并打印报告摘要, 不写入 JSON/Markdown 文件"
             " (可用 QUANT_DRY_RUN=1 预设)",
    )
    args = parser.parse_args()

    # P1-1: CLI/env 解析结果广播到统一三态开关 (深层模块经 is_dry_run() 感知)
    set_mode(dry_run=args.dry_run)

    report_date_arg = args.date or now_bj().strftime("%Y-%m-%d")

    # 切换到项目根目录 (保证相对路径正确)
    project_root = Path(__file__).resolve().parent
    os.chdir(project_root)

    # P2 修复 (2026-09-09): 用 DEFAULT_POSITIONS_PATH, 不依赖 CWD
    from utils.positions_loader import DEFAULT_POSITIONS_PATH

    positions_file = str(DEFAULT_POSITIONS_PATH)

    # 自动查找最新的对冲执行文件
    hedge_dir = project_root / "v8.3_institutional" / "reports"
    hedge_file = None
    if hedge_dir.exists():
        # 优先查找当日的对冲执行文件
        date_compact = report_date_arg.replace("-", "")
        hedge_candidates = sorted(
            hedge_dir.glob(f"hedge_execution_fill_{report_date_arg}*.json"),
            reverse=True,
        )
        if not hedge_candidates:
            hedge_candidates = sorted(hedge_dir.glob("hedge_execution_fill_*.json"), reverse=True)
        if hedge_candidates:
            hedge_file = str(hedge_candidates[0])

    if hedge_file is None:
        # 不再硬编码回退到可能不存在的文件; 打印警告, 后续逻辑处理 hedge_file=None 的情况
        pass

    # 自动查找当日持仓快照
    sim_dir = project_root / "v8.3_institutional" / "sim_snapshots"
    positions_snapshot = None
    if sim_dir.exists():
        snap_candidates = sorted(sim_dir.glob(f"positions_{date_compact}*.json"), reverse=True)
        if snap_candidates:
            positions_snapshot = str(snap_candidates[0])

    # 自动查找当日 trade_plan (用于获取第一次交易开盘价作为成本价)
    plan_dir = project_root / "v8.3_institutional" / "trade_plans"
    trade_plan_file = None
    if plan_dir.exists():
        plan_candidates = sorted(plan_dir.glob(f"trade_plan_{date_compact}*.json"), reverse=True)
        if plan_candidates:
            trade_plan_file = str(plan_candidates[0])

    analyzer = PortfolioAnalyzer(positions_file, hedge_file)
    # 如果有持仓快照, 用它覆盖 positions.json 中的 shares 字段
    if positions_snapshot:
        analyzer._apply_positions_snapshot(positions_snapshot, trade_plan_file)
    report = analyzer.generate_report(report_date=report_date_arg)

    # 打印摘要
    print_report_summary(report)

    if args.dry_run:
        logger.info("[dry-run] 报告未落盘 (跳过 JSON/Markdown 写入)")
        return report

    # 保存JSON报告 (使用 report_date_arg 而非全局 REPORT_DATE)
    # 输出到 v8.3_institutional/reports/ 目录
    reports_dir = project_root / "v8.3_institutional" / "reports"
    reports_dir.mkdir(parents=True, exist_ok=True)
    json_output = str(reports_dir / f"daily_pnl_report_{report_date_arg}.json")
    save_report(report, json_output)

    # 保存Markdown报告
    md_content = generate_markdown_report(report)
    md_output = str(reports_dir / f"daily_pnl_report_{report_date_arg}.md")
    with open(md_output, "w", encoding="utf-8") as f:
        f.write(md_content)

    # 只读追加 TrendCast 信号卡片 (fail-open + 幂等; 仅作决策上下文, 不参与交易决策)
    try:
        from utils.reporting.trendcast_card import append_trendcast_card

        append_trendcast_card([md_output], report_date_arg)
    except Exception as exc:  # noqa: BLE001  # 观测路径 fail-open, 不得影响日报生成
        logger.warning("[TrendCast] 信号卡片追加跳过: %s", exc)

    return report


if __name__ == "__main__":
    main()
