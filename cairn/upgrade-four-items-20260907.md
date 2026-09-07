# 量化系统升级四件套 — T1~T5 执行记录 (2026-09-07)

> 任务: 用户确认的六项升级中实际需补的四项 + 全量核验。
> 结论先行: 四项全部零新增生产依赖 (catboost 砍掉守 2027 禁令, MOSEK 不买),
> 全部 flag 默认关闭 / fail-open 回退, 完成三连 (ruff+format+pytest) 全绿。

## T1 — CVaR 生产配置显式固化

- **核查发现**: G11 蒙特卡洛 CVaR 代码层早已修复 (`wt_risk_control.py:35` `_CVAR_CONFIG_DEFAULT` = monte_carlo/student_t/dof5/50000 路径/seed42, 且 `tests/test_g11_monte_carlo_cvar.py` 锁住), 但 `system_config.json` 缺 `risk_management.cvar` 段 → 行为靠隐式默认, 默认值一改即静默漂移。
- **改动**: `config/system_config.json` 显式固化 cvar 段 (与 `_CVAR_CONFIG_DEFAULT` 逐字段一致)。
- **回归锁**: `tests/unit/test_t1_cvar_config_explicit.py` — ① config 段与代码默认逐字段一致 (漂移检测) ② `_load_cvar_config()` 实际生效 monte_carlo ③ `CVaRConfig.from_system_config()` (risk_metric 子段路径) 不因兄弟段新增崩溃 (双代码路径盲区防御)。
- **实证**: 3 passed; G11 既有 11 tests 无回归。

## T2 — 波动率调制 no-trade band

- **核心**: `utils/risk/no_trade_band.py` — band = max(abs_tol=2%, k=0.5 × 目标权重 × sigma)。
- **失败语义**: sigma 缺失/无效 → fail-open 退化 abs_tol (reason=sigma_unavailable_abs_tol); current_weight 无效 → fail-close 保守触发调仓 (invalid_current_weight_fail_close)。
- **接入**: `utils/execution/rebalance_execution_orders.py` `generate_rebalance_orders(..., *, volatility=None)` keyword-only 可选参数 — 4 处既有调用方 (automated_execution_system/run_daily_eod/根薄包装) 零破坏; main() 读可选 `reports/style_volatility.json` (缺失时 info 提示未启用)。
- **实证**: 23 passed; T3.6 迁移 + G7 automated_execution_boost 回归 157 passed。⚠️ 浮点边界教训: 0.17-0.15 = 0.0200...018 > 0.02, 边界用例须避开浮点精确相等。

## T3 — CVXPY 凸优化组合权重

- **发现**: `utils/institutional_optimizer.py` 是解析近似 (风险平价+信号倾斜), 非凸优化; 生产入口 `etf_option_hedge_rebalancer.py:654` 走 `adjust_target_weights` (α=0.05 线性混合)。
- **新增** (portfolio_optimizer.py): `optimize_weights_cvx()` — Ledoit-Wolf shrinkage (sklearn) → Black-Litterman 后验 (信号∈[-1,1] 作绝对观点, Q=signal×view_scale, Ω=τΣ) → CVXPY: max μᵀw − γwᵀΣw − cost·‖w−w0‖₁, 约束 sum≤1 / 0≤w≤12% (单票上限对齐根 CLAUDE.md) / 换手≤15%。求解器链 CLARABEL→ECOS→SCS→线性混合。
- **分流**: `optimize_target_weights()` — flag `USE_CVX_PORTFOLIO_OPTIMIZER` (已注册, 默认 false) 控制; 开启时影子双轨对比落盘 `reports/portfolio_optimizer_shadow/`。
- **依赖懒加载**: cvxpy/sklearn 在方法内 import (A 级依赖懒加载铁律), 缺失时 fail-open 回退。
- **实证**: 8 passed (约束满足/信号方向性/数据不足回退/空输入/换手约束/bind+flag 分流); 既有 portfolio_optimizer 29 tests 无回归。

## T4 — 两树 Stacking Ensemble + TimesFM

- **核心**: `utils/alpha/ensemble_stacker.py` — `purged_kfold_indices` (验证折连续块, 训练折两侧剔除 embargo=5, 根除 5 日前瞻标签跨折泄漏) + L1=LGB+XGB OOF + L2=Ridge; TimesFM 预测列由调用方传入作 meta 特征 (库内不加载模型, 解耦); xgboost 缺失 fail-open 单树。
- **CLI**: `ensemble_trainer.py` (根目录, subprocess 契约与 lgb_enhanced_trainer 一致) — 复用 `_build_all_features`, y=5日前瞻收益 shift(-5) 防前视, 落盘 `models/ensemble/{symbol}_ensemble_meta.json`。⚠️ 依赖真实行情数据源, 端到端未跑 (诚实标注), 算法层 10 tests 锁定。
- **编排**: `run_auto_retrain.py` 阶段 3.5 (`run_phase3_5_ensemble`, flag `USE_ENSEMBLE_STACKING` 已注册默认 false) — 对重训成功标的批量拟合, 超时/失败非阻断。
- **实证**: 10 passed — 含无泄漏证明 (embargo 隔离断言) / 覆盖完整性 / OOF IC>0.1 / 单模型降级。

## T5 — verification-gate 全量核验 (只读)

- 接线冒烟: T1 config↔代码一致 / T2 band 数值 / T3+T4 flag 注册且默认 false / CLI AST 可解析 — PASS
- check_dangling_refs: 无悬挂引用 — PASS
- industrial_grade_check: 10 PASS / 2 WARN (fills 新鲜度等既有项, 与本次改动无关) / 0 FAIL
- ruff 增量门禁: 通过
- 四项测试全集: **44 passed**

## 边界与未完成 (诚实声明)

1. `ensemble_trainer.py` 端到端需真实行情 (Wind/新浪), 本会话未跑真实数据 — 算法层已锁定, 首次真实运行应在有数据源的会话验证 OOF IC 与 lgb_enhanced 单模型对照。
2. T3/T4 均为影子/观察层: cvx 权重未接入生产权重链 (flag 关), ensemble 未替换 LGB 信号 (flag 关) — 接入生产需影子验证期 + 单独评审。
3. catboost 未装 (2027 禁令), 两树而非三树; MOSEK 未买 (26 标的 CLARABEL 毫秒级够用)。
4. 执行拆单 IS 算法明确不做: 300 万体量 TWAP/VWAP 足够 (10 亿以下铁律)。
