# -*- coding: utf-8 -*-
import pathlib

p = pathlib.Path(r"E:\各种PY程序\28-终极量化交易系统7.1\institutional_pipeline_runner.py")
text = p.read_text(encoding="utf-8")

old = """        self.risk_budget_engine = RiskBudgetEngine(
            total_capital=self.ctx.total_capital,
            max_weight=0.20,
            max_daily_var_95=0.03,
            max_single_var_95=0.01,
        )"""
new = """        self.risk_budget_engine = RiskBudgetEngine(
            total_capital=self.ctx.total_capital,
            max_weight=0.25,
            max_daily_var_95=0.035,
            max_single_var_95=0.012,
        )"""

if old not in text:
    raise SystemExit("risk budget target not found")
text = text.replace(old, new)
p.write_text(text, encoding="utf-8")
print("relaxed risk budget params")
