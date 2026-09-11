#!/usr/bin/env python
"""
validate_configs.py — config/*.yaml 轻量 schema 校验 (ROADMAP AUTO-8).

背景 (ROADMAP §云端自动开发任务池 AUTO-8):
    config/ 是主业务活跃配置目录 (由 utils/config_manager.py 搜索路径第 3 优先级
    加载), 含 feature_flags / mlops / llm_pricing / lgb_training / 归因与组合等
    异构 YAML。PyYAML 默认 safe_load 遇到重复 key 时**静默保留最后一个** ——
    这是最容易出现的"配置腐化不报错"来源之一; 另需保证文件可解析、顶层为映射、
    且关键顶层区块未被误删。

本脚本提供纯 stdlib + PyYAML 的轻量校验, 供本地与 CI 变更检测使用:

    1. 可解析性: 每个 config/*.yaml 必须是合法 YAML 且顶层为**非空映射**。
    2. 重复 key 检测 (任意嵌套深度): 复用 yaml.compose 的节点树, 不经过
       safe_load 的"后值覆盖前值", 显式捕获会被静默吞掉的重复 key。
    3. 关键顶层区块白名单: 每个已知配置文件要求其**当前已在产**的顶层区块存在
       (防止误删/回归), 详见 REQUIRED_TOP_SECTIONS。
    4. 语义校验 (v9.1 组合专属): portfolio_200w_etf_v91.yaml 额外执行
       check_portfolio_v91(), 把《ETF期权组合诊脉书_20260911》指出的七处硬伤
       固化为**可机读护栏** (跨资产/科创50 降级/预算口径/备兑名义上限/卫星 alpha
       实证状态/期权数据链契约/可观测状态机), 并校验权重与目标自洽、状态隔离。
       硬伤条目以 [硬伤N] 前缀汇报, 便于回归定位。
    5. gnn_factor 子目录仅做可解析性冒烟, 不强制顶层结构。

    退出码:
     0 = 全部通过; 1 = 存在解析错误 / 重复 key / 缺失关键区块 / 语义护栏不通过。

    用法:
     python scripts/validate_configs.py                # 校验 config/*.yaml
     python scripts/validate_configs.py --verbose      # 逐文件输出明细
     python scripts/validate_configs.py --file mlops.yaml   # 仅校验指定文件
     python scripts/validate_configs.py --selftest     # 内建自测 (无需 pytest)


只读、不触碰任何运行配置/资金数据; 缺文件时以当前 git 追踪清单为准 (skipped 不失败),
避免 .gitignore 内本地运行配置 (configs/, 复数) 干扰。
"""
from __future__ import annotations

import argparse
import copy
import sys
import tempfile
from pathlib import Path

import yaml
from yaml.nodes import MappingNode, Node, SequenceNode

ROOT = Path(__file__).resolve().parent.parent
CONFIG_DIR = ROOT / "config"

#: 每个已知配置要求存在的顶层区块 (取自当前在产结构, 防误删回归)。
REQUIRED_TOP_SECTIONS: dict[str, tuple[str, ...]] = {
    "feature_flags.yaml": ("settings", "flags"),
    "mlops.yaml": ("auto_retrain", "retrain_workflow", "drift_monitor", "ab_testing"),
    "llm_pricing.yaml": ("models", "daily_token_budget"),
    "lgb_training.yaml": (
        "lookback_days",
        "min_samples",
        "test_ratio",
        "n_splits",
        "model_quality_threshold",
        "lgb_params",
    ),
    "brinson_attribution.yaml": ("benchmark_sector_weights", "sectors"),
    "factor_attribution.yaml": ("benchmark_factor_exposures", "factors"),
    "portfolio_200w_etf.yaml": ("portfolio", "target"),
    # v9.1「守正」(诊脉书修复版): 除基础结构外还须通过 check_portfolio_v91 语义护栏
    "portfolio_200w_etf_v91.yaml": (
        "portfolio",
        "target",
        "core_holdings",
        "satellite_holdings",
        "ballast_holdings",
        "cash_holdings",
        "options_strategy",
        "option_params",
        "data_chain",
        "regime_state_machine",
        "circuit_breaker",
        "tool_pool",
    ),
}

#: 期权预算口径唯一合法值 (硬伤三): 年度净值计提, 本金划拨口径已废止。
V91_BUDGET_BASIS = "annual_return_provision"

#: 备兑认购行权价地板 = 当年目标收益 + 该附加点数 (派生值, 必须随 target 联动)。
V91_STRIKE_FLOOR_PREMIUM = 0.08

#: 状态机 condition 必须命中的可观测量化关键词 (硬伤七: 禁止不可证伪判断)。
V91_OBSERVABLE_KEYWORDS = (
    "RV", "回撤", "均线", "宽度", "分位", "波动率", "熔断", "IV", "利差",
)


def _collect_duplicate_keys(doc: Node) -> list[str]:
    """递归遍历 yaml.compose 节点树, 返回重复出现的 key (含嵌套层级)。"""
    dups: list[str] = []

    def walk(node: Node) -> None:
        if isinstance(node, MappingNode):
            seen: set[str] = set()
            for key_node, value_node in node.value:
                key = key_node.value
                if key in seen:
                    dups.append(str(key))
                seen.add(key)
                walk(value_node)
        elif isinstance(node, SequenceNode):
            for item in node.value:
                walk(item)

    walk(doc)
    return dups


def _num(value):
    """取出数值 (排除 bool); 非数值返回 None。"""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


def _mapping(value) -> dict:
    """安全取 dict, 非映射返回空 dict。"""
    return value if isinstance(value, dict) else {}


def _items(value) -> list:
    """安全取 list, 非列表返回空列表。"""
    return value if isinstance(value, list) else []


def _weights(entries, key: str) -> float:
    """累加条目列表的指定权重字段 (缺失记 0)。"""
    return sum(_num(_mapping(e).get(key)) or 0.0 for e in entries)


def check_portfolio_v91(data: dict) -> list[str]:
    """portfolio_200w_etf_v91.yaml 语义护栏 — 七处硬伤 + 口径自洽。

    对应《ETF期权组合诊脉书_20260911》:
        [硬伤一] 核心仓清一色 A 股      → 必须存在国债/黄金跨资产压舱
        [硬伤二] 最贵的科创50 当压舱石  → 588000 必须降级出核心且限权
        [硬伤三] 期权预算会计口径错误   → 预算基数必须是年度收益计提 (非本金划拨)
        [硬伤四] 熊市配置自相矛盾       → 禁熊市条款; 卖出认购名义与成本必须封顶
        [硬伤五] 卫星层 alpha 未获实证  → 必须显式禁用 alpha 聚合并标注 unproven
        [硬伤六] 期权数据链未通         → 数据链契约/IV 来源/覆盖边界必须自洽
        [硬伤七] 康波「五年之锚」不可证伪 → 状态机必须由可观测量驱动

    Args:
        data: yaml.safe_load 后的配置映射。

    Returns:
        错误消息列表 (空 = 通过); 每条以 [硬伤N] 或 [口径] 前缀定位。
    """
    errs: list[str] = []
    portfolio = _mapping(data.get("portfolio"))
    target = _mapping(data.get("target"))
    core = _items(data.get("core_holdings"))
    satellite = _items(data.get("satellite_holdings"))
    ballast = _items(data.get("ballast_holdings"))
    cash = _items(data.get("cash_holdings"))
    opts = _mapping(data.get("options_strategy"))
    budget = _mapping(opts.get("budget"))

    # ---------------- 硬伤一: 跨资产压舱 ----------------
    ballast_names = [str(_mapping(h).get("name")) for h in ballast]
    if len(ballast) < 2:
        errs.append("[硬伤一] ballast_holdings 不足 2 项 — 核心仓仍为清一色 A 股, 无跨资产对冲")
    else:
        if not any(("国债" in n or "债券" in n) for n in ballast_names):
            errs.append("[硬伤一] ballast_holdings 缺少国债/债券类资产")
        if not any("黄金" in n for n in ballast_names):
            errs.append("[硬伤一] ballast_holdings 缺少黄金类资产")
    if _num(portfolio.get("defense_exposure_target")) is None:
        errs.append("[硬伤一] portfolio.defense_exposure_target 未声明 — 防御敞口不可核")

    # ---------------- 硬伤二: 科创50 降级 ----------------
    core_codes = [str(_mapping(h).get("code")) for h in core]
    if "588000" in core_codes:
        errs.append("[硬伤二] 588000(科创50) 仍在 core_holdings — 最贵资产不得当压舱石")
    for h in satellite:
        if str(_mapping(h).get("code")) != "588000":
            continue
        w = _num(_mapping(h).get("weight_base"))
        if w is None or w > 0.07:
            errs.append(f"[硬伤二] 588000 weight_base 必须 ≤ 0.07 (当前 {w})")
        if _mapping(h).get("demoted_from_core") is not True:
            errs.append("[硬伤二] 588000 必须标记 demoted_from_core: true (留痕降级依据)")

    # ---------------- 硬伤三: 预算会计口径 ----------------
    if budget.get("basis") != V91_BUDGET_BASIS:
        errs.append(
            f"[硬伤三] options_strategy.budget.basis 必须为 {V91_BUDGET_BASIS!r}"
            " (本金划拨口径已废止, 权利金为年度消耗品)"
        )
    nav_rng = _items(budget.get("annual_pct_nav"))
    if len(nav_rng) != 2 or any(_num(x) is None for x in nav_rng):
        errs.append("[硬伤三] budget.annual_pct_nav 必须是 2 元素数值区间")
    else:
        lo, hi = _num(nav_rng[0]), _num(nav_rng[1])
        if not (0 < lo <= hi <= 0.03):
            errs.append(f"[硬伤三] budget.annual_pct_nav 须满足 0 < lo ≤ hi ≤ 0.03 (当前 {nav_rng})")
        cap = _num(budget.get("hard_cap_pct_nav"))
        if cap is None or not (hi <= cap <= 0.03):
            errs.append("[硬伤三] budget.hard_cap_pct_nav 必须 ≥ 区间上限且 ≤ 0.03")
    if "principal_allocation_pct" in budget:
        errs.append("[硬伤三] budget 仍含 principal_allocation_pct — 本金划拨口径未清除")
    # 口径同源: 预算 / 期权参数 / Greeks 三处必须字面一致, 绝不留双口径
    for label, basis in (
        ("option_params.budget_basis", _mapping(data.get("option_params")).get("budget_basis")),
        ("greeks.theta.budget_basis", _mapping(_mapping(data.get("greeks")).get("theta")).get("budget_basis")),
    ):
        if basis != V91_BUDGET_BASIS:
            errs.append(f"[硬伤三] {label} 与 budget.basis 必须同源为 {V91_BUDGET_BASIS!r} (当前 {basis!r})")

    # ---------------- 硬伤四: 熊市条款与名义/成本封顶 ----------------
    for key in opts:
        if "bear" in str(key).lower() or "熊市" in str(key):
            errs.append(f"[硬伤四] options_strategy.{key} 为熊市条款 — 与预算上限自相矛盾")
    collar = _mapping(opts.get("collar"))
    notional = _num(_mapping(collar.get("call_leg")).get("max_notional_pct"))
    if notional is None:
        notional = _num(_mapping(opts.get("covered_call")).get("max_notional_pct"))
    if notional is None or not (0 < notional <= 1.0):
        errs.append("[硬伤四] 卖出认购名义必须显式封顶 0 < max_notional_pct ≤ 1.0 (禁裸卖/超预算)")
    cost_rng = _items(collar.get("cost_target_pct"))
    if len(cost_rng) != 2 or any(_num(x) is None for x in cost_rng):
        errs.append("[硬伤四] collar.cost_target_pct 缺失 — 保护成本无法与预算对账")
    else:
        cap = _num(budget.get("hard_cap_pct_nav"))
        if cap is None or _num(cost_rng[1]) > cap + 1e-9:
            errs.append("[硬伤四] collar.cost_target_pct 上限必须 ≤ budget.hard_cap_pct_nav")

    # ---------------- 硬伤五: 卫星 alpha 实证状态 ----------------
    rot = _mapping(data.get("satellite_rotation"))
    if rot.get("alpha_aggregation") is not False:
        errs.append("[硬伤五] satellite_rotation.alpha_aggregation 必须为 false (S13 已证伪 -3.41pp)")
    if rot.get("evidence_status") != "unproven":
        errs.append("[硬伤五] satellite_rotation.evidence_status 必须显式标注 'unproven'")
    tilt = _num(rot.get("max_active_tilt_pct"))
    if tilt is None or tilt > 0.03:
        errs.append(f"[硬伤五] satellite_rotation.max_active_tilt_pct 必须 ≤ 0.03 (当前 {tilt})")

    # ---------------- 硬伤六: 期权数据链契约 ----------------
    oc = _mapping(_mapping(data.get("data_chain")).get("option_chain"))
    if not oc:
        errs.append("[硬伤六] data_chain.option_chain 未定义 — 期权数据链契约缺失")
    covered_u = {str(x) for x in _items(oc.get("covered_underlyings"))}
    uncovered_u = {str(x) for x in _items(oc.get("uncovered_underlyings"))}
    if not covered_u:
        errs.append("[硬伤六] data_chain 未声明 covered_underlyings")
    if covered_u & uncovered_u:
        errs.append(f"[硬伤六] covered/uncovered 标的存在交集 {sorted(covered_u & uncovered_u)} — IV 来源不可信")
    if oc.get("require_real_iv_for_hedge") is not True:
        errs.append("[硬伤六] require_real_iv_for_hedge 必须为 true (无真实 IV 不得上调保护比例)")
    if oc.get("iv_source_field") != "iv_source":
        errs.append("[硬伤六] iv_source_field 必须为 'iv_source' (缺失来源须显式标记, 禁伪造实测值)")
    pool = {str(x) for x in _items(_mapping(data.get("tool_pool")).get("etf_options_whitelist"))}
    undeclared = pool - covered_u - uncovered_u
    if undeclared:
        errs.append(f"[硬伤六] tool_pool.etf_options_whitelist 含未声明覆盖的标的 {sorted(undeclared)}")
    if pool & uncovered_u:
        errs.append(f"[硬伤六] tool_pool 白名单含无真实期权链标的 {sorted(pool & uncovered_u)}")
    targets = set()
    for key in ("collar", "covered_call", "protective_put"):
        targets |= {str(x) for x in _items(_mapping(opts.get(key)).get("targets"))}
    no_option = {
        str(_mapping(h).get("code"))
        for h in list(core) + list(satellite) + list(ballast)
        if _mapping(h).get("option_available") is False
    }
    if targets & no_option:
        errs.append(f"[硬伤六] 期权 targets 含 option_available=false 标的 {sorted(targets & no_option)}")

    # ---------------- 硬伤七: 可观测状态机 (替代康波纪年) ----------------
    sm = _mapping(data.get("regime_state_machine"))
    if not sm:
        errs.append("[硬伤七] regime_state_machine 缺失 — 仍以康波纪年为主驱动")
    else:
        if not _items(sm.get("removed_anchors")):
            errs.append("[硬伤七] regime_state_machine 未登记被废除的不可证伪锚")
        if not _items(sm.get("inputs")):
            errs.append("[硬伤七] 状态机 inputs 为空 — 无可观测量输入")
        states = [_mapping(s) for s in _items(sm.get("states"))]
        if len(states) < 3:
            errs.append("[硬伤七] 状态机 states 少于 3 个 — 无法覆盖风险区间")
        for st in states:
            cond = st.get("condition")
            if not isinstance(cond, str) or not any(k in cond for k in V91_OBSERVABLE_KEYWORDS):
                errs.append(f"[硬伤七] 状态 {st.get('name')!r} 的 condition 非可观测量化信号")
    for banned in ("kondratieff", "kondratiev", "cycle_phase"):
        if banned in data:
            errs.append(f"[硬伤七] 顶层仍含周期纪年区块 {banned!r}")
    exit_plan = _mapping(data.get("exit_plan"))
    if "extend_condition" in exit_plan:
        errs.append("[硬伤七] exit_plan.extend_condition 仍以不可证伪的周期阶段作为退出依据")
    if "removed_condition" not in exit_plan:
        errs.append("[硬伤七] exit_plan 未显式声明被废除的不可证伪退出条件")
    if not _mapping(data.get("circuit_breaker")).get("guardrail"):
        errs.append("[硬伤七] circuit_breaker.guardrail 护栏说明缺失 (禁止不可证伪触发条件)")

    # ---------------- 口径自洽 ----------------
    core_w = _weights(core, "weight")
    sat_w = _weights(satellite, "weight_base")
    total = core_w + sat_w + _weights(ballast, "weight") + _weights(cash, "weight")
    if abs(total - 1.0) > 0.005:
        errs.append(f"[口径] 各类别权重合计 {total:.4f} ≠ 1.0 — 配置与资金不可对账")
    eq = _num(portfolio.get("equity_exposure_target"))
    if eq is not None and abs(eq - (core_w + sat_w)) > 0.005:
        errs.append(f"[口径] equity_exposure_target {eq} 与核心+卫星基准权重 {core_w + sat_w:.4f} 不一致")
    ar = _num(target.get("annual_return"))
    arr = [_num(x) for x in _items(target.get("annual_return_range"))]
    if ar is None or len(arr) != 2 or any(v is None for v in arr):
        errs.append("[口径] target.annual_return / annual_return_range 缺失或非法")
    elif not (arr[0] <= ar <= arr[1]):
        errs.append(f"[口径] target.annual_return {ar} 不在 annual_return_range {arr} 内")
    # 目标必须有自下而上基据: 净中枢 = 毛中枢 − 保护成本, 且 target 不得高于净中枢
    # (防"目标高于自身测算上限"这类不可达成目标回潮, 见诊脉书卷四 vs 卷六矛盾)
    gross = _num(target.get("gross_return_center"))
    cost = _num(target.get("hedge_cost_target_pct"))
    net = _num(target.get("net_return_center"))
    if gross is None or cost is None or net is None:
        errs.append(
            "[口径] target 缺少自下而上基据 "
            "(gross_return_center / hedge_cost_target_pct / net_return_center)"
        )
    else:
        if abs(net - (gross - cost)) > 0.0005:
            errs.append(f"[口径] net_return_center {net} ≠ 毛 {gross} − 保护成本 {cost}")
        if ar is not None and ar > net + 0.0005:
            errs.append(
                f"[口径] target.annual_return {ar} 高于自下而上净收益中枢 {net} — 不可达成目标"
            )
        # 成本口径链接: target 的成本假设必须等于引擎实际计费 (collar.cost_target_pct 中值) —
        # 否则「净中枢 = 毛 − 成本」纸面自洽, 回测却按另一套成本扣钱 (实测 0.012 vs 0.0125)
        ctp = _items(_mapping(opts.get("collar")).get("cost_target_pct"))
        collar_cost = [ _num(x) for x in ctp if _num(x) is not None ]
        if len(collar_cost) == 2:
            collar_mid = (collar_cost[0] + collar_cost[1]) / 2
            if abs(cost - collar_mid) > 0.0005:
                errs.append(
                    f"[口径] hedge_cost_target_pct {cost} ≠ collar.cost_target_pct 中值 "
                    f"{collar_mid} — 目标成本假设与回测实际计费脱钩"
                )
    # 基据块必须显式存在且可复核 (基据是单一事实源, 不是注释 — 删掉它目标就失去推导链)
    basis = _mapping(target.get("target_basis"))
    if not basis:
        errs.append("[口径] target.target_basis 基据块缺失 — 目标失去可追溯推导链")
    else:
        if basis.get("method") != "bottom_up":
            errs.append(f"[口径] target_basis.method 必须为 'bottom_up' (当前 {basis.get('method')!r})")
        for key in ("gross_formula", "net_formula", "cross_check"):
            if not str(basis.get(key) or "").strip():
                errs.append(f"[口径] target_basis.{key} 缺失 — 基据不可复核")
    # 单一口径: 目标即净中枢, 不允许两处各留一个数 (防口径漂移回潮)
    if ar is not None and net is not None and abs(ar - net) > 0.0005:
        errs.append(f"[口径] target.annual_return {ar} ≠ 自下而上净中枢 {net} — 存在第二口径")
    # 派生值联动: 备兑认购行权价地板 = 目标收益 + 8pp (0.14 对应已废止的 6% 目标)
    call_leg = _mapping(_mapping(opts.get("collar")).get("call_leg"))
    floor = _num(call_leg.get("strike_floor_pct"))
    if floor is not None and ar is not None:
        expect_floor = ar + V91_STRIKE_FLOOR_PREMIUM
        if abs(floor - expect_floor) > 0.005:
            errs.append(
                f"[口径] collar.call_leg.strike_floor_pct {floor} ≠ 目标 {ar} + "
                f"{V91_STRIKE_FLOOR_PREMIUM} = {expect_floor:.3f} — 派生值未随目标口径联动"
            )
    # 情景概率加权中枢必须 = 净中枢 (否则情景预期会另立第二口径)
    scen = _mapping(data.get("scenario_analysis"))
    if not scen:
        errs.append("[口径] scenario_analysis 缺失 — 目标无情景支撑")
    else:
        probs: list[float] = []
        mids: list[float] = []
        for name in ("optimistic", "base", "pessimistic"):
            node = _mapping(scen.get(name))
            rng = [_num(x) for x in _items(node.get("annual_return_pct"))]
            p = _num(node.get("probability"))
            if len(rng) != 2 or any(v is None for v in rng) or p is None:
                errs.append(f"[口径] scenario_analysis.{name} 缺 annual_return_pct / probability")
                continue
            probs.append(p)
            mids.append((float(rng[0]) + float(rng[1])) / 2 / 100.0)
        if len(probs) == 3:
            total_p = sum(probs)
            if abs(total_p - 1.0) > 0.005:
                errs.append(f"[口径] 情景概率合计 {total_p:.4f} ≠ 1.0")
            exp_ret = sum(probs[i] * mids[i] for i in range(3))
            if net is not None and abs(exp_ret - net) > 0.005:
                errs.append(
                    f"[口径] 情景概率加权收益中枢 {exp_ret:.4f} ≠ 净中枢 {net} — 情景预期另立口径"
                )
    # 旧口径必须并存证 (防删证后「不可达成目标」回潮)
    legacy = _mapping(target.get("legacy_target_infeasible"))
    if not legacy or not str(legacy.get("verdict") or "").strip():
        errs.append("[口径] target.legacy_target_infeasible 并存证缺失 — 禁止删除历史口径证据")
    mdd = _num(target.get("max_drawdown"))
    mddr = [_num(x) for x in _items(target.get("max_drawdown_range"))]
    if mdd is None or len(mddr) != 2 or any(v is None for v in mddr):
        errs.append("[口径] target.max_drawdown / max_drawdown_range 缺失或非法")
    elif not (mddr[0] <= mdd <= mddr[1]):
        errs.append(f"[口径] target.max_drawdown {mdd} 不在 max_drawdown_range {mddr} 内")

    levels = [_mapping(x) for x in _items(_mapping(data.get("circuit_breaker")).get("levels"))]
    if len(levels) < 4:
        errs.append(f"[口径] circuit_breaker.levels 少于 4 级 (当前 {len(levels)}) — 熔断阶梯不完整")
    else:
        eq_targets = [_num(x.get("equity_target")) for x in levels[:4]]
        if any(v is None for v in eq_targets):
            errs.append(f"[口径] 熔断 L1~L4 缺少 equity_target (当前 {eq_targets})")
        elif any(eq_targets[i] <= eq_targets[i + 1] for i in range(len(eq_targets) - 1)):
            errs.append(f"[口径] 熔断 L1~L4 equity_target 必须严格递减 (当前 {eq_targets})")
    dd = _mapping(data.get("drawdown_action"))
    dd_keys = ("warning_threshold", "l2_threshold", "danger_threshold", "breach_threshold")
    dd_vals = [_num(dd.get(k)) for k in dd_keys]
    if any(v is None for v in dd_vals):
        errs.append("[口径] drawdown_action 必须定义 warning/l2/danger/breach 四级阈值")
    elif any(dd_vals[i] >= dd_vals[i + 1] for i in range(len(dd_vals) - 1)):
        errs.append(f"[口径] drawdown_action 四级阈值必须严格递增 (当前 {dd_vals})")
    elif mdd is not None and abs(dd_vals[2] - mdd) > 1e-9:
        errs.append(f"[口径] L3(danger) 阈值 {dd_vals[2]} 必须等于 target.max_drawdown {mdd} (硬约束对齐)")

    # ---------------- 状态隔离 (避免与 v1.0 互相污染) ----------------
    v10_paths = {
        "state_file": "config/portfolio_200w_etf_state.json",
        "nav_history_file": "config/portfolio_200w_etf_nav.json",
        "audit_db": "data/portfolio_200w_etf_audit.db",
    }
    for key, legacy in v10_paths.items():
        if data.get(key) == legacy:
            errs.append(f"[口径] {key} 与 v1.0 共用路径 {legacy!r} — 状态会互相污染")
    return errs


def _check_file(path: Path, verbose: bool) -> list[str]:
    """校验单个 YAML, 返回错误消息列表 (空 = 通过)。"""
    errors: list[str] = []
    fname = path.name

    # 1. 可解析性 + 顶层非空映射
    try:
        with open(path, encoding="utf-8") as fh:
            data = yaml.safe_load(fh)
    except yaml.YAMLError as exc:  # 解析错误 → 直接失败
        errors.append(f"{fname}: YAML 解析失败 — {exc}")
        return errors
    except OSError as exc:
        errors.append(f"{fname}: 读取失败 — {exc}")
        return errors

    if data is None or not isinstance(data, dict):
        errors.append(f"{fname}: 顶层必须是映射 (当前为 {type(data).__name__})")
        return errors
    if not data:
        errors.append(f"{fname}: 顶层映射为空")
        return errors

    # 2. 重复 key 检测 (基于 compose 节点树)
    try:
        with open(path, encoding="utf-8") as fh:
            doc = yaml.compose(fh)
        if doc is not None:
            for key in _collect_duplicate_keys(doc):
                errors.append(f"{fname}: 重复 key 检测到 {key!r} (后值将静默覆盖前值)")
    except yaml.YAMLError as exc:
        errors.append(f"{fname}: 重复 key 扫描解析失败 — {exc}")

    # 3. 关键顶层区块白名单
    for section in REQUIRED_TOP_SECTIONS.get(fname, ()):
        if section not in data:
            errors.append(f"{fname}: 缺少关键顶层区块 {section!r}")

    # 4. v9.1 组合专属语义护栏 (七处硬伤 + 口径自洽)
    if fname == "portfolio_200w_etf_v91.yaml":
        errors.extend(f"{fname}: {msg}" for msg in check_portfolio_v91(data))

    if verbose:
        kind = "OK" if not errors else "FAIL"
        print(f"  [{kind}] {fname}  top={list(data.keys())}")
    return errors


def _discover_targets(args) -> list[Path]:
    """确定待校验文件集合: --file 指定或默认 config/*.yaml (存在的顶层文件)。"""
    if args.file:
        return [CONFIG_DIR / f for f in args.file if (CONFIG_DIR / f).exists()]
    return sorted(p for p in CONFIG_DIR.glob("*.yaml") if p.is_file())


def _selftest() -> int:
    """构造临时畸形配置, 断言检测逻辑正确返回错误 (供无 pytest 云端沙箱自验证)。"""
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = Path(tmpdir)

        # 1) 重复 key (safe_load 静默后覆盖) → 必须检出
        dup = tmp / "dup.yaml"
        dup.write_text("settings:\n  x: 1\nsettings:\n  y: 2\n", encoding="utf-8")
        if not any("重复 key" in e for e in _check_file(dup, False)):
            print("selftest FAIL: 未检出重复 key")
            return 1

        # 2) 已知配置缺失关键区块 → 必须检出
        missing = tmp / "llm_pricing.yaml"
        missing.write_text("models:\n  glm5:\n    cost_per_1k_input: 1\n", encoding="utf-8")
        if not any("daily_token_budget" in e for e in _check_file(missing, False)):
            print("selftest FAIL: 未检出缺失关键区块")
            return 1

        # 3) 顶层非映射 (序列) → 必须检出
        seq = tmp / "seq.yaml"
        seq.write_text("- just\n- a\n- list\n", encoding="utf-8")
        if not any("顶层必须是映射" in e for e in _check_file(seq, False)):
            print("selftest FAIL: 未检出非映射顶层")
            return 1

        # 4) 非法 YAML → 必须检出
        broken = tmp / "broken.yaml"
        broken.write_text("settings: [unclosed\n", encoding="utf-8")
        if not any("解析失败" in e for e in _check_file(broken, False)):
            print("selftest FAIL: 未检出解析错误")
            return 1

        # 5) v9.1 七处硬伤护栏 — 在产配置须通过, 且每处硬伤都能被变异拦下
        real = CONFIG_DIR / "portfolio_200w_etf_v91.yaml"
        if real.exists():
            with open(real, encoding="utf-8") as fh:
                baseline = yaml.safe_load(fh)
            baseline_errs = check_portfolio_v91(baseline)
            if baseline_errs:
                print("selftest FAIL: 在产 v9.1 配置未通过硬伤护栏")
                for msg in baseline_errs:
                    print(f"    - {msg}")
                return 1

            cases = []

            mutant = copy.deepcopy(baseline)
            mutant.pop("ballast_holdings", None)
            cases.append(("缺少跨资产压舱", mutant, "[硬伤一]"))

            mutant = copy.deepcopy(baseline)
            mutant["core_holdings"].append(
                {"code": "588000", "name": "科创50ETF", "type": "etf", "weight": 0.0}
            )
            cases.append(("科创50 重回核心资产", mutant, "[硬伤二]"))

            mutant = copy.deepcopy(baseline)
            mutant["options_strategy"]["budget"]["basis"] = "principal_allocation"
            cases.append(("预算口径回退本金划拨", mutant, "[硬伤三]"))

            mutant = copy.deepcopy(baseline)
            mutant["options_strategy"]["bear_market"] = {"put_notional_pct": 0.35, "cash_pct": 0.05}
            cases.append(("熊市 Put 35% 名义条款", mutant, "[硬伤四]"))

            mutant = copy.deepcopy(baseline)
            mutant["satellite_rotation"]["alpha_aggregation"] = True
            cases.append(("启用未实证 alpha 聚合", mutant, "[硬伤五]"))

            mutant = copy.deepcopy(baseline)
            mutant.pop("data_chain", None)
            cases.append(("缺失期权数据链契约", mutant, "[硬伤六]"))

            mutant = copy.deepcopy(baseline)
            mutant.pop("regime_state_machine", None)
            cases.append(("回退康波纪年驱动", mutant, "[硬伤七]"))

            for label, mutant, expect in cases:
                cand = tmp / "portfolio_200w_etf_v91.yaml"
                cand.write_text(yaml.safe_dump(mutant, allow_unicode=True), encoding="utf-8")
                if not any(expect in e for e in _check_file(cand, False)):
                    print(f"selftest FAIL: 变异「{label}」未被 {expect} 护栏拦下")
                    return 1

    print("selftest PASS: 重复 key / 缺失区块 / 非映射顶层 / 解析错误 / v9.1 七处硬伤护栏 均正确检出")
    return 0


def _main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--verbose", action="store_true", help="逐文件输出明细")
    parser.add_argument(
        "--file", nargs="*", default=[], help="仅校验指定文件名 (config 目录下)"
    )
    parser.add_argument(
        "--strict-gnn", action="store_true",
        help="同时对 config/gnn_factor/*.yaml 做可解析性冒烟",
    )
    parser.add_argument(
        "--selftest", action="store_true",
        help="运行内建自测 (构造临时畸形配置断言检测逻辑), 无需 pytest",
    )
    args = parser.parse_args()

    if args.selftest:
        return _selftest()

    targets = _discover_targets(args)
    if not targets:
        print("没有找到待校验的 config/*.yaml")
        return 0

    errors: list[str] = []
    print(f"校验 {len(targets)} 个 config/*.yaml ...")
    for path in targets:
        errors.extend(_check_file(path, args.verbose))

    if args.strict_gnn:  # gnn_factor 子目录: 可选冒烟
        for path in sorted((CONFIG_DIR / "gnn_factor").glob("*.yaml")):
            errors.extend(_check_file(path, args.verbose))

    if errors:
        print("\n配置校验失败:")
        for e in errors:
            print(f"  - {e}")
        return 1

    # 注意: 不得输出 ✓ 等非 GBK 字符 —— Windows 控制台会抛 UnicodeEncodeError,
    # 使「校验成功」反而以非零退出码收场 (门禁假失败)。
    print("全部通过 (PASS)")
    return 0


if __name__ == "__main__":
    sys.exit(_main())
