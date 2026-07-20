# -*- coding: utf-8 -*-
"""Patch optimizer for target: annualized>=8%, max drawdown<15%"""
import pathlib

root = pathlib.Path(r"E:\各种PY程序\28-终极量化交易系统7.1")

# 1) patch institutional_pipeline_runner.py
p = root / "institutional_pipeline_runner.py"
text = p.read_text(encoding="utf-8")
text = text.replace(
    "self.risk_budget_engine = RiskBudgetEngine(\n"
    "            total_capital=self.ctx.total_capital,\n"
    "            max_weight=0.30,\n"
    "            max_daily_var_95=0.045,\n"
    "            max_single_var_95=0.015,\n"
    "        )\n"
    "        self.optimizer = InstitutionalPortfolioOptimizer(\n"
    "            total_capital=self.ctx.total_capital,\n"
    "            max_weight=0.30,\n"
    "        )",
    "self.risk_budget_engine = RiskBudgetEngine(\n"
    "            total_capital=self.ctx.total_capital,\n"
    "            max_weight=0.20,\n"
    "            max_daily_var_95=0.025,\n"
    "            max_single_var_95=0.008,\n"
    "        )\n"
    "        self.optimizer = InstitutionalPortfolioOptimizer(\n"
    "            total_capital=self.ctx.total_capital,\n"
    "            max_weight=0.20,\n"
    "            min_position_weight=0.03,\n"
    "        )"
)
p.write_text(text, encoding="utf-8")
print("patched pipeline runner risk/optimizer params")

# 2) patch institutional_optimizer.py
p = root / "utils" / "institutional_optimizer.py"
text = p.read_text(encoding="utf-8")
old = "            candidate = np.where(\n" \
      "                mu > 0.0,\n" \
      "                0.6 * base_weights + 0.4 * signal_weights,\n" \
      "                0.0,\n" \
      "            )"
new = "            candidate = np.where(\n" \
      "                mu > 0.0,\n" \
      "                0.55 * base_weights + 0.45 * signal_weights,\n" \
      "                0.0,\n" \
      "            )\n" \
      "            # 给正收益标的设最低参与权重，避免过度集中\n" \
      "            active = mu > 0.0\n" \
      "            if np.any(active):\n" \
      "                candidate[active] = np.maximum(candidate[active], self.min_position_weight)"
if old not in text:
    raise SystemExit("optimizer target block not found")
text = text.replace(old, new)
p.write_text(text, encoding="utf-8")
print("patched optimizer signal tilt + min weight")
