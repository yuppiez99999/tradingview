"""绩效报告生成器 (empyrical + pyfolio, Wave 12-A #5).

功能:
  1. 标准绩效指标计算 (Sharpe/Sortino/MaxDD/Calmar/年化收益/波动率/胜率)
  2. HTML 回测报告生成
  3. 文本摘要生成
  4. 降级不崩溃 (empyrical 不可用时手动计算)

依赖: empyrical (已安装 v0.5.5), pyfolio (已安装 v0.9.2, 可选)
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

import numpy as np

if not hasattr(np, "NINF"):
    np.NINF = -np.inf

try:
    import empyrical as ep

    _EMPYRICAL_AVAILABLE = True
except ImportError:
    ep = None
    _EMPYRICAL_AVAILABLE = False

logger = logging.getLogger(__name__)

_DEFAULT_FREQ = 252
_RISK_FREE = 0.0


class PerformanceReporter:
    """绩效报告生成器 (empyrical + pyfolio, Wave 12-A #5)."""

    def calculate_metrics(
        self,
        returns: Any,
        benchmark: Any = None,
        freq: int = _DEFAULT_FREQ,
    ) -> dict[str, float | str]:
        """计算标准绩效指标.

        Args:
            returns: 日收益率序列 (list/np.ndarray/pd.Series).
            benchmark: 基准收益率序列 (可选).
            freq: 年化频率 (默认 252 交易日).

        Returns:
            dict: 绩效指标字典.
        """
        rets = self._to_array(returns)
        if len(rets) == 0:
            return {"error": "empty_returns"}

        metrics: dict[str, float | str] = {}

        if _EMPYRICAL_AVAILABLE:
            metrics.update(self._metrics_empyrical(rets, freq))
        else:
            metrics.update(self._metrics_manual(rets, freq))

        metrics["total_return"] = float(np.prod(1 + rets) - 1)
        metrics["n_periods"] = int(len(rets))
        metrics["positive_periods"] = int(np.sum(rets > 0))
        metrics["win_rate"] = float(np.mean(rets > 0))

        if benchmark is not None:
            bench = self._to_array(benchmark)
            if len(bench) == len(rets):
                metrics.update(self._benchmark_metrics(rets, bench, freq))

        return metrics

    def generate_html_report(
        self,
        returns: Any,
        benchmark: Any = None,
        title: str = "回测绩效报告",
        freq: int = _DEFAULT_FREQ,
    ) -> str:
        """生成 HTML 回测报告.

        Args:
            returns: 日收益率序列.
            benchmark: 基准收益率序列 (可选).
            title: 报告标题.
            freq: 年化频率.

        Returns:
            str: HTML 报告字符串.
        """
        metrics = self.calculate_metrics(returns, benchmark, freq)
        cum_returns = self._cumulative_returns(returns)

        lines = []
        lines.append("<!DOCTYPE html>")
        lines.append('<html lang="zh-CN"><head>')
        lines.append('<meta charset="UTF-8">')
        lines.append(f"<title>{title}</title>")
        lines.append("<style>")
        lines.append("body { font-family: 'Segoe UI', sans-serif; margin: 20px; background: #0d1117; color: #e6edf3; }")
        lines.append("h1 { color: #1890FF; }")
        lines.append("table { border-collapse: collapse; width: 100%; }")
        lines.append("th, td { border: 1px solid #30363d; padding: 8px 12px; text-align: left; }")
        lines.append("th { background: #161b22; color: #1890FF; }")
        lines.append("td { background: #0d1117; }")
        lines.append(".positive { color: #3fb950; }")
        lines.append(".negative { color: #f85149; }")
        lines.append("</style>")
        lines.append("</head><body>")
        lines.append(f"<h1>{title}</h1>")
        lines.append(f"<p>生成时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}</p>")

        lines.append("<h2>绩效指标</h2>")
        lines.append("<table><tr><th>指标</th><th>值</th></tr>")
        for key, value in metrics.items():
            if isinstance(value, float):
                display = f"{value:.4f}"
                css = "positive" if value >= 0 else "negative"
                lines.append(f'<tr><td>{self._metric_label(key)}</td><td class="{css}">{display}</td></tr>')
            else:
                lines.append(f"<tr><td>{self._metric_label(key)}</td><td>{value}</td></tr>")
        lines.append("</table>")

        lines.append("<h2>累计收益曲线</h2>")
        lines.append('<div id="cumchart" style="height:300px;"></div>')
        lines.append("<script>")
        lines.append("const cumData = " + str([round(float(x), 4) for x in cum_returns]) + ";")
        lines.append("""
const canvas = document.createElement('canvas');
canvas.width = 800; canvas.height = 300;
document.getElementById('cumchart').appendChild(canvas);
const ctx = canvas.getContext('2d');
const max = Math.max(...cumData), min = Math.min(...cumData);
const range = max - min || 1;
ctx.strokeStyle = '#1890FF'; ctx.lineWidth = 2;
cumData.forEach((v, i) => {
  const x = (i / (cumData.length - 1)) * canvas.width;
  const y = canvas.height - ((v - min) / range) * canvas.height;
  if (i === 0) ctx.moveTo(x, y); else ctx.lineTo(x, y);
});
ctx.stroke();
""")
        lines.append("</script>")

        lines.append("</body></html>")
        return "\n".join(lines)

    def generate_summary(
        self,
        returns: Any,
        benchmark: Any = None,
        freq: int = _DEFAULT_FREQ,
    ) -> str:
        """生成文本摘要.

        Returns:
            str: 绩效摘要文本.
        """
        m = self.calculate_metrics(returns, benchmark, freq)
        if "error" in m:
            return f"计算失败: {m['error']}"

        lines = [
            "=== 绩效摘要 ===",
            f"年化收益: {m.get('annual_return', 0):.2%}",
            f"年化波动: {m.get('annual_volatility', 0):.2%}",
            f"Sharpe:   {m.get('sharpe_ratio', 0):.4f}",
            f"Sortino:  {m.get('sortino_ratio', 0):.4f}",
            f"最大回撤: {m.get('max_drawdown', 0):.2%}",
            f"Calmar:   {m.get('calmar_ratio', 0):.4f}",
            f"胜率:     {m.get('win_rate', 0):.2%}",
            f"总收益:   {m.get('total_return', 0):.2%}",
        ]
        return "\n".join(lines)

    def _metrics_empyrical(self, rets: np.ndarray, freq: int) -> dict[str, float]:
        """empyrical 后端计算."""
        return {
            "annual_return": float(ep.annual_return(rets, annualization=freq)),
            "annual_volatility": float(ep.annual_volatility(rets, annualization=freq)),
            "sharpe_ratio": float(ep.sharpe_ratio(rets, risk_free=_RISK_FREE, annualization=freq)),
            "sortino_ratio": float(ep.sortino_ratio(rets, required_return=_RISK_FREE, annualization=freq)),
            "max_drawdown": float(ep.max_drawdown(rets)),
            "calmar_ratio": float(ep.calmar_ratio(rets, annualization=freq)),
            "omega_ratio": float(ep.omega_ratio(rets, risk_free=_RISK_FREE, annualization=freq)),
            "downside_risk": float(ep.downside_risk(rets, required_return=_RISK_FREE, annualization=freq)),
        }

    @staticmethod
    def _metrics_manual(rets: np.ndarray, freq: int) -> dict[str, float]:
        """手动降级计算."""
        mean_daily = float(np.mean(rets))
        std_daily = float(np.std(rets, ddof=1)) if len(rets) > 1 else 0.0

        annual_return = (1 + mean_daily) ** freq - 1
        annual_vol = std_daily * np.sqrt(freq)
        sharpe = (mean_daily * freq) / (annual_vol) if annual_vol > 0 else 0.0

        downside = rets[rets < 0]
        downside_std = float(np.std(downside, ddof=1)) if len(downside) > 1 else 0.0
        sortino = (mean_daily * freq) / (downside_std * np.sqrt(freq)) if downside_std > 0 else 0.0

        cum = np.cumprod(1 + rets)
        peak = np.maximum.accumulate(cum)
        drawdown = (cum - peak) / peak
        max_dd = float(np.min(drawdown))

        calmar = annual_return / abs(max_dd) if max_dd < 0 else 0.0

        return {
            "annual_return": annual_return,
            "annual_volatility": annual_vol,
            "sharpe_ratio": sharpe,
            "sortino_ratio": sortino,
            "max_drawdown": max_dd,
            "calmar_ratio": calmar,
            "omega_ratio": 0.0,
            "downside_risk": downside_std * np.sqrt(freq),
        }

    @staticmethod
    def _benchmark_metrics(rets: np.ndarray, bench: np.ndarray, freq: int) -> dict[str, float]:
        """基准对比指标."""
        if _EMPYRICAL_AVAILABLE:
            return {
                "alpha": float(ep.alpha(rets, bench, annualization=freq)),
                "beta": float(ep.beta(rets, bench)),
                "information_ratio": float(ep.excess_sharpe(rets, bench)),
            }
        excess = rets - bench
        std_excess = float(np.std(excess, ddof=1)) if len(excess) > 1 else 0.0
        return {
            "alpha": float(np.mean(excess) * freq),
            "beta": float(np.cov(rets, bench)[0, 1] / np.var(bench)) if np.var(bench) > 0 else 0.0,
            "information_ratio": (float(np.mean(excess)) * freq) / (std_excess * np.sqrt(freq))
            if std_excess > 0
            else 0.0,
        }

    @staticmethod
    def _to_array(data: Any) -> np.ndarray:
        """转换为 numpy 数组."""
        if hasattr(data, "values"):
            data = data.values
        return np.asarray(data, dtype=np.float64).ravel()

    @staticmethod
    def _cumulative_returns(returns: Any) -> np.ndarray:
        """计算累计收益."""
        rets = PerformanceReporter._to_array(returns)
        return np.cumprod(1 + rets) - 1

    @staticmethod
    def _metric_label(key: str) -> str:
        """指标中文标签."""
        labels = {
            "annual_return": "年化收益率",
            "annual_volatility": "年化波动率",
            "sharpe_ratio": "Sharpe 比率",
            "sortino_ratio": "Sortino 比率",
            "max_drawdown": "最大回撤",
            "calmar_ratio": "Calmar 比率",
            "omega_ratio": "Omega 比率",
            "downside_risk": "下行风险",
            "total_return": "总收益率",
            "n_periods": "交易周期数",
            "positive_periods": "盈利周期数",
            "win_rate": "胜率",
            "alpha": "Alpha",
            "beta": "Beta",
            "information_ratio": "信息比率",
        }
        return labels.get(key, key)
