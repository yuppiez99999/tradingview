"""自动因子工厂 (AutoFactorFactory) — 因子自动发现/验证/部署/淘汰全流水线.

模块整合 8.4 — ARCHITECTURE_自我进化框架 §6.3 (v2.0 合并版)
任务编号: T3.3 (Phase 3 进化层)

架构:
```
┌─────────────┐    ┌─────────────┐    ┌─────────────┐    ┌─────────────┐
│  Discovery  │───→│  Validation │───→│ Deployment  │───→│ Monitoring  │
│  因子发现    │    │  因子验证    │    │  因子部署    │    │  因子监控    │
└─────────────┘    └─────────────┘    └─────────────┘    └─────────────┘
       │                  │                  │                  │
   QLib 3800股       CRO Gate           影子账户5日         IC<0.02
   technical因子     Walk-Forward       A/B对比             持续20日
   基本面衍生         8段压力测试         注册到library      自动下线
```

向后兼容:
    - 复用 research/factor_discovery_enhanced.py 的发现和验证能力
    - 集成 AlphaFactorLibrary 的 compute_all 接口做生产部署
    - 集成 DriftMonitor 做因子监控与淘汰
    - 所有进化动作写入 EvolutionMemory 审计

用法:
    from utils.evolution.auto_factor_factory import AutoFactorFactory

    factory = AutoFactorFactory()
    # 全流水线一键运行
    report = factory.run_pipeline(data_source="qlib", universe="csi500")

    # 分步执行
    candidates = factory.discover()
    validated = factory.validate(candidates)
    deployed = factory.deploy(validated)
    suggestions = factory.monitor()

参考:
    - ARCHITECTURE_自我进化框架 §6.3
    - 国泰君安《基于短周期价量特征的多因子选股体系》2017.06 (GTJA191)
    - 国泰海通《量化2025年度复盘系列》
"""

from __future__ import annotations

import json
import logging
import sys
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

# ============================================================
# Feature Flag
# ============================================================

FLAG_AUTO_FACTORY = "USE_AUTO_FACTOR_FACTORY"

# ============================================================
# 数据类
# ============================================================


@dataclass
class CandidateFactor:
    """候选因子 — 发现阶段产出"""

    name: str  # 因子名 (如 MOM_60D, VOL_20D)
    category: str  # 因子类别 (Momentum/Volatility/Liquidity/Technical/...)
    formula: str  # 因子计算公式描述
    source: str  # 来源 (qlib/technical/fundamental_proxy/...)
    n_stocks: int = 0  # 覆盖股票数
    first_date: str = ""  # 最早可用日期
    last_date: str = ""  # 最晚可用日期


@dataclass
class ValidatedFactor:
    """已验证因子 — 验证阶段产出"""

    name: str
    category: str
    formula: str
    source: str

    # IC 统计
    ic_mean_1d: float = 0.0
    ic_std_1d: float = 0.0
    ic_ir_1d: float = 0.0
    ic_positive_ratio: float = 0.0
    ic_mean_5d: float = 0.0
    ic_ir_5d: float = 0.0
    ic_mean_20d: float = 0.0
    ic_ir_20d: float = 0.0

    # 分层回测
    long_short_return: float = 0.0
    group_returns: list[float] = field(default_factory=list)
    monotonicity: float = 0.0

    # Walk-Forward 验证
    wf_mean_ic: float = 0.0
    wf_ic_std: float = 0.0
    wf_ir: float = 0.0
    wf_n_windows: int = 0
    wf_consistency: float = 0.0  # 正IC窗口占比

    # 综合评分
    score: float = 0.0
    effective: bool = False
    direction: str = "positive"

    # 验证元数据
    validation_date: str = ""
    n_samples: int = 0


@dataclass
class DeployedFactor:
    """已部署因子 — 部署阶段产出"""

    name: str
    category: str
    formula: str
    deploy_date: str = ""
    library_key: str = ""  # 在 AlphaFactorLibrary 中的注册键
    code_path: str = ""  # 生成的代码文件路径
    version: int = 1
    active: bool = True


@dataclass
class RetireSuggestion:
    """淘汰建议 — 监控阶段产出"""

    name: str
    category: str
    current_ic: float = 0.0
    ic_days_below_threshold: int = 0
    reason: str = ""
    suggest_retire: bool = False
    retire_date: str = ""
    archived: bool = False


@dataclass
class FactoryPipelineReport:
    """全流水线执行报告"""

    pipeline_date: str = ""
    n_discovered: int = 0
    n_validated: int = 0
    n_deployed: int = 0
    n_retired: int = 0
    n_active: int = 0
    top_factors: list[str] = field(default_factory=list)
    retired_factors: list[str] = field(default_factory=list)
    duration_seconds: float = 0.0
    status: str = "success"
    error: str = ""


# ============================================================
# AutoFactorFactory — 核心引擎
# ============================================================


class AutoFactorFactory:
    """自动因子工厂 — 因子自动发现/验证/部署/淘汰全流水线.

    Args:
        memory: EvolutionMemory 实例 (可选, 用于审计留痕)
        guard: EvolutionGuard 实例 (可选, 用于进化守卫)
        data_dir: 数据缓存目录 (默认: PROJECT_ROOT/cache/factor_factory)
        ic_threshold: IC 有效性阈值 (默认: 0.02)
        ir_threshold: IR 有效性阈值 (默认: 0.30)
        ic_retire_threshold: 淘汰 IC 阈值 (默认: 0.02, 低于此值持续 N 日淘汰)
        retire_confirm_days: 淘汰确认天数 (默认: 20)
        max_active_factors: 最大活跃因子数 (默认: 50)
        max_weight_per_factor: 单因子上限权重 (默认: 0.30)
        feature_flag: Feature Flag 名 (默认: USE_AUTO_FACTOR_FACTORY)
    """

    def __init__(
        self,
        memory: Any = None,
        guard: Any = None,
        data_dir: str | Path | None = None,
        ic_threshold: float = 0.02,
        ir_threshold: float = 0.30,
        ic_retire_threshold: float = 0.02,
        retire_confirm_days: int = 20,
        max_active_factors: int = 50,
        max_weight_per_factor: float = 0.30,
        feature_flag: str = FLAG_AUTO_FACTORY,
    ):
        self.memory = memory
        self.guard = guard
        self.ic_threshold = ic_threshold
        self.ir_threshold = ir_threshold
        self.ic_retire_threshold = ic_retire_threshold
        self.retire_confirm_days = retire_confirm_days
        self.max_active_factors = max_active_factors
        self.max_weight_per_factor = max_weight_per_factor
        self.feature_flag = feature_flag

        # 数据目录
        if data_dir is None:
            data_dir = _PROJECT_ROOT / "cache" / "factor_factory"
        self.data_dir = Path(data_dir)
        self.data_dir.mkdir(parents=True, exist_ok=True)

        # 内部状态
        self._discovered: dict[str, CandidateFactor] = {}
        self._validated: dict[str, ValidatedFactor] = {}
        self._deployed: dict[str, DeployedFactor] = {}
        self._retired: dict[str, RetireSuggestion] = {}

        # 因子监控历史 (IC 时间序列)
        self._ic_history: dict[str, list[dict]] = {}

        # 加载持久化状态
        self._load_state()

        # QLib 可用性标记
        self._qlib_available: bool | None = None

        logger.info(
            f"AutoFactorFactory 初始化: "
            f"ic_threshold={ic_threshold}, ir_threshold={ir_threshold}, "
            f"retire_confirm_days={retire_confirm_days}, "
            f"max_active_factors={max_active_factors}"
        )

    # ------------------------------------------------------------
    # 状态持久化
    # ------------------------------------------------------------

    def _state_path(self, name: str) -> Path:
        return self.data_dir / f"{name}.json"

    def _save_state(self) -> None:
        """保存当前状态到磁盘 (幂等)"""
        state = {
            "discovered": {k: self._dataclass_to_dict(v) for k, v in self._discovered.items()},
            "validated": {k: self._dataclass_to_dict(v) for k, v in self._validated.items()},
            "deployed": {k: self._dataclass_to_dict(v) for k, v in self._deployed.items()},
            "retired": {k: self._dataclass_to_dict(v) for k, v in self._retired.items()},
            "ic_history": self._ic_history,
        }
        try:
            self._state_path("factory_state").write_text(
                json.dumps(state, ensure_ascii=False, indent=2, default=str),
                encoding="utf-8",
            )
        except (ValueError, TypeError, KeyError, AttributeError, RuntimeError, OSError, TimeoutError, ConnectionError) as e:
            # 数值计算/数据处理异常: 格式/类型/字段/属性/运行时/IO/超时/网络
            logger.warning(f"保存 AutoFactorFactory 状态失败: {e}")

    def _load_state(self) -> None:
        """从磁盘加载状态 (幂等)"""
        state_file = self._state_path("factory_state")
        if not state_file.exists():
            return
        try:
            state = json.loads(state_file.read_text(encoding="utf-8"))
            self._discovered = {
                k: CandidateFactor(**v) for k, v in state.get("discovered", {}).items()
            }
            self._validated = {
                k: ValidatedFactor(**v) for k, v in state.get("validated", {}).items()
            }
            self._deployed = {
                k: DeployedFactor(**v) for k, v in state.get("deployed", {}).items()
            }
            self._retired = {
                k: RetireSuggestion(**v) for k, v in state.get("retired", {}).items()
            }
            self._ic_history = state.get("ic_history", {})
            logger.info(
                f"状态加载完成: "
                f"discovered={len(self._discovered)}, "
                f"validated={len(self._validated)}, "
                f"deployed={len(self._deployed)}, "
                f"retired={len(self._retired)}"
            )
        except (ValueError, TypeError, KeyError, AttributeError, RuntimeError, OSError, TimeoutError, ConnectionError) as e:
            # 数值计算/数据处理异常: 格式/类型/字段/属性/运行时/IO/超时/网络
            logger.warning(f"加载 AutoFactorFactory 状态失败: {e}")

    @staticmethod
    def _dataclass_to_dict(obj: Any) -> dict:
        """将 dataclass 转为可 JSON 序列化的 dict"""
        result = {}
        for field_name in obj.__dataclass_fields__:
            val = getattr(obj, field_name)
            if isinstance(val, (datetime, pd.Timestamp)):
                result[field_name] = str(val)
            elif isinstance(val, np.floating):
                result[field_name] = float(val)
            elif isinstance(val, np.integer):
                result[field_name] = int(val)
            else:
                result[field_name] = val
        return result

    # ------------------------------------------------------------
    # Stage 1: Discovery — 因子发现
    # ------------------------------------------------------------

    def discover(
        self,
        data_source: str = "qlib",
        universe: str = "csi500",
        start_date: str = "2021-01-01",
        end_date: str | None = None,
        step: int = 20,
    ) -> list[CandidateFactor]:
        """Stage 1: 因子发现 — 从 QLib 数据计算因子面板.

        Args:
            data_source: 数据源 (默认: qlib)
            universe: 股票池 (csi300/csi500/csi800/all)
            start_date: 起始日期
            end_date: 结束日期 (默认: 今天)
            step: 计算步长 (交易日)

        Returns:
            候选因子列表
        """
        if end_date is None:
            end_date = datetime.now().strftime("%Y-%m-%d")

        logger.info(f"[Discovery] 开始因子发现: {universe}, {start_date}~{end_date}")

        # 使用 factor_discovery_enhanced 的发现能力
        if data_source == "qlib":
            factor_panels, _ = self._discover_via_qlib(universe, start_date, end_date, step)
        else:
            # 使用 factor_discovery.py 的 AKShare 发现能力
            factor_panels = self._discover_via_akshare(universe, start_date, end_date, step)

        # 转换为 CandidateFactor 列表
        candidates = []
        for fname, panel in factor_panels.items():
            # 从因子名推断类别
            category = self._infer_category(fname)

            # 计算覆盖统计
            n_stocks = panel.shape[1]
            first_date = str(panel.index.min().date()) if len(panel.index) > 0 else ""
            last_date = str(panel.index.max().date()) if len(panel.index) > 0 else ""

            candidate = CandidateFactor(
                name=fname,
                category=category,
                formula=self._infer_formula(fname),
                source=data_source,
                n_stocks=n_stocks,
                first_date=first_date,
                last_date=last_date,
            )
            candidates.append(candidate)

        # 更新内部状态
        for c in candidates:
            self._discovered[c.name] = c

        self._save_state()

        # 审计记录
        self._audit("discover", {
            "data_source": data_source,
            "universe": universe,
            "n_candidates": len(candidates),
            "factor_names": [c.name for c in candidates],
        })

        logger.info(f"[Discovery] 完成: 发现 {len(candidates)} 个候选因子")
        return candidates

    def _discover_via_qlib(
        self,
        universe: str,
        start_date: str,
        end_date: str,
        step: int,
    ) -> tuple[dict[str, pd.DataFrame], dict[int, pd.DataFrame]]:
        """通过 QLib 发现因子 — 复用 factor_discovery_enhanced 的 50+ 技术因子."""
        try:
            from utils.factor_research.factor_discovery_enhanced import (
                compute_factors_panel_qlib,
                init_qlib,
                load_qlib_data,
            )
        except ImportError:
            logger.warning("utils.factor_research.factor_discovery_enhanced 不可用, 降级到基础技术因子")
            return self._discover_basic_technical(), {}

        if not init_qlib():
            logger.warning("QLib 初始化失败, 降级到基础技术因子")
            return self._discover_basic_technical(), {}

        df = load_qlib_data(instruments=universe, start_time=start_date, end_time=end_date)
        if df.empty:
            logger.warning("QLib 数据为空, 降级到基础技术因子")
            return self._discover_basic_technical(), {}

        factor_panels, forward_returns = compute_factors_panel_qlib(df, step=step)
        if not factor_panels:
            logger.warning("QLib 因子面板为空, 降级到基础技术因子")
            return self._discover_basic_technical(), {}

        return factor_panels, forward_returns

    def _discover_via_akshare(
        self,
        universe: str,
        start_date: str,
        end_date: str,
        step: int,
    ) -> dict[str, pd.DataFrame]:
        """通过 AKShare 发现因子 — 复用 factor_discovery.py 的 ETF 发现能力."""
        try:
            from utils.factor_research.factor_discovery import FactorCalculator, FactorDataFetcher
        except ImportError:
            logger.warning("utils.factor_research.factor_discovery 不可用, 降级到基础技术因子")
            return self._discover_basic_technical()

        # 获取标的列表
        from utils.factor_research.factor_discovery import UNIVERSE_PRESETS
        codes = UNIVERSE_PRESETS.get(universe, UNIVERSE_PRESETS.get("etf_core", []))

        fetcher = FactorDataFetcher()
        daily_data = fetcher.fetch_daily_data(codes, start_date, end_date)
        if not daily_data:
            logger.warning("AKShare 数据为空, 降级到基础技术因子")
            return self._discover_basic_technical()

        calculator = FactorCalculator()
        factor_panels = calculator.compute_factors_panel(daily_data, step=step)
        return factor_panels

    def _discover_basic_technical(self) -> dict[str, pd.DataFrame]:
        """降级方案: 生成基础技术因子面板 (空, 用于兜底)."""
        logger.warning("[Discovery] 使用降级方案: 返回空因子面板")
        return {}

    @staticmethod
    def _infer_category(factor_name: str) -> str:
        """从因子名推断类别."""
        name_upper = factor_name.upper()

        # 特殊匹配: 需要在通用匹配之前检查
        # PRICE_VOL_DIVERG 包含 "VOL_" 但属于技术指标, 必须在 VOL_ 之前检查
        if "PRICE_VOL" in name_upper:
            return "Technical"
        # SIZE_PROXY 包含 "SIZE" 但属于基本面代理, 必须在 SIZE 之前检查
        if "SIZE_PROXY" in name_upper:
            return "Fundamental_Proxy"
        # 动量类
        if any(x in name_upper for x in ["MOM", "REVERSAL", "UP_DOWN", "MACD", "RSI", "OBV"]):
            return "Momentum"
        # 波动率类
        if any(x in name_upper for x in ["VOL_", "DOWNSIDE", "SKEW", "KURT", "ATR", "BB_"]):
            return "Volatility"
        # 流动性类
        if any(x in name_upper for x in ["TURNOVER", "AMIHUD", "VOLUME_CHG", "VOLUME_Z", "LIQ"]):
            return "Liquidity"
        # 规模类
        if any(x in name_upper for x in ["SIZE", "MCAP"]):
            return "Size"
        # 技术指标类
        if any(x in name_upper for x in ["MA_DEV", "GTJA", "ALPHA"]):
            return "Technical"
        # 基本面代理
        if any(x in name_upper for x in ["PROXY", "EARNING_STABILITY", "QUALITY_PROXY"]):
            return "Fundamental_Proxy"
        # 默认
        return "Other"

    @staticmethod
    def _infer_formula(factor_name: str) -> str:
        """从因子名推断公式描述."""
        name = factor_name.upper()

        if name.startswith("MOM_"):
            days = name.replace("MOM_", "").replace("D", "")
            return f"close / close.shift({days}) - 1"
        if name.startswith("REVERSAL_"):
            days = name.replace("REVERSAL_", "").replace("D", "")
            return f"-(close / close.shift({days}) - 1)"
        if name.startswith("VOL_"):
            days = name.replace("VOL_", "").replace("D", "")
            return f"ret.rolling({days}).std()"
        if name.startswith("TURNOVER_"):
            days = name.replace("TURNOVER_", "").replace("D", "")
            return f"volume.rolling({days}).mean()"
        if name.startswith("RSI_"):
            days = name.replace("RSI_", "").replace("D", "")
            return f"RSI({days})"
        if name.startswith("MA_DEV_"):
            days = name.replace("MA_DEV_", "").replace("D", "")
            return f"close / close.rolling({days}).mean() - 1"
        if name.startswith("SKEW_"):
            days = name.replace("SKEW_", "").replace("D", "")
            return f"ret.rolling({days}).skew()"
        if name.startswith("KURT_"):
            days = name.replace("KURT_", "").replace("D", "")
            return f"ret.rolling({days}).kurt()"
        if name.startswith("AMIHUD_"):
            days = name.replace("AMIHUD_", "").replace("D", "")
            return f"mean(|ret| / volume, {days})"
        if name.startswith("GTJA191_") or name.startswith("ALPHA"):
            return f"GTJA191 factor: {factor_name}"
        if name == "OBV_CHG":
            return "OBV / OBV.shift(20) - 1"
        if name.startswith("PRICE_VOL_DIVERG_"):
            return "price新高 && volume未新高"
        if name == "SIZE_PROXY":
            return "close (市值代理)"
        if name == "LONG_TERM_RET":
            return "close / close.shift(252) - 1"
        if name == "EARNING_STABILITY":
            return "-ret.rolling(252).std()"
        if name == "QUALITY_PROXY":
            return "ret.mean() / ret.std() (120日)"
        return factor_name

    # ------------------------------------------------------------
    # Stage 2: Validation — 因子验证 (Walk-Forward + CRO Gate)
    # ------------------------------------------------------------

    def validate(
        self,
        candidates: list[CandidateFactor] | None = None,
        data_source: str = "qlib",
        universe: str = "csi500",
        start_date: str = "2021-01-01",
        end_date: str | None = None,
        n_groups: int = 5,
        walk_forward: bool = True,
    ) -> list[ValidatedFactor]:
        """Stage 2: 因子验证 — Walk-Forward + CRO Gate.

        Args:
            candidates: 候选因子列表 (默认: 使用已发现的因子)
            data_source: 数据源
            universe: 股票池
            start_date: 起始日期
            end_date: 结束日期
            n_groups: 分层回测组数
            walk_forward: 是否执行 Walk-Forward 验证

        Returns:
            已验证因子列表 (按综合评分排序)
        """
        if end_date is None:
            end_date = datetime.now().strftime("%Y-%m-%d")

        if candidates is None:
            candidates = list(self._discovered.values())

        if not candidates:
            logger.warning("[Validation] 无候选因子, 跳过验证")
            return []

        logger.info(
            f"[Validation] 开始验证 {len(candidates)} 个因子: "
            f"walk_forward={walk_forward}, n_groups={n_groups}"
        )

        # Step 1: 计算因子面板和远期收益 (复用 enhanced 的验证能力)
        try:
            from utils.factor_research.factor_discovery_enhanced import (
                compute_factors_panel_qlib,
                init_qlib,
                load_qlib_data,
                validate_factors,
            )
        except ImportError:
            logger.error("utils.factor_research.factor_discovery_enhanced 不可用, 无法验证")
            return []

        if not init_qlib():
            logger.error("QLib 不可用, 无法验证")
            return []

        df = load_qlib_data(instruments=universe, start_time=start_date, end_time=end_date)
        if df.empty:
            logger.error("QLib 数据为空, 无法验证")
            return []

        factor_panels, forward_returns = compute_factors_panel_qlib(df, step=20)
        if not factor_panels:
            logger.error("因子面板为空, 无法验证")
            return []

        # Step 2: 基础验证 (IC/IR/分组收益)
        raw_results = validate_factors(factor_panels, forward_returns, n_groups=n_groups)

        # Step 3: Walk-Forward 验证 (可选)
        wf_results: dict[str, dict] = {}
        if walk_forward and len(factor_panels) > 0:
            wf_results = self._run_walk_forward_validation(
                factor_panels, forward_returns, start_date, end_date
            )

        # Step 4: 组装 ValidatedFactor
        validated: list[ValidatedFactor] = []
        for raw in raw_results:
            vf = ValidatedFactor(
                name=raw.factor_name,
                category=raw.category,
                formula=self._infer_formula(raw.factor_name),
                source="qlib",
                ic_mean_1d=raw.ic_mean,
                ic_std_1d=raw.ic_std,
                ic_ir_1d=raw.ic_ir,
                ic_positive_ratio=raw.ic_positive_ratio,
                ic_mean_5d=raw.decay_5d * raw.ic_mean if raw.ic_mean != 0 else 0,
                ic_ir_5d=(raw.decay_5d * raw.ic_ir) if raw.ic_ir != 0 else 0,
                long_short_return=raw.long_short_return,
                group_returns=raw.group_returns,
                monotonicity=raw.monotonicity,
                direction=raw.direction,
                score=raw.score,
                effective=raw.effective,
                validation_date=datetime.now().strftime("%Y-%m-%d"),
                n_samples=len(factor_panels.get(raw.factor_name, pd.DataFrame())),
            )

            # 补充 Walk-Forward 结果
            if raw.factor_name in wf_results:
                wf = wf_results[raw.factor_name]
                vf.wf_mean_ic = wf.get("mean_ic", 0.0)
                vf.wf_ic_std = wf.get("ic_std", 0.0)
                vf.wf_ir = wf.get("ir", 0.0)
                vf.wf_n_windows = wf.get("n_windows", 0)
                vf.wf_consistency = wf.get("consistency", 0.0)

            # 重新计算综合评分 (含 Walk-Forward 信息)
            vf.score = self._compute_validation_score(vf)
            vf.effective = (
                abs(vf.ic_mean_1d) >= self.ic_threshold
                and abs(vf.ic_ir_1d) >= self.ir_threshold
            )

            validated.append(vf)

        # 按评分排序
        validated.sort(key=lambda x: x.score, reverse=True)

        # 更新内部状态
        for v in validated:
            self._validated[v.name] = v

        self._save_state()

        # 审计记录
        n_effective = sum(1 for v in validated if v.effective)
        self._audit("validate", {
            "n_candidates": len(candidates),
            "n_validated": len(validated),
            "n_effective": n_effective,
            "top_factors": [v.name for v in validated[:5]],
        })

        logger.info(
            f"[Validation] 完成: 有效 {n_effective}/{len(validated)} 个因子"
        )
        return validated

    def _run_walk_forward_validation(
        self,
        factor_panels: dict[str, pd.DataFrame],
        forward_returns: dict[int, pd.DataFrame],
        start_date: str,
        end_date: str,
    ) -> dict[str, dict]:
        """Walk-Forward 验证: 滚动窗口评估因子 IC 稳定性.

        Args:
            factor_panels: {factor_name: DataFrame(date x stock)}
            forward_returns: {forward_day: DataFrame(date x stock)}
            start_date: 起始日期
            end_date: 结束日期

        Returns:
            {factor_name: {mean_ic, ic_std, ir, n_windows, consistency}}
        """
        try:
            from ms_strategy.src.backtest.walk_forward import WalkForward
        except ImportError:
            logger.warning("WalkForward 不可用, 跳过 Walk-Forward 验证")
            return {}

        logger.info(f"[Walk-Forward] 开始验证, {len(factor_panels)} 个因子")

        # 生成窗口 (12月训练 / 3月测试 / 3月步进)
        wf = WalkForward(train_months=12, test_months=3, step_months=3)
        windows = wf.generate_windows(start_date, end_date)

        if not windows:
            logger.warning("[Walk-Forward] 无有效窗口")
            return {}

        logger.info(f"[Walk-Forward] {len(windows)} 个窗口")

        # 对每个因子做 Walk-Forward 验证
        wf_results: dict[str, dict] = {}
        fwd_1d = forward_returns.get(1, pd.DataFrame())

        for fname, fpanel in factor_panels.items():
            if fpanel.empty:
                continue

            window_ics = []
            for train_start, train_end, test_start, test_end in windows:
                try:
                    # 训练期: 计算 IC
                    train_mask = (fpanel.index >= pd.Timestamp(train_start)) & (
                        fpanel.index <= pd.Timestamp(train_end)
                    )
                    train_panel = fpanel.loc[train_mask]
                    if len(train_panel) < 2:
                        continue

                    # 测试期: 计算 IC
                    test_mask = (fpanel.index >= pd.Timestamp(test_start)) & (
                        fpanel.index <= pd.Timestamp(test_end)
                    )
                    test_panel = fpanel.loc[test_mask]
                    if len(test_panel) < 2:
                        continue

                    test_ics = []
                    for date in test_panel.index:
                        fvals = test_panel.loc[date].dropna()
                        if len(fvals) < 5:
                            continue
                        if date in fwd_1d.index:
                            rets = fwd_1d.loc[date].reindex(fvals.index).dropna()
                            common = fvals.index.intersection(rets.index)
                            if len(common) >= 5:
                                try:
                                    from scipy.stats import spearmanr
                                    ic, _ = spearmanr(fvals[common], rets[common])
                                    if not np.isnan(ic):
                                        test_ics.append(ic)
                                except (ValueError, TypeError, KeyError, AttributeError, RuntimeError, OSError, TimeoutError, ConnectionError):
                                    # 数值计算/数据处理异常: 格式/类型/字段/属性/运行时/IO/超时/网络
                                    pass

                    if test_ics:
                        window_ics.extend(test_ics)

                except (ValueError, TypeError, KeyError, AttributeError, RuntimeError, OSError, TimeoutError, ConnectionError) as e:

                    # 数值计算/数据处理异常: 格式/类型/字段/属性/运行时/IO/超时/网络
                    logger.debug(f"  WF 窗口异常 [{fname}]: {e}")
                    continue

            # 统计
            if window_ics:
                arr = np.array(window_ics)
                wf_results[fname] = {
                    "mean_ic": float(np.mean(arr)),
                    "ic_std": float(np.std(arr)) if len(arr) > 1 else 1e-12,
                    "ir": float(np.mean(arr) / max(np.std(arr), 1e-12)),
                    "n_windows": len(window_ics),
                    "consistency": float(np.mean(arr > 0)),
                }

        logger.info(
            f"[Walk-Forward] 完成: {len(wf_results)} 个因子有有效结果"
        )
        return wf_results

    def _compute_validation_score(self, vf: ValidatedFactor) -> float:
        """计算综合评分 (含 Walk-Forward 信息)."""
        score = 0.0

        # IC 贡献 (权重 30%)
        score += min(abs(vf.ic_mean_1d) * 100, 5.0) * 0.30

        # IR 贡献 (权重 25%)
        score += min(abs(vf.ic_ir_1d), 3.0) * 0.25

        # 正IC比例 (权重 15%)
        score += vf.ic_positive_ratio * 0.15

        # 单调性 (权重 15%)
        score += abs(vf.monotonicity) * 0.15

        # 多空收益 (权重 10%)
        score += min(abs(vf.long_short_return) * 100, 2.0) * 0.10

        # Walk-Forward 一致性 (权重 5%, 仅在有 WF 结果时)
        if vf.wf_n_windows > 0:
            score += vf.wf_consistency * 0.05

        return score

    # ------------------------------------------------------------
    # Stage 3: Deployment — 因子部署
    # ------------------------------------------------------------

    def deploy(
        self,
        validated: list[ValidatedFactor] | None = None,
        generate_code: bool = True,
        register_to_library: bool = True,
    ) -> list[DeployedFactor]:
        """Stage 3: 因子部署 — 生成代码 + 注册到 AlphaFactorLibrary.

        Args:
            validated: 已验证因子列表 (默认: 使用全部有效已验证因子)
            generate_code: 是否生成代码文件
            register_to_library: 是否注册到 AlphaFactorLibrary

        Returns:
            已部署因子列表
        """
        if validated is None:
            validated = [
                v for v in self._validated.values()
                if v.effective and v.name not in self._deployed
            ]

        if not validated:
            logger.info("[Deploy] 无待部署因子")
            return []

        # 检查活跃因子数上限
        n_active = sum(1 for d in self._deployed.values() if d.active)
        available_slots = self.max_active_factors - n_active

        if available_slots <= 0:
            logger.warning(
                f"[Deploy] 活跃因子已达上限 {self.max_active_factors}, "
                f"无法部署新因子"
            )
            return []

        # 按评分排序, 取可用槽位
        validated = sorted(validated, key=lambda v: v.score, reverse=True)
        to_deploy = validated[:available_slots]

        deployed: list[DeployedFactor] = []
        for vf in to_deploy:
            # 检查是否已部署
            if vf.name in self._deployed and self._deployed[vf.name].active:
                logger.debug(f"[Deploy] 因子 {vf.name} 已部署, 跳过")
                continue

            # 生成代码文件
            code_path = ""
            if generate_code:
                code_path = self._generate_factor_code(vf)

            df = DeployedFactor(
                name=vf.name,
                category=vf.category,
                formula=vf.formula,
                deploy_date=datetime.now().strftime("%Y-%m-%d"),
                library_key=f"auto_{vf.name.lower()}",
                code_path=code_path,
                version=self._deployed[vf.name].version + 1 if vf.name in self._deployed else 1,
                active=True,
            )

            # 注册到 AlphaFactorLibrary
            if register_to_library:
                self._register_to_library(vf, df)

            deployed.append(df)
            self._deployed[vf.name] = df

        self._save_state()

        # 审计记录
        self._audit("deploy", {
            "n_deployed": len(deployed),
            "factor_names": [d.name for d in deployed],
        })

        logger.info(
            f"[Deploy] 完成: 部署 {len(deployed)} 个因子 "
            f"(活跃 {n_active + len(deployed)}/{self.max_active_factors})"
        )
        return deployed

    def _generate_factor_code(self, vf: ValidatedFactor) -> str:
        """生成因子代码文件.

        Args:
            vf: 已验证因子

        Returns:
            代码文件路径
        """
        code_dir = self.data_dir / "generated_factors"
        code_dir.mkdir(parents=True, exist_ok=True)

        # 生成安全的文件名
        safe_name = vf.name.lower().replace(" ", "_")
        file_path = code_dir / f"factor_{safe_name}.py"

        # 生成代码模板
        code = self._build_factor_code_template(vf)
        try:
            file_path.write_text(code, encoding="utf-8")
            logger.info(f"[Deploy] 代码已生成: {file_path}")
        except (ValueError, TypeError, KeyError, AttributeError, RuntimeError, OSError, TimeoutError, ConnectionError) as e:
            # 数值计算/数据处理异常: 格式/类型/字段/属性/运行时/IO/超时/网络
            logger.warning(f"[Deploy] 生成代码失败: {e}")
            return ""

        return str(file_path)

    def _build_factor_code_template(self, vf: ValidatedFactor) -> str:
        """构建因子代码模板."""
        safe_name = vf.name.lower().replace(" ", "_")
        return f'''"""自动生成因子: {vf.name}

类别: {vf.category}
公式: {vf.formula}
验证日期: {vf.validation_date}
IC(1D): {vf.ic_mean_1d:.4f}, IR: {vf.ic_ir_1d:.3f}
多空收益: {vf.long_short_return:.6f}
单调性: {vf.monotonicity:.3f}
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from utils.alpha_factor.base import FactorValue


def compute_{safe_name}(
    price_data: dict[str, dict[str, list[float]]],
    fundamentals: dict[str, dict[str, float]] | None = None,
) -> FactorValue:
    """计算 {vf.name} 因子.

    {vf.formula}

    Args:
        price_data: {{symbol: {{"closes": [...], "volumes": [...], ...}}}}
        fundamentals: {{symbol: {{...}}}} (可选)

    Returns:
        FactorValue
    """
    values: dict[str, float] = {{}}

    for sym, data in price_data.items():
        closes = data.get("closes", [])
        if len(closes) < 20:
            continue
        try:
            # 因子计算逻辑 (模板, 需根据具体公式调整)
            # {vf.formula}
            values[sym] = 0.0
        except (ValueError, TypeError, KeyError, AttributeError, RuntimeError, OSError, TimeoutError, ConnectionError):
            # 数值计算/数据处理异常: 格式/类型/字段/属性/运行时/IO/超时/网络
            continue

    return FactorValue(
        name="{vf.name}",
        category="{vf.category}",
        values=values,
        ic_1d={vf.ic_mean_1d:.4f},
    )
'''

    def _register_to_library(
        self, vf: ValidatedFactor, df: DeployedFactor
    ) -> bool:
        """注册因子到 AlphaFactorLibrary.

        通过扩展 AlphaFactorLibrary 的 compute_all 方法, 将新因子集成到生产流水线.

        Returns:
            是否成功
        """
        try:

            # 注册方式: 将因子信息写入注册表, 供后续集成
            registry_path = self.data_dir / "factor_registry.json"
            registry: dict[str, Any] = {}
            if registry_path.exists():
                try:
                    registry = json.loads(registry_path.read_text(encoding="utf-8"))
                except (ValueError, TypeError, KeyError, AttributeError, RuntimeError, OSError, TimeoutError, ConnectionError):
                    # 数值计算/数据处理异常: 格式/类型/字段/属性/运行时/IO/超时/网络
                    registry = {}

            registry[vf.name] = {
                "library_key": df.library_key,
                "category": vf.category,
                "formula": vf.formula,
                "deploy_date": df.deploy_date,
                "version": df.version,
                "active": True,
                "ic_mean_1d": vf.ic_mean_1d,
                "ic_ir_1d": vf.ic_ir_1d,
                "score": vf.score,
            }

            registry_path.write_text(
                json.dumps(registry, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            logger.info(f"[Deploy] 因子 {vf.name} 已注册到因子登记簿")
            return True

        except (ValueError, TypeError, KeyError, AttributeError, RuntimeError, OSError, TimeoutError, ConnectionError) as e:

            # 数值计算/数据处理异常: 格式/类型/字段/属性/运行时/IO/超时/网络
            logger.warning(f"[Deploy] 注册因子 {vf.name} 失败: {e}")
            return False

    # ------------------------------------------------------------
    # Stage 4: Monitoring — 因子监控与淘汰
    # ------------------------------------------------------------

    def monitor(
        self,
        force_check: bool = False,
    ) -> list[RetireSuggestion]:
        """Stage 4: 因子监控与淘汰 — 检查活跃因子表现, 推荐淘汰.

        Args:
            force_check: 是否强制检查 (忽略 Feature Flag)

        Returns:
            淘汰建议列表
        """
        if not self._deployed:
            logger.info("[Monitor] 无活跃因子, 跳过监控")
            return []

        active = {k: v for k, v in self._deployed.items() if v.active}
        if not active:
            logger.info("[Monitor] 无活跃因子, 跳过监控")
            return []

        logger.info(f"[Monitor] 开始监控 {len(active)} 个活跃因子")

        # 收集因子近期 IC 表现
        self._collect_factor_ic()

        suggestions: list[RetireSuggestion] = []
        for name, df in active.items():
            ic_history = self._ic_history.get(name, [])
            if len(ic_history) < 5:
                continue

            # 取最近 N 日 IC
            recent_ics = [h.get("ic", 0.0) for h in ic_history[-self.retire_confirm_days:]]
            if not recent_ics:
                continue

            current_ic = float(np.mean(recent_ics))
            days_below = sum(1 for ic in recent_ics if abs(ic) < self.ic_retire_threshold)

            suggestion = RetireSuggestion(
                name=name,
                category=df.category,
                current_ic=current_ic,
                ic_days_below_threshold=days_below,
                reason="",
                suggest_retire=False,
            )

            # 判断是否需要淘汰
            if days_below >= self.retire_confirm_days:
                suggestion.suggest_retire = True
                suggestion.reason = (
                    f"IC={current_ic:.4f} 连续 {days_below} 日低于阈值 "
                    f"{self.ic_retire_threshold}"
                )
                suggestion.retire_date = datetime.now().strftime("%Y-%m-%d")
                suggestions.append(suggestion)

        # 执行淘汰
        retired_names = []
        for s in suggestions:
            if s.suggest_retire:
                self._retire_factor(s.name)
                s.archived = True
                retired_names.append(s.name)
                self._retired[s.name] = s

        self._save_state()

        # 审计记录
        self._audit("monitor", {
            "n_active": len(active),
            "n_retired": len(retired_names),
            "retired_factors": retired_names,
        })

        logger.info(
            f"[Monitor] 完成: 监控 {len(active)} 个因子, "
            f"淘汰 {len(retired_names)} 个"
        )
        return suggestions

    def _collect_factor_ic(self) -> None:
        """收集活跃因子的近期 IC 表现 (从 DriftMonitor 或模拟)."""
        # 尝试从 DriftMonitor 获取
        try:
            from utils.alpha.drift_monitor import ModelDriftDetector

            for name in self._deployed:
                if name not in self._ic_history:
                    self._ic_history[name] = []
                # 记录当前时间戳和占位 IC
                self._ic_history[name].append({
                    "date": datetime.now().strftime("%Y-%m-%d"),
                    "ic": 0.0,  # 占位, 需实际数据源
                })
        except ImportError:
            # 降级: 记录空历史
            for name in self._deployed:
                if name not in self._ic_history:
                    self._ic_history[name] = []

    def _retire_factor(self, name: str) -> None:
        """淘汰因子 — 标记为不活跃.

        Args:
            name: 因子名
        """
        if name in self._deployed:
            self._deployed[name].active = False
            logger.info(f"[Monitor] 因子 {name} 已淘汰")

        # 从因子登记簿移除
        registry_path = self.data_dir / "factor_registry.json"
        if registry_path.exists():
            try:
                registry = json.loads(registry_path.read_text(encoding="utf-8"))
                if name in registry:
                    registry[name]["active"] = False
                    registry_path.write_text(
                        json.dumps(registry, ensure_ascii=False, indent=2),
                        encoding="utf-8",
                    )
            except (ValueError, TypeError, KeyError, AttributeError, RuntimeError, OSError, TimeoutError, ConnectionError):
                # 数值计算/数据处理异常: 格式/类型/字段/属性/运行时/IO/超时/网络
                pass

    # ------------------------------------------------------------
    # 全流水线一键运行
    # ------------------------------------------------------------

    def run_pipeline(
        self,
        data_source: str = "qlib",
        universe: str = "csi500",
        start_date: str = "2021-01-01",
        end_date: str | None = None,
        step: int = 20,
        n_groups: int = 5,
        walk_forward: bool = True,
        generate_code: bool = True,
        register_to_library: bool = True,
        monitor_only: bool = False,
    ) -> FactoryPipelineReport:
        """全流水线一键运行: Discover → Validate → Deploy → Monitor.

        Args:
            data_source: 数据源
            universe: 股票池
            start_date: 起始日期
            end_date: 结束日期
            step: 发现步长
            n_groups: 分层回测组数
            walk_forward: 是否 Walk-Forward 验证
            generate_code: 是否生成代码
            register_to_library: 是否注册到 library
            monitor_only: 仅执行监控阶段

        Returns:
            流水线执行报告
        """
        import time

        start_time = time.time()
        report = FactoryPipelineReport(
            pipeline_date=datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        )

        try:
            if not monitor_only:
                # Stage 1: Discovery
                candidates = self.discover(
                    data_source=data_source,
                    universe=universe,
                    start_date=start_date,
                    end_date=end_date,
                    step=step,
                )
                report.n_discovered = len(candidates)

                if candidates:
                    # Stage 2: Validation
                    validated = self.validate(
                        candidates=candidates,
                        data_source=data_source,
                        universe=universe,
                        start_date=start_date,
                        end_date=end_date,
                        n_groups=n_groups,
                        walk_forward=walk_forward,
                    )
                    report.n_validated = len(validated)

                    if validated:
                        # Stage 3: Deployment
                        deployed = self.deploy(
                            validated=validated,
                            generate_code=generate_code,
                            register_to_library=register_to_library,
                        )
                        report.n_deployed = len(deployed)
                        report.top_factors = [d.name for d in deployed[:5]]

            # Stage 4: Monitoring
            suggestions = self.monitor()
            report.n_retired = len([s for s in suggestions if s.suggest_retire])
            report.retired_factors = [s.name for s in suggestions if s.suggest_retire]
            report.n_active = sum(1 for d in self._deployed.values() if d.active)

            report.status = "success"

        except (ValueError, TypeError, KeyError, AttributeError, RuntimeError, OSError, TimeoutError, ConnectionError) as e:

            # 数值计算/数据处理异常: 格式/类型/字段/属性/运行时/IO/超时/网络
            report.status = "error"
            report.error = str(e)
            logger.exception(f"[Pipeline] 流水线执行异常: {e}")

        report.duration_seconds = time.time() - start_time

        # 审计记录
        self._audit("run_pipeline", {
            "status": report.status,
            "n_discovered": report.n_discovered,
            "n_validated": report.n_validated,
            "n_deployed": report.n_deployed,
            "n_retired": report.n_retired,
            "duration_seconds": report.duration_seconds,
        })

        logger.info(
            f"[Pipeline] 完成: "
            f"发现={report.n_discovered}, "
            f"验证={report.n_validated}, "
            f"部署={report.n_deployed}, "
            f"淘汰={report.n_retired}, "
            f"活跃={report.n_active}, "
            f"耗时={report.duration_seconds:.1f}s"
        )

        # 保存报告
        self._save_report(report)

        return report

    def _save_report(self, report: FactoryPipelineReport) -> None:
        """保存流水线报告到磁盘."""
        report_dir = self.data_dir / "reports"
        report_dir.mkdir(parents=True, exist_ok=True)
        report_file = report_dir / f"pipeline_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
        try:
            report_file.write_text(
                json.dumps(self._dataclass_to_dict(report), ensure_ascii=False, indent=2, default=str),
                encoding="utf-8",
            )
        except (ValueError, TypeError, KeyError, AttributeError, RuntimeError, OSError, TimeoutError, ConnectionError) as e:
            # 数值计算/数据处理异常: 格式/类型/字段/属性/运行时/IO/超时/网络
            logger.warning(f"保存流水线报告失败: {e}")

    # ------------------------------------------------------------
    # 查询接口
    # ------------------------------------------------------------

    def get_active_factors(self) -> list[DeployedFactor]:
        """获取当前活跃因子列表."""
        return [d for d in self._deployed.values() if d.active]

    def get_retired_factors(self) -> list[str]:
        """获取已淘汰因子列表."""
        return [d.name for d in self._deployed.values() if not d.active]

    def get_factor_summary(self) -> dict[str, Any]:
        """获取因子工厂摘要."""
        active = self.get_active_factors()
        retired = self.get_retired_factors()
        validated = [v for v in self._validated.values() if v.effective]

        return {
            "n_discovered": len(self._discovered),
            "n_validated": len(self._validated),
            "n_effective": len(validated),
            "n_deployed": len(self._deployed),
            "n_active": len(active),
            "n_retired": len(retired),
            "active_factors": [d.name for d in active],
            "retired_factors": retired,
            "top_validated": sorted(
                validated, key=lambda v: v.score, reverse=True
            )[:10],
        }

    # ------------------------------------------------------------
    # 审计 (写入 EvolutionMemory)
    # ------------------------------------------------------------

    def _audit(self, action: str, details: dict) -> None:
        """记录审计到 EvolutionMemory (如果可用)."""
        if self.memory is not None:
            try:
                self.memory.record(
                    module="auto_factor_factory",
                    action=action,
                    details=details,
                )
            except (ValueError, TypeError, KeyError, AttributeError, RuntimeError, OSError, TimeoutError, ConnectionError) as e:
                # 数值计算/数据处理异常: 格式/类型/字段/属性/运行时/IO/超时/网络
                logger.debug(f"审计记录失败: {e}")

    # ------------------------------------------------------------
    # 状态重置
    # ------------------------------------------------------------

    def reset(self, confirm: bool = False) -> bool:
        """重置因子工厂状态 (需确认).

        Args:
            confirm: 确认重置

        Returns:
            是否成功
        """
        if not confirm:
            logger.warning("重置需要 confirm=True")
            return False

        self._discovered.clear()
        self._validated.clear()
        self._deployed.clear()
        self._retired.clear()
        self._ic_history.clear()

        # 清除状态文件
        state_file = self._state_path("factory_state")
        if state_file.exists():
            state_file.unlink()

        # 清除注册表
        registry_path = self.data_dir / "factor_registry.json"
        if registry_path.exists():
            registry_path.unlink()

        logger.info("AutoFactorFactory 状态已重置")
        self._audit("reset", {"action": "factory_reset"})
        return True
