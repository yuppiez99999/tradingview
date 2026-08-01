# -*- coding: utf-8 -*-
"""
测试覆盖率检查脚本 - 简化版

功能:
1. 运行所有测试并统计覆盖率
2. 生成HTML格式覆盖率报告
3. 输出关键指标

使用方法:
    cd v8.3_institutional
    python tests/run_coverage_check.py

依赖:
    - Python 3.8+
    - pytest (已安装)
    - coverage.py (需要安装: pip install coverage)
"""

import json
import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path

try:
    import coverage
except ImportError:
    coverage = None

# 设置路径
ROOT_DIR = Path(__file__).parent.parent.parent.parent.parent
PROJECT_DIR = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_DIR))


def check_coverage_installed():
    """检查coverage.py是否已安装"""
    try:
        import coverage
        print(f"✓ coverage.py 已安装 (版本 {coverage.__version__})")
        return True
    except ImportError:
        print("✗ coverage.py 未安装")
        print("\n请安装coverage.py:")
        print("  pip install coverage pytest-cov")
        print("\n或者使用简化模式运行测试:")
        print("  python -m pytest tests/ -v")
        return False


def run_tests_with_coverage():
    """使用coverage.py运行测试并生成报告"""
    print("\n" + "="*80)
    print("开始运行测试覆盖率检查...")
    print("="*80)

    # 配置coverage
    cov = coverage.Coverage(
        source=['src'],  # 源代码目录
        omit=[
            '*/tests/*',
            '*/__init__.py',
            '*/setup.py',
        ],
        branch=True,  # 分支覆盖率
    )

    cov.start()

    # 运行pytest
    test_dir = os.path.join(str(PROJECT_DIR), 'tests')
    result = subprocess.run(
        ['python', '-m', 'pytest', test_dir, '-v', '--tb=short'],
        cwd=str(PROJECT_DIR),
        capture_output=True,
        text=True
    )

    cov.stop()

    # 生成HTML报告
    report_dir = os.path.join(str(PROJECT_DIR), 'reports')
    os.makedirs(report_dir, exist_ok=True)

    cov.html_report(directory=os.path.join(report_dir, 'coverage_html'))
    cov.report(show_missing=True, precision=2)

    # 保存JSON格式结果
    json_result = {
        'timestamp': datetime.now().isoformat(),
        'total_tests': result.stdout.count('PASSED'),
        'failed_tests': result.stdout.count('FAILED'),
        'coverage_summary': cov.report(),
        'html_report_path': os.path.join(report_dir, 'coverage_html', 'index.html'),
    }

    with open(os.path.join(report_dir, 'coverage_result.json'), 'w', encoding='utf-8') as f:
        json.dump(json_result, f, ensure_ascii=False, indent=2)

    print(f"\n✓ HTML覆盖率报告已生成: {json_result['html_report_path']}")
    print(f"✓ JSON结果已保存: {os.path.join(report_dir, 'coverage_result.json')}")

    return result.returncode == 0


def run_tests_without_coverage():
    """简化模式:仅运行pytest测试"""
    print("\n" + "="*80)
    print("简化模式: 运行pytest测试套件")
    print("="*80)

    test_dir = os.path.join(str(PROJECT_DIR), 'tests')
    result = subprocess.run(
        ['python', '-m', 'pytest', test_dir, '-v', '--tb=short', '-x'],
        cwd=str(PROJECT_DIR),
        capture_output=True,
        text=True
    )

    print(result.stdout)
    if result.stderr:
        print("\n错误信息:")
        print(result.stderr)

    # 统计测试结果
    passed = result.stdout.count('PASSED')
    failed = result.stdout.count('FAILED')
    skipped = result.stdout.count('SKIPPED')

    print(f"\n{'='*80}")
    print("测试统计:")
    print(f"  ✓ 通过: {passed}")
    print(f"  ✗ 失败: {failed}")
    print(f"  ⏭️  跳过: {skipped}")
    print(f"{'='*80}\n")

    return result.returncode == 0


def generate_coverage_summary():
    """生成覆盖率检查摘要报告"""
    report_dir = os.path.join(str(PROJECT_DIR), 'reports')
    os.makedirs(report_dir, exist_ok=True)

    summary = {
        'check_time': datetime.now().isoformat(),
        'coverage_py_installed': check_coverage_installed(),
        'test_files': [f for f in os.listdir(os.path.join(str(PROJECT_DIR), 'tests')) if f.startswith('test_') and f.endswith('.py')],
        'recommendations': [
            '安装coverage.py以获得更详细的覆盖率数据',
            '定期运行覆盖率检查确保代码质量',
            '为目标模块补充异常处理和边界条件测试',
        ]
    }

    with open(os.path.join(report_dir, 'coverage_summary.json'), 'w', encoding='utf-8') as f:
        json.dump(summary, f, ensure_ascii=False, indent=2)

    print(f"\n✓ 覆盖率摘要已保存: {os.path.join(report_dir, 'coverage_summary.json')}")


if __name__ == '__main__':
    print(f"\n项目根目录: {ROOT_DIR}")
    print(f"项目目录: {PROJECT_DIR}")

    # 检查coverage.py
    has_coverage = check_coverage_installed()

    if has_coverage:
        # 使用coverage运行测试
        success = run_tests_with_coverage()
    else:
        # 简化模式
        print("\n切换到简化模式...")
        success = run_tests_without_coverage()

    # 生成摘要
    generate_coverage_summary()

    sys.exit(0 if success else 1)
