# -*- coding: utf-8 -*-
"""V7.1 信号后处理补丁: 在 _lgb_walkforward_train 后对 bull regime 下高波动股施加信号惩罚

逻辑:
  1. 训练完成后, 计算当前大盘 regime (基于 510300 MA60)
  2. 获取每个标的的 volatility_20
  3. 若 regime==bull 且 vol20>BULL_HIGH_VOL_THRESHOLD 且 signal>0:
     - signal *= BULL_VOL_PENALTY (默认 0.5)
     - 降低高波动股在 bull regime 的权重, 定向解决 2024-06 crash

接入位置:
  - 在 _lgb_walkforward_train 的 "训练完成" 日志前调用
  - 不修改 _lgb_get_signal, 直接修改 _lgb_models[code]["signal"]
"""

fp = r'e:\各种PY程序\28-终极量化交易系统8.4\institutional_pipeline_runner.py'
with open(fp, 'r', encoding='utf-8') as f:
    content = f.read()

# 1. 检查是否已应用 V7.1 补丁
if '_apply_v71_signal_penalty' in content:
    print('V7.1 补丁已存在, 跳过')
    exit(0)

# 2. 添加 V7.1 常量 (在 _CROSS_MARKET_PROXY_SYMBOLS 后)
const_anchor = '_CROSS_MARKET_PROXY_SYMBOLS = ["518880", "600036", "588000", "515180"]'
v71_constants = const_anchor + '''

# V7.1 信号后处理参数 (bull regime 下高波动股惩罚)
_V71_REGIME_PROXY_SYMBOL = "510300"  # 大盘代理 (沪深300ETF)
_V71_MA_PERIOD = 60                  # MA60 中期趋势
_V71_MA_SLOPE_WINDOW = 5             # MA60 斜率窗口
_V71_BULL_HIGH_VOL_THRESHOLD = 0.045  # bull regime 下高波动股阈值 (日波动率 4.5%)
_V71_BULL_VOL_PENALTY = 0.5          # 高波动股正信号惩罚系数 (signal *= 0.5)'''

if const_anchor in content:
    content = content.replace(const_anchor, v71_constants)
    print('V7.1 常量已添加')
else:
    print('ERROR: const_anchor not found')
    exit(1)

# 3. 在 _lgb_walkforward_train 的 "训练完成" 日志前插入 V7.1 调用
old_log = '''        logger.info("[LGB-WF] 训练完成: trained=%d failed=%d skipped=%d",
                    trained, failed, skipped)
        return {"trained": trained, "failed": failed, "skipped": skipped, "results": self._lgb_models}'''

new_log = '''        # V7.1 信号后处理: bull regime 下高波动股正信号惩罚
        try:
            penalty_stats = self._apply_v71_signal_penalty(featured_dict)
            if penalty_stats["penalized"] > 0:
                logger.info("[V7.1] 信号惩罚: regime=%s penalized=%d/%d",
                            penalty_stats["regime"], penalty_stats["penalized"],
                            penalty_stats["total"])
        except Exception as e:
            logger.warning("[V7.1] 信号后处理失败: %s", e)

        logger.info("[LGB-WF] 训练完成: trained=%d failed=%d skipped=%d",
                    trained, failed, skipped)
        return {"trained": trained, "failed": failed, "skipped": skipped, "results": self._lgb_models}'''

if old_log in content:
    content = content.replace(old_log, new_log)
    print('V7.1 调用已插入')
else:
    print('ERROR: old_log not found')
    exit(1)

# 4. 在 _lgb_walkforward_train 方法后添加 _apply_v71_signal_penalty 方法
# 找到 _lgb_walkforward_train 方法的结束 (return 语句后的下一个 def)
method_anchor = '        return {"trained": trained, "failed": failed, "skipped": skipped, "results": self._lgb_models}\n'

v71_method = method_anchor + '''
    # ------------------------------------------------------------
    # V7.1 信号后处理: bull regime 下高波动股惩罚
    # ------------------------------------------------------------

    def _compute_current_regime(self) -> str:
        """计算当前大盘 regime (基于 510300 MA60, 无前视偏差)

        Returns:
            regime: "bull" / "bear" / "choppy" / "rebound" / "unknown"
        """
        cutoff = pd.Timestamp(self.ctx.report_date).normalize()
        try:
            if hasattr(cutoff, "tz") and cutoff.tz is not None:
                cutoff = cutoff.tz_localize(None)
        except Exception:
            pass

        df = self._load_base_cache(_V71_REGIME_PROXY_SYMBOL)
        if df is None or df.empty:
            if self.data_provider is not None:
                try:
                    df = self.data_provider.get_historical_data(_V71_REGIME_PROXY_SYMBOL, period="3y")
                except Exception:
                    df = None
        if df is None or df.empty:
            return "unknown"

        if hasattr(df.index, "tz") and df.index.tz is not None:
            df.index = df.index.tz_localize(None)
        df = df.sort_index()
        df = df[df.index <= cutoff]
        if len(df) < _V71_MA_PERIOD + _V71_MA_SLOPE_WINDOW:
            return "unknown"

        close = df["close"]
        ma = close.rolling(_V71_MA_PERIOD).mean()
        latest_close = float(close.iloc[-1])
        latest_ma = float(ma.iloc[-1])
        ma_slope = float(ma.iloc[-1] - ma.iloc[-1 - _V71_MA_SLOPE_WINDOW])
        ma_rising = ma_slope > 0
        above_ma = latest_close > latest_ma

        if above_ma and ma_rising:
            return "bull"
        elif above_ma and not ma_rising:
            return "choppy"
        elif not above_ma and ma_rising:
            return "rebound"
        else:
            return "bear"

    def _apply_v71_signal_penalty(self, featured_dict: Dict[str, pd.DataFrame]) -> Dict[str, Any]:
        """V7.1 信号后处理: bull regime 下高波动股正信号惩罚

        动机:
            V7-Model 模型层 regime-aware 特征失败 (Sharpe CV 0.55->0.74)
            根因: 2024-06-03 (bull regime) 688017 权重10%但跌34.82%, 300308 权重8%但跌17.34%
            LGB 仍给高波动股高正信号, 模型层无法定向干预

        方案 (信号层硬编码惩罚):
            1. 计算当前 regime (510300 MA60)
            2. 若 regime==bull, 遍历所有持仓标的:
               - 获取 volatility_20 (日波动率)
               - 若 vol20 > 4.5% 且 signal > 0: signal *= 0.5
            3. 直接修改 self._lgb_models[code]["signal"]

        Args:
            featured_dict: 特征字典 (含 volatility_20 列)

        Returns:
            {regime, total, penalized, details}
        """
        regime = self._compute_current_regime()
        total = len(self._lgb_models)
        penalized = 0
        details = []

        if regime != "bull":
            return {"regime": regime, "total": total, "penalized": 0, "details": []}

        for code, model_info in self._lgb_models.items():
            signal = float(model_info.get("signal", 0.0))
            if signal <= 0:
                continue  # 仅惩罚正信号 (做多)

            # 获取 volatility_20
            vol20 = None
            if code in featured_dict:
                df = featured_dict[code]
                if "volatility_20" in df.columns and len(df) > 0:
                    vol20 = float(df["volatility_20"].iloc[-1])
                elif "close" in df.columns and len(df) >= 21:
                    # 回退计算: 20 日日收益标准差
                    daily_rets = df["close"].pct_change().tail(20)
                    vol20 = float(daily_rets.std())

            if vol20 is None or vol20 <= _V71_BULL_HIGH_VOL_THRESHOLD:
                continue

            # 应用惩罚
            old_signal = signal
            new_signal = signal * _V71_BULL_VOL_PENALTY
            model_info["signal"] = new_signal
            penalized += 1
            details.append({
                "code": code,
                "vol20": vol20,
                "old_signal": old_signal,
                "new_signal": new_signal,
            })
            logger.info(
                "[V7.1] %s: bull regime 高波动惩罚 vol20=%.4f > %.4f, signal %.4f -> %.4f",
                code, vol20, _V71_BULL_HIGH_VOL_THRESHOLD, old_signal, new_signal,
            )

        return {"regime": regime, "total": total, "penalized": penalized, "details": details}

'''

if method_anchor in content:
    content = content.replace(method_anchor, v71_method, 1)
    print('V7.1 方法已添加')
else:
    print('ERROR: method_anchor not found')
    exit(1)

with open(fp, 'w', encoding='utf-8') as f:
    f.write(content)
print('V7.1 补丁应用完成')
