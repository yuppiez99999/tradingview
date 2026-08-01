#!/usr/bin/env python3
"""
测试执行和覆盖率检查脚本
不依赖外部包,使用标准库实现
"""

import importlib.util
import inspect
import sys
import traceback
import unittest
from datetime import datetime
from pathlib import Path


class TestCase:
    """测试用例基类"""

    def __init__(self, name):
        self.name = name
        self.result = 'pending'  # pending, passed, failed, error
        self.message = ''
        self.duration = 0

    def run(self):
        raise NotImplementedError


class UnittestTestCase(TestCase):
    """unittest测试用例适配器"""

    def __init__(self, test_suite):
        super().__init__(test_suite._testMethodName if hasattr(test_suite, '_testMethodName') else str(test_suite))
        self.test_suite = test_suite

    def run(self):
        result = unittest.TestResult()
        self.test_suite.run(result)

        if result.wasSuccessful():
            self.result = 'passed'
            self.message = '测试通过'
        else:
            if result.errors:
                self.result = 'error'
                self.message = f'执行错误: {result.errors[0][1][:200]}'
            if result.failures:
                self.result = 'failed'
                self.message = f'断言失败: {result.failures[0][1][:200]}'


class TestFunctionCase(TestCase):
    """函数式测试用例"""

    def __init__(self, name, func):
        super().__init__(name)
        self.func = func

    def run(self):
        start = datetime.now()
        try:
            self.func()
            self.result = 'passed'
            self.message = '测试通过'
        except AssertionError as e:
            self.result = 'failed'
            self.message = f'断言失败: {e!s}'
        except Exception as e:
            self.result = 'error'
            self.message = f'执行错误: {e!s}'
            traceback.print_exc()
        finally:
            end = datetime.now()
            self.duration = (end - start).total_seconds()


class TestRunner:
    """测试运行器"""

    def __init__(self, test_dir='.'):
        self.test_dir = Path(test_dir)
        self.test_cases = []
        self.results = []

    def discover_tests(self):
        """发现测试文件"""
        test_files = list(self.test_dir.glob('test_*.py'))
        print(f"发现 {len(test_files)} 个测试文件")

        for test_file in test_files:
            self._load_test_file(test_file)

    def _load_test_file(self, file_path):
        """加载测试文件"""
        try:
            spec = importlib.util.spec_from_file_location(file_path.stem, file_path)
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)

            # 首先尝试作为unittest加载
            loader = unittest.TestLoader()
            suite = loader.loadTestsFromModule(module)
            tests = list(suite)

            if tests:
                # 使用unittest发现的测试
                for test in tests:
                    self.test_cases.append(UnittestTestCase(test))
            else:
                # 回退到查找测试函数
                for name, obj in inspect.getmembers(module):
                    if name.startswith('test_') and callable(obj) and not inspect.isclass(obj):
                        self.test_cases.append(TestFunctionCase(name, obj))

        except Exception as e:
            print(f"加载测试文件失败 {file_path}: {e}")

    def run_all(self, verbose=True):
        """运行所有测试"""
        print(f"\n开始运行 {len(self.test_cases)} 个测试...")
        print("=" * 70)

        passed = failed = errors = 0

        for i, case in enumerate(self.test_cases, 1):
            if verbose:
                print(f"\n[{i}/{len(self.test_cases)}] {case.name}")

            case.run()
            self.results.append(case)

            if case.result == 'passed':
                passed += 1
                status = "[PASS]"
            elif case.result == 'failed':
                failed += 1
                status = "[FAIL]"
            else:
                errors += 1
                status = "[ERROR]"

            if verbose:
                print(f"  状态: {status} ({case.duration:.3f}s)")
                if case.message and case.result != 'passed':
                    print(f"  信息: {case.message[:200]}")

        print("\n" + "=" * 70)
        print("测试结果汇总:")
        print(f"  总计: {len(self.test_cases)}")
        print(f"  通过: {passed} ({passed/max(len(self.test_cases),1)*100:.1f}%)")
        print(f"  失败: {failed}")
        print(f"  错误: {errors}")

        return passed, failed, errors

    def generate_report(self, filename='test_report.txt'):
        """生成测试报告"""
        with open(filename, 'w', encoding='utf-8') as f:
            f.write("测试报告\n")
            f.write("=" * 70 + "\n")
            f.write(f"生成时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
            f.write(f"测试目录: {self.test_dir}\n\n")

            passed = sum(1 for r in self.results if r.result == 'passed')
            failed = sum(1 for r in self.results if r.result == 'failed')
            errors = sum(1 for r in self.results if r.result == 'error')

            f.write("测试汇总:\n")
            f.write(f"  总计: {len(self.results)}\n")
            f.write(f"  通过: {passed}\n")
            f.write(f"  失败: {failed}\n")
            f.write(f"  错误: {errors}\n\n")

            f.write("详细结果:\n")
            f.write("-" * 70 + "\n")

            for result in self.results:
                symbol = "[PASS]" if result.result == 'passed' else "[FAIL]"
                f.write(f"{symbol} {result.name}: {result.result}\n")
                if result.message and result.result != 'passed':
                    f.write(f"  信息: {result.message[:200]}\n")
                f.write(f"  耗时: {result.duration:.3f}s\n\n")


def estimate_coverage(source_dirs=None):
    """估算代码覆盖率(简化版)"""
    if source_dirs is None:
        source_dirs = ['.']

    total_functions = 0
    covered_functions = 0

    print("\n估算代码覆盖率...")
    print("-" * 70)

    for source_dir in source_dirs:
        for py_file in Path(source_dir).rglob('*.py'):
            if '__pycache__' in str(py_file) or '.pyc' in str(py_file):
                continue

            try:
                with open(py_file, encoding='utf-8') as f:
                    content = f.read()

                # 统计函数定义
                import re
                functions = re.findall(r'^\s*def\s+(\w+)', content, re.MULTILINE)
                total_functions += len(functions)

                # 简单估算:如果函数名出现在测试文件中,认为被覆盖
                test_content = ''
                for test_file in Path('.').glob('test_*.py'):
                    with open(test_file, encoding='utf-8') as f:
                        test_content += f.read()

                for func in functions:
                    if func in test_content:
                        covered_functions += 1

            except Exception as e:
                print(f"分析文件失败 {py_file}: {e}")

    coverage = covered_functions / max(total_functions, 1) * 100
    print(f"总函数数: {total_functions}")
    print(f"覆盖函数数: {covered_functions}")
    print(f"估算覆盖率: {coverage:.1f}%")

    return coverage


def main():
    """主函数"""
    print("=" * 70)
    print("量化交易系统 - 测试执行和覆盖率检查")
    print(f"执行时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 70)

    # 设置测试目录
    test_dir = Path(__file__).parent

    # 1. 发现并运行测试
    runner = TestRunner(test_dir)
    runner.discover_tests()

    if not runner.test_cases:
        print("\n未找到测试用例!")
        print("请确保测试目录中有 test_*.py 文件")
        sys.exit(1)

    passed, failed, errors = runner.run_all(verbose=True)

    # 2. 生成测试报告
    report_file = test_dir / 'test_report.txt'
    runner.generate_report(str(report_file))
    print(f"\n测试报告已保存到: {report_file}")

    # 3. 估算覆盖率
    coverage = estimate_coverage([test_dir.parent / 'src', test_dir.parent])

    # 4. 输出总结
    print("\n" + "=" * 70)
    print("执行总结:")
    print(f"  测试通过率: {passed/max(len(runner.results),1)*100:.1f}%")
    print(f"  估算覆盖率: {coverage:.1f}%")
    print("  目标覆盖率: 80.0%")

    if coverage >= 80.0:
        print("  [PASS] 覆盖率达标!")
    else:
        print("  [FAIL] 覆盖率未达标,需要补充更多测试")

    print("=" * 70)

    # 返回退出码
    if failed > 0 or errors > 0:
        sys.exit(1)
    else:
        sys.exit(0)


if __name__ == '__main__':
    main()
