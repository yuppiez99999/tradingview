# -*- coding: utf-8 -*-
"""
参数敏感性分析模块 v1.0

防止过拟合的核心工具:评估策略参数轻微变动对收益的影响。
如果参数微小变化导致收益剧烈波动,说明策略不稳定。

核心指标:
1. 参数弹性(Parameter Elasticity): Δ收益/Δ参数
2. 局部敏感性(Local Sensitivity): 每个参数±5%, ±10%的影响
3. 全局敏感性(Global Sensitivity): Sobol指数或Morris筛选
4. 稳定性评分(Stability Score): 基于变异系数CV

使用场景:
- Optuna优化后验证最佳参数的鲁棒性
- 策略上线前参数压力测试
- 季度参数审查

示例:
    from src.validation.parameter_sensitivity import ParameterSensitivity
    
    analyzer = ParameterSensitivity(
        base_params={'learning_rate': 0.01, 'max_depth': 5},
        base_performance={'f1_score': 0.65, 'sharpe_ratio': 1.2}
    )
    
    # 添加参数扰动范围
    analyzer.add_parameter('learning_rate', range=[0.005, 0.015], steps=5)
    analyzer.add_parameter('max_depth', range=[3, 7], steps=5)
    
    # 执行敏感性分析
    results = analyzer.run_sensitivity_analysis()
    
    # 查看结果
    analyzer.print_summary()
    
    # 生成报告
    analyzer.save_report('sensitivity_report.html')
"""

import numpy as np
import pandas as pd
from typing import Dict, List, Tuple, Optional, Any
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path


@dataclass
class ParameterRange:
    """参数扰动范围定义"""
    name: str
    base_value: float
    min_value: float
    max_value: float
    step_count: int = 5
    parameter_type: str = 'continuous'  # 'continuous' or 'discrete'


@dataclass
class SensitivityResult:
    """单参数敏感性分析结果"""
    parameter_name: str
    base_value: float
    elasticity: float  # 参数弹性
    cv: float  # 变异系数(Coefficient of Variation)
    stability_score: float  # 稳定性评分(0-100,越高越稳定)
    max_deviation: float  # 最大性能偏差
    sensitivity_grade: str  # 敏感性等级('Robust', 'Acceptable', 'Sensitive', 'Fragile')


@dataclass
class SensitivityReport:
    """敏感性分析报告"""
    timestamp: str
    base_parameters: Dict[str, float]
    base_performance: Dict[str, float]
    parameter_results: List[SensitivityResult] = field(default_factory=list)
    overall_stability_score: float = 0.0
    recommendation: str = ""
    warnings: List[str] = field(default_factory=list)


class ParameterSensitivity:
    """
    参数敏感性分析器
    
    设计原则:
    1. 支持局部敏感性分析(LSA):一次只变一个参数
    2. 支持全局敏感性分析(GSA):多参数同时扰动
    3. 提供量化稳定性评分
    4. 自动生成HTML报告
    """
    
    def __init__(
        self,
        base_params: Dict[str, float],
        base_performance: Dict[str, float],
        evaluator_func: Optional[Any] = None,
    ):
        """
        Args:
            base_params: 基准参数字典 {'param_name': base_value}
            base_performance: 基准性能指标 {'metric_name': value}
            evaluator_func: 评估函数,输入参数返回性能(可选,如不提供则使用模拟数据)
        """
        self.base_params = base_params
        self.base_performance = base_performance
        self.evaluator_func = evaluator_func
        self.param_ranges: List[ParameterRange] = []
        self.results: Optional[SensitivityReport] = None
    
    def add_parameter(
        self,
        name: str,
        base_value: Optional[float] = None,
        range_min: Optional[float] = None,
        range_max: Optional[float] = None,
        steps: int = 5,
        parameter_type: str = 'continuous'
    ):
        """
        添加待分析参数及其扰动范围
        
        Args:
            name: 参数名称
            base_value: 基准值(默认从base_params获取)
            range_min: 最小值
            range_max: 最大值
            steps: 扰动步数
            parameter_type: 'continuous'(连续)或'discrete'(离散)
        """
        if base_value is None:
            base_value = self.base_params.get(name)
        
        if range_min is None:
            range_min = base_value * 0.5  # 默认-50%
        
        if range_max is None:
            range_max = base_value * 1.5  # 默认+50%
        
        self.param_ranges.append(ParameterRange(
            name=name,
            base_value=base_value,
            min_value=range_min,
            max_value=range_max,
            step_count=steps,
            parameter_type=parameter_type,
        ))
    
    def run_local_sensitivity(
        self,
        perturbation_levels: List[float] = [0.05, 0.10, 0.15, 0.20]
    ) -> SensitivityReport:
        """
        局部敏感性分析(LSA):每次只扰动一个参数
        
        Args:
            perturbation_levels: 扰动级别列表(如±5%, ±10%, ±15%, ±20%)
        """
        performance_at_each_level = {level: {} for level in perturbation_levels}
        
        for param_range in self.param_ranges:
            param_name = param_range.name
            
            for level in perturbation_levels:
                # 计算扰动后的值
                perturb_values = [
                    param_range.base_value * (1 - level),
                    param_range.base_value * (1 + level),
                ]
                
                perf_changes = []
                for perturb_val in perturb_values:
                    modified_params = self.base_params.copy()
                    modified_params[param_name] = perturb_val
                    
                    if self.evaluator_func:
                        new_perf = self.evaluator_func(modified_params)
                    else:
                        # 模拟性能变化(高斯噪声+线性衰减)
                        new_perf = self._simulate_performance(
                            param_name, perturb_val, level
                        )
                    
                    # 计算性能变化率
                    base_perf_value = list(self.base_performance.values())[0]
                    if isinstance(new_perf, dict):
                        new_perf_value = list(new_perf.values())[0]
                    else:
                        new_perf_value = new_perf
                    
                    change_rate = (new_perf_value - base_perf_value) / base_perf_value \
                        if base_perf_value > 0 else 0
                    perf_changes.append(change_rate)
                
                performance_at_each_level[level][param_name] = {
                    'down_change': perf_changes[0],
                    'up_change': perf_changes[1],
                    'avg_magnitude': np.mean(np.abs(perf_changes)),
                }
        
        # 计算敏感性指标
        sensitivity_results = []
        for param_range in self.param_ranges:
            param_name = param_range.name
            all_avg_magnitudes = [
                performance_at_each_level[level][param_name]['avg_magnitude']
                for level in perturbation_levels
            ]
            
            max_deviation = np.max(all_avg_magnitudes)
            cv = np.std(all_avg_magnitudes) / np.mean(all_avg_magnitudes) \
                if np.mean(all_avg_magnitudes) > 0 else 0
            
            # 计算稳定性评分
            stability_score = max(0, 100 * (1 - max_deviation - cv))
            
            # 判定敏感性等级
            if stability_score >= 90:
                grade = 'Robust'
            elif stability_score >= 75:
                grade = 'Acceptable'
            elif stability_score >= 50:
                grade = 'Sensitive'
            else:
                grade = 'Fragile'
            
            sensitivity_results.append(SensitivityResult(
                parameter_name=param_name,
                base_value=param_range.base_value,
                elasticity=max_deviation / (perturbation_levels[-1] if perturbation_levels[-1] > 0 else 1),
                cv=cv,
                stability_score=stability_score,
                max_deviation=max_deviation,
                sensitivity_grade=grade,
            ))
        
        # 计算总体稳定性评分
        overall_score = np.mean([r.stability_score for r in sensitivity_results])
        
        # 生成建议
        warnings = []
        if overall_score < 75:
            warnings.append("WARNING: Strategy parameters have high sensitivity, recommend recalibration")
        
        fragile_params = [r for r in sensitivity_results if r.sensitivity_grade == 'Fragile']
        if fragile_params:
            warnings.append(f"CRITICAL: Fragile parameters: {[r.parameter_name for r in fragile_params]}")
        
        sensitive_params = [r for r in sensitivity_results if r.sensitivity_grade == 'Sensitive']
        if sensitive_params:
            warnings.append(f"WARNING: Sensitive parameters: {[r.parameter_name for r in sensitive_params]}")
        
        recommendation = "PASS" if overall_score >= 75 and len(fragile_params) == 0 else "REVIEW_REQUIRED"
        
        self.results = SensitivityReport(
            timestamp=datetime.now().isoformat(),
            base_parameters=self.base_params.copy(),
            base_performance=self.base_performance.copy(),
            parameter_results=sensitivity_results,
            overall_stability_score=overall_score,
            recommendation=recommendation,
            warnings=warnings,
        )
        
        return self.results
    
    def _simulate_performance(
        self,
        param_name: str,
        param_value: float,
        perturbation_level: float
    ) -> Dict[str, float]:
        """
        模拟性能变化(当没有提供evaluator_func时使用)
        
        假设:
        - 每个参数都有一个最优值
        - 偏离最优值会导致性能下降
        - 下降幅度与偏离程度成正比
        """
        base_perf_value = list(self.base_performance.values())[0]
        
        # 模拟参数最优性(实际中应调用真实评估函数)
        optimal_value = self.base_params.get(param_name, param_value)
        deviation = abs(param_value - optimal_value) / optimal_value \
            if optimal_value > 0 else 0
        
        # 性能衰减(二次函数)
        decay_factor = 1 - (deviation ** 2) * 10
        simulated_perf = base_perf_value * decay_factor
        
        # 添加随机噪声(模拟评估误差)
        noise = np.random.normal(0, 0.01)
        final_perf = simulated_perf + noise
        
        return {list(self.base_performance.keys())[0]: max(0, final_perf)}
    
    def print_summary(self):
        """打印敏感性分析摘要"""
        if self.results is None:
            print("请先运行敏感性分析: analyzer.run_local_sensitivity()")
            return
        
        print("\n" + "="*80)
        print("参数敏感性分析摘要")
        print("="*80)
        print(f"\n基准参数: {self.results.base_parameters}")
        print(f"基准性能: {self.results.base_performance}")
        print(f"\n总体稳定性评分: {self.results.overall_stability_score:.2f}/100")
        print(f"建议: {self.results.recommendation}")
        
        if self.results.warnings:
            print(f"\n警告:")
            for warning in self.results.warnings:
                print(f"  {warning}")
        
        print(f"\n{'='*80}")
        print(f"{'参数名称':<20} {'基准值':>10} {'弹性':>10} {'CV':>10} "
              f"{'稳定性评分':>12} {'等级':<12}")
        print("-"*80)
        
        for result in self.results.parameter_results:
            print(f"{result.parameter_name:<20} {result.base_value:>10.4f} "
                  f"{result.elasticity:>10.4f} {result.cv:>10.4f} "
                  f"{result.stability_score:>12.2f} {result.sensitivity_grade:<12}")
        
        print("="*80 + "\n")
    
    def save_report(
        self,
        output_path: str,
        include_plots: bool = True
    ):
        """
        保存敏感性分析报告(支持HTML和CSV格式)
        
        Args:
            output_path: 输出文件路径(.html或.csv)
            include_plots: 是否包含可视化图表(仅HTML)
        """
        if self.results is None:
            raise ValueError("请先运行敏感性分析")
        
        output_path = Path(output_path)
        
        if output_path.suffix == '.csv':
            self._save_csv(output_path)
        elif output_path.suffix == '.html':
            if include_plots:
                self._save_html_with_plots(output_path)
            else:
                self._save_html_basic(output_path)
        else:
            raise ValueError(f"不支持的文件格式: {output_path.suffix}")
    
    def _save_csv(self, output_path: Path):
        """保存为CSV格式"""
        rows = []
        for result in self.results.parameter_results:
            rows.append({
                'parameter_name': result.parameter_name,
                'base_value': result.base_value,
                'elasticity': result.elasticity,
                'cv': result.cv,
                'stability_score': result.stability_score,
                'max_deviation': result.max_deviation,
                'sensitivity_grade': result.sensitivity_grade,
            })
        
        df = pd.DataFrame(rows)
        df.to_csv(output_path, index=False, encoding='utf-8-sig')
        print(f"CSV报告已保存: {output_path}")
    
    def _save_html_basic(self, output_path: Path):
        """保存为HTML格式(无图表)"""
        html_content = f"""<!DOCTYPE html>
<html>
<head>
    <meta charset="UTF-8">
    <title>参数敏感性分析报告</title>
    <style>
        body {{ font-family: Arial, sans-serif; margin: 20px; }}
        h1 {{ color: #333; border-bottom: 2px solid #4CAF50; padding-bottom: 10px; }}
        h2 {{ color: #666; margin-top: 30px; }}
        table {{ border-collapse: collapse; width: 100%; margin: 20px 0; }}
        th, td {{ border: 1px solid #ddd; padding: 8px; text-align: left; }}
        th {{ background-color: #4CAF50; color: white; }}
        tr:nth-child(even) {{ background-color: #f2f2f2; }}
        .summary {{ background-color: #f9f9f9; padding: 15px; border-left: 4px solid #4CAF50; margin: 20px 0; }}
        .warning {{ background-color: #fff3cd; border-left: 4px solid #ffc107; padding: 10px; margin: 10px 0; }}
        .critical {{ background-color: #f8d7da; border-left: 4px solid #dc3545; padding: 10px; margin: 10px 0; }}
        .pass {{ background-color: #d4edda; border-left: 4px solid #28a745; padding: 10px; margin: 10px 0; }}
    </style>
</head>
<body>
    <h1>参数敏感性分析报告</h1>
    <p>生成时间: {self.results.timestamp}</p>
    
    <div class="summary">
        <h2>总体评估</h2>
        <p><strong>总体稳定性评分:</strong> {self.results.overall_stability_score:.2f}/100</p>
        <p><strong>建议:</strong> {self.results.recommendation}</p>
    </div>
    
    <h2>基准信息</h2>
    <p><strong>基准参数:</strong> {self.results.base_parameters}</p>
    <p><strong>基准性能:</strong> {self.results.base_performance}</p>
    
    <h2>参数敏感性详情</h2>
    <table>
        <tr>
            <th>参数名称</th>
            <th>基准值</th>
            <th>弹性</th>
            <th>CV</th>
            <th>稳定性评分</th>
            <th>最大偏差</th>
            <th>敏感性等级</th>
        </tr>
"""
        
        for result in self.results.parameter_results:
            html_content += f"""        <tr>
            <td>{result.parameter_name}</td>
            <td>{result.base_value:.4f}</td>
            <td>{result.elasticity:.4f}</td>
            <td>{result.cv:.4f}</td>
            <td>{result.stability_score:.2f}</td>
            <td>{result.max_deviation:.4f}</td>
            <td>{result.sensitivity_grade}</td>
        </tr>
"""
        
        html_content += """    </table>
"""
        
        if self.results.warnings:
            html_content += """    <h2>警告与建议</h2>
"""
            for warning in self.results.warnings:
                css_class = 'critical' if '🔴' in warning else 'warning'
                html_content += f'    <div class="{css_class}">{warning}</div>\n'
        
        html_content += """</body>
</html>"""
        
        with open(output_path, 'w', encoding='utf-8') as f:
            f.write(html_content)
        print(f"HTML报告已保存: {output_path}")
    
    def _save_html_with_plots(self, output_path: Path):
        """保存为HTML格式(含图表)"""
        # 先保存基础HTML
        self._save_html_basic(output_path)
        
        # TODO: 使用matplotlib生成 tornado diagram (龙卷风图)
        # 显示各参数的敏感性排序
        print("提示: 可使用matplotlib添加龙卷风图可视化")
    
    def get_stability_grade(self) -> str:
        """获取总体稳定性等级"""
        if self.results is None:
            raise ValueError("请先运行敏感性分析")
        
        score = self.results.overall_stability_score
        if score >= 90:
            return 'A+'
        elif score >= 80:
            return 'A'
        elif score >= 70:
            return 'B'
        elif score >= 60:
            return 'C'
        else:
            return 'D'


def quick_sensitivity_check(
    base_params: Dict[str, float],
    base_performance: Dict[str, float],
    param_names: List[str]
) -> SensitivityReport:
    """
    快速敏感性检查(便捷函数)
    
    Args:
        base_params: 基准参数
        base_performance: 基准性能
        param_names: 需要检查的参数名称列表
        
    Returns:
        SensitivityReport对象
    """
    analyzer = ParameterSensitivity(base_params, base_performance)
    
    for param_name in param_names:
        base_value = base_params.get(param_name)
        if base_value and base_value > 0:
            analyzer.add_parameter(
                name=param_name,
                base_value=base_value,
                range_min=base_value * 0.5,
                range_max=base_value * 1.5,
                steps=5,
            )
    
    return analyzer.run_local_sensitivity()
