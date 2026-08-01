# T01 MyPy 错误基线报告

- 生成时间: 2026-07-27
- mypy 退出码: 1
- 总错误数: 681
- 受影响模块数: 85
- 检查源文件数: 150

## 一、按错误类型统计

| 错误类型 | 数量 |
|---------|------|
| error | 515 |
| note | 122 |
| Unused "type | 44 |

## 二、按错误代码统计 (Top 20)

| 错误代码 | 数量 |
|---------|------|
| (no-code) | 633 |
| Any, Any | 16 |
| misc | 7 |
| unused-ignore | 5 |
| annotation-unchecked | 4 |
| str | 4 |
| _T | 3 |
| int | 3 |
| TensorflowLSTMPredictor | 2 |
| Any | 2 |
| BacktestDataLoader | 1 |
| ABTestResult | 1 |

## 三、按模块统计 (Top 30)

| 模块 | 错误数 |
|------|-------|
| wt_spread_strategy | 50 |
| data_provider | 43 |
| ifind_client | 37 |
| research/vibe_trading_factor_analysis/pipeline/pipeline_orchestrator | 30 |
| tf_price_predictor | 28 |
| build_plan_executor | 27 |
| execution/broker_adapters | 25 |
| vol_target_controller | 18 |
| risk_metrics | 18 |
| execution/automated_execution_system | 18 |
| risk_guard_integrator | 18 |
| overnight_gap_monitor | 17 |
| hedge_execution_engine | 17 |
| v10_config_loader | 16 |
| liquidation_scheduler | 16 |
| wt_risk_control | 15 |
| wt_backtest_engine | 13 |
| tdx_data_source | 12 |
| akshare_data_source | 12 |
| directional_futures_trader | 12 |
| protective_put_engine | 11 |
| alpha/drift_monitor | 11 |
| market_circuit_breaker | 11 |
| attribution/daily_panel | 11 |
| web_scraper | 10 |
| etf_flow_decision | 9 |
| kill_switch | 8 |
| quant_neutral_runner | 8 |
| greek_hedge_manager | 8 |
| external_data_source | 6 |

## 四、分级策略

### P0 关键模块 (必须真正修复 type hints)
- kill_switch (核心风控)
- broker_adapters / qmt_broker (实盘下单)
- config_manager (配置管理)
- trading_env (环境隔离)

### P1 重要模块 (优先修复)
- risk/* (风控)
- execution/* (执行)
- portfolio_optimizer (资金管理)

### P2 普通模块 (允许 # type: ignore[code] 压制)
- alpha/* (因子库, 已通过 T04 验证)
- attribution/* (归因)
- reporting/* (报告生成)
- 其他辅助模块

## 五、完整错误清单

<details><summary>展开查看完整错误列表</summary>

```
utils\transaction_cost_model.py:172:9: error: Returning Any from function
declared to return "float"  [no-any-return]
            return base_impact * volatility_adj
            ^~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
utils\transaction_cost_model.py:278:13: error: Dict entry 1 has incompatible
type "str": "str"; expected "str": "float"  [dict-item]
                "tier": tier.value,
                ^~~~~~~~~~~~~~~~~~
utils\risk_attribution.py:85:13: error: Returning Any from function declared to
return "dict[str, Any]"  [no-any-return]
                return json.load(f).get("hedge_positions", {})
                ^~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
utils\akshare_futures.py:262:9: error: Returning Any from function declared to
return "dict[str, Any]"  [no-any-return]
            return quotes
            ^~~~~~~~~~~~~
utils\akshare_futures.py:316:9: error: Returning Any from function declared to
return "dict[str, Any]"  [no-any-return]
            return info
            ^~~~~~~~~~~
utils\wt_risk_control.py:22:39: error: Incompatible default for parameter
"config" (default has type "None", parameter has type "dict[Any, Any]") 
[assignment]
        def __init__(self, config: Dict = None):
                                          ^~~~
utils\wt_risk_control.py:22:39: note: PEP 484 prohibits implicit Optional. Accordingly, mypy has changed its default to no_implicit_optional=True
utils\wt_risk_control.py:22:39: note: Use https://github.com/hauntsaninja/no_implicit_optional to automatically upgrade your codebase
utils\wt_risk_control.py:116:9: error: Incompatible types in assignment
(expression has type "float", variable has type "int")  [assignment]
            self.daily_volume += volume
            ^~~~~~~~~~~~~~~~~~~~~~~~~~~
utils\wt_risk_control.py:187:9: error: Need type annotation for
"stop_loss_orders" (hint: "stop_loss_orders: dict[<type>, <type>] = ...") 
[var-annotated]
            self.stop_loss_orders = {}
            ^~~~~~~~~~~~~~~~~~~~~
utils\wt_risk_control.py:280:9: error: Returning Any from function declared to
return "float"  [no-any-return]
            return total_value * volatility * z_score
            ^~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
utils\wt_risk_control.py:289:9: error: Returning Any from function declared to
return "float"  [no-any-return]
            return total_value * cvar_factor
            ^~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
utils\wt_risk_control.py:332:49: error: Incompatible types in assignment
(expression has type "float", target has type "int")  [assignment]
                    sectors[sector]["percentage"] = sectors[sector]["value...
                                                    ^~~~~~~~~~~~~~~~~~~~~~...
utils\wt_risk_control.py:389:66: error: Incompatible default for parameter
"sector_map" (default has type "None", parameter has type "dict[Any, Any]") 
[assignment]
    ...                     positions: Dict, sector_map: Dict = None) -> str:
                                                                ^~~~
utils\wt_risk_control.py:389:66: note: PEP 484 prohibits implicit Optional. Accordingly, mypy has changed its default to no_implicit_optional=True
utils\wt_risk_control.py:389:66: note: Use https://github.com/hauntsaninja/no_implicit_optional to automatically upgrade your codebase
utils\wt_risk_control.py:472:40: error: Incompatible default for parameter
"config" (default has type "None", parameter has type "dict[Any, Any]") 
[assignment]
    def create_risk_control(config: Dict = None) -> RiskControl:
                                           ^~~~
utils\wt_risk_control.py:472:40: note: PEP 484 prohibits implicit Optional. Accordingly, mypy has changed its default to no_implicit_optional=True
utils\wt_risk_control.py:472:40: note: Use https://github.com/hauntsaninja/no_implicit_optional to automatically upgrade your codebase
utils\wt_risk_control.py:496:64: error: Argument 3 to "set_stop_loss" of
"StopLossManager" has incompatible type "float"; expected "int"  [arg-type]
    ...    stop_loss_manager.set_stop_loss(code, pos["avg_cost"], pos["qty"])
                                                                  ^~~~~~~~~~
utils\trading_rules.py:158:36: error: Incompatible types in assignment
(expression has type "float", target has type "int | bool | str")  [assignment]
            rules['price_limit_pct'] = 0.10  # 股指期货 ±10%
                                       ^~~~
utils\trading_rules.py:161:36: error: Incompatible types in assignment
(expression has type "float", target has type "int | bool | str")  [assignment]
            rules['price_limit_pct'] = 0.0  # 期权无涨跌停
                                       ^~~
utils\trading_rules.py:165:36: error: Incompatible types in assignment
(expression has type "float", target has type "int | bool | str")  [assignment]
            rules['price_limit_pct'] = 0.20 if code_clean.startswith('68')...
                                       ^~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~...
utils\trading_rules.py:168:36: error: Incompatible types in assignment
(expression has type "float", target has type "int | bool | str")  [assignment]
            rules['price_limit_pct'] = 0.20  # 创业板
                                       ^~~~
utils\trading_rules.py:171:36: error: Incompatible types in assignment
(expression has type "float", target has type "int | bool | str")  [assignment]
            rules['price_limit_pct'] = 0.10  # 主板
                                       ^~~~
utils\trade_calendar.py:45: error: Unused "type: ignore" comment 
[unused-ignore]
            import akshare as ak  # type: ignore
            ^~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
utils\wt_execution_algo.py:192:35: error: Incompatible types in assignment
(expression has type "float", variable has type "int")  [assignment]
                avg_execution_price = total_amount / total_executed
                                      ^~~~~~~~~~~~~~~~~~~~~~~~~~~~~
utils\wt_execution_algo.py:384:17: error: Unsupported operand types for +
("object" and "int")  [operator]
                    orders[-1]["qty"] += remaining
                    ^
utils\wt_execution_algo.py:385:46: error: Unsupported operand types for *
("object" and "float")  [operator]
                    orders[-1]["amount"] = round(orders[-1]["qty"] * ref_p...
                                                 ^
utils\wt_execution_algo.py:407:31: error: Incompatible types in assignment
(expression has type "None", variable has type "TransactionCostModel") 
[assignment]
                self.cost_model = None
                                  ^~~~
ms_strategy\src\execution\broker_api.py:64:9: note: By default the bodies of untyped functions are not checked, consider using --check-untyped-defs  [annotation-unchecked]
ms_strategy\src\execution\broker_api.py:65:9: note: By default the bodies of untyped functions are not checked, consider using --check-untyped-defs  [annotation-unchecked]
ms_strategy\src\execution\broker_api.py:66:9: note: By default the bodies of untyped functions are not checked, consider using --check-untyped-defs  [annotation-unchecked]
ms_strategy\src\execution\broker_api.py:67:9: note: By default the bodies of untyped functions are not checked, consider using --check-untyped-defs  [annotation-unchecked]
utils\v10_config_loader.py:63:9: error: Returning Any from function declared to
return "dict[str, float]"  [no-any-return]
            return meta.get("allocation", {})
            ^~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
utils\v10_config_loader.py:68:9: error: Returning Any from function declared to
return "dict[str, Any]"  [no-any-return]
            return cfg.get("meta", {}).get("hedge_fund_standard", {})
            ^~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
utils\v10_config_loader.py:73:9: error: Returning Any from function declared to
return "list[dict[Any, Any]]"  [no-any-return]
            return cfg.get("stock_long_account", {}).get("positions", [])
            ^~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
utils\v10_config_loader.py:78:9: error: Returning Any from function declared to
return "list[dict[Any, Any]]"  [no-any-return]
            return cfg.get("etf_account", {}).get("positions", [])
            ^~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
utils\v10_config_loader.py:83:9: error: Returning Any from function declared to
return "dict[Any, Any]"  [no-any-return]
            return cfg.get("macro_hedge_account", {})
            ^~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
utils\v10_config_loader.py:88:9: error: Returning Any from function declared to
return "dict[Any, Any]"  [no-any-return]
            return cfg.get("quant_neutral_account", {})
            ^~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
utils\v10_config_loader.py:93:9: error: Returning Any from function declared to
return "dict[Any, Any]"  [no-any-return]
            return cfg.get("options_account", {})
            ^~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
utils\v10_config_loader.py:98:9: error: Returning Any from function declared to
return "dict[Any, Any]"  [no-any-return]
            return cfg.get("cash_management", {})
            ^~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
utils\v10_config_loader.py:142:9: error: Returning Any from function declared
to return "dict[str, dict[Any, Any]]"  [no-any-return]
            return cfg.get("daily_schedule", {})
            ^~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
utils\v10_config_loader.py:147:9: error: Returning Any from function declared
to return "dict[str, Any]"  [no-any-return]
            return cfg.get("risk_automation", {})
            ^~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
utils\v10_config_loader.py:152:9: error: Returning Any from function declared
to return "dict[str, Any]"  [no-any-return]
            return cfg.get("dynamic_rebalance", {})
            ^~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
utils\v10_config_loader.py:157:9: error: Returning Any from function declared
to return "dict[str, Any]"  [no-any-return]
            return risk.get("drawdown_control", {})
            ^~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
utils\v10_config_loader.py:162:9: error: Returning Any from function declared
to return "dict[str, Any]"  [no-any-return]
            return risk.get("var_monitoring", {})
            ^~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
utils\v10_config_loader.py:167:9: error: Returning Any from function declared
to return "dict[str, Any]"  [no-any-return]
            return risk.get("concentration_limits", {})
            ^~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
utils\v10_config_loader.py:172:9: error: Returning Any from function declared
to return "dict[str, Any]"  [no-any-return]
            return risk.get("stress_test_scenarios", {})
            ^~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
utils\v10_config_loader.py:177:9: error: Returning Any from function declared
to return "dict[str, Any]"  [no-any-return]
            return risk.get("early_warning_signals", {})
            ^~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
utils\trade_plan_validator.py:468:5: error: Name "sys" is not defined 
[name-defined]
        sys.exit_code = main()
        ^~~
utils\tdx_data_source.py:54:55: error: Incompatible types in assignment
(expression has type "str", target has type "bool | None")  [assignment]
                self.source_health['tdx']['last_error'] = str(e)
                                                          ^~~~~~
utils\tdx_data_source.py:87:69: error: Incompatible types in assignment
(expression has type "str", target has type "bool | None")  [assignment]
    ...     self.source_health['tdx']['last_success'] = datetime.now().isofor...
                                                        ^~~~~~~~~~~~~~~~~~~~~...
utils\tdx_data_source.py:100:55: error: Incompatible types in assignment
(expression has type "str", target has type "bool | None")  [assignment]
                self.source_health['tdx']['last_error'] = str(e)
                                                          ^~~~~~
utils\tdx_data_source.py:173:22: error: Item "None" of "Any | None" has no
attribute "get_security_quotes"  [union-attr]
                quotes = self._api.get_security_quotes([(market, code)])
                         ^~~~~~~~~~~~~~~~~~~~~~~~~~~~~
utils\tdx_data_source.py:181:30: error: Item "None" of "Any | None" has no
attribute "get_security_info"  [union-attr]
                    stock_info = self._api.get_security_info(market, code)
                                 ^~~~~~~~~~~~~~~~~~~~~~~~~~~
utils\tdx_data_source.py:200:57: error: Incompatible types in assignment
(expression has type "str", target has type "bool | None")  [assignment]
    ...     self.source_health['tdx']['last_success'] = datetime.now().isofor...
                                                        ^~~~~~~~~~~~~~~~~~~~~...
utils\tdx_data_source.py:205:55: error: Incompatible types in assignment
(expression has type "str", target has type "bool | None")  [assignment]
                self.source_health['tdx']['last_error'] = str(e)
                                                          ^~~~~~
utils\tdx_data_source.py:240:22: error: Item "None" of "Any | None" has no
attribute "get_security_bars"  [union-attr]
                klines = self._api.get_security_bars(tdx_period, market, c...
                         ^~~~~~~~~~~~~~~~~~~~~~~~~~~
utils\tdx_data_source.py:264:57: error: Incompatible types in assignment
(expression has type "str", target has type "bool | None")  [assignment]
    ...     self.source_health['tdx']['last_success'] = datetime.now().isofor...
                                                        ^~~~~~~~~~~~~~~~~~~~~...
utils\tdx_data_source.py:269:55: error: Incompatible types in assignment
(expression has type "str", target has type "bool | None")  [assignment]
                self.source_health['tdx']['last_error'] = str(e)
                                                          ^~~~~~
utils\tdx_data_source.py:286:23: error: Item "None" of "Any | None" has no
attribute "get_finance_info"  [union-attr]
                finance = self._api.get_finance_info(market, code)
                          ^~~~~~~~~~~~~~~~~~~~~~~~~~
utils\tdx_data_source.py:302:55: error: Incompatible types in assignment
(expression has type "str", target has type "bool | None")  [assignment]
                self.source_health['tdx']['last_error'] = str(e)
                                                          ^~~~~~
utils\tca_pre_trade_estimator.py:279:18: error: Argument "tier" to
"PreTradeEstimate" has incompatible type "float | str"; expected "str" 
[arg-type]
                tier=tier,
                     ^~~~
utils\tca_post_trade_attribution.py:514:9: error: Returning Any from function
declared to return "float | None"  [no-any-return]
            return new_threshold
            ^~~~~~~~~~~~~~~~~~~~
utils\stop_loss.py:115:74: error: Unsupported operand types for / ("None" and
"int")  [operator]
    ...sl if safe_sl is not None else (safe_base * (1 + safe_float(stop_loss_...
                                                        ^
utils\stop_loss.py:115:74: note: Left operand is of type "float | None"
utils\stop_loss.py:116:74: error: Unsupported operand types for / ("None" and
"int")  [operator]
    ...tp if safe_tp is not None else (safe_base * (1 + safe_float(take_profi...
                                                        ^
utils\stop_loss.py:116:74: note: Left operand is of type "float | None"
utils\protective_put_engine.py:116:47: error: Incompatible default for
parameter "total_capital" (default has type "None", parameter has type "float") 
[assignment]
        def __init__(self, total_capital: float = None):
                                                  ^~~~
utils\protective_put_engine.py:116:47: note: PEP 484 prohibits implicit Optional. Accordingly, mypy has changed its default to no_implicit_optional=True
utils\protective_put_engine.py:116:47: note: Use https://github.com/hauntsaninja/no_implicit_optional to automatically upgrade your codebase
utils\protective_put_engine.py:118:34: error: Incompatible types in assignment
(expression has type "float", variable has type "int")  [assignment]
                self.TOTAL_CAPITAL = total_capital
                                     ^~~~~~~~~~~~~
utils\protective_put_engine.py:167:17: error: Returning Any from function
declared to return "float"  [no-any-return]
                    return pos.get("est_price", 0)
                    ^~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
utils\protective_put_engine.py:193:9: error: Returning Any from function
declared to return "float"  [no-any-return]
            return max(put_price, 0.0001)  # 最低价
            ^~~~~~~~~~~~~~~~~~~~~~~~~~~~~
utils\protective_put_engine.py:282:45: error: Argument 1 to
"_get_etf_spot_price" of "ProtectivePutEngine" has incompatible type "object";
expected "str"  [arg-type]
                spot = self._get_etf_spot_price(code)
                                                ^~~~
utils\protective_put_engine.py:291:29: error: Unsupported operand types for *
("object" and "float")  [operator]
                contracts = int(target["contracts"] * contract_multiplier)
                                ^
utils\protective_put_engine.py:397:76: error: Incompatible default for
parameter "actual_premium" (default has type "None", parameter has type "float")
 [assignment]
    ...ord_execution(self, orders: List[Dict], actual_premium: float = None):
                                                                       ^~~~
utils\protective_put_engine.py:397:76: note: PEP 484 prohibits implicit Optional. Accordingly, mypy has changed its default to no_implicit_optional=True
utils\protective_put_engine.py:397:76: note: Use https://github.com/hauntsaninja/no_implicit_optional to automatically upgrade your codebase
utils\multi_strategy_coordinator.py:411:33: error: Incompatible types in
assignment (expression has type "Any | None", variable has type "str") 
[assignment]
                        direction = signal.get("direction", signal.get("ac...
                                    ^~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~...
utils\multi_strategy_coordinator.py:614:26: error: Argument "strategy_pnl" to
"coordinate" of "MultiStrategyCoordinator" has incompatible type
"dict[str, int]"; expected "dict[str, float] | None"  [arg-type]
                strategy_pnl=pnl,
                             ^~~
utils\multi_strategy_coordinator.py:614:26: note: "dict" is invariant -- see https://mypy.readthedocs.io/en/stable/common_issues.html#variance
utils\multi_strategy_coordinator.py:614:26: note: Consider using "Mapping" instead, which is covariant in the value type
utils\lgb_signal_monitor.py:210:23: error: Need type annotation for
"multiplier_dist"  [var-annotated]
        multiplier_dist = Counter()
                          ^~~~~~~~~
utils\lgb_signal_monitor.py:244:24: error: Need type annotation for
"per_symbol_stats"  [var-annotated]
        per_symbol_stats = defaultdict(lambda: {"boost": 0, "cut": 0, "neu...
                           ^~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~...
utils\lgb_signal_monitor.py:252:22: error: Need type annotation for
"per_date_stats"  [var-annotated]
        per_date_stats = defaultdict(lambda: {"boost": 0, "cut": 0, "neutr...
                         ^~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~...
utils\lgb_signal_monitor.py:376:49: error: Argument "key" to "max" has
incompatible type overloaded function; expected
"Callable[[float], SupportsDunderLT[Any] | SupportsDunderGT[Any]]"  [arg-type]
                max_mult = max(multiplier_dist, key=multiplier_dist.get)
                                                    ^~~~~~~~~~~~~~~~~~~
utils\futures_rollover_manager.py:332:52: error: Argument 2 to
"get_active_contract" of "FuturesRolloverManager" has incompatible type
"str | None"; expected "str"  [arg-type]
            active = self.get_active_contract(product, exchange)
                                                       ^~~~~~~~
utils\external_data_source.py:584:13: error: Returning Any from function
declared to return "dict[str, Any]"  [no-any-return]
                return cached
                ^~~~~~~~~~~~~
utils\external_data_source.py:597:43: error: Incompatible types in assignment
(expression has type "dict[str, float]", target has type
"dict[str, dict[Any, Any]]")  [assignment]
                snapshot["treasury_yields"] = treasury_yields
                                              ^~~~~~~~~~~~~~~
utils\external_data_source.py:620:13: error: Returning Any from function
declared to return "dict[Any, Any] | None"  [no-any-return]
                return cached
                ^~~~~~~~~~~~~
utils\external_data_source.py:641:13: error: Returning Any from function
declared to return "dict[Any, Any] | None"  [no-any-return]
                return cached
                ^~~~~~~~~~~~~
utils\external_data_source.py:653:13: error: Returning Any from function
declared to return "list[dict[Any, Any]]"  [no-any-return]
                return cached
                ^~~~~~~~~~~~~
utils\external_data_source.py:674:21: error: Need type annotation for
"sentiment"  [var-annotated]
            sentiment = {
                        ^
utils\execution_algo_engine.py:367:25: error: Incompatible types in assignment
(expression has type "datetime", variable has type "int")  [assignment]
                slice_end = current_start + timedelta(minutes=slice_minute...
                            ^~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
utils\execution_algo_engine.py:371:29: error: Incompatible types in assignment
(expression has type "datetime", variable has type "int")  [assignment]
                    slice_end = current_start + timedelta(minutes=slice_mi...
                                ^~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~...
utils\execution_algo_engine.py:381:26: error: "int" has no attribute "strftime"
 [attr-defined]
                    end_time=slice_end.strftime("%H:%M"),
                             ^~~~~~~~~~~~~~~~~~
utils\execution_algo_engine.py:387:29: error: Incompatible types in assignment
(expression has type "int", variable has type "datetime")  [assignment]
                current_start = slice_end
                                ^~~~~~~~~
utils\execution_algo_engine.py:611:22: error: Incompatible types in assignment
(expression has type "float", variable has type "int")  [assignment]
                prev_x = x_i
                         ^~~
utils\config_manager.py:41: error: Unused "type: ignore" comment 
[unused-ignore]
    import yaml  # type: ignore[import-untyped]  # PyYAML 无官方类型存根, 静态检查忽略
    ^~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
utils\akshare_data_source.py:59:61: error: Incompatible types in assignment
(expression has type "str", target has type "bool | None")  [assignment]
    ... self.source_health['akshare']['last_success'] = datetime.now().isofor...
                                                        ^~~~~~~~~~~~~~~~~~~~~...
utils\akshare_data_source.py:62:59: error: Incompatible types in assignment
(expression has type "str", target has type "bool | None")  [assignment]
    ...          self.source_health['akshare']['last_error'] = f"模块导入失败: {e}"
                                                               ^~~~~~~~~~~~~~
utils\akshare_data_source.py:65:59: error: Incompatible types in assignment
(expression has type "str", target has type "bool | None")  [assignment]
                self.source_health['akshare']['last_error'] = str(e)
                                                              ^~~~~~
utils\akshare_data_source.py:117:18: error: Item "None" of "Any | None" has no
attribute "stock_zh_a_spot_em"  [union-attr]
                df = self._ak.stock_zh_a_spot_em()
                     ^~~~~~~~~~~~~~~~~~~~~~~~~~~
utils\akshare_data_source.py:124:41: error: Incompatible types in assignment
(expression has type "float", variable has type "int")  [assignment]
                    self._spot_cache_time = now
                                            ^~~
utils\akshare_data_source.py:160:61: error: Incompatible types in assignment
(expression has type "str", target has type "bool | None")  [assignment]
    ... self.source_health['akshare']['last_success'] = datetime.now().isofor...
                                                        ^~~~~~~~~~~~~~~~~~~~~...
utils\akshare_data_source.py:178:59: error: Incompatible types in assignment
(expression has type "str", target has type "bool | None")  [assignment]
                self.source_health['akshare']['last_error'] = str(e)
                                                              ^~~~~~
utils\akshare_data_source.py:212:22: error: Item "None" of "Any | None" has no
attribute "stock_zh_a_hist"  [union-attr]
                    df = self._ak.stock_zh_a_hist(
                         ^~~~~~~~~~~~~~~~~~~~~~~~
utils\akshare_data_source.py:220:22: error: Item "None" of "Any | None" has no
attribute "stock_zh_a_minute"  [union-attr]
                    df = self._ak.stock_zh_a_minute(
                         ^~~~~~~~~~~~~~~~~~~~~~~~~~
utils\akshare_data_source.py:288:61: error: Incompatible types in assignment
(expression has type "str", target has type "bool | None")  [assignment]
    ... self.source_health['akshare']['last_success'] = datetime.now().isofor...
                                                        ^~~~~~~~~~~~~~~~~~~~~...
utils\akshare_data_source.py:294:59: error: Incompatible types in assignment
(expression has type "str", target has type "bool | None")  [assignment]
                self.source_health['akshare']['last_error'] = str(e)
                                                              ^~~~~~
utils\akshare_data_source.py:309:18: error: Item "None" of "Any | None" has no
attribute "stock_financial_report_sina"  [union-attr]
                df = self._ak.stock_financial_report_sina(stock=code)
                     ^~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
utils\execution\broker_adapters.py:42: error: Unused "type: ignore" comment 
[unused-ignore]
        from v8_3_institutional.src.bridges.broker_adapter import (  # typ...
        ^~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~...
utils\execution\broker_adapters.py:54: error: Unused "type: ignore" comment 
[unused-ignore]
            from src.bridges.broker_adapter import (  # type: ignore
            ^~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
utils\execution\broker_adapters.py:61: error: Unused "type: ignore" comment 
[unused-ignore]
            BrokerAdapter = object  # type: ignore
            ^~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
utils\execution\broker_adapters.py:62: error: Unused "type: ignore" comment 
[unused-ignore]
            BrokerOrder = None  # type: ignore
            ^~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
utils\execution\broker_adapters.py:63: error: Unused "type: ignore" comment 
[unused-ignore]
            OrderSide = None  # type: ignore
            ^~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
utils\execution\broker_adapters.py:64: error: Unused "type: ignore" comment 
[unused-ignore]
            OrderStatus = None  # type: ignore
            ^~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
utils\execution\broker_adapters.py:65: error: Unused "type: ignore" comment 
[unused-ignore]
            OrderType = None  # type: ignore
            ^~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
utils\execution\broker_adapters.py:82: error: Unused "type: ignore" comment 
[unused-ignore]
    class _BaseLiveAdapter(BrokerAdapter):  # type: ignore[misc]
    ^~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
utils\execution\broker_adapters.py:147: error: Unused "type: ignore" comment 
[unused-ignore]
        def submit_order(self, order: BrokerOrder) -> bool:  # type: ignor...
        ^~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~...
utils\execution\broker_adapters.py:158: error: Unused "type: ignore" comment 
[unused-ignore]
                order.status = OrderStatus.SUBMITTED  # type: ignore[union...
                ^~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~...
utils\execution\broker_adapters.py:178: error: Unused "type: ignore" comment 
[unused-ignore]
                order.status = OrderStatus.ERROR  # type: ignore[union-att...
                ^~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
utils\execution\broker_adapters.py:186: error: Unused "type: ignore" comment 
[unused-ignore]
        def cancel_order(self, order_id: str) -> bool:  # type: ignore[ove...
        ^~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~...
utils\execution\broker_adapters.py:205: error: Unused "type: ignore" comment 
[unused-ignore]
        def get_positions(self) -> List[Dict]:  # type: ignore[override]
        ^~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
utils\execution\broker_adapters.py:216: error: Unused "type: ignore" comment 
[unused-ignore]
        def get_account_info(self) -> Dict:  # type: ignore[override]
        ^~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
utils\execution\broker_adapters.py:235: error: Unused "type: ignore" comment 
[unused-ignore]
                            count: int = 100) -> Dict:  # type: ignore[ove...
                            ^~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~...
utils\execution\broker_adapters.py:258: error: Unused "type: ignore" comment 
[unused-ignore]
                order.status = OrderStatus.REJECTED  # type: ignore[union-...
                ^~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~...
utils\execution\broker_adapters.py:273: error: Unused "type: ignore" comment 
[unused-ignore]
                order.status = OrderStatus.REJECTED  # type: ignore[union-...
                ^~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~...
utils\execution\broker_adapters.py:439: error: Unused "type: ignore" comment 
[unused-ignore]
                order.status = OrderStatus.SUBMITTED  # type: ignore[union...
                ^~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~...
utils\execution\broker_adapters.py:448: error: Unused "type: ignore" comment 
[unused-ignore]
                order.status = OrderStatus.SUBMITTED  # type: ignore[union...
                ^~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~...
utils\execution\broker_adapters.py:450: error: Unused "type: ignore" comment 
[unused-ignore]
            order.status = OrderStatus.REJECTED  # type: ignore[union-attr...
            ^~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
utils\execution\broker_adapters.py:557: error: Unused "type: ignore" comment 
[unused-ignore]
                order.status = OrderStatus.REJECTED  # type: ignore[union-...
                ^~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~...
utils\execution\broker_adapters.py:568: error: Unused "type: ignore" comment 
[unused-ignore]
                order.status = OrderStatus.SUBMITTED  # type: ignore[union...
                ^~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~...
utils\execution\broker_adapters.py:573: error: Unused "type: ignore" comment 
[unused-ignore]
                    order.status = OrderStatus.REJECTED  # type: ignore[un...
                    ^~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~...
utils\execution\broker_adapters.py:582: error: Unused "type: ignore" comment 
[unused-ignore]
                order.status = OrderStatus.SUBMITTED  # type: ignore[union...
                ^~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~...
utils\execution\broker_adapters.py:584: error: Unused "type: ignore" comment 
[unused-ignore]
            order.status = OrderStatus.REJECTED  # type: ignore[union-attr...
            ^~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
utils\alpha\model_registry.py:407:17: error: "None" has no attribute
"transition_model_version_stage"  [attr-defined]
                    self._mlflow_client.transition_model_version_stage(
                    ^~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
utils\alpha\model_registry.py:531:22: error: Incompatible types in assignment
(expression has type "ModelVersion | None", variable has type "ModelVersion") 
[assignment]
                target = self.get_production_version(name) if target_stage...
                         ^~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~...
utils\alpha\model_registry.py:621:13: error: Unsupported target for indexed
assignment ("object")  [index]
                result["models"][name] = {
                ^~~~~~~~~~~~~~~~~~~~~~
utils\alpha\drift_monitor.py:49: error: Unused "type: ignore" comment 
[unused-ignore]
        from src.ml.drift_detector import (  # type: ignore
        ^~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
utils\alpha\drift_monitor.py:59: error: Unused "type: ignore" comment 
[unused-ignore]
            from src.ml.drift_detector import (  # type: ignore
            ^~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
utils\alpha\drift_monitor.py:70: error: Unused "type: ignore" comment 
[unused-ignore]
            ADWINDetector = None  # type: ignore
            ^~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
utils\alpha\drift_monitor.py:71: error: Unused "type: ignore" comment 
[unused-ignore]
            ModelDriftDetector = None  # type: ignore
            ^~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
utils\alpha\drift_monitor.py:72: error: Unused "type: ignore" comment 
[unused-ignore]
            DriftAlert = None  # type: ignore
            ^~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
utils\alpha\drift_monitor.py:73: error: Unused "type: ignore" comment 
[unused-ignore]
            DriftType = None  # type: ignore
            ^~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
utils\alpha\drift_monitor.py:74: error: Unused "type: ignore" comment 
[unused-ignore]
            Severity = None  # type: ignore
            ^~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
utils\alpha\drift_monitor.py:190:13: error: Returning Any from function
declared to return "list[Any]"  [no-any-return]
                return alerts
                ^~~~~~~~~~~~~
utils\alpha\drift_monitor.py:205:13: error: Returning Any from function
declared to return "list[Any]"  [no-any-return]
                return alerts
                ^~~~~~~~~~~~~
utils\alpha\drift_monitor.py:256:28: error: Item "None" of "Any | None" has no
attribute "value"  [union-attr]
                severity_val = severity.value if hasattr(severity, "value"...
                               ^~~~~~~~~~~~~~
utils\alpha\drift_monitor.py:362:13: error: Returning Any from function
declared to return "dict[str, Any]"  [no-any-return]
                return report
                ^~~~~~~~~~~~~
utils\wt_backtest_engine.py:33:9: error: Need type annotation for "positions"
(hint: "positions: dict[<type>, <type>] = ...")  [var-annotated]
            self.positions = {}
            ^~~~~~~~~~~~~~
utils\wt_backtest_engine.py:34:9: error: Need type annotation for "trades"
(hint: "trades: list[<type>] = ...")  [var-annotated]
            self.trades = []
            ^~~~~~~~~~~
utils\wt_backtest_engine.py:35:9: error: Need type annotation for "daily_pnl"
(hint: "daily_pnl: list[<type>] = ...")  [var-annotated]
            self.daily_pnl = []
            ^~~~~~~~~~~~~~
utils\wt_backtest_engine.py:36:9: error: Need type annotation for
"equity_curve" (hint: "equity_curve: list[<type>] = ...")  [var-annotated]
            self.equity_curve = []
            ^~~~~~~~~~~~~~~~~
utils\wt_backtest_engine.py:141:9: error: Returning Any from function declared
to return "float"  [no-any-return]
            return self.cash + position_value
            ^~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
utils\wt_backtest_engine.py:207:35: error: Argument 1 to "record_daily_pnl" of
"BacktestEngine" has incompatible type "None"; expected "str"  [arg-type]
                self.record_daily_pnl(self.current_date)
                                      ^~~~~~~~~~~~~~~~~
utils\wt_backtest_engine.py:282:50: error: Incompatible default for parameter
"signal_thresholds" (default has type "None", parameter has type
"dict[Any, Any]")  [assignment]
        def __init__(self, signal_thresholds: Dict = None, max_position_pc...
                                                     ^~~~
utils\wt_backtest_engine.py:282:50: note: PEP 484 prohibits implicit Optional. Accordingly, mypy has changed its default to no_implicit_optional=True
utils\wt_backtest_engine.py:282:50: note: Use https://github.com/hauntsaninja/no_implicit_optional to automatically upgrade your codebase
utils\wt_backtest_engine.py:350:86: error: Incompatible default for parameter
"tickers" (default has type "None", parameter has type "list[str]") 
[assignment]
    ...(positions_history_dir: str, tickers: List[str] = None) -> List[Dict]:
                                                         ^~~~
utils\wt_backtest_engine.py:350:86: note: PEP 484 prohibits implicit Optional. Accordingly, mypy has changed its default to no_implicit_optional=True
utils\wt_backtest_engine.py:350:86: note: Use https://github.com/hauntsaninja/no_implicit_optional to automatically upgrade your codebase
utils\wt_backtest_engine.py:388:25: error: "type[BacktestDataLoader]" has no
attribute "_warned_est_price"  [attr-defined]
                            BacktestDataLoader._warned_est_price = True
                            ^~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
utils\directional_futures_trader.py:188:22: error: Argument "name" to
"FuturesSignal" has incompatible type "object"; expected "str"  [arg-type]
                    name=spec["name"],
                         ^~~~~~~~~~~~
utils\directional_futures_trader.py:336:47: error: Unsupported operand types
for * ("float" and "object")  [operator]
            one_contract_margin = current_price * multiplier * margin_rate
                                                  ^~~~~~~~~~
utils\directional_futures_trader.py:342:48: error: Unsupported operand types
for * ("float" and "object")  [operator]
            notional = contracts * current_price * multiplier
                                                   ^~~~~~~~~~
utils\directional_futures_trader.py:343:29: error: Unsupported operand types
for * ("float" and "object")  [operator]
            margin = notional * margin_rate
                                ^~~~~~~~~~~
utils\directional_futures_trader.py:460:22: error: Argument "name" to
"FuturesOrder" has incompatible type "object"; expected "str"  [arg-type]
                    name=spec["name"],
                         ^~~~~~~~~~~~
utils\directional_futures_trader.py:461:26: error: Argument "exchange" to
"FuturesOrder" has incompatible type "object"; expected "str"  [arg-type]
                    exchange=spec["exchange"],
                             ^~~~~~~~~~~~~~~~
utils\directional_futures_trader.py:489:18: error: Argument "name" to
"FuturesOrder" has incompatible type "object"; expected "str"  [arg-type]
                name=spec["name"],
                     ^~~~~~~~~~~~
utils\directional_futures_trader.py:490:22: error: Argument "exchange" to
"FuturesOrder" has incompatible type "object"; expected "str"  [arg-type]
                exchange=spec["exchange"],
                         ^~~~~~~~~~~~~~~~
utils\directional_futures_trader.py:696:27: error: Argument 1 to "append" of
"list" has incompatible type "float"; expected "int"  [arg-type]
                closes.append(closes[-1] * (1 + random.uniform(-0.02, 0.02...
                              ^~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
utils\directional_futures_trader.py:704:16: error: Argument "prices" to "run"
of "DirectionalFuturesTrader" has incompatible type "dict[str, int]"; expected
"dict[str, float]"  [arg-type]
            prices=prices,
                   ^~~~~~
utils\directional_futures_trader.py:704:16: note: "dict" is invariant -- see https://mypy.readthedocs.io/en/stable/common_issues.html#variance
utils\directional_futures_trader.py:704:16: note: Consider using "Mapping" instead, which is covariant in the value type
ms_strategy\src\execution\smart_order_router.py:191:9: error: Need type
annotation for "slip_per_symbol"  [var-annotated]
            self.slip_per_symbol = defaultdict(float)
            ^~~~~~~~~~~~~~~~~~~~
ms_strategy\src\execution\smart_order_router.py:273:27: error: "BrokerAPI" has
no attribute "get_account_info"  [attr-defined]
                    account = self.broker.get_account_info()
                              ^~~~~~~~~~~~~~~~~~~~~~~~~~~~
ms_strategy\src\execution\smart_order_router.py:300:32: error: Argument 1 to
"float" has incompatible type "Any | None"; expected
"str | Buffer | SupportsFloat | SupportsIndex"  [arg-type]
                fill_price = float(fill.get("price", limit_price))
                                   ^~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
ms_strategy\src\execution\smart_order_router.py:373:30: error: Item "None" of
"Match[str] | None" has no attribute "group"  [union-attr]
            opt_type = "CALL" if match.group(2) == "C" else "PUT"
                                 ^~~~~~~~~~~
ms_strategy\src\execution\smart_order_router.py:387:23: error: "BrokerAPI" has
no attribute "get_account_info"  [attr-defined]
                account = self.broker.get_account_info()
                          ^~~~~~~~~~~~~~~~~~~~~~~~~~~~
ms_strategy\src\execution\smart_order_router.py:396:26: error: Item "None" of
"Match[str] | None" has no attribute "group"  [union-attr]
                strike_str = match.group(5)[1:]
                             ^~~~~~~~~~~
ms_strategy\src\execution\qmt_broker.py:279:24: error: Item "None" of
"Any | None" has no attribute "orderStock"  [union-attr]
                order_id = self._xt_trader.orderStock(
                           ^~~~~~~~~~~~~~~~~~~~~~~~~~
ms_strategy\src\execution\qmt_broker.py:324:22: error: Item "None" of
"Any | None" has no attribute "cancelOrder"  [union-attr]
                result = self._xt_trader.cancelOrder(
                         ^~~~~~~~~~~~~~~~~~~~~~~~~~~
ms_strategy\src\execution\qmt_broker.py:388:26: error: Item "None" of
"Any | None" has no attribute "queryOrder"  [union-attr]
                all_orders = self._xt_trader.queryOrder(self.account_id)
                             ^~~~~~~~~~~~~~~~~~~~~~~~~~
ms_strategy\src\execution\qmt_broker.py:455:25: error: Item "None" of
"Any | None" has no attribute "queryAsset"  [union-attr]
                    asset = self._xt_trader.queryAsset(self.account_id)
                            ^~~~~~~~~~~~~~~~~~~~~~~~~~
ms_strategy\src\execution\qmt_broker.py:475:29: error: Item "None" of
"Any | None" has no attribute "queryPosition"  [union-attr]
                    positions = self._xt_trader.queryPosition(self.account...
                                ^~~~~~~~~~~~~~~~~~~~~~~~~~~~~
utils\logger.py:75:25: error: Incompatible default for parameter "log_file"
(default has type "None", parameter has type "str")  [assignment]
            log_file: str = None,
                            ^~~~
utils\logger.py:75:25: note: PEP 484 prohibits implicit Optional. Accordingly, mypy has changed its default to no_implicit_optional=True
utils\logger.py:75:25: note: Use https://github.com/hauntsaninja/no_implicit_optional to automatically upgrade your codebase
utils\cash_manager.py:54:5: error: Cannot assign to a type  [misc]
        V10ConfigLoader = None
        ^~~~~~~~~~~~~~~
utils\cash_manager.py:54:23: error: Incompatible types in assignment
(expression has type "None", variable has type "type[V10ConfigLoader]") 
[assignment]
        V10ConfigLoader = None
                          ^~~~
utils\phase_manager.py:471:13: error: Dict entry 0 has incompatible type "str":
"str"; expected "str": "float"  [dict-item]
                "phase": phase.phase_name,
                ^~~~~~~~~~~~~~~~~~~~~~~~~
utils\phase_manager.py:712:43: error: Item "None" of "dict[Any, Any] | None"
has no attribute "get"  [union-attr]
                print(f"\n2030 清仓动作 ({actions.get('period', '')}):")
                                              ^~~~~~~~~~~
utils\phase_manager.py:713:32: error: Item "None" of "dict[Any, Any] | None"
has no attribute "get"  [union-attr]
                print(f"  名称: {actions.get('name', '')}")
                                   ^~~~~~~~~~~
utils\phase_manager.py:715:27: error: Item "None" of "dict[Any, Any] | None"
has no attribute "get"  [union-attr]
                for action in actions.get("actions", []):
                              ^~~~~~~~~~~
utils\overnight_gap_monitor.py:77:37: error: Incompatible default for parameter
"sp500_l2_threshold" (default has type "None", parameter has type "float") 
[assignment]
            sp500_l2_threshold: float = None,
                                        ^~~~
utils\overnight_gap_monitor.py:77:37: note: PEP 484 prohibits implicit Optional. Accordingly, mypy has changed its default to no_implicit_optional=True
utils\overnight_gap_monitor.py:77:37: note: Use https://github.com/hauntsaninja/no_implicit_optional to automatically upgrade your codebase
utils\overnight_gap_monitor.py:78:37: error: Incompatible default for parameter
"sp500_l3_threshold" (default has type "None", parameter has type "float") 
[assignment]
            sp500_l3_threshold: float = None,
                                        ^~~~
utils\overnight_gap_monitor.py:78:37: note: PEP 484 prohibits implicit Optional. Accordingly, mypy has changed its default to no_implicit_optional=True
utils\overnight_gap_monitor.py:78:37: note: Use https://github.com/hauntsaninja/no_implicit_optional to automatically upgrade your codebase
utils\overnight_gap_monitor.py:79:35: error: Incompatible default for parameter
"adr_l2_threshold" (default has type "None", parameter has type "float") 
[assignment]
            adr_l2_threshold: float = None,
                                      ^~~~
utils\overnight_gap_monitor.py:79:35: note: PEP 484 prohibits implicit Optional. Accordingly, mypy has changed its default to no_implicit_optional=True
utils\overnight_gap_monitor.py:79:35: note: Use https://github.com/hauntsaninja/no_implicit_optional to automatically upgrade your codebase
utils\overnight_gap_monitor.py:80:35: error: Incompatible default for parameter
"adr_l3_threshold" (default has type "None", parameter has type "float") 
[assignment]
            adr_l3_threshold: float = None,
                                      ^~~~
utils\overnight_gap_monitor.py:80:35: note: PEP 484 prohibits implicit Optional. Accordingly, mypy has changed its default to no_implicit_optional=True
utils\overnight_gap_monitor.py:80:35: note: Use https://github.com/hauntsaninja/no_implicit_optional to automatically upgrade your codebase
utils\overnight_gap_monitor.py:81:34: error: Incompatible default for parameter
"fail_closed_pct" (default has type "None", parameter has type "float") 
[assignment]
            fail_closed_pct: float = None,
                                     ^~~~
utils\overnight_gap_monitor.py:81:34: note: PEP 484 prohibits implicit Optional. Accordingly, mypy has changed its default to no_implicit_optional=True
utils\overnight_gap_monitor.py:81:34: note: Use https://github.com/hauntsaninja/no_implicit_optional to automatically upgrade your codebase
utils\overnight_gap_monitor.py:325:20: error: Incompatible return value type
(got "tuple[float | None, float | None, str]", expected
"tuple[float, float, str]")  [return-value]
                return sp500, adr, "external_data"
                       ^~~~~~~~~~~~~~~~~~~~~~~~~~~
utils\overnight_gap_monitor.py:330:20: error: Incompatible return value type
(got "tuple[float | None, float | None, str]", expected
"tuple[float, float, str]")  [return-value]
                return sp500, adr, "cache"
                       ^~~~~~~~~~~~~~~~~~~
utils\theta_engine.py:79:17: error: Returning Any from function declared to
return "dict[Any, Any]"  [no-any-return]
                    return theta_cfg
                    ^~~~~~~~~~~~~~~~
utils\liquidation_scheduler.py:78:17: error: Returning Any from function
declared to return "dict[Any, Any]"  [no-any-return]
                    return cfg
                    ^~~~~~~~~~
utils\liquidation_scheduler.py:212:12: error: Value of type
"dict[Any, Any] | None" is not indexable  [index]
            if current["phase"] == 0:
               ^~~~~~~~~~~~~~~~
utils\liquidation_scheduler.py:214:25: error: Value of type
"dict[Any, Any] | None" is not indexable  [index]
                days_left = current["days_to_next_phase"]
                            ^~~~~~~~~~~~~~~~~~~~~~~~~~~~~
utils\liquidation_scheduler.py:228:14: error: Value of type
"dict[Any, Any] | None" is not indexable  [index]
            elif current["phase"] in (1, 2):
                 ^~~~~~~~~~~~~~~~
utils\liquidation_scheduler.py:229:25: error: Value of type
"dict[Any, Any] | None" is not indexable  [index]
                days_left = current["days_to_next_phase"]
                            ^~~~~~~~~~~~~~~~~~~~~~~~~~~~~
utils\liquidation_scheduler.py:233:29: error: Value of type
"dict[Any, Any] | None" is not indexable  [index]
                        "type": f"phase_{current['phase']}_ending",
                                ^~~~~~~~~~~~~~~~~~~~~~~~~
utils\liquidation_scheduler.py:235:32: error: Value of type
"dict[Any, Any] | None" is not indexable  [index]
                        "message": f"Phase {current['phase']} 即将结束, 请准备下一阶...
                                   ^~~~~~~~~~~~~~~~~~~~~~~~~
utils\liquidation_scheduler.py:236:37: error: Value of type
"dict[Any, Any] | None" is not indexable  [index]
                        "next_actions": self.get_current_phase()["actions"...
                                        ^~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
utils\liquidation_scheduler.py:280:15: error: Value of type
"dict[Any, Any] | None" is not indexable  [index]
            print(f"Phase: {phase['phase']}")
                  ^~~~~~~~~~~~~~~~~~~~~~~~
utils\liquidation_scheduler.py:281:15: error: Value of type
"dict[Any, Any] | None" is not indexable  [index]
            print(f"名称: {phase['name']}")
                  ^~~~~~~~~~~~~~~~~~~~~~~
utils\liquidation_scheduler.py:282:15: error: Value of type
"dict[Any, Any] | None" is not indexable  [index]
            print(f"周期: {phase['period']}")
                  ^~~~~~~~~~~~~~~~~~~~~~~~~
utils\liquidation_scheduler.py:283:15: error: Value of type
"dict[Any, Any] | None" is not indexable  [index]
            print(f"距下一阶段: {phase['days_to_next_phase']} 天")
                  ^~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
utils\liquidation_scheduler.py:284:12: error: Item "None" of
"dict[Any, Any] | None" has no attribute "get"  [union-attr]
            if phase.get("actions"):
               ^~~~~~~~~
utils\liquidation_scheduler.py:286:22: error: Value of type
"dict[Any, Any] | None" is not indexable  [index]
                for a in phase["actions"]:
                         ^~~~~~~~~~~~~~~~
utils\liquidation_scheduler.py:288:12: error: Item "None" of
"dict[Any, Any] | None" has no attribute "get"  [union-attr]
            if phase.get("target"):
               ^~~~~~~~~
utils\liquidation_scheduler.py:289:19: error: Value of type
"dict[Any, Any] | None" is not indexable  [index]
                print(f"目标: {phase['target']}")
                      ^~~~~~~~~~~~~~~~~~~~~~~~~
utils\kill_switch.py:29: error: Unused "type: ignore" comment  [unused-ignore]
    import yaml  # type: ignore[import-untyped]
    ^~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
utils\kill_switch.py:90: error: Unused "type: ignore" comment  [unused-ignore]
                    return cfg  # type: ignore[no-any-return]
                    ^~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
utils\kill_switch.py:710:13: error: Unsupported operand types for + ("float"
and "None")  [operator]
                total_value += mv
                ^
utils\kill_switch.py:710:13: note: Right operand is of type "Any | None"
utils\kill_switch.py:718:40: error: Argument "key" to "max" has incompatible
type "Callable[[Any], Any | None]"; expected
"Callable[[Any], SupportsDunderLT[Any] | SupportsDunderGT[Any]]"  [arg-type]
            max_code = max(pos_values, key=lambda k: pos_values.get(k, 0.0...
                                           ^
utils\kill_switch.py:718:50: error: Incompatible return value type (got
"Any | None", expected "SupportsDunderLT[Any] | SupportsDunderGT[Any]") 
[return-value]
            max_code = max(pos_values, key=lambda k: pos_values.get(k, 0.0...
                                                     ^~~~~~~~~~~~~~~~~~~~~~
utils\kill_switch.py:719:20: error: Unsupported operand types for / ("None" and
"float")  [operator]
            max_conc = pos_values[max_code] / total_value
                       ^
utils\kill_switch.py:719:20: note: Left operand is of type "Any | None"
utils\gamma_engine.py:74:17: error: Returning Any from function declared to
return "dict[Any, Any]"  [no-any-return]
                    return cfg
                    ^~~~~~~~~~
utils\execution\broker_failover.py:151:9: error: Returning Any from function
declared to return "float"  [no-any-return]
            return sum(self._results) / len(self._results)
            ^~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
utils\alpha\ab_testing.py:188:20: error: "type[ABTestResult]" has no attribute
"from_dict"  [attr-defined]
                result=ABTestResult.from_dict(d["result"]) if d.get("resul...
                       ^~~~~~~~~~~~~~~~~~~~~~
utils\alpha\ab_testing.py:497:9: error: Need type annotation for "all_keys"
(hint: "all_keys: set[<type>] = ...")  [var-annotated]
            all_keys = set()
            ^~~~~~~~
utils\ifind_news_analyzer.py:56: error: Unused "type: ignore" comment 
[unused-ignore]
                from call import call as _call  # type: ignore
                ^~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
utils\astock_realtime.py:58:5: error: Returning Any from function declared to
return "bytes"  [no-any-return]
        return _opener.open(req, timeout=timeout).read()
        ^~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
utils\astock_realtime.py:157:17: error: Returning Any from function declared to
return "dict[str, dict[Any, Any]]"  [no-any-return]
                    return c[1]
                    ^~~~~~~~~~~
utils\ai_report_agent.py:58: error: Unused "type: ignore" comment 
[unused-ignore]
            import llm_client  # type: ignore
            ^~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
utils\ai_report_agent.py:174:13: error: Returning Any from function declared to
return "str | None"  [no-any-return]
                return result
                ^~~~~~~~~~~~~
utils\reporting\daily_report_generator.py:458:25: error: Missing positional
argument "name" in call to "is_enabled" of "FeatureFlags"  [call-arg]
                return bool(FeatureFlags.is_enabled(self._feature_flag_nam...
                            ^~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
utils\reporting\daily_report_generator.py:458:49: error: Argument 1 to
"is_enabled" of "FeatureFlags" has incompatible type "str"; expected
"FeatureFlags"  [arg-type]
                return bool(FeatureFlags.is_enabled(self._feature_flag_nam...
                                                    ^~~~~~~~~~~~~~~~~~~~~~~
utils\reporting\daily_report_generator.py:520:21: error: Missing positional
argument "name" in call to "is_enabled" of "FeatureFlags"  [call-arg]
            return bool(FeatureFlags.is_enabled(FLAG_NAME))
                        ^~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
utils\reporting\daily_report_generator.py:520:45: error: Argument 1 to
"is_enabled" of "FeatureFlags" has incompatible type "str"; expected
"FeatureFlags"  [arg-type]
            return bool(FeatureFlags.is_enabled(FLAG_NAME))
                                                ^~~~~~~~~
utils\attribution\factor_attribution.py:430:13: error: Incompatible types in
assignment (expression has type "str", variable has type "FactorAttribution") 
[assignment]
                for f in self.concentrated_factors:
                ^
utils\attribution\factor_attribution.py:436:13: error: Incompatible types in
assignment (expression has type "str", variable has type "FactorAttribution") 
[assignment]
                for f in self.missing_factors:
                ^
utils\attribution\brinson_attribution.py:687:47: error: Incompatible default
for parameter "portfolio_returns" (default has type "None", parameter has type
"dict[str, float]")  [assignment]
            portfolio_returns: Dict[str, float] = None,
                                                  ^~~~
utils\attribution\brinson_attribution.py:687:47: note: PEP 484 prohibits implicit Optional. Accordingly, mypy has changed its default to no_implicit_optional=True
utils\attribution\brinson_attribution.py:687:47: note: Use https://github.com/hauntsaninja/no_implicit_optional to automatically upgrade your codebase
utils\attribution\brinson_attribution.py:688:47: error: Incompatible default
for parameter "benchmark_returns" (default has type "None", parameter has type
"dict[str, float]")  [assignment]
            benchmark_returns: Dict[str, float] = None,
                                                  ^~~~
utils\attribution\brinson_attribution.py:688:47: note: PEP 484 prohibits implicit Optional. Accordingly, mypy has changed its default to no_implicit_optional=True
utils\attribution\brinson_attribution.py:688:47: note: Use https://github.com/hauntsaninja/no_implicit_optional to automatically upgrade your codebase
utils\alpha\auto_retrain_scheduler.py:189:13: error: Returning Any from
function declared to return "dict[str, Any]"  [no-any-return]
                return mlops_cfg.get("auto_retrain", {})
                ^~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
utils\market_circuit_breaker.py:69:31: error: Incompatible default for
parameter "l2_threshold" (default has type "None", parameter has type "float") 
[assignment]
            l2_threshold: float = None,
                                  ^~~~
utils\market_circuit_breaker.py:69:31: note: PEP 484 prohibits implicit Optional. Accordingly, mypy has changed its default to no_implicit_optional=True
utils\market_circuit_breaker.py:69:31: note: Use https://github.com/hauntsaninja/no_implicit_optional to automatically upgrade your codebase
utils\market_circuit_breaker.py:70:31: error: Incompatible default for
parameter "l3_threshold" (default has type "None", parameter has type "float") 
[assignment]
            l3_threshold: float = None,
                                  ^~~~
utils\market_circuit_breaker.py:70:31: note: PEP 484 prohibits implicit Optional. Accordingly, mypy has changed its default to no_implicit_optional=True
utils\market_circuit_breaker.py:70:31: note: Use https://github.com/hauntsaninja/no_implicit_optional to automatically upgrade your codebase
utils\market_circuit_breaker.py:71:34: error: Incompatible default for
parameter "fail_closed_pct" (default has type "None", parameter has type
"float")  [assignment]
            fail_closed_pct: float = None,
                                     ^~~~
utils\market_circuit_breaker.py:71:34: note: PEP 484 prohibits implicit Optional. Accordingly, mypy has changed its default to no_implicit_optional=True
utils\market_circuit_breaker.py:71:34: note: Use https://github.com/hauntsaninja/no_implicit_optional to automatically upgrade your codebase
utils\market_circuit_breaker.py:235:20: error: Incompatible return value type
(got "tuple[float | None, str]", expected "tuple[float, str]")  [return-value]
                return change_pct, "astock_realtime"
                       ^~~~~~~~~~~~~~~~~~~~~~~~~~~~~
utils\market_circuit_breaker.py:240:20: error: Incompatible return value type
(got "tuple[float | None, str]", expected "tuple[float, str]")  [return-value]
                return change_pct, "akshare"
                       ^~~~~~~~~~~~~~~~~~~~~
utils\attribution\daily_panel.py:880:13: error: Unsupported left operand type
for - ("object")  [operator]
                normalized["total_pnl"]
                ^~~~~~~~~~~~~~~~~~~~~~~
utils\attribution\daily_panel.py:1034:25: error: Missing positional argument
"name" in call to "is_enabled" of "FeatureFlags"  [call-arg]
                return bool(FeatureFlags.is_enabled(self._feature_flag_nam...
                            ^~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
utils\attribution\daily_panel.py:1034:49: error: Argument 1 to "is_enabled" of
"FeatureFlags" has incompatible type "str"; expected "FeatureFlags"  [arg-type]
                return bool(FeatureFlags.is_enabled(self._feature_flag_nam...
                                                    ^~~~~~~~~~~~~~~~~~~~~~~
utils\attribution\daily_panel.py:1044:25: error: Missing positional argument
"name" in call to "is_enabled" of "FeatureFlags"  [call-arg]
                return bool(FeatureFlags.is_enabled(BRINSON_FLAG))
                            ^~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
utils\attribution\daily_panel.py:1044:49: error: Argument 1 to "is_enabled" of
"FeatureFlags" has incompatible type "str"; expected "FeatureFlags"  [arg-type]
                return bool(FeatureFlags.is_enabled(BRINSON_FLAG))
                                                    ^~~~~~~~~~~~
utils\attribution\daily_panel.py:1053:25: error: Missing positional argument
"name" in call to "is_enabled" of "FeatureFlags"  [call-arg]
                return bool(FeatureFlags.is_enabled(FACTOR_FLAG))
                            ^~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
utils\attribution\daily_panel.py:1053:49: error: Argument 1 to "is_enabled" of
"FeatureFlags" has incompatible type "str"; expected "FeatureFlags"  [arg-type]
                return bool(FeatureFlags.is_enabled(FACTOR_FLAG))
                                                    ^~~~~~~~~~~
utils\attribution\daily_panel.py:1061:25: error: Missing positional argument
"name" in call to "is_enabled" of "FeatureFlags"  [call-arg]
                return bool(FeatureFlags.is_enabled("USE_TCA_POST_TRADE_AT...
                            ^~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~...
utils\attribution\daily_panel.py:1061:49: error: Argument 1 to "is_enabled" of
"FeatureFlags" has incompatible type "str"; expected "FeatureFlags"  [arg-type]
                return bool(FeatureFlags.is_enabled("USE_TCA_POST_TRADE_AT...
                                                    ^~~~~~~~~~~~~~~~~~~~~~...
utils\attribution\daily_panel.py:1176:21: error: Missing positional argument
"name" in call to "is_enabled" of "FeatureFlags"  [call-arg]
            return bool(FeatureFlags.is_enabled(FLAG_NAME))
                        ^~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
utils\attribution\daily_panel.py:1176:45: error: Argument 1 to "is_enabled" of
"FeatureFlags" has incompatible type "str"; expected "FeatureFlags"  [arg-type]
            return bool(FeatureFlags.is_enabled(FLAG_NAME))
                                                ^~~~~~~~~
utils\alpha\mlops_pipeline.py:324:17: error: Unsupported target for indexed
assignment ("object")  [index]
                    status["components"]["drift_monitor"] = self._drift_mo...
                    ^~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
utils\alpha\mlops_pipeline.py:329:17: error: Unsupported target for indexed
assignment ("object")  [index]
                    status["components"]["retrain_scheduler"] = self._retr...
                    ^~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
utils\alpha\mlops_pipeline.py:334:17: error: Unsupported target for indexed
assignment ("object")  [index]
                    status["components"]["model_registry"] = {
                    ^~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
utils\alpha\mlops_pipeline.py:341:17: error: Unsupported target for indexed
assignment ("object")  [index]
                    status["components"]["ab_framework"] = {
                    ^~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
utils\ifind_client.py:161:17: error: "Sequence[str]" has no attribute "append" 
[attr-defined]
                    out["datas"].append(d.get("data", {}))
                    ^~~~~~~~~~~~~~~~~~~
utils\ifind_client.py:170:21: error: "Sequence[str]" has no attribute "extend" 
[attr-defined]
                        out["tables"].extend(table)
                        ^~~~~~~~~~~~~~~~~~~~
utils\ifind_client.py:178:25: error: "Sequence[str]" has no attribute "extend" 
[attr-defined]
                            out["tables"].extend([_normalize_row(r) for r ...
                            ^~~~~~~~~~~~~~~~~~~~
utils\ifind_client.py:183:17: error: "Sequence[str]" has no attribute "extend" 
[attr-defined]
                    out["tables"].extend([_normalize_row(r) for r in val i...
                    ^~~~~~~~~~~~~~~~~~~~
utils\ifind_client.py:247:33: error: Incompatible default for parameter "t"
(default has type "None", parameter has type "str")  [assignment]
        def _headers(self, t: str = None) -> Dict:
                                    ^~~~
utils\ifind_client.py:247:33: note: PEP 484 prohibits implicit Optional. Accordingly, mypy has changed its default to no_implicit_optional=True
utils\ifind_client.py:247:33: note: Use https://github.com/hauntsaninja/no_implicit_optional to automatically upgrade your codebase
utils\ifind_client.py:351:23: error: Item "None" of
"Any | dict[Any, Any] | None" has no attribute "get"  [union-attr]
                content = data.get('result', {}).get('content', [])
                          ^~~~~~~~
utils\ifind_client.py:521:27: error: Argument "key" to "sort" of "list" has
incompatible type "Callable[[dict[str, object]], object]"; expected
"Callable[[dict[str, object]], SupportsDunderLT[Any] | SupportsDunderGT[Any]]" 
[arg-type]
            all_rows.sort(key=lambda x: x["date"])
                              ^
utils\ifind_client.py:521:37: error: Incompatible return value type (got
"object", expected "SupportsDunderLT[Any] | SupportsDunderGT[Any]") 
[return-value]
            all_rows.sort(key=lambda x: x["date"])
                                        ^~~~~~~~~
utils\ifind_client.py:591:39: error: Argument 1 to "float" has incompatible
type "str | None"; expected "str | Buffer | SupportsFloat | SupportsIndex" 
[arg-type]
                        "preClose": float(_col(row, "preClose", "前收盘价", "昨...
                                          ^~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~...
utils\ifind_client.py:592:35: error: Argument 1 to "float" has incompatible
type "str | None"; expected "str | Buffer | SupportsFloat | SupportsIndex" 
[arg-type]
                        "open": float(_col(row, "open", "开盘价", "开盘")) if _...
                                      ^~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~...
utils\ifind_client.py:593:35: error: Argument 1 to "float" has incompatible
type "str | None"; expected "str | Buffer | SupportsFloat | SupportsIndex" 
[arg-type]
                        "high": float(_col(row, "high", "最高价", "最高")) if _...
                                      ^~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~...
utils\ifind_client.py:594:34: error: Argument 1 to "float" has incompatible
type "str | None"; expected "str | Buffer | SupportsFloat | SupportsIndex" 
[arg-type]
                        "low": float(_col(row, "low", "最低价", "最低")) if _co...
                                     ^~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
utils\ifind_client.py:595:37: error: Argument 1 to "float" has incompatible
type "str | None"; expected "str | Buffer | SupportsFloat | SupportsIndex" 
[arg-type]
                        "latest": float(_col(row, "latest", "最新价", "现价")) ...
                                        ^~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~...
utils\ifind_client.py:596:41: error: Argument 1 to "int" has incompatible type
"str | None"; expected
"str | Buffer | SupportsInt | SupportsIndex | SupportsTrunc"  [arg-type]
                        "latestVolume": int(_col(row, "latestVolume", "现手"...
                                            ^~~~~~~~~~~~~~~~~~~~~~~~~~~~~~...
utils\ifind_client.py:597:39: error: Argument 1 to "float" has incompatible
type "str | None"; expected "str | Buffer | SupportsFloat | SupportsIndex" 
[arg-type]
                        "avgPrice": float(_col(row, "avgPrice", "均价")) if ...
                                          ^~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
utils\ifind_client.py:598:37: error: Argument 1 to "float" has incompatible
type "str | None"; expected "str | Buffer | SupportsFloat | SupportsIndex" 
[arg-type]
                        "volume": float(_col(row, "volume", "成交量")) if _co...
                                        ^~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
utils\ifind_client.py:599:37: error: Argument 1 to "float" has incompatible
type "str | None"; expected "str | Buffer | SupportsFloat | SupportsIndex" 
[arg-type]
                        "change": float(_col(row, "change", "涨跌")) if _col...
                                        ^~~~~~~~~~~~~~~~~~~~~~~~~~~~~
utils\ifind_client.py:600:43: error: Argument 1 to "float" has incompatible
type "str | None"; expected "str | Buffer | SupportsFloat | SupportsIndex" 
[arg-type]
                        "changeSettle": float(_col(row, "changeSettle", "涨...
                                              ^~~~~~~~~~~~~~~~~~~~~~~~~~~~...
utils\ifind_client.py:601:42: error: Argument 1 to "float" has incompatible
type "str | None"; expected "str | Buffer | SupportsFloat | SupportsIndex" 
[arg-type]
                        "changeRatio": float(_col(row, "changeRatio", "涨跌幅...
                                             ^~~~~~~~~~~~~~~~~~~~~~~~~~~~~...
utils\ifind_client.py:602:48: error: Argument 1 to "float" has incompatible
type "str | None"; expected "str | Buffer | SupportsFloat | SupportsIndex" 
[arg-type]
                        "changeRatioSettle": float(_col(row, "changeRatioS...
                                                   ^~~~~~~~~~~~~~~~~~~~~~~...
utils\ifind_client.py:603:50: error: Argument 1 to "float" has incompatible
type "str | None"; expected "str | Buffer | SupportsFloat | SupportsIndex" 
[arg-type]
                        "increasePositionVol": float(_col(row, "increasePo...
                                                     ^~~~~~~~~~~~~~~~~~~~~...
utils\ifind_client.py:604:44: error: Argument 1 to "float" has incompatible
type "str | None"; expected "str | Buffer | SupportsFloat | SupportsIndex" 
[arg-type]
                        "preSettlement": float(_col(row, "preSettlement", ...
                                               ^~~~~~~~~~~~~~~~~~~~~~~~~~~...
utils\ifind_client.py:605:41: error: Argument 1 to "float" has incompatible
type "str | None"; expected "str | Buffer | SupportsFloat | SupportsIndex" 
[arg-type]
                        "sellVolume": float(_col(row, "sellVolume", "内盘"))...
                                            ^~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
utils\ifind_client.py:606:40: error: Argument 1 to "float" has incompatible
type "str | None"; expected "str | Buffer | SupportsFloat | SupportsIndex" 
[arg-type]
                        "buyVolume": float(_col(row, "buyVolume", "外盘")) i...
                                           ^~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
utils\ifind_client.py:607:52: error: Argument 1 to "float" has incompatible
type "str | None"; expected "str | Buffer | SupportsFloat | SupportsIndex" 
[arg-type]
    ...                  "dailyIncreasePosition": float(_col(row, "dailyIncre...
                                                        ^~~~~~~~~~~~~~~~~~~~~...
utils\ifind_client.py:608:36: error: Argument 1 to "float" has incompatible
type "str | None"; expected "str | Buffer | SupportsFloat | SupportsIndex" 
[arg-type]
                        "swing": float(_col(row, "swing", "振幅")) if _col(r...
                                       ^~~~~~~~~~~~~~~~~~~~~~~~~~~~
utils\ifind_client.py:609:43: error: Argument 1 to "float" has incompatible
type "str | None"; expected "str | Buffer | SupportsFloat | SupportsIndex" 
[arg-type]
                        "latest_price": float(_col(row, "latest_price", "最...
                                              ^~~~~~~~~~~~~~~~~~~~~~~~~~~~...
utils\ifind_client.py:610:41: error: Argument 1 to "float" has incompatible
type "str | None"; expected "str | Buffer | SupportsFloat | SupportsIndex" 
[arg-type]
                        "settlement": float(_col(row, "settlement", "结算价")...
                                            ^~~~~~~~~~~~~~~~~~~~~~~~~~~~~~...
utils\ifind_client.py:613:43: error: Argument 1 to "float" has incompatible
type "str | None"; expected "str | Buffer | SupportsFloat | SupportsIndex" 
[arg-type]
                        "openInterest": float(_col(row, "openInterest", "持...
                                              ^~~~~~~~~~~~~~~~~~~~~~~~~~~~...
utils\ifind_client.py:614:43: error: Argument 1 to "float" has incompatible
type "str | None"; expected "str | Buffer | SupportsFloat | SupportsIndex" 
[arg-type]
                        "positionDiff": float(_col(row, "positionDiff", "仓...
                                              ^~~~~~~~~~~~~~~~~~~~~~~~~~~~...
utils\ifind_client.py:615:42: error: Argument 1 to "float" has incompatible
type "str | None"; expected "str | Buffer | SupportsFloat | SupportsIndex" 
[arg-type]
                        "capitalFlow": float(_col(row, "capitalFlow", "资金流...
                                             ^~~~~~~~~~~~~~~~~~~~~~~~~~~~~...
utils\ifind_client.py:616:48: error: Argument 1 to "float" has incompatible
type "str | None"; expected "str | Buffer | SupportsFloat | SupportsIndex" 
[arg-type]
                        "capitalDeposition": float(_col(row, "capitalDepos...
                                                   ^~~~~~~~~~~~~~~~~~~~~~~...
utils\ifind_client.py:617:40: error: Argument 1 to "float" has incompatible
type "str | None"; expected "str | Buffer | SupportsFloat | SupportsIndex" 
[arg-type]
                        "amplitude": float(_col(row, "amplitude", "振幅")) i...
                                           ^~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
utils\ifind_client.py:618:41: error: Argument 1 to "float" has incompatible
type "str | None"; expected "str | Buffer | SupportsFloat | SupportsIndex" 
[arg-type]
                        "upperLimit": float(_col(row, "upperLimit", "涨停价")...
                                            ^~~~~~~~~~~~~~~~~~~~~~~~~~~~~~...
utils\ifind_client.py:619:40: error: Argument 1 to "float" has incompatible
type "str | None"; expected "str | Buffer | SupportsFloat | SupportsIndex" 
[arg-type]
                        "downLimit": float(_col(row, "downLimit", "跌停价")) ...
                                           ^~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~...
utils\etf_flow_monitor.py:90:55: error: Argument 1 to "module_from_spec" has
incompatible type "ModuleSpec | None"; expected "ModuleSpec"  [arg-type]
                    mod = importlib.util.module_from_spec(spec)
                                                          ^~~~
utils\etf_flow_monitor.py:91:17: error: Item "None" of "ModuleSpec | None" has
no attribute "loader"  [union-attr]
                    spec.loader.exec_module(mod)
                    ^~~~~~~~~~~
utils\etf_flow_monitor.py:91:17: error: Item "None" of "Loader | Any | None"
has no attribute "exec_module"  [union-attr]
                    spec.loader.exec_module(mod)
                    ^~~~~~~~~~~~~~~~~~~~~~~
utils\etf_flow_monitor.py:538:52: error: Incompatible default for parameter
"positions_file" (default has type "None", parameter has type "str") 
[assignment]
    def refresh_etf_flow_signals(positions_file: str = None) -> Dict:
                                                       ^~~~
utils\etf_flow_monitor.py:538:52: note: PEP 484 prohibits implicit Optional. Accordingly, mypy has changed its default to no_implicit_optional=True
utils\etf_flow_monitor.py:538:52: note: Use https://github.com/hauntsaninja/no_implicit_optional to automatically upgrade your codebase
utils\web_scraper.py:56: error: Unused "type: ignore" comment  [unused-ignore]
        from scrapling import StealthyFetcher, Fetcher  # type: ignore
        ^~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
utils\web_scraper.py:60: error: Unused "type: ignore" comment  [unused-ignore]
        StealthyFetcher = None  # type: ignore
        ^~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
utils\web_scraper.py:61: error: Unused "type: ignore" comment  [unused-ignore]
        Fetcher = None  # type: ignore
        ^~~~~~~~~~~~~~~~~~~~~~~~~~~~~
utils\web_scraper.py:212:21: error: Returning Any from function declared to
return "str | None"  [no-any-return]
                        return page.body
                        ^~~~~~~~~~~~~~~~
utils\web_scraper.py:223:17: error: Returning Any from function declared to
return "str | None"  [no-any-return]
                    return resp.text
                    ^~~~~~~~~~~~~~~~
utils\web_scraper.py:291:13: error: Returning Any from function declared to
return "list[NewsItem]"  [no-any-return]
                return cached
                ^~~~~~~~~~~~~
utils\web_scraper.py:422:13: error: Returning Any from function declared to
return "list[NewsItem]"  [no-any-return]
                return cached
                ^~~~~~~~~~~~~
utils\web_scraper.py:515:13: error: Returning Any from function declared to
return "list[NewsItem]"  [no-any-return]
                return cached
                ^~~~~~~~~~~~~
utils\web_scraper.py:537:33: error: Argument 1 to "_parse_html" of "WebScraper"
has incompatible type "str | None"; expected "str"  [arg-type]
            soup = self._parse_html(html)
                                    ^~~~
utils\web_scraper.py:555:21: error: Argument "url" to "NewsItem" has
incompatible type "str | AttributeValueList | None"; expected "str"  [arg-type]
                    url=link,
                        ^~~~
research\vibe_trading_factor_analysis\committee\factor_committee.py:440:24: error:
"object" has no attribute "vote"  [attr-defined]
                    vote = agent.vote(factor_report)
                           ^~~~~~~~~~
research\vibe_trading_factor_analysis\committee\factor_committee.py:444:34: error:
"object" has no attribute "name"  [attr-defined]
                        factor_name, agent.name, vote.score, vote.veto, vo...
                                     ^~~~~~~~~~
research\vibe_trading_factor_analysis\committee\factor_committee.py:447:84: error:
"object" has no attribute "name"  [attr-defined]
    ...ogger.error("[Committee] %s | %s 评分失败：%s", factor_name, agent.name, e)
                                                                         ^~~~
research\vibe_trading_factor_analysis\committee\factor_committee.py:449:32: error:
"object" has no attribute "name"  [attr-defined]
                        agent_name=agent.name, score=0.0, veto=True,
                                   ^~~~~~~~~~
research\vibe_trading_factor_analysis\validators\regime_conditioner.py:102: error:
Unused "type: ignore" comment  [unused-ignore]
                    r.per_regime_ic_ir[reg] = None  # type: ignore
                    ^~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
utils\wt_spread_strategy.py:52:34: error: Invalid index type "float" for
"dict[str, float]"; expected type "str"  [index]
                    result += prices[code] * ratio
                                     ^~~~
utils\wt_spread_strategy.py:54:34: error: Invalid index type "float" for
"dict[str, float]"; expected type "str"  [index]
                    result -= prices[code] * ratio
                                     ^~~~
utils\wt_spread_strategy.py:69:24: error: Invalid index type "float" for
"dict[str, BarData]"; expected type "str"  [index]
                bar = bars[code]
                           ^~~~
utils\wt_spread_strategy.py:135:49: error: Key expression in dictionary
comprehension has incompatible type "float"; expected type "str"  [misc]
            self.leg_positions: Dict[str, float] = {leg["code"]: 0.0 for l...
                                                    ^~~~~~~~~~~
utils\wt_spread_strategy.py:136:48: error: Key expression in dictionary
comprehension has incompatible type "float"; expected type "str"  [misc]
            self.leg_avg_cost: Dict[str, float] = {leg["code"]: 0.0 for le...
                                                   ^~~~~~~~~~~
utils\wt_spread_strategy.py:148:21: error: No overload variant of "get" of
"dict" matches argument types "float", "int"  [call-overload]
                price = leg_prices.get(code, 0)
                        ^~~~~~~~~~~~~~~~~~~~~~~
utils\wt_spread_strategy.py:148:21: note: Possible overload variants:
utils\wt_spread_strategy.py:148:21: note:     def get(self, str, None = ..., /) -> float | None
utils\wt_spread_strategy.py:148:21: note:     def get(self, str, float, /) -> float
utils\wt_spread_strategy.py:148:21: note:     def [_T] get(self, str, _T, /) -> float | _T
utils\wt_spread_strategy.py:154:57: error: Argument 1 to "calc_commission" of
"ContractsManager" has incompatible type "float"; expected "str"  [arg-type]
    ...     commission = self.contracts.calc_commission(code, amount, directi...
                                                        ^~~~
utils\wt_spread_strategy.py:154:71: error: Argument 3 to "calc_commission" of
"ContractsManager" has incompatible type "float | str"; expected "str" 
[arg-type]
    ...  commission = self.contracts.calc_commission(code, amount, direction)
                                                                   ^~~~~~~~~
utils\wt_spread_strategy.py:155:49: error: Argument 1 to "calc_margin" of
"ContractsManager" has incompatible type "float"; expected "str"  [arg-type]
                margin = self.contracts.calc_margin(code, amount)
                                                    ^~~~
utils\wt_spread_strategy.py:163:42: error: Invalid index type "float" for
"dict[str, float]"; expected type "str"  [index]
                old_pos = self.leg_positions[code]
                                             ^~~~
utils\wt_spread_strategy.py:164:42: error: Invalid index type "float" for
"dict[str, float]"; expected type "str"  [index]
                old_cost = self.leg_avg_cost[code]
                                             ^~~~
utils\wt_spread_strategy.py:167:35: error: Invalid index type "float" for
"dict[str, float]"; expected type "str"  [index]
                    self.leg_avg_cost[code] = (
                                      ^~~~
utils\wt_spread_strategy.py:171:32: error: Invalid index type "float" for
"dict[str, float]"; expected type "str"  [index]
                self.leg_positions[code] = new_pos
                                   ^~~~
utils\wt_spread_strategy.py:176:22: error: Argument "code" to "TradeData" has
incompatible type "float"; expected "str"  [arg-type]
                    code=code, exchange="SSE",
                         ^~~~
utils\wt_spread_strategy.py:177:27: error: Argument "direction" to "TradeData"
has incompatible type "float | str"; expected "str"  [arg-type]
                    direction=direction, offset="OPEN",
                              ^~~~~~~~~
utils\wt_spread_strategy.py:195:21: error: No overload variant of "get" of
"dict" matches argument types "float", "int"  [call-overload]
                price = leg_prices.get(code, 0)
                        ^~~~~~~~~~~~~~~~~~~~~~~
utils\wt_spread_strategy.py:195:21: note: Possible overload variants:
utils\wt_spread_strategy.py:195:21: note:     def get(self, str, None = ..., /) -> float | None
utils\wt_spread_strategy.py:195:21: note:     def get(self, str, float, /) -> float
utils\wt_spread_strategy.py:195:21: note:     def [_T] get(self, str, _T, /) -> float | _T
utils\wt_spread_strategy.py:200:57: error: Argument 1 to "calc_commission" of
"ContractsManager" has incompatible type "float"; expected "str"  [arg-type]
    ...     commission = self.contracts.calc_commission(code, amount, reverse...
                                                        ^~~~
utils\wt_spread_strategy.py:207:42: error: Invalid index type "float" for
"dict[str, float]"; expected type "str"  [index]
                old_pos = self.leg_positions[code]
                                             ^~~~
utils\wt_spread_strategy.py:209:32: error: Invalid index type "float" for
"dict[str, float]"; expected type "str"  [index]
                self.leg_positions[code] = new_pos
                                   ^~~~
utils\wt_spread_strategy.py:214:22: error: Argument "code" to "TradeData" has
incompatible type "float"; expected "str"  [arg-type]
                    code=code, exchange="SSE",
                         ^~~~
utils\wt_spread_strategy.py:230:9: error: Returning Any from function declared
to return "float"  [no-any-return]
            return self.leg_positions.get(first_code, 0)
            ^~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
utils\wt_spread_strategy.py:230:16: error: No overload variant of "get" of
"dict" matches argument types "float", "int"  [call-overload]
            return self.leg_positions.get(first_code, 0)
                   ^~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
utils\wt_spread_strategy.py:230:16: note: Possible overload variants:
utils\wt_spread_strategy.py:230:16: note:     def get(self, str, None = ..., /) -> float | None
utils\wt_spread_strategy.py:230:16: note:     def get(self, str, float, /) -> float
utils\wt_spread_strategy.py:230:16: note:     def [_T] get(self, str, _T, /) -> float | _T
utils\wt_spread_strategy.py:332:14: error: Dict entry 0 has incompatible type
"str": "str"; expected "str": "float"  [dict-item]
                {"code": "510300.SH", "ratio": 1.0, "direction": "BUY"},
                 ^~~~~~~~~~~~~~~~~~~
utils\wt_spread_strategy.py:332:49: error: Dict entry 2 has incompatible type
"str": "str"; expected "str": "float"  [dict-item]
                {"code": "510300.SH", "ratio": 1.0, "direction": "BUY"},
                                                    ^~~~~~~~~~~~~~~~~~
utils\wt_spread_strategy.py:333:14: error: Dict entry 0 has incompatible type
"str": "str"; expected "str": "float"  [dict-item]
                {"code": "510050.SH", "ratio": 1.0, "direction": "SELL"},
                 ^~~~~~~~~~~~~~~~~~~
utils\wt_spread_strategy.py:333:49: error: Dict entry 2 has incompatible type
"str": "str"; expected "str": "float"  [dict-item]
                {"code": "510050.SH", "ratio": 1.0, "direction": "SELL"},
                                                    ^~~~~~~~~~~~~~~~~~~
utils\wt_spread_strategy.py:341:14: error: Dict entry 0 has incompatible type
"str": "str"; expected "str": "float"  [dict-item]
                {"code": "510500.SH", "ratio": 1.0, "direction": "BUY"},
                 ^~~~~~~~~~~~~~~~~~~
utils\wt_spread_strategy.py:341:49: error: Dict entry 2 has incompatible type
"str": "str"; expected "str": "float"  [dict-item]
                {"code": "510500.SH", "ratio": 1.0, "direction": "BUY"},
                                                    ^~~~~~~~~~~~~~~~~~
utils\wt_spread_strategy.py:342:14: error: Dict entry 0 has incompatible type
"str": "str"; expected "str": "float"  [dict-item]
                {"code": "512100.SH", "ratio": 1.0, "direction": "SELL"},
                 ^~~~~~~~~~~~~~~~~~~
utils\wt_spread_strategy.py:342:49: error: Dict entry 2 has incompatible type
"str": "str"; expected "str": "float"  [dict-item]
                {"code": "512100.SH", "ratio": 1.0, "direction": "SELL"},
                                                    ^~~~~~~~~~~~~~~~~~~
utils\wt_spread_strategy.py:350:14: error: Dict entry 0 has incompatible type
"str": "str"; expected "str": "float"  [dict-item]
                {"code": "588080.SH", "ratio": 1.0, "direction": "BUY"},
                 ^~~~~~~~~~~~~~~~~~~
utils\wt_spread_strategy.py:350:49: error: Dict entry 2 has incompatible type
"str": "str"; expected "str": "float"  [dict-item]
                {"code": "588080.SH", "ratio": 1.0, "direction": "BUY"},
                                                    ^~~~~~~~~~~~~~~~~~
utils\wt_spread_strategy.py:351:14: error: Dict entry 0 has incompatible type
"str": "str"; expected "str": "float"  [dict-item]
                {"code": "588000.SH", "ratio": 1.0, "direction": "SELL"},
                 ^~~~~~~~~~~~~~~~~~~
utils\wt_spread_strategy.py:351:49: error: Dict entry 2 has incompatible type
"str": "str"; expected "str": "float"  [dict-item]
                {"code": "588000.SH", "ratio": 1.0, "direction": "SELL"},
                                                    ^~~~~~~~~~~~~~~~~~~
utils\wt_spread_strategy.py:360:14: error: Dict entry 0 has incompatible type
"str": "str"; expected "str": "float"  [dict-item]
                {"code": "510300.SH", "ratio": 1.0, "direction": "BUY"},
                 ^~~~~~~~~~~~~~~~~~~
utils\wt_spread_strategy.py:360:49: error: Dict entry 2 has incompatible type
"str": "str"; expected "str": "float"  [dict-item]
                {"code": "510300.SH", "ratio": 1.0, "direction": "BUY"},
                                                    ^~~~~~~~~~~~~~~~~~
utils\wt_spread_strategy.py:361:14: error: Dict entry 0 has incompatible type
"str": "str"; expected "str": "float"  [dict-item]
                {"code": "IF.CFFEX", "ratio": 1.0, "direction": "SELL"},
                 ^~~~~~~~~~~~~~~~~~
utils\wt_spread_strategy.py:361:48: error: Dict entry 2 has incompatible type
"str": "str"; expected "str": "float"  [dict-item]
                {"code": "IF.CFFEX", "ratio": 1.0, "direction": "SELL"},
                                                   ^~~~~~~~~~~~~~~~~~~
utils\vol_target_controller.py:76:44: error: Incompatible default for parameter
"target_vol" (default has type "None", parameter has type "float")  [assignment]
        def __init__(self, target_vol: float = None):
                                               ^~~~
utils\vol_target_controller.py:76:44: note: PEP 484 prohibits implicit Optional. Accordingly, mypy has changed its default to no_implicit_optional=True
utils\vol_target_controller.py:76:44: note: Use https://github.com/hauntsaninja/no_implicit_optional to automatically upgrade your codebase
utils\vol_target_controller.py:81:62: error: Incompatible default for parameter
"daily_returns" (default has type "None", parameter has type "list[float]") 
[assignment]
    ...f calc_realized_vol(self, daily_returns: List[float] = None) -> float:
                                                              ^~~~
utils\vol_target_controller.py:81:62: note: PEP 484 prohibits implicit Optional. Accordingly, mypy has changed its default to no_implicit_optional=True
utils\vol_target_controller.py:81:62: note: Use https://github.com/hauntsaninja/no_implicit_optional to automatically upgrade your codebase
utils\vol_target_controller.py:118:52: error: Incompatible default for
parameter "realized_vol" (default has type "None", parameter has type "float") 
[assignment]
        def calc_vol_scale(self, realized_vol: float = None) -> float:
                                                       ^~~~
utils\vol_target_controller.py:118:52: note: PEP 484 prohibits implicit Optional. Accordingly, mypy has changed its default to no_implicit_optional=True
utils\vol_target_controller.py:118:52: note: Use https://github.com/hauntsaninja/no_implicit_optional to automatically upgrade your codebase
utils\vol_target_controller.py:143:51: error: Incompatible default for
parameter "realized_vol" (default has type "None", parameter has type "float") 
[assignment]
                                realized_vol: float = None,
                                                      ^~~~
utils\vol_target_controller.py:143:51: note: PEP 484 prohibits implicit Optional. Accordingly, mypy has changed its default to no_implicit_optional=True
utils\vol_target_controller.py:143:51: note: Use https://github.com/hauntsaninja/no_implicit_optional to automatically upgrade your codebase
utils\vol_target_controller.py:144:50: error: Incompatible default for
parameter "force_scale" (default has type "None", parameter has type "float") 
[assignment]
                                force_scale: float = None) -> Dict[str, fl...
                                                     ^~~~
utils\vol_target_controller.py:144:50: note: PEP 484 prohibits implicit Optional. Accordingly, mypy has changed its default to no_implicit_optional=True
utils\vol_target_controller.py:144:50: note: Use https://github.com/hauntsaninja/no_implicit_optional to automatically upgrade your codebase
utils\vol_target_controller.py:204:16: error: Incompatible return value type
(got "dict[str, object]", expected "dict[str, float]")  [return-value]
            return result
                   ^~~~~~
utils\vol_target_controller.py:276:9: error: Returning Any from function
declared to return "list[float]"  [no-any-return]
            return simulated_returns
            ^~~~~~~~~~~~~~~~~~~~~~~~
utils\vol_target_controller.py:296:13: error: Returning Any from function
declared to return "float | None"  [no-any-return]
                return data.get("vol_scale")
                ^~~~~~~~~~~~~~~~~~~~~~~~~~~~
utils\var_monitor.py:45:45: error: Incompatible default for parameter
"lookback_days" (default has type "None", parameter has type "int") 
[assignment]
        def __init__(self, lookback_days: int = None):
                                                ^~~~
utils\var_monitor.py:45:45: note: PEP 484 prohibits implicit Optional. Accordingly, mypy has changed its default to no_implicit_optional=True
utils\var_monitor.py:45:45: note: Use https://github.com/hauntsaninja/no_implicit_optional to automatically upgrade your codebase
utils\var_monitor.py:146:18: error: Item "None" of "list[int] | None" has no
attribute "__iter__" (not iterable)  [union-attr]
            for i in dates:
                     ^~~~~
utils\tf_price_predictor.py:124:13: error: "None" has no attribute "compile" 
[attr-defined]
                self._model.compile(config)
                ^~~~~~~~~~~~~~~~~~~
utils\tf_price_predictor.py:213:17: error: "type[TensorflowLSTMPredictor]" has
no attribute "_tf_warned"  [attr-defined]
                    self.__class__._tf_warned = True
                    ^~~~~~~~~~~~~~~~~~~~~~~~~
utils\tf_price_predictor.py:217:17: error: "type[TensorflowLSTMPredictor]" has
no attribute "_tf_warned"  [attr-defined]
                    self.__class__._tf_warned = True
                    ^~~~~~~~~~~~~~~~~~~~~~~~~
utils\tf_price_predictor.py:226:17: error: "None" has no attribute "keras" 
[attr-defined]
            model = tf.keras.Sequential([
                    ^~~~~~~~
utils\tf_price_predictor.py:227:13: error: "None" has no attribute "keras" 
[attr-defined]
                tf.keras.layers.LSTM(64, return_sequences=True,
                ^~~~~~~~
utils\tf_price_predictor.py:229:13: error: "None" has no attribute "keras" 
[attr-defined]
                tf.keras.layers.Dropout(0.2),
                ^~~~~~~~
utils\tf_price_predictor.py:230:13: error: "None" has no attribute "keras" 
[attr-defined]
                tf.keras.layers.LSTM(32),
                ^~~~~~~~
utils\tf_price_predictor.py:231:13: error: "None" has no attribute "keras" 
[attr-defined]
                tf.keras.layers.Dropout(0.2),
                ^~~~~~~~
utils\tf_price_predictor.py:232:13: error: "None" has no attribute "keras" 
[attr-defined]
                tf.keras.layers.Dense(16, activation='relu'),
                ^~~~~~~~
utils\tf_price_predictor.py:233:13: error: "None" has no attribute "keras" 
[attr-defined]
                tf.keras.layers.Dense(horizon)
                ^~~~~~~~
utils\tf_price_predictor.py:281:13: error: "None" has no attribute "fit" 
[attr-defined]
                self._model.fit(
                ^~~~~~~~~~~~~~~
utils\tf_price_predictor.py:297:37: error: "None" has no attribute "predict" 
[attr-defined]
                prediction_normalized = self._model.predict(input_3d, verb...
                                        ^~~~~~~~~~~~~~~~~~~
utils\tf_price_predictor.py:301:13: error: Returning Any from function declared
to return "ndarray[Any, Any] | None"  [no-any-return]
                return prediction
                ^~~~~~~~~~~~~~~~~
utils\tf_price_predictor.py:391:20: error: Incompatible types in assignment
(expression has type "ndarray[tuple[int, ...], dtype[Any]]", variable has type
"list[Any]")  [assignment]
            forecast = np.array(forecast)
                       ^~~~~~~~~~~~~~~~~~
utils\tf_price_predictor.py:395:32: error: Unsupported operand types for *
("list[Any]" and "float")  [operator]
                "q10": (forecast * 0.95).tolist(),
                                   ^~~~
utils\tf_price_predictor.py:396:20: error: "list[Any]" has no attribute
"tolist"  [attr-defined]
                "q50": forecast.tolist(),
                       ^~~~~~~~~~~~~~~
utils\tf_price_predictor.py:397:32: error: Unsupported operand types for *
("list[Any]" and "float")  [operator]
                "q90": (forecast * 1.05).tolist(),
                                   ^~~~
utils\tf_price_predictor.py:400:16: error: Incompatible return value type (got
"tuple[list[Any], dict[str, Any]]", expected
"tuple[ndarray[Any, Any], dict[Any, Any]]")  [return-value]
            return forecast, quantiles
                   ^~~~~~~~~~~~~~~~~~~
utils\tf_price_predictor.py:439:64: error: Name "prizes" is not defined; did
you mean "prices"?  [name-defined]
    ...       logger.warning(f"{symbol} 历史数据不足 ({len(prizes)} < 10), 返回中性预测")
                                                                 ^~~~~~
utils\tf_price_predictor.py:461:22: error: Incompatible types in assignment
(expression has type "ndarray[Any, Any] | None", variable has type
"tuple[ndarray[Any, Any], ndarray[Any, Any]] | None")  [assignment]
                result = self.lstm.train_and_predict(prices, horizon)
                         ^~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
utils\tf_price_predictor.py:463:28: error: Incompatible types in assignment
(expression has type "tuple[ndarray[Any, Any], ndarray[Any, Any]]", variable has
type "ndarray[Any, Any] | None")  [assignment]
                    forecast = result
                               ^~~~~~
utils\tf_price_predictor.py:467:28: error: "float" has no attribute "tolist" 
[attr-defined]
                        "q10": (forecast * 0.95).tolist(),
                               ^~~~~~~~~~~~~~~~~~~~~~~~
utils\tf_price_predictor.py:467:29: error: Unsupported operand types for *
("None" and "float")  [operator]
                        "q10": (forecast * 0.95).tolist(),
                                ^
utils\tf_price_predictor.py:468:28: error: "None" has no attribute "tolist" 
[attr-defined]
                        "q50": forecast.tolist(),
                               ^~~~~~~~~~~~~~~
utils\tf_price_predictor.py:469:28: error: "float" has no attribute "tolist" 
[attr-defined]
                        "q90": (forecast * 1.05).tolist(),
                               ^~~~~~~~~~~~~~~~~~~~~~~~
utils\tf_price_predictor.py:469:29: error: Unsupported operand types for *
("None" and "float")  [operator]
                        "q90": (forecast * 1.05).tolist(),
                                ^
utils\tf_price_predictor.py:618:19: error: Value of type "float | list[int]" is
not indexable  [index]
                q10 = result.quantiles.get("q10", [0])[-1]
                      ^~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
utils\tf_price_predictor.py:619:19: error: Value of type "float | list[int]" is
not indexable  [index]
                q90 = result.quantiles.get("q90", [0])[-1]
                      ^~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
utils\supply_chain_graph.py:464:17: error: Returning Any from function declared
to return "list[str] | None"  [no-any-return]
                    return path
                    ^~~~~~~~~~~
utils\smart_order_router.py:256:44: error: Item "None" of "Venue | None" has no
attribute "venue_type"  [union-attr]
                scores = [s for s in scores if self.venues.get(s.venue_nam...
                                               ^~~~~~~~~~~~~~~~~~~~~~~~~~~...
utils\smart_beta_engine.py:158:13: error: Need type annotation for
"all_factors" (hint: "all_factors: set[<type>] = ...")  [var-annotated]
                all_factors = set()
                ^~~~~~~~~~~
utils\risk_metrics.py:71:9: error: Returning Any from function declared to
return "float"  [no-any-return]
            return var
            ^~~~~~~~~~
utils\risk_metrics.py:110:9: error: Returning Any from function declared to
return "float"  [no-any-return]
            return es
            ^~~~~~~~~
utils\risk_metrics.py:152:16: error: Incompatible return value type (got
"tuple[Any, signedinteger[_32Bit | _64Bit], signedinteger[_32Bit | _64Bit]]",
expected "tuple[float, int, int]")  [return-value]
            return max_dd, peak_idx, trough_idx
                   ^~~~~~~~~~~~~~~~~~~~~~~~~~~~
utils\risk_metrics.py:194:9: error: Returning Any from function declared to
return "float"  [no-any-return]
            return sharpe_ratio
            ^~~~~~~~~~~~~~~~~~~
utils\risk_metrics.py:238:9: error: Returning Any from function declared to
return "float"  [no-any-return]
            return sortino_ratio
            ^~~~~~~~~~~~~~~~~~~~
utils\risk_metrics.py:273:9: error: Returning Any from function declared to
return "float"  [no-any-return]
            return calmar_ratio
            ^~~~~~~~~~~~~~~~~~~
utils\risk_metrics.py:305:9: error: Returning Any from function declared to
return "float"  [no-any-return]
            return volatility
            ^~~~~~~~~~~~~~~~~
utils\risk_metrics.py:350:9: error: Returning Any from function declared to
return "float"  [no-any-return]
            return beta
            ^~~~~~~~~~~
utils\risk_metrics.py:385:9: error: Returning Any from function declared to
return "float"  [no-any-return]
            return alpha
            ^~~~~~~~~~~~
utils\risk_metrics.py:444:9: error: Returning Any from function declared to
return "float"  [no-any-return]
            return tracking_error
            ^~~~~~~~~~~~~~~~~~~~~
utils\risk_metrics.py:480:9: error: Returning Any from function declared to
return "float"  [no-any-return]
            return information_ratio
            ^~~~~~~~~~~~~~~~~~~~~~~~
utils\risk_metrics.py:511:16: error: Incompatible return value type (got
"floating[Any]", expected "float")  [return-value]
            return win_rate
                   ^~~~~~~~
utils\risk_metrics.py:558:77: error: Incompatible default for parameter
"prices" (default has type "None", parameter has type "ndarray[Any, Any]") 
[assignment]
    ...te_performance_metrics(returns: np.ndarray, prices: np.ndarray = None,
                                                                        ^~~~
utils\risk_metrics.py:558:77: note: PEP 484 prohibits implicit Optional. Accordingly, mypy has changed its default to no_implicit_optional=True
utils\risk_metrics.py:558:77: note: Use https://github.com/hauntsaninja/no_implicit_optional to automatically upgrade your codebase
utils\risk_metrics.py:559:65: error: Incompatible default for parameter
"benchmark_returns" (default has type "None", parameter has type
"ndarray[Any, Any]")  [assignment]
                                    benchmark_returns: np.ndarray = None,
                                                                    ^~~~
utils\risk_metrics.py:559:65: note: PEP 484 prohibits implicit Optional. Accordingly, mypy has changed its default to no_implicit_optional=True
utils\risk_metrics.py:559:65: note: Use https://github.com/hauntsaninja/no_implicit_optional to automatically upgrade your codebase
utils\risk_constraints.py:169:9: error: Incompatible types in assignment
(expression has type "ndarray[tuple[int, ...], dtype[Any]]", variable has type
"float")  [assignment]
        w = np.array(weights, dtype=float)
            ^~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
utils\risk_constraints.py:170:14: error: "float" has no attribute "sum" 
[attr-defined]
        w = w / (w.sum() if w.sum() > 0 else 1.0)
                 ^~~~~
utils\risk_budget_optimizer.py:475:17: error: Dict entry 2 has incompatible
type "str": "float"; expected "str": "ndarray[Any, Any]"  [dict-item]
                    "expected_te": 0.0,
                    ^~~~~~~~~~~~~~~~~~
utils\quant_neutral_runner.py:64:5: error: Cannot assign to a type  [misc]
        ICHedgeCalculator = None
        ^~~~~~~~~~~~~~~~~
utils\quant_neutral_runner.py:64:25: error: Incompatible types in assignment
(expression has type "None", variable has type "type[ICHedgeCalculator]") 
[assignment]
        ICHedgeCalculator = None
                            ^~~~
utils\quant_neutral_runner.py:65:5: error: Cannot assign to a type  [misc]
        ICHedgeResult = None
        ^~~~~~~~~~~~~
utils\quant_neutral_runner.py:65:21: error: Incompatible types in assignment
(expression has type "None", variable has type "type[ICHedgeResult]") 
[assignment]
        ICHedgeResult = None
                        ^~~~
utils\quant_neutral_runner.py:66:5: error: Cannot assign to a type  [misc]
        V10ConfigLoader = None
        ^~~~~~~~~~~~~~~
utils\quant_neutral_runner.py:66:23: error: Incompatible types in assignment
(expression has type "None", variable has type "type[V10ConfigLoader]") 
[assignment]
        V10ConfigLoader = None
                          ^~~~
utils\quant_neutral_runner.py:182:28: error: Incompatible types in assignment
(expression has type "None", variable has type "ICHedgeCalculator") 
[assignment]
                self.ic_calc = None
                               ^~~~
utils\quant_neutral_runner.py:390:9: error: Returning Any from function
declared to return "float"  [no-any-return]
            return weighted_beta / total_weight
            ^~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
utils\market_impact_model.py:278:24: error: Incompatible types in assignment
(expression has type "ndarray[tuple[int, ...], dtype[floating[Any]]]", variable
has type "list[Any]")  [assignment]
                holdings = total_shares * (1 - t_array / T)
                           ^~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
utils\market_impact_model.py:283:18: error: Incompatible types in assignment
(expression has type "ndarray[tuple[int, ...], dtype[Any]]", variable has type
"list[Any]")  [assignment]
            trades = np.diff(-holdings)
                     ^~~~~~~~~~~~~~~~~~
utils\market_impact_model.py:283:26: error: Unsupported operand type for unary
- ("list[Any]")  [operator]
            trades = np.diff(-holdings)
                             ^~~~~~~~~
utils\market_impact_model.py:289:63: error: Unsupported operand type for unary
- ("list[Any]")  [operator]
    ...    trades_full = np.diff(np.concatenate([[total_shares], -holdings]))
                                                                 ^~~~~~~~~
utils\market_impact_model.py:305:43: error: Unsupported operand types for **
("list[Any]" and "int")  [operator]
            cost_var = sigma * sigma * np.sum(holdings[:-1] ** 2) * dt
                                              ^
utils\market_impact_model.py:319:22: error: "list[Any]" has no attribute
"tolist"  [attr-defined]
                holdings=holdings.tolist(),
                         ^~~~~~~~~~~~~~~
utils\ledoit_wolf_covariance.py:262:2: error: Name "pd" is not defined 
[name-defined]
            returns: Union[np.ndarray, "pd.DataFrame"],
            ^
utils\greek_hedge_manager.py:129:12: error: Item "None" of
"IVEnvironment | None" has no attribute "long_term_median_iv"  [union-attr]
            if iv.long_term_median_iv > 0:
               ^~~~~~~~~~~~~~~~~~~~~~
utils\greek_hedge_manager.py:130:24: error: Item "None" of
"IVEnvironment | None" has no attribute "current_iv"  [union-attr]
                iv_ratio = iv.current_iv / iv.long_term_median_iv
                           ^~~~~~~~~~~~~
utils\greek_hedge_manager.py:130:40: error: Item "None" of
"IVEnvironment | None" has no attribute "long_term_median_iv"  [union-attr]
                iv_ratio = iv.current_iv / iv.long_term_median_iv
                                           ^~~~~~~~~~~~~~~~~~~~~~
utils\greek_hedge_manager.py:138:12: error: Item "None" of
"IVEnvironment | None" has no attribute "second_month_iv"  [union-attr]
            if iv.second_month_iv > 0:
               ^~~~~~~~~~~~~~~~~~
utils\greek_hedge_manager.py:139:26: error: Item "None" of
"IVEnvironment | None" has no attribute "front_month_iv"  [union-attr]
                term_ratio = iv.front_month_iv / iv.second_month_iv
                             ^~~~~~~~~~~~~~~~~
utils\greek_hedge_manager.py:139:46: error: Item "None" of
"IVEnvironment | None" has no attribute "second_month_iv"  [union-attr]
                term_ratio = iv.front_month_iv / iv.second_month_iv
                                                 ^~~~~~~~~~~~~~~~~~
utils\greek_hedge_manager.py:149:16: error: Item "None" of
"IVEnvironment | None" has no attribute "put_25d_iv"  [union-attr]
            skew = iv.put_25d_iv - iv.call_25d_iv
                   ^~~~~~~~~~~~~
utils\greek_hedge_manager.py:149:32: error: Item "None" of
"IVEnvironment | None" has no attribute "call_25d_iv"  [union-attr]
            skew = iv.put_25d_iv - iv.call_25d_iv
                                   ^~~~~~~~~~~~~~
utils\data_quality_monitor.py:57:10: error: Incompatible types in assignment
(expression has type "None", variable has type Module)  [assignment]
        np = None
             ^~~~
utils\data_quality_monitor.py:225:9: error: Need type annotation for
"all_fields" (hint: "all_fields: set[<type>] = ...")  [var-annotated]
            all_fields = set()
            ^~~~~~~~~~
utils\data_quality_monitor.py:338:36: error: Argument 1 to "float" has
incompatible type "Any | None"; expected
"str | Buffer | SupportsFloat | SupportsIndex"  [arg-type]
                        high_f = float(high)
                                       ^~~~
utils\data_quality_monitor.py:339:35: error: Argument 1 to "float" has
incompatible type "Any | None"; expected
"str | Buffer | SupportsFloat | SupportsIndex"  [arg-type]
                        low_f = float(low)
                                      ^~~
utils\data_quality_monitor.py:340:37: error: Argument 1 to "float" has
incompatible type "Any | None"; expected
"str | Buffer | SupportsFloat | SupportsIndex"  [arg-type]
                        close_f = float(close)
                                        ^~~~~
utils\data_quality_monitor.py:639:37: error: Argument 1 to "float" has
incompatible type "Any | None"; expected
"str | Buffer | SupportsFloat | SupportsIndex"  [arg-type]
                        c, h, l = float(close), float(high), float(low)
                                        ^~~~~
utils\black_litterman_optimizer.py:340:17: error: Returning Any from function
declared to return "ndarray[Any, Any]"  [no-any-return]
                    return w_unconstrained / w_unconstrained.sum()
                    ^~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
utils\black_litterman_optimizer.py:379:17: error: Returning Any from function
declared to return "ndarray[Any, Any]"  [no-any-return]
                    return w
                    ^~~~~~~~
utils\black_litterman_optimizer.py:390:13: error: Returning Any from function
declared to return "ndarray[Any, Any]"  [no-any-return]
                return w / w.sum()
                ^~~~~~~~~~~~~~~~~~
utils\alpha_evaluator.py:140:17: error: Dict entry 0 has incompatible type
"str": "str"; expected "str": "float"  [dict-item]
                    "date": report_date,
                    ^~~~~~~~~~~~~~~~~~~
research\vibe_trading_factor_analysis\shadow\shadow_account.py:239:39: error:
Incompatible types in assignment (expression has type "float", variable has type
"int")  [assignment]
                r.derisk_triggered_days = rm_stats["derisk_triggered_days"...
                                          ^~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
utils\data_provider.py:40:11: error: Incompatible types in assignment
(expression has type "None", variable has type Module)  [assignment]
        _np = None
              ^~~~
utils\data_provider.py:98:9: error: Need type annotation for "data_cache"
(hint: "data_cache: dict[<type>, <type>] = ...")  [var-annotated]
            self.data_cache = {}
            ^~~~~~~~~~~~~~~
utils\data_provider.py:139:31: error: Incompatible types in assignment
(expression has type "str", variable has type "None")  [assignment]
            self._backtest_date = report_date
                                  ^~~~~~~~~~~
utils\data_provider.py:152:55: error: Argument 1 to "module_from_spec" has
incompatible type "ModuleSpec | None"; expected "ModuleSpec"  [arg-type]
                    mod = importlib.util.module_from_spec(spec)
                                                          ^~~~
utils\data_provider.py:153:17: error: Item "None" of "ModuleSpec | None" has no
attribute "loader"  [union-attr]
                    spec.loader.exec_module(mod)
                    ^~~~~~~~~~~
utils\data_provider.py:153:17: error: Item "None" of "Loader | Any | None" has
no attribute "exec_module"  [union-attr]
                    spec.loader.exec_module(mod)
                    ^~~~~~~~~~~~~~~~~~~~~~~
utils\data_provider.py:154:41: error: Incompatible types in assignment
(expression has type "dict[str, Any]", variable has type "None")  [assignment]
                    self._wind_mcp_client = {
                                            ^
utils\data_provider.py:161:64: error: Incompatible types in assignment
(expression has type "str", target has type "bool | None")  [assignment]
    ...  self.source_health['wind_mcp']['last_error'] = f"文件不存在: {wind_path}"
                                                        ^~~~~~~~~~~~~~~~~~~~~
utils\data_provider.py:164:60: error: Incompatible types in assignment
(expression has type "str", target has type "bool | None")  [assignment]
                self.source_health['wind_mcp']['last_error'] = str(e)
                                                               ^~~~~~
utils\data_provider.py:181:65: error: Incompatible types in assignment
(expression has type "str", target has type "bool | None")  [assignment]
    ... self.source_health['ifind_mcp']['last_error'] = "IFIND_TOKEN 环境变量未设置"
                                                        ^~~~~~~~~~~~~~~~~~~~~
utils\data_provider.py:191:34: error: Incompatible types in assignment
(expression has type "IFindClient", variable has type "None")  [assignment]
                self._ifind_client = IFindClient()
                                     ^~~~~~~~~~~~~
utils\data_provider.py:197:61: error: Incompatible types in assignment
(expression has type "str", target has type "bool | None")  [assignment]
                self.source_health['ifind_mcp']['last_error'] = str(e)
                                                                ^~~~~~
utils\data_provider.py:200:61: error: Incompatible types in assignment
(expression has type "str", target has type "bool | None")  [assignment]
                self.source_health['ifind_mcp']['last_error'] = str(e)
                                                                ^~~~~~
utils\data_provider.py:207:32: error: Incompatible types in assignment
(expression has type "TDXDataSource | None", variable has type "None") 
[assignment]
                self._tdx_source = get_tdx_source()
                                   ^~~~~~~~~~~~~~~~
utils\data_provider.py:212:59: error: Incompatible types in assignment
(expression has type "str", target has type "bool | None")  [assignment]
                    self.source_health['tdx']['last_error'] = "通达信连接初始化失败"
                                                              ^~~~~~~~~~~~
utils\data_provider.py:215:55: error: Incompatible types in assignment
(expression has type "str", target has type "bool | None")  [assignment]
                self.source_health['tdx']['last_error'] = f"模块导入失败: {e}"
                                                          ^~~~~~~~~~~~~~
utils\data_provider.py:218:55: error: Incompatible types in assignment
(expression has type "str", target has type "bool | None")  [assignment]
                self.source_health['tdx']['last_error'] = str(e)
                                                          ^~~~~~
utils\data_provider.py:225:36: error: Incompatible types in assignment
(expression has type "AKShareDataSource", variable has type "None") 
[assignment]
                self._akshare_source = get_akshare_source()
                                       ^~~~~~~~~~~~~~~~~~~~
utils\data_provider.py:230:63: error: Incompatible types in assignment
(expression has type "str", target has type "bool | None")  [assignment]
    ...         self.source_health['akshare']['last_error'] = "AKShare 初始化失败"
                                                              ^~~~~~~~~~~~~~~
utils\data_provider.py:233:59: error: Incompatible types in assignment
(expression has type "str", target has type "bool | None")  [assignment]
    ...          self.source_health['akshare']['last_error'] = f"模块导入失败: {e}"
                                                               ^~~~~~~~~~~~~~
utils\data_provider.py:236:59: error: Incompatible types in assignment
(expression has type "str", target has type "bool | None")  [assignment]
                self.source_health['akshare']['last_error'] = str(e)
                                                              ^~~~~~
utils\data_provider.py:685:65: error: Incompatible types in assignment
(expression has type "str", target has type "bool | None")  [assignment]
    ...       self.source_health['sina_http']['last_error'] = 'empty_payload'
                                                              ^~~~~~~~~~~~~~~
utils\data_provider.py:690:65: error: Incompatible types in assignment
(expression has type "str", target has type "bool | None")  [assignment]
    ... self.source_health['sina_http']['last_error'] = 'insufficient_fields'
                                                        ^~~~~~~~~~~~~~~~~~~~~
utils\data_provider.py:711:65: error: Incompatible types in assignment
(expression has type "str", target has type "bool | None")  [assignment]
    ...          self.source_health['sina_http']['last_error'] = 'zero_price'
                                                                 ^~~~~~~~~~~~
utils\data_provider.py:731:61: error: Incompatible types in assignment
(expression has type "str", target has type "bool | None")  [assignment]
                self.source_health['sina_http']['last_error'] = str(e)
                                                                ^~~~~~
utils\data_provider.py:735:45: error: Incompatible default for parameter
"symbol" (default has type "None", parameter has type "str")  [assignment]
        def get_market_data(self, symbol: str = None) -> Dict:
                                                ^~~~
utils\data_provider.py:735:45: note: PEP 484 prohibits implicit Optional. Accordingly, mypy has changed its default to no_implicit_optional=True
utils\data_provider.py:735:45: note: Use https://github.com/hauntsaninja/no_implicit_optional to automatically upgrade your codebase
utils\data_provider.py:745:21: error: Returning Any from function declared to
return "dict[Any, Any]"  [no-any-return]
                        return cached_data['data']
                        ^~~~~~~~~~~~~~~~~~~~~~~~~~
utils\data_provider.py:839:48: error: Incompatible default for parameter
"symbol" (default has type "None", parameter has type "str")  [assignment]
        def get_sentiment_data(self, symbol: str = None) -> Optional[Dict]...
                                                   ^~~~
utils\data_provider.py:839:48: note: PEP 484 prohibits implicit Optional. Accordingly, mypy has changed its default to no_implicit_optional=True
utils\data_provider.py:839:48: note: Use https://github.com/hauntsaninja/no_implicit_optional to automatically upgrade your codebase
utils\data_provider.py:849:21: error: Returning Any from function declared to
return "dict[Any, Any] | None"  [no-any-return]
                        return cached_data['data']
                        ^~~~~~~~~~~~~~~~~~~~~~~~~~
utils\data_provider.py:1050:13: error: Returning Any from function declared to
return "float"  [no-any-return]
                return _mean(data)
                ^~~~~~~~~~~~~~~~~~
utils\data_provider.py:1058:9: error: Returning Any from function declared to
return "float"  [no-any-return]
            return ema
            ^~~~~~~~~~
utils\data_provider.py:1262:54: error: Incompatible types in assignment
(expression has type "bool", target has type "dict[Any, Any]")  [assignment]
                    status[f'{module_name}_available'] = True
                                                         ^~~~
utils\data_provider.py:1264:54: error: Incompatible types in assignment
(expression has type "bool", target has type "dict[Any, Any]")  [assignment]
                    status[f'{module_name}_available'] = False
                                                         ^~~~~
utils\data_provider.py:1283:35: error: Incompatible default for parameter
"symbol" (default has type "None", parameter has type "str")  [assignment]
    def get_market_data(symbol: str = None) -> Dict:
                                      ^~~~
utils\data_provider.py:1283:35: note: PEP 484 prohibits implicit Optional. Accordingly, mypy has changed its default to no_implicit_optional=True
utils\data_provider.py:1283:35: note: Use https://github.com/hauntsaninja/no_implicit_optional to automatically upgrade your codebase
utils\data_provider.py:1295:38: error: Incompatible default for parameter
"symbol" (default has type "None", parameter has type "str")  [assignment]
    def get_sentiment_data(symbol: str = None) -> Optional[Dict]:
                                         ^~~~
utils\data_provider.py:1295:38: note: PEP 484 prohibits implicit Optional. Accordingly, mypy has changed its default to no_implicit_optional=True
utils\data_provider.py:1295:38: note: Use https://github.com/hauntsaninja/no_implicit_optional to automatically upgrade your codebase
utils\etf_flow_decision.py:122:59: error: Argument 1 to "module_from_spec" has
incompatible type "ModuleSpec | None"; expected "ModuleSpec"  [arg-type]
                        mod = importlib.util.module_from_spec(spec)
                                                              ^~~~
utils\etf_flow_decision.py:123:21: error: Item "None" of "ModuleSpec | None"
has no attribute "loader"  [union-attr]
                        spec.loader.exec_module(mod)
                        ^~~~~~~~~~~
utils\etf_flow_decision.py:123:21: error: Item "None" of "Loader | Any | None"
has no attribute "exec_module"  [union-attr]
                        spec.loader.exec_module(mod)
                        ^~~~~~~~~~~~~~~~~~~~~~~
utils\etf_flow_decision.py:149:17: error: Returning Any from function declared
to return "str | None"  [no-any-return]
                    return result
                    ^~~~~~~~~~~~~
utils\etf_flow_decision.py:321:21: error: Value of type "object" is not
indexable  [index]
            logger.info(f"【盘前决策】完成: {decision_result['summary']['strong_si...
                        ^~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~...
utils\etf_flow_decision.py:352:17: error: Returning Any from function declared
to return "dict[str, Any]"  [no-any-return]
                    return cached["result"]
                    ^~~~~~~~~~~~~~~~~~~~~~~
utils\etf_flow_decision.py:419:21: error: Value of type "object" is not
indexable  [index]
            logger.info(f"【盘中决策】完成: {decision_result['summary']['sudden_ch...
                        ^~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~...
utils\etf_flow_decision.py:668:24: error: Incompatible types in assignment
(expression has type "Thread", variable has type "None")  [assignment]
            self._thread = threading.Thread(
                           ^~~~~~~~~~~~~~~~~
utils\etf_flow_decision.py:673:9: error: "None" has no attribute "start" 
[attr-defined]
            self._thread.start()
            ^~~~~~~~~~~~~~~~~~
utils\risk_budget_allocator.py:50:9: error: Returning Any from function
declared to return "float"  [no-any-return]
            return float(np.std(returns, ddof=1)) * np.sqrt(252)
            ^~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
utils\institutional_optimizer.py:172:23: error: Incompatible types in
assignment (expression has type "ndarray[tuple[int, ...], dtype[float64]]",
variable has type "ndarray[tuple[int], dtype[Any]]")  [assignment]
                weights = weights / total_value
                          ^~~~~~~~~~~~~~~~~~~~~
utils\factor_model.py:36:5: error: Cannot assign to a type  [misc]
        GTJA191Factors = None
        ^~~~~~~~~~~~~~
utils\factor_model.py:36:22: error: Incompatible types in assignment
(expression has type "None", variable has type "type[GTJA191Factors]") 
[assignment]
        GTJA191Factors = None
                         ^~~~
utils\factor_model.py:219:9: error: Returning Any from function declared to
return "float"  [no-any-return]
            return round(score, 4)
            ^~~~~~~~~~~~~~~~~~~~~~
utils\factor_model.py:353:9: error: Need type annotation for "dist" (hint:
"dist: dict[<type>, <type>] = ...")  [var-annotated]
            dist = {}
            ^~~~
utils\factor_model.py:364:39: error: Argument 1 to "_to_signal" of
"FactorModel" has incompatible type "floating[Any]"; expected "float" 
[arg-type]
                'signal': self._to_signal(avg),
                                          ^~~
utils\hedge_execution_engine.py:97:48: error: Incompatible types in assignment
(expression has type "PostTradeAttribution", variable has type "None") 
[assignment]
                    self._post_trade_attribution = PostTradeAttribution(sa...
                                                   ^~~~~~~~~~~~~~~~~~~~~~~...
utils\hedge_execution_engine.py:125:13: error: "None" has no attribute "record"
 [attr-defined]
                self._post_trade_attribution.record(fill, estimate)
                ^~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
utils\hedge_execution_engine.py:143:17: error: Returning Any from function
declared to return "dict[Any, Any]"  [no-any-return]
                    return json.load(f)
                    ^~~~~~~~~~~~~~~~~~~
utils\hedge_execution_engine.py:152:35: error: Incompatible types in assignment
(expression has type "GreekHedgeManager", variable has type "None") 
[assignment]
                self._hedge_manager = GreekHedgeManager(
                                      ^~~~~~~~~~~~~~~~~~
utils\hedge_execution_engine.py:218:65: error: Incompatible default for
parameter "portfolio_value" (default has type "None", parameter has type
"float")  [assignment]
                                           portfolio_value: float = None,
                                                                    ^~~~
utils\hedge_execution_engine.py:218:65: note: PEP 484 prohibits implicit Optional. Accordingly, mypy has changed its default to no_implicit_optional=True
utils\hedge_execution_engine.py:218:65: note: Use https://github.com/hauntsaninja/no_implicit_optional to automatically upgrade your codebase
utils\hedge_execution_engine.py:219:64: error: Incompatible default for
parameter "portfolio_beta" (default has type "None", parameter has type "float")
 [assignment]
                                           portfolio_beta: float = None,
                                                                   ^~~~
utils\hedge_execution_engine.py:219:64: note: PEP 484 prohibits implicit Optional. Accordingly, mypy has changed its default to no_implicit_optional=True
utils\hedge_execution_engine.py:219:64: note: Use https://github.com/hauntsaninja/no_implicit_optional to automatically upgrade your codebase
utils\hedge_execution_engine.py:220:61: error: Incompatible default for
parameter "target_beta" (default has type "None", parameter has type "float") 
[assignment]
                                           target_beta: float = None,
                                                                ^~~~
utils\hedge_execution_engine.py:220:61: note: PEP 484 prohibits implicit Optional. Accordingly, mypy has changed its default to no_implicit_optional=True
utils\hedge_execution_engine.py:220:61: note: Use https://github.com/hauntsaninja/no_implicit_optional to automatically upgrade your codebase
utils\hedge_execution_engine.py:308:66: error: Incompatible default for
parameter "portfolio_value" (default has type "None", parameter has type
"float")  [assignment]
                                            portfolio_value: float = None,
                                                                     ^~~~
utils\hedge_execution_engine.py:308:66: note: PEP 484 prohibits implicit Optional. Accordingly, mypy has changed its default to no_implicit_optional=True
utils\hedge_execution_engine.py:308:66: note: Use https://github.com/hauntsaninja/no_implicit_optional to automatically upgrade your codebase
utils\hedge_execution_engine.py:590:17: error: Returning Any from function
declared to return "float"  [no-any-return]
                    return etf_price * 1000  # e.g. 4.65 → 4650
                    ^~~~~~~~~~~~~~~~~~~~~~~
utils\attribution\managers.py:480:13: error: Returning Any from function
declared to return "dict[str, dict[Any, Any]]"  [no-any-return]
                return tracker.get_all_etf_fund_flows()
                ^~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
utils\attribution\managers.py:495:13: error: Returning Any from function
declared to return "dict[Any, Any] | None"  [no-any-return]
                return tracker.get_etf_fund_flow(etf_code)
                ^~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
utils\attribution\managers.py:511:13: error: Returning Any from function
declared to return "list[dict[Any, Any]]"  [no-any-return]
                return tracker.detect_signals(flow_data)
                ^~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
utils\attribution\managers.py:527:13: error: Returning Any from function
declared to return "dict[Any, Any]"  [no-any-return]
                return tracker.get_signal_summary(flow_data)
                ^~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
research\vibe_trading_factor_analysis\adapters\vibe_trading_factor_adapter.py:1459:9: error:
Name "values" already defined on line 1344  [no-redef]
            values: Dict[str, float] = {}
            ^~~~~~
build_plan_executor.py:129:27: error: Value of type "dict[Any, Any] | None" is
not indexable  [index]
            phase_summaries = self.plan_data["phase_summary"]
                              ^~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
build_plan_executor.py:140:25: error: Unsupported operand types for + ("object"
and "timedelta")  [operator]
                phase_end = pc["start"] + timedelta(days=pc["duration"])
                            ^
build_plan_executor.py:140:54: error: Argument "days" to "timedelta" has
incompatible type "object"; expected "float"  [arg-type]
                phase_end = pc["start"] + timedelta(days=pc["duration"])
                                                         ^~~~~~~~~~~~~~
build_plan_executor.py:141:16: error: Unsupported operand types for >= ("date"
and "object")  [operator]
                if pc["start"] <= target_date <= phase_end:
                   ^
build_plan_executor.py:141:46: error: Unsupported operand types for <= ("date"
and "timedelta")  [operator]
                if pc["start"] <= target_date <= phase_end:
                                                 ^~~~~~~~~
build_plan_executor.py:145:26: error: Unsupported operand types for < ("date"
and "object")  [operator]
            if target_date < build_phases_config[0]["start"]:
                             ^~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
build_plan_executor.py:150:26: error: Unsupported operand types for + ("object"
and "timedelta")  [operator]
            last_phase_end = build_phases_config[-1]["start"] + timedelta(
                             ^
build_plan_executor.py:151:18: error: Argument "days" to "timedelta" has
incompatible type "object"; expected "float"  [arg-type]
                days=build_phases_config[-1]["duration"])
                     ^~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
build_plan_executor.py:152:26: error: Unsupported operand types for > ("date"
and "timedelta")  [operator]
            if target_date > last_phase_end:
                             ^~~~~~~~~~~~~~
build_plan_executor.py:157:30: error: Unsupported operand types for > ("date"
and "object")  [operator]
                if target_date > build_phases_config[i]["start"]:
                                 ^~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
build_plan_executor.py:193:31: error: Value of type "dict[Any, Any] | None" is
not indexable  [index]
                    total_capital=self.plan_data["metadata"]["total_capita...
                                  ^~~~~~~~~~~~~~~~~~~~~~~~~~
build_plan_executor.py:205:16: error: Value of type "dict[Any, Any] | None" is
not indexable  [index]
            plan = self.plan_data["position_plan"]
                   ^~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
build_plan_executor.py:206:24: error: Value of type "dict[Any, Any] | None" is
not indexable  [index]
            phase_assets = phase_summary["assets"]
                           ^~~~~~~~~~~~~~~~~~~~~~~
build_plan_executor.py:277:27: error: Item "None" of "dict[Any, Any] | None"
has no attribute "get"  [union-attr]
                target_info = self.plan_data.get("target_portfolio", {}).g...
                              ^~~~~~~~~~~~~~~~~~
build_plan_executor.py:336:24: error: Value of type "dict[Any, Any] | None" is
not indexable  [index]
                phase_name=phase_summary["name"],
                           ^~~~~~~~~~~~~~~~~~~~~
build_plan_executor.py:337:26: error: Value of type "dict[Any, Any] | None" is
not indexable  [index]
                phase_number=phase_summary["phase"],
                             ^~~~~~~~~~~~~~~~~~~~~~
build_plan_executor.py:338:27: error: Value of type "dict[Any, Any] | None" is
not indexable  [index]
                total_capital=self.plan_data["metadata"]["total_capital"],
                              ^~~~~~~~~~~~~~~~~~~~~~~~~~
build_plan_executor.py:513:24: error: Value of type "dict[Any, Any] | None" is
not indexable  [index]
                if i < len(self.plan_data["phase_summary"]):
                           ^~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
build_plan_executor.py:514:38: error: Value of type "dict[Any, Any] | None" is
not indexable  [index]
                    completed_capital += self.plan_data["phase_summary"][i...
                                         ^~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
build_plan_executor.py:517:25: error: Value of type "dict[Any, Any] | None" is
not indexable  [index]
            total_capital = self.plan_data["metadata"]["total_capital"]
                            ^~~~~~~~~~~~~~~~~~~~~~~~~~
build_plan_executor.py:527:29: error: Value of type "dict[Any, Any] | None" is
not indexable  [index]
                "target_count": self.plan_data["metadata"]["target_count"]...
                                ^~~~~~~~~~~~~~~~~~~~~~~~~~
build_plan_executor.py:528:29: error: Value of type "dict[Any, Any] | None" is
not indexable  [index]
                "build_phases": self.plan_data["metadata"]["build_phases"]...
                                ^~~~~~~~~~~~~~~~~~~~~~~~~~
build_plan_executor.py:815:9: error: Need type annotation for "positions"
(hint: "positions: dict[<type>, <type>] = ...")  [var-annotated]
            positions = {}
            ^~~~~~~~~
build_plan_executor.py:817:9: error: Need type annotation for "style_counts"
(hint: "style_counts: dict[<type>, <type>] = ...")  [var-annotated]
            style_counts = {}
            ^~~~~~~~~~~~
build_plan_executor.py:818:9: error: Need type annotation for "style_amounts"
(hint: "style_amounts: dict[<type>, <type>] = ...")  [var-annotated]
            style_amounts = {}
            ^~~~~~~~~~~~~
build_plan_executor.py:836:21: error: Incompatible types in assignment
(expression has type "TradeOrder | None", variable has type "TradeOrder") 
[assignment]
                order = next((o for o in all_orders if o.code == code), No...
                        ^~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
build_plan_executor.py:847:44: error: Argument "key" to "max" has incompatible
type overloaded function; expected
"Callable[[str], SupportsDunderLT[Any] | SupportsDunderGT[Any]]"  [arg-type]
            top_style = max(style_weights, key=style_weights.get, default=...
                                               ^~~~~~~~~~~~~~~~~
utils\execution\automated_execution_system.py:65:14: error: Incompatible types
in assignment (expression has type "logging.Logger", variable has type
"utils.logger.Logger")  [assignment]
        logger = logging.getLogger('automated_execution_system')
                 ^~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
utils\execution\automated_execution_system.py:66:18: error: Incompatible types
in assignment (expression has type
"def safe_float(x: Any, default: float | None = ...) -> Any | float | None",
variable has type
"def safe_float(val: Any, default: float | None = ...) -> float | None") 
[assignment]
        safe_float = lambda x, default=None: x if x is not None else defau...
                     ^
utils\execution\automated_execution_system.py:197:47: error: Incompatible
default for parameter "date" (default has type "None", parameter has type
"datetime")  [assignment]
        def is_trading_day(self, date: datetime = None) -> bool:
                                                  ^~~~
utils\execution\automated_execution_system.py:197:47: note: PEP 484 prohibits implicit Optional. Accordingly, mypy has changed its default to no_implicit_optional=True
utils\execution\automated_execution_system.py:197:47: note: Use https://github.com/hauntsaninja/no_implicit_optional to automatically upgrade your codebase
utils\execution\automated_execution_system.py:209:20: error: Incompatible
return value type (got "object", expected "bool")  [return-value]
                return self.special_days[date_str]['is_trading']
                       ^~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
utils\execution\automated_execution_system.py:296:21: error: "object" has no
attribute "append"  [attr-defined]
                        day_schedule['executions'].append({
                        ^~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
utils\execution\automated_execution_system.py:369:36: error: Item "None" of
"datetime | None" has no attribute "isoformat"  [union-attr]
                'next_execution_time': self.get_next_execution_time().isof...
                                       ^~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~...
utils\execution\automated_execution_system.py:567:13: error: Returning Any from
function declared to return "float"  [no-any-return]
                return min(correlation_std / 0.5, 1.0)  # 归一化到0-1
                ^~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
utils\execution\automated_execution_system.py:1110:37: error: Unsupported
operand types for * ("int" and "object")  [operator]
            queue_wait = active_count * pool['max_concurrent'] * 5.0
                                        ^~~~~~~~~~~~~~~~~~~~~~
utils\execution\automated_execution_system.py:1170:28: error: Unsupported
operand types for >= ("int" and "object")  [operator]
            if active_count >= pool['max_concurrent']:
                               ^~~~~~~~~~~~~~~~~~~~~~
utils\execution\automated_execution_system.py:1410:37: error: Incompatible
types in assignment (expression has type "Thread", variable has type "None") 
[assignment]
                self.execution_thread = threading.Thread(target=self._exec...
                                        ^~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~...
utils\execution\automated_execution_system.py:1411:13: error: "None" has no
attribute "daemon"  [attr-defined]
                self.execution_thread.daemon = True
                ^~~~~~~~~~~~~~~~~~~~~~~~~~~~
utils\execution\automated_execution_system.py:1412:13: error: "None" has no
attribute "start"  [attr-defined]
                self.execution_thread.start()
                ^~~~~~~~~~~~~~~~~~~~~~~~~~~
utils\execution\automated_execution_system.py:1531:40: error: Incompatible
types in assignment (expression has type "dict[Any, Any] | None", variable has
type "None")  [assignment]
                    self.last_hedge_plan = hedge_plan
                                           ^~~~~~~~~~
utils\execution\automated_execution_system.py:1559:43: error: Incompatible
types in assignment (expression has type "dict[Any, Any]", variable has type
"None")  [assignment]
                self.current_execution_plan = execution_plan
                                              ^~~~~~~~~~~~~~
utils\execution\automated_execution_system.py:1690:31: error: Item "None" of
"Any | None" has no attribute "coordinate"  [union-attr]
                plan = cast(Dict, self.hedge_coordinator.coordinate(
                                  ^~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
utils\execution\automated_execution_system.py:1869:26: error: Missing
positional arguments "hedge_positions", "positions_data" in call to
"build_orders"  [call-arg]
                    orders = build_orders(plan, positions, prices)
                             ^~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
utils\risk_guard_integrator.py:171:13: error: Returning Any from function
declared to return "str | None"  [no-any-return]
                return codes[0]
                ^~~~~~~~~~~~~~~
utils\risk_guard_integrator.py:178:43: error: Incompatible default for
parameter "report_date" (default has type "None", parameter has type "str") 
[assignment]
        def __init__(self, report_date: str = None, total_capital: float =...
                                              ^~~~
utils\risk_guard_integrator.py:178:43: note: PEP 484 prohibits implicit Optional. Accordingly, mypy has changed its default to no_implicit_optional=True
utils\risk_guard_integrator.py:178:43: note: Use https://github.com/hauntsaninja/no_implicit_optional to automatically upgrade your codebase
utils\risk_guard_integrator.py:181:9: error: Need type annotation for
"log_entries" (hint: "log_entries: list[<type>] = ...")  [var-annotated]
            self.log_entries = []
            ^~~~~~~~~~~~~~~~
utils\risk_guard_integrator.py:222:25: error: Returning Any from function
declared to return "dict[Any, Any] | None"  [no-any-return]
                            return json.load(f)
                            ^~~~~~~~~~~~~~~~~~~
utils\risk_guard_integrator.py:231:9: error: Returning Any from function
declared to return "dict[Any, Any]"  [no-any-return]
            return pnl_report.get('portfolio_pnl', {}).get('summary', {})
            ^~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
utils\risk_guard_integrator.py:277:13: error: Returning Any from function
declared to return "dict[Any, Any]"  [no-any-return]
                return summary
                ^~~~~~~~~~~~~~
utils\risk_guard_integrator.py:279:9: error: Returning Any from function
declared to return "dict[Any, Any]"  [no-any-return]
            return pnl_report.get('summary', {})
            ^~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
utils\risk_guard_integrator.py:288:17: error: Returning Any from function
declared to return "dict[Any, Any] | None"  [no-any-return]
                    return json.load(f)
                    ^~~~~~~~~~~~~~~~~~~
utils\risk_guard_integrator.py:452:40: error: Argument 1 to "calc_vol_scale" of
"VolTargetController" has incompatible type "float | None"; expected "float" 
[arg-type]
            vol_scale = vtc.calc_vol_scale(realized_vol)
                                           ^~~~~~~~~~~~
utils\risk_guard_integrator.py:764:13: error: Cannot assign to a type  [misc]
                KillSwitch = None
                ^~~~~~~~~~
utils\risk_guard_integrator.py:764:26: error: Incompatible types in assignment
(expression has type "None", variable has type "type[KillSwitch]")  [assignment]
                KillSwitch = None
                             ^~~~
utils\risk_guard_integrator.py:766:30: error: Function "KillSwitch" could
always be true in boolean context  [truthy-function]
            ks = KillSwitch() if KillSwitch else None
                                 ^~~~~~~~~~
utils\risk_guard_integrator.py:1045:54: error: Incompatible default for
parameter "pnl_report" (default has type "None", parameter has type
"dict[Any, Any]")  [assignment]
        def _fetch_limit_counts(self, pnl_report: Dict = None) -> tuple:
                                                         ^~~~
utils\risk_guard_integrator.py:1045:54: note: PEP 484 prohibits implicit Optional. Accordingly, mypy has changed its default to no_implicit_optional=True
utils\risk_guard_integrator.py:1045:54: note: Use https://github.com/hauntsaninja/no_implicit_optional to automatically upgrade your codebase
utils\risk_guard_integrator.py:1282:28: error: Need type annotation for
"returns_data"  [var-annotated]
                returns_data = {sym: [] for sym in symbols}
                               ^~~~~~~~~~~~~~~~~~~~~~~~~~~~
research\vibe_trading_factor_analysis\pipeline\pipeline_orchestrator.py:554:50: error:
Incompatible default for parameter "factor_history" (default has type "None",
parameter has type "list[dict[str, float]]")  [assignment]
            factor_history: List[Dict[str, float]] = None,
                                                     ^~~~
research\vibe_trading_factor_analysis\pipeline\pipeline_orchestrator.py:554:50: note: PEP 484 prohibits implicit Optional. Accordingly, mypy has changed its default to no_implicit_optional=True
research\vibe_trading_factor_analysis\pipeline\pipeline_orchestrator.py:554:50: note: Use https://github.com/hauntsaninja/no_implicit_optional to automatically upgrade your codebase
research\vibe_trading_factor_analysis\pipeline\pipeline_orchestrator.py:555:59: error:
Incompatible default for parameter "forward_returns_history" (default has type
"None", parameter has type "list[dict[str, float]]")  [assignment]
            forward_returns_history: List[Dict[str, float]] = None,
                                                              ^~~~
research\vibe_trading_factor_analysis\pipeline\pipeline_orchestrator.py:555:59: note: PEP 484 prohibits implicit Optional. Accordingly, mypy has changed its default to no_implicit_optional=True
research\vibe_trading_factor_analysis\pipeline\pipeline_orchestrator.py:555:59: note: Use https://github.com/hauntsaninja/no_implicit_optional to automatically upgrade your codebase
research\vibe_trading_factor_analysis\pipeline\pipeline_orchestrator.py:648:50: error:
Incompatible default for parameter "factor_history" (default has type "None",
parameter has type "list[dict[str, float]]")  [assignment]
            factor_history: List[Dict[str, float]] = None,
                                                     ^~~~
research\vibe_trading_factor_analysis\pipeline\pipeline_orchestrator.py:648:50: note: PEP 484 prohibits implicit Optional. Accordingly, mypy has changed its default to no_implicit_optional=True
research\vibe_trading_factor_analysis\pipeline\pipeline_orchestrator.py:648:50: note: Use https://github.com/hauntsaninja/no_implicit_optional to automatically upgrade your codebase
research\vibe_trading_factor_analysis\pipeline\pipeline_orchestrator.py:649:59: error:
Incompatible default for parameter "forward_returns_history" (default has type
"None", parameter has type "list[dict[str, float]]")  [assignment]
            forward_returns_history: List[Dict[str, float]] = None,
                                                              ^~~~
research\vibe_trading_factor_analysis\pipeline\pipeline_orchestrator.py:649:59: note: PEP 484 prohibits implicit Optional. Accordingly, mypy has changed its default to no_implicit_optional=True
research\vibe_trading_factor_analysis\pipeline\pipeline_orchestrator.py:649:59: note: Use https://github.com/hauntsaninja/no_implicit_optional to automatically upgrade your codebase
research\vibe_trading_factor_analysis\pipeline\pipeline_orchestrator.py:752:50: error:
Incompatible default for parameter "factor_history" (default has type "None",
parameter has type "list[dict[str, float]]")  [assignment]
            factor_history: List[Dict[str, float]] = None,
                                                     ^~~~
research\vibe_trading_factor_analysis\pipeline\pipeline_orchestrator.py:752:50: note: PEP 484 prohibits implicit Optional. Accordingly, mypy has changed its default to no_implicit_optional=True
research\vibe_trading_factor_analysis\pipeline\pipeline_orchestrator.py:752:50: note: Use https://github.com/hauntsaninja/no_implicit_optional to automatically upgrade your codebase
research\vibe_trading_factor_analysis\pipeline\pipeline_orchestrator.py:753:59: error:
Incompatible default for parameter "forward_returns_history" (default has type
"None", parameter has type "list[dict[str, float]]")  [assignment]
            forward_returns_history: List[Dict[str, float]] = None,
                                                              ^~~~
research\vibe_trading_factor_analysis\pipeline\pipeline_orchestrator.py:753:59: note: PEP 484 prohibits implicit Optional. Accordingly, mypy has changed its default to no_implicit_optional=True
research\vibe_trading_factor_analysis\pipeline\pipeline_orchestrator.py:753:59: note: Use https://github.com/hauntsaninja/no_implicit_optional to automatically upgrade your codebase
research\vibe_trading_factor_analysis\pipeline\pipeline_orchestrator.py:935:50: error:
Incompatible default for parameter "factor_history" (default has type "None",
parameter has type "list[dict[str, float]]")  [assignment]
            factor_history: List[Dict[str, float]] = None,
                                                     ^~~~
research\vibe_trading_factor_analysis\pipeline\pipeline_orchestrator.py:935:50: note: PEP 484 prohibits implicit Optional. Accordingly, mypy has changed its default to no_implicit_optional=True
research\vibe_trading_factor_analysis\pipeline\pipeline_orchestrator.py:935:50: note: Use https://github.com/hauntsaninja/no_implicit_optional to automatically upgrade your codebase
research\vibe_trading_factor_analysis\pipeline\pipeline_orchestrator.py:936:59: error:
Incompatible default for parameter "forward_returns_history" (default has type
"None", parameter has type "list[dict[str, float]]")  [assignment]
            forward_returns_history: List[Dict[str, float]] = None,
                                                              ^~~~
research\vibe_trading_factor_analysis\pipeline\pipeline_orchestrator.py:936:59: note: PEP 484 prohibits implicit Optional. Accordingly, mypy has changed its default to no_implicit_optional=True
research\vibe_trading_factor_analysis\pipeline\pipeline_orchestrator.py:936:59: note: Use https://github.com/hauntsaninja/no_implicit_optional to automatically upgrade your codebase
research\vibe_trading_factor_analysis\pipeline\pipeline_orchestrator.py:1014:50: error:
Incompatible default for parameter "factor_history" (default has type "None",
parameter has type "list[dict[str, float]]")  [assignment]
            factor_history: List[Dict[str, float]] = None,
                                                     ^~~~
research\vibe_trading_factor_analysis\pipeline\pipeline_orchestrator.py:1014:50: note: PEP 484 prohibits implicit Optional. Accordingly, mypy has changed its default to no_implicit_optional=True
research\vibe_trading_factor_analysis\pipeline\pipeline_orchestrator.py:1014:50: note: Use https://github.com/hauntsaninja/no_implicit_optional to automatically upgrade your codebase
research\vibe_trading_factor_analysis\pipeline\pipeline_orchestrator.py:1015:59: error:
Incompatible default for parameter "forward_returns_history" (default has type
"None", parameter has type "list[dict[str, float]]")  [assignment]
            forward_returns_history: List[Dict[str, float]] = None,
                                                              ^~~~
research\vibe_trading_factor_analysis\pipeline\pipeline_orchestrator.py:1015:59: note: PEP 484 prohibits implicit Optional. Accordingly, mypy has changed its default to no_implicit_optional=True
research\vibe_trading_factor_analysis\pipeline\pipeline_orchestrator.py:1015:59: note: Use https://github.com/hauntsaninja/no_implicit_optional to automatically upgrade your codebase
utils\execution\daily_build_and_hedge.py:128:13: error: Module
"utils.etf_flow_monitor" has no attribute "ETFMonitor"  [attr-defined]
                from utils.etf_flow_monitor import ETFMonitor
                ^~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
utils\research_distiller.py:72: error: Unused "type: ignore" comment 
[unused-ignore]
            import llm_client  # type: ignore
            ^~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
utils\research_distiller.py:641:18: error: "None" not callable  [misc]
            result = _chat_fn(
                     ^~~~~~~~~
utils\research_distiller.py:924: error: Unused "type: ignore" comment 
[unused-ignore]
                    import pdfplumber  # type: ignore
                    ^~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
utils\research_distiller.py:935: error: Unused "type: ignore" comment 
[unused-ignore]
                    from pdfminer.high_level import extract_text  # type: ...
                    ^~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~...
mypy.ini: note: unused section(s): [mypy-v8.3_institutional.daily_workflow]
Found 560 errors in 85 files (checked 150 source files)
```

</details>