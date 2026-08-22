"""
供应链综合风险决策模型 - 推理服务
=========================================

提供简单易用的 API，让你可以在日常工作中使用模型:

1. Python API - 在你的脚本中直接调用
2. 交互式评估 - 命令行交互评估供应商
3. 批量评估 - 从 CSV 批量读取数据并输出评估报告
4. 风险报告生成 - 自动生成结构化评估报告

使用方式:
    # 方式 1: 导入使用
    from supply_chain_risk_model_predict import evaluate_supplier
    result = evaluate_supplier(finance_data, energy_data)

    # 方式 2: 运行交互式评估
    python supply_chain_risk_model_predict.py --interactive

    # 方式 3: 批量评估
    python supply_chain_risk_model_predict.py --batch --input data.csv --output report.csv
"""

import argparse
import json
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd

# ============================================================
# 核心配置
# ============================================================

BASE_DIR = Path(__file__).resolve().parent
MODEL_DIR = BASE_DIR / "models"
MODEL_PATH = MODEL_DIR / "combined_risk_model_v1.0.pkl"
METADATA_PATH = MODEL_DIR / "model_metadata.json"
MAPPING_PATH = MODEL_DIR / "category_mapping.json"


# ============================================================
# 从训练文件导入核心模型类
# ============================================================

import sys

sys.path.insert(0, str(BASE_DIR))

from train import (
    CombinedDecisionEngine,
)

# ============================================================
# 推理 API
# ============================================================

def load_engine():
    """加载已训练的综合决策引擎"""
    if not MODEL_PATH.exists():
        raise FileNotFoundError(
            f"模型文件不存在: {MODEL_PATH}\n"
            f"请先运行: python supply_chain_risk_model_train.py"
        )

    engine = CombinedDecisionEngine.load(MODEL_PATH)
    return engine


def evaluate_supplier(finance_data, energy_data_list=None):
    """
    评估单个供应商 - 最简单的 API

    Args:
        finance_data: dict，包含以下字段:
            - category: 金融类别 (如 'banking', 'securities', 'insurance' 等)
            - data_type: 数据类型 (如 'credit_score', 'risk_control' 等)
            - data_quality_score: 数据质量评分 (0-100)
            - completeness: 完整性评分 (0-100)
            - accuracy: 准确性评分 (0-100)
            - timeliness: 时效性评分 (0-100)
            - compliance_score: 合规性评分 (0-100)

        energy_data_list: list of dict，可选，能源相关数据，每个 dict 包含:
            - category: 能源类别 (如 'electricity', 'coal', 'oil_gas' 等)
            - data_type: 数据类型 (如 'production', 'consumption' 等)
            - data_quality_score: 数据质量评分 (0-100)
            - completeness, accuracy, timeliness, compliance_score

    Returns:
        dict: 综合评估结果

    示例:
        finance = {
            'category': 'banking',
            'data_type': 'credit_score',
            'data_quality_score': 97.5,
            'completeness': 98.0,
            'accuracy': 97.8,
            'timeliness': 96.5,
            'compliance_score': 100.0
        }
        result = evaluate_supplier(finance)
        print(result['decision'], result['combined_score'])
    """
    engine = load_engine()

    # 转换为 Series/DataFrame
    finance_row = pd.Series(finance_data)
    energy_rows = pd.DataFrame(energy_data_list) if energy_data_list else None

    return engine.evaluate_supplier(finance_row, energy_rows)


def evaluate_batch(input_data, output_path=None):
    """
    批量评估多个供应商

    Args:
        input_data: DataFrame or list of dict, 每个元素包含 finance_data 字段
        output_path: 可选输出文件路径

    Returns:
        DataFrame: 评估结果
    """
    engine = load_engine()

    if isinstance(input_data, list):
        input_data = pd.DataFrame(input_data)

    results = []
    for idx, row in input_data.iterrows():
        # 检查是否有能源数据字段
        energy_data = None
        if 'energy_category' in row:
            energy_data = pd.DataFrame([{
                'category': row.get('energy_category', 'electricity'),
                'data_type': row.get('energy_data_type', 'production'),
                'data_quality_score': row.get('energy_quality', 95.0),
                'completeness': row.get('energy_completeness', 95.0),
                'accuracy': row.get('energy_accuracy', 95.0),
                'timeliness': row.get('energy_timeliness', 95.0),
                'compliance_score': row.get('energy_compliance', 100.0)
            }])

        # 构造金融数据
        finance_data = {
            'category': row.get('category', row.get('finance_category', 'banking')),
            'data_type': row.get('data_type', row.get('finance_data_type', 'credit_score')),
            'data_quality_score': row.get('data_quality_score', row.get('finance_quality', 95.0)),
            'completeness': row.get('completeness', row.get('finance_completeness', 95.0)),
            'accuracy': row.get('accuracy', row.get('finance_accuracy', 95.0)),
            'timeliness': row.get('timeliness', row.get('finance_timeliness', 95.0)),
            'compliance_score': row.get('compliance_score', row.get('finance_compliance', 100.0))
        }

        finance_row = pd.Series(finance_data)
        result = engine.evaluate_supplier(finance_row, energy_data)

        results.append({
            'index': idx,
            'supplier_id': row.get('supplier_id', row.get('entity_id', f'SUPPLIER_{idx:03d}')),
            'finance_category': finance_data['category'],
            'finance_data_type': finance_data['data_type'],
            'finance_score': result['finance_score'],
            'energy_alert_level': result['energy_alert']['alert_level'],
            'energy_status': result['energy_alert']['status'],
            'combined_score': result['combined_score'],
            'decision': result['decision'],
            'priority': result['priority'],
            'recommendations': '\n'.join(result['suggestions']),
            'evaluation_time': result['timestamp']
        })

    result_df = pd.DataFrame(results)

    if output_path:
        output_path = Path(output_path)
        if output_path.suffix == '.csv':
            result_df.to_csv(output_path, index=False, encoding='utf-8-sig')
        elif output_path.suffix in ['.xlsx', '.xls']:
            result_df.to_excel(output_path, index=False)
        else:
            result_df.to_csv(output_path, index=False, encoding='utf-8-sig')

    return result_df


# ============================================================
# 交互式评估
# ============================================================

def interactive_evaluation():
    """交互式评估 - 引导用户输入数据并输出决策建议"""

    engine = load_engine()

    # 加载类别映射
    with open(MAPPING_PATH, encoding='utf-8') as f:
        json.load(f)


    # 评估多个供应商
    all_results = []

    while True:

        # 基本信息
        supplier_name = input("\n  供应商/客户名称 (留空退出): ").strip()
        if not supplier_name:
            break

        supplier_id = input("  供应商ID (可选): ").strip() or f"SUPPLIER_{datetime.now().strftime('%Y%m%d%H%M%S')}"

        # 金融数据输入
        finance_category = input("  类别 (默认 banking): ").strip() or 'banking'

        finance_data_type = input("  数据类型 (默认 credit_score): ").strip() or 'credit_score'

        quality = input("    数据质量评分 [95]: ").strip() or '95'
        completeness = input("    完整性评分 [95]: ").strip() or '95'
        accuracy = input("    准确性评分 [95]: ").strip() or '95'
        timeliness = input("    时效性评分 [95]: ").strip() or '95'
        compliance = input("    合规性评分 [100]: ").strip() or '100'

        finance_data = {
            'category': finance_category,
            'data_type': finance_data_type,
            'data_quality_score': float(quality),
            'completeness': float(completeness),
            'accuracy': float(accuracy),
            'timeliness': float(timeliness),
            'compliance_score': float(compliance)
        }

        # 能源数据输入
        include_energy = input("  是否补充能源维度数据? (y/N): ").strip().lower() == 'y'
        energy_data = None

        if include_energy:
            energy_category = input("  能源类别 (默认 electricity): ").strip() or 'electricity'

            energy_data_type = input("  数据类型 (默认 production): ").strip() or 'production'

            e_quality = input("    数据质量评分 [95]: ").strip() or '95'
            e_completeness = input("    完整性评分 [95]: ").strip() or '95'
            e_accuracy = input("    准确性评分 [95]: ").strip() or '95'
            e_timeliness = input("    时效性评分 [95]: ").strip() or '95'
            e_compliance = input("    合规性评分 [100]: ").strip() or '100'

            energy_data = pd.DataFrame([{
                'category': energy_category,
                'data_type': energy_data_type,
                'data_quality_score': float(e_quality),
                'completeness': float(e_completeness),
                'accuracy': float(e_accuracy),
                'timeliness': float(e_timeliness),
                'compliance_score': float(e_compliance)
            }])

        # 执行评估
        finance_row = pd.Series(finance_data)
        result = engine.evaluate_supplier(finance_row, energy_data)

        # 展示结果

        for suggestion in result['suggestions']:
            pass

        # 保存结果
        all_results.append({
            'supplier_name': supplier_name,
            'supplier_id': supplier_id,
            **result
        })

        # 继续评估?
        continue_eval = input("\n  是否继续评估下一个供应商? (Y/n): ").strip().lower()
        if continue_eval == 'n':
            break

    # 所有评估完成后输出汇总
    if all_results:

        scores = [r['combined_score'] for r in all_results]
        decisions = [r['decision'] for r in all_results]

        from collections import Counter
        for decision, count in Counter(decisions).items():
            pass

        # 保存报告
        save = input("\n  是否保存评估报告? (Y/n): ").strip().lower()
        if save != 'n':
            report_dir = BASE_DIR / "reports"
            report_dir.mkdir(parents=True, exist_ok=True)

            report_file = report_dir / f"evaluation_report_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"

            # 清理无法序列化的数据
            serializable_results = []
            for r in all_results:
                sr = {k: v for k, v in r.items()
                      if isinstance(v, (str, int, float, bool, list)) or v is None}
                sr['energy_alert'] = r.get('energy_alert', {})
                serializable_results.append(sr)

            report = {
                'report_id': f"RPT_{datetime.now().strftime('%Y%m%d%H%M%S')}",
                'generated_at': datetime.now().isoformat(),
                'total_suppliers': len(all_results),
                'average_score': np.mean(scores),
                'decision_distribution': dict(Counter(decisions)),
                'results': serializable_results
            }

            with open(report_file, 'w', encoding='utf-8') as f:
                json.dump(report, f, ensure_ascii=False, indent=2)




# ============================================================
# 实用工具函数
# ============================================================

def generate_quick_report(results_df):
    """生成快速评估报告 (Markdown 格式)"""
    report_lines = []

    report_lines.append("# 供应链综合风险评估报告")
    report_lines.append(f"**生成时间**: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    report_lines.append(f"**评估数量**: {len(results_df)} 个供应商")
    report_lines.append("")

    # 统计摘要
    avg_score = results_df['combined_score'].mean()
    report_lines.append("## 📊 统计摘要")
    report_lines.append(f"- 平均综合评分: **{avg_score:.1f}/100**")
    report_lines.append(f"- 最高评分: **{results_df['combined_score'].max():.1f}**")
    report_lines.append(f"- 最低评分: **{results_df['combined_score'].min():.1f}**")
    report_lines.append("")

    # 决策分布
    decision_dist = results_df['decision'].value_counts()
    report_lines.append("## 🎯 决策分布")
    for decision, count in decision_dist.items():
        report_lines.append(f"- {decision}: {count} ({count/len(results_df)*100:.1f}%)")
    report_lines.append("")

    # 详细评估
    report_lines.append("## 📋 详细评估结果")
    report_lines.append("")
    report_lines.append("| 序号 | 供应商ID | 综合评分 | 决策 | 建议 |")
    report_lines.append("|------|---------|---------|------|------|")

    for idx, row in results_df.iterrows():
        report_lines.append(
            f"| {idx+1} | {row['supplier_id']} | {row['combined_score']:.1f} | "
            f"{row['decision']} | {row['priority']} |"
        )

    return "\n".join(report_lines)


def print_model_info():
    """打印模型信息"""
    if not METADATA_PATH.exists():
        return

    with open(METADATA_PATH, encoding='utf-8') as f:
        metadata = json.load(f)




    metadata['thresholds']['combined_decision_levels']



# ============================================================
# 主入口
# ============================================================

def main():
    parser = argparse.ArgumentParser(description="供应链综合风险决策模型")
    parser.add_argument('--interactive', '-i', action='store_true',
                       help='交互式评估模式')
    parser.add_argument('--batch', '-b', action='store_true',
                       help='批量评估模式')
    parser.add_argument('--input', '-f', type=str,
                       help='批量评估输入文件路径 (CSV/Excel)')
    parser.add_argument('--output', '-o', type=str,
                       help='批量评估输出文件路径')
    parser.add_argument('--info', action='store_true',
                       help='显示模型信息')
    parser.add_argument('--demo', '-d', action='store_true',
                       help='运行演示示例')

    args = parser.parse_args()

    if args.info:
        print_model_info()
        return

    if args.demo:
        # 运行演示

        # 演示 1: 高质量供应商
        demo_finance_1 = {
            'category': 'banking',
            'data_type': 'credit_score',
            'data_quality_score': 99.0,
            'completeness': 98.5,
            'accuracy': 99.0,
            'timeliness': 98.0,
            'compliance_score': 100.0
        }
        evaluate_supplier(demo_finance_1)

        # 演示 2: 中等质量供应商
        demo_finance_2 = {
            'category': 'fintech',
            'data_type': 'risk_control',
            'data_quality_score': 94.0,
            'completeness': 94.0,
            'accuracy': 94.0,
            'timeliness': 92.0,
            'compliance_score': 100.0
        }
        demo_energy_2 = [{
            'category': 'nuclear',
            'data_type': 'radiation_monitor',
            'data_quality_score': 94.0,
            'completeness': 93.0,
            'accuracy': 94.0,
            'timeliness': 92.0,
            'compliance_score': 100.0
        }]
        evaluate_supplier(demo_finance_2, demo_energy_2)

        # 演示 3: 高风险供应商
        demo_finance_3 = {
            'category': 'trust',
            'data_type': 'risk_control',
            'data_quality_score': 85.0,
            'completeness': 80.0,
            'accuracy': 82.0,
            'timeliness': 75.0,
            'compliance_score': 90.0
        }
        evaluate_supplier(demo_finance_3)

        return

    if args.batch:
        if not args.input:
            return

        input_path = Path(args.input)
        if not input_path.exists():
            return

        # 读取输入
        if input_path.suffix in ['.csv']:
            df = pd.read_csv(input_path)
        elif input_path.suffix in ['.xlsx', '.xls']:
            df = pd.read_excel(input_path)
        else:
            return


        # 设置输出路径
        output_path = args.output or input_path.parent / f"evaluation_result_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"

        # 执行批量评估
        result_df = evaluate_batch(df, output_path)

        for decision, count in result_df['decision'].value_counts().items():
            pass

        return

    # 默认进入交互式模式
    interactive_evaluation()


if __name__ == '__main__':
    main()
