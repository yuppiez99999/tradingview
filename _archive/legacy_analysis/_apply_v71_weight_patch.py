"""在 backtest_runner.py 中添加 V7.1 权重级惩罚代码"""
from pathlib import Path

target = Path("research/backtest_runner.py")
src = target.read_text(encoding="utf-8")

# === 1. 添加 V7.1 参数 (在回撤熔断参数之后) ===
OLD_PARAMS = """_DRAWDOWN_BREAKER_THRESHOLD = 0.05   # 5% 回撤触发熔断
_DRAWDOWN_BREAKER_FACTOR = 0.6       # 触发后下月仓位×0.6 (V2: 0.5→0.6)
_DRAWDOWN_SEVERE_THRESHOLD = 0.10    # 10% 严重回撤
_DRAWDOWN_SEVERE_FACTOR = 0.4        # 严重回撤下月仓位×0.4 (V2: 0.3→0.4)"""

NEW_PARAMS = """_DRAWDOWN_BREAKER_THRESHOLD = 0.05   # 5% 回撤触发熔断
_DRAWDOWN_BREAKER_FACTOR = 0.6       # 触发后下月仓位×0.6 (V2: 0.5→0.6)
_DRAWDOWN_SEVERE_THRESHOLD = 0.10    # 10% 严重回撤
_DRAWDOWN_SEVERE_FACTOR = 0.4        # 严重回撤下月仓位×0.4 (V2: 0.3→0.4)

# === V7.1 信号后处理参数 (bull regime 下高波动股权重惩罚) ===
# 动机: 2024-06 (bull regime) 688017 权重10%但跌34.82%, 300308 权重8%但跌17.34%
#       LGB 在 bull regime 给高波动股高权重但信号失效, 满仓无个股级保护
#       V7-Model (regime-aware 特征) 已证明无法从模型层解决, 需硬编码权重惩罚
# 方案: bull regime 下, vol20 > 4.5% 的股票权重 ×0.5 (直接在权重级应用, 不依赖 LGB 重训)
_V71_BULL_HIGH_VOL_THRESHOLD = 0.045  # bull regime 下高波动股阈值 (日波动率 4.5%)
_V71_BULL_VOL_PENALTY = 0.5          # 高波动股权重惩罚系数 (weight *= 0.5)"""

if OLD_PARAMS in src:
    src = src.replace(OLD_PARAMS, NEW_PARAMS, 1)
    print("[OK] 1. V7.1 参数已添加")
else:
    print("[SKIP] 1. V7.1 参数已存在或未找到锚点")

# === 2. 添加 _apply_v71_weight_penalty 函数 (在 _apply_market_regime_scaling 之后) ===
# 锚点: _load_existing_pipeline_result 函数定义之前
ANCHOR_FUNC = "def _load_existing_pipeline_result(date_str: str) -> Dict:"

V71_FUNC = '''def _apply_v71_weight_penalty(
    weights: Dict[str, float],
    date: pd.Timestamp,
    regime_info: Dict,
) -> tuple:
    """V7.1 权重级惩罚: bull regime 下高波动股权重减半

    动机:
        2024-06 (bull regime): 688017 权重10%但跌34.82%, 300308 权重8%但跌17.34%
        LGB 在 bull regime 给高波动股高权重但信号失效, 满仓无个股级保护。
        V7-Model (regime-aware 特征) 已证明无法从模型层解决, 需硬编码权重惩罚。

    方案:
        bull regime 下, vol20 > 4.5% 的股票权重 ×0.5
        (直接在权重级应用, 不依赖 LGB 重训, 兼容 resume=True 缓存复用)

    Args:
        weights: 当前月份的目标权重字典 {symbol: weight}
        date: 当前月份的 pd.Timestamp
        regime_info: 来自 _apply_market_regime_scaling 的 regime 信息

    Returns:
        (adjusted_weights, penalty_info)
    """
    regime = regime_info.get("regime", "unknown")
    if regime != "bull":
        return weights, {"regime": regime, "penalized": 0, "details": []}

    cutoff = pd.Timestamp(date).normalize()
    try:
        if hasattr(cutoff, "tz") and cutoff.tz is not None:
            cutoff = cutoff.tz_localize(None)
    except Exception:
        pass

    penalized = []
    adjusted = dict(weights)

    for code, w in weights.items():
        if w <= 0:
            continue

        # 读取个股历史数据 (与 _apply_market_regime_scaling 同源, 无前视偏差)
        sym_file = Path("data_cache") / f"historical_{code}_5y_base.parquet"
        if not sym_file.exists():
            continue

        try:
            df_sym = pd.read_parquet(sym_file)
            if hasattr(df_sym.index, "tz") and df_sym.index.tz is not None:
                df_sym.index = df_sym.index.tz_localize(None)
            df_sym = df_sym.sort_index()
            df_sym = df_sym[df_sym.index <= cutoff]
            if len(df_sym) < 21:
                continue

            daily_rets = df_sym["close"].pct_change()
            vol20 = float(daily_rets.tail(20).std())

            if vol20 > _V71_BULL_HIGH_VOL_THRESHOLD:
                old_w = w
                new_w = w * _V71_BULL_VOL_PENALTY
                adjusted[code] = new_w
                penalized.append({
                    "code": code,
                    "vol20": vol20,
                    "old_weight": old_w,
                    "new_weight": new_w,
                })
                logger.info(
                    "[V7.1] %s: bull regime 高波动惩罚 vol20=%.4f > %.4f, weight %.4f -> %.4f",
                    code, vol20, _V71_BULL_HIGH_VOL_THRESHOLD, old_w, new_w,
                )
        except Exception as e:
            logger.warning("[V7.1] %s 波动率计算失败: %s", code, e)

    if penalized:
        logger.info(
            "[V7.1] %s: bull regime 惩罚 %d/%d 只股票",
            cutoff.strftime("%Y-%m-%d"), len(penalized), len(weights),
        )

    return adjusted, {
        "regime": regime,
        "penalized": len(penalized),
        "details": penalized,
    }


'''

if ANCHOR_FUNC in src and "_apply_v71_weight_penalty" not in src:
    src = src.replace(ANCHOR_FUNC, V71_FUNC + ANCHOR_FUNC, 1)
    print("[OK] 2. _apply_v71_weight_penalty 函数已添加")
elif "_apply_v71_weight_penalty" in src:
    print("[SKIP] 2. _apply_v71_weight_penalty 函数已存在")
else:
    print("[FAIL] 2. 未找到锚点 _load_existing_pipeline_result")

# === 3. 在主循环中调用 V7.1 惩罚 (在 regime scaling 之后, 板块约束之前) ===
OLD_CALL = """        if weights and not regime_info:
            weights, regime_info = _apply_market_regime_scaling(weights, date)

        # === 二次板块集中度硬约束 (修复缓存权重缺失板块映射的Bug) ==="""

NEW_CALL = """        if weights and not regime_info:
            weights, regime_info = _apply_market_regime_scaling(weights, date)

        # === V7.1 信号后处理: bull regime 下高波动股权重惩罚 ===
        # 动机: 2024-06 (bull regime) 688017/300308 高权重但大跌, 需个股级硬约束
        # 在 regime scaling 之后应用, 仅 bull regime 下对 vol20>4.5% 的股票权重×0.5
        if weights:
            weights, v71_info = _apply_v71_weight_penalty(weights, date, regime_info)
            if v71_info["penalized"] > 0:
                regime_info["v71_penalty"] = v71_info

        # === 二次板块集中度硬约束 (修复缓存权重缺失板块映射的Bug) ==="""

if OLD_CALL in src and "v71_info" not in src:
    src = src.replace(OLD_CALL, NEW_CALL, 1)
    print("[OK] 3. V7.1 调用点已添加")
elif "v71_info" in src:
    print("[SKIP] 3. V7.1 调用点已存在")
else:
    print("[FAIL] 3. 未找到调用点锚点")

# === 写回文件 ===
target.write_text(src, encoding="utf-8")
print(f"\n[DONE] 文件已更新: {target}")
print(f"文件大小: {target.stat().st_size} bytes")
