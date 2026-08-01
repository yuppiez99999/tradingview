# -*- coding: utf-8 -*-
"""T06: 给 walk_forward.py 添加 run_cpcv 方法."""
from __future__ import annotations

from pathlib import Path

FILE = Path(r"E:\各种PY程序\28-终极量化交易系统8.4\ms_strategy\src\backtest\walk_forward.py")

OLD_BLOCK = """    def summary(self) -> str:
        \"\"\"打印汇总\"\"\"
        metrics = self.aggregate_metrics()
        lines = [
            f\"=== Walk-Forward 汇总 ({metrics.get('n_windows', 0)} 个窗口) ===\",
            f\"Sortino Ratio:  {metrics.get('sortino', 0):.4f}\",
            f\"Calmar Ratio:   {metrics.get('calmar', 0):.4f}\",
            f\"Max DD:         {metrics.get('max_dd', 0):.2%}\",
            f\"Annual Return:  {metrics.get('annual_return', 0):.2%}\",
            f\"Annual Vol:     {metrics.get('annual_vol', 0):.2%}\",
            f\"Sharpe:         {metrics.get('sharpe', 0):.4f}\",
            f\"Win Rate:       {metrics.get('win_rate', 0):.2%}\",
        ]
        return '\\n'.join(lines)"""

NEW_BLOCK = """    def summary(self) -> str:
        \"\"\"打印汇总\"\"\"
        metrics = self.aggregate_metrics()
        lines = [
            f\"=== Walk-Forward 汇总 ({metrics.get('n_windows', 0)} 个窗口) ===\",
            f\"Sortino Ratio:  {metrics.get('sortino', 0):.4f}\",
            f\"Calmar Ratio:   {metrics.get('calmar', 0):.4f}\",
            f\"Max DD:         {metrics.get('max_dd', 0):.2%}\",
            f\"Annual Return:  {metrics.get('annual_return', 0):.2%}\",
            f\"Annual Vol:     {metrics.get('annual_vol', 0):.2%}\",
            f\"Sharpe:         {metrics.get('sharpe', 0):.4f}\",
            f\"Win Rate:       {metrics.get('win_rate', 0):.2%}\",
        ]
        return '\\n'.join(lines)

    # ============================================================
    # T06 (2026-07-28): Combinatorial Purged CV 集成
    # ============================================================
    def run_cpcv(self, data: pd.DataFrame,
                 strategy_fn: Callable,
                 n_groups: int = 6,
                 n_test_groups: int = 2,
                 purge_pct: float = 0.0,
                 embargo_pct: float = 0.01,
                 verbose: bool = True) -> dict:
        \"\"\"执行 Combinatorial Purged CV (Lopez de Prado 2018).

        替代原 5-fold CV, 解决 lookahead/leakage 问题.
        生成 C(N, k) 个独立回测路径, 给出 Sharpe 分布.

        Args:
            data: 全量价格/因子数据 (带时间索引)
            strategy_fn: (train_df, test_df) -> test_returns Series
            n_groups: 组数 N (默认 6)
            n_test_groups: 测试组数 k (默认 2)
            purge_pct: purge 比例 (默认 0, 按时间重叠 purge)
            embargo_pct: embargo 比例 (默认 0.01 = 1%)
            verbose: 是否打印日志

        Returns:
            {
                'status': 'OK'/'NO_VALID_PATH',
                'n_paths': int,
                'results': List[CPCVResult],
                'aggregated': dict,
                'summary': str,
            }
        \"\"\"
        from .combinatorial_purged_cv import CPCVConfig, CombinatorialPurgedCV

        config = CPCVConfig(
            n_groups=n_groups,
            n_test_groups=n_test_groups,
            purge_pct=purge_pct,
            embargo_pct=embargo_pct,
            min_train_samples=50,
        )
        cv = CombinatorialPurgedCV(config)
        results = cv.run(data, strategy_fn, verbose=verbose)

        if not results:
            return {
                'status': 'NO_VALID_PATH',
                'n_paths': 0,
                'results': [],
                'aggregated': {},
                'summary': 'CPCV: 无有效路径',
            }

        aggregated = cv.aggregate(results)
        summary_str = cv.summary(results)

        return {
            'status': 'OK',
            'n_paths': len(results),
            'results': results,
            'aggregated': aggregated,
            'summary': summary_str,
        }"""


def main() -> None:
    text = FILE.read_text(encoding="utf-8")
    if OLD_BLOCK not in text:
        print("ERROR: 未找到原始代码块")
        return
    if "run_cpcv" in text:
        print("已存在 run_cpcv 方法, 跳过")
        return
    new_text = text.replace(OLD_BLOCK, NEW_BLOCK, 1)
    FILE.write_text(new_text, encoding="utf-8")
    print(f"已修改: {FILE.name}")


if __name__ == "__main__":
    main()
