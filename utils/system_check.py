# -*- coding: utf-8 -*-
"""
P0 启动自检系统 (System Check)
==================================
版本: v8.6.13
创建日期: 2026-07-31
用途: 在任何交易系统入口点启动前,执行统一的全维度健康检查,
      在错误进入工作流前拦截,从根本上减少 bug 的产生与传播。

设计原则 (Bug Prevention Philosophy):
    1. Fail-Fast: P0 检查失败立即终止,不让错误传播到下游
    2. Single Source of Truth: 所有自检逻辑集中在此模块,避免分散重复
    3. Actionable: 每个失败项必须给出可执行的修复建议 (remediation)
    4. Idempotent: 同一检查可被多次调用,结果一致
    5. Non-Destructive: 自检过程不修改任何业务数据

退出码标准化:
    0 = 全部通过 (PASS) - 可安全进入工作流
    1 = 存在失败项 (FAIL) - 必须人工干预后才能继续
    2 = 自检脚本异常 (ERROR) - 自检本身出错

检查维度 (9 大类):
    C1. 关键文件存在性 (positions.json / trade_plan / portfolio.yaml)
    C2. 环境变量与凭证 (WIND_API_KEY / IFIND_TOKEN / TS_TOKEN)
    C3. 数据源连通性 (Wind MCP / iFinD / TDX / AKShare)
    C4. 配置文件 Schema (positions.json 字段完整性)
    C5. Python 依赖与关键模块导入
    C6. 目录权限与磁盘空间
    C7. 关键子模块 smoke 测试 (hedge / data_provider / signal_fusion)
    C8. 历史数据完整性 (daily_returns.jsonl / heartbeat)
    C9. 兜底价格新鲜度 (DEFAULT_FUTURES_PRICES 时间戳校验, R1 新增 2026-08-01)

用法:
    # 命令行
    python scripts/run_p0_startup_check.py
    python scripts/run_p0_startup_check.py --strict  # 任何 WARN 也算失败
    python scripts/run_p0_startup_check.py --json    # 输出 JSON 报告

    # 模块集成
    from utils.system_check import run_system_check, SystemChecker
    checker = SystemChecker()
    result = checker.run_all()
    if not result.passed:
        sys.exit(1)
"""
from __future__ import annotations

import json
import os
import shutil
import sys
import traceback
from dataclasses import dataclass, field, asdict
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

# 项目根目录 (此模块位于 utils/system_check.py, 父目录即项目根)
PROJECT_ROOT = Path(__file__).resolve().parents[1]

# 日志 (避免循环导入,使用独立 logger)
import logging
logger = logging.getLogger("system_check")


# ============================================================================
# 数据结构
# ============================================================================

class CheckLevel(str, Enum):
    """检查项级别"""
    ERROR = "ERROR"  # 必须通过,否则阻止工作流
    WARN = "WARN"    # 警告,不阻止但需关注
    INFO = "INFO"    # 信息性,不影响通过


class CheckStatus(str, Enum):
    """检查结果状态"""
    PASS = "PASS"
    FAIL = "FAIL"
    SKIP = "SKIP"


@dataclass
class CheckResult:
    """单个检查项的结果"""
    code: str                       # 检查项编号 (如 "C1.1")
    name: str                      # 检查项名称
    level: CheckLevel              # ERROR / WARN / INFO
    status: CheckStatus            # PASS / FAIL / SKIP
    detail: str = ""               # 详细信息
    remediation: str = ""          # 修复建议 (失败时必填)
    elapsed_ms: float = 0.0        # 执行耗时 (ms)

    @property
    def is_blocking(self) -> bool:
        """是否为阻止性失败 (ERROR 级别且 FAIL)"""
        return self.level == CheckLevel.ERROR and self.status == CheckStatus.FAIL


@dataclass
class SystemCheckReport:
    """整体自检报告"""
    check_time: str                          # ISO 时间戳
    project_root: str                        # 项目根目录
    total: int = 0                           # 总检查项
    passed: int = 0                          # 通过数
    failed: int = 0                          # 失败数 (含 ERROR FAIL)
    skipped: int = 0                         # 跳过数
    warnings: int = 0                        # 警告数 (WARN FAIL)
    blocking_failures: int = 0               # 阻止性失败数
    exit_code: int = 0                       # 推荐退出码 (0/1/2)
    results: List[CheckResult] = field(default_factory=list)
    error_summary: List[str] = field(default_factory=list)  # 失败项摘要

    @property
    def all_passed(self) -> bool:
        """是否全部通过 (无 ERROR FAIL)"""
        return self.blocking_failures == 0


# ============================================================================
# 主检查器
# ============================================================================

class SystemChecker:
    """P0 启动自检器 - 所有检查逻辑集中于此

    使用方式:
        checker = SystemChecker()
        report = checker.run_all()
        if not report.all_passed:
            print(checker.format_report(report))
            sys.exit(1)
    """

    # 关键文件清单 (相对项目根,适配 28-终极量化交易系统8.4 实际结构)
    CRITICAL_FILES = [
        ("config/positions.json", "现货持仓状态"),
        ("config/shadow_account_config.json", "影子账户配置"),
        ("config/stop_loss_vol_adjusted.yaml", "止损规则配置"),
        ("utils/hedge_execution_engine.py", "对冲执行引擎"),
        ("utils/data_provider.py", "数据提供器"),
        ("utils/risk_guard_integrator.py", "风控守卫集成器"),
        ("utils/signal_fusion.py", "信号融合引擎"),
        ("v8.3_institutional/trade_plans", "trade_plans 目录"),
        ("每日报告归档", "报告归档目录"),
    ]

    # 关键环境变量
    CRITICAL_ENV_VARS = [
        ("WIND_API_KEY", "Wind MCP 认证密钥 (P1 数据源)"),
        ("IFIND_TOKEN", "iFinD MCP 认证令牌 (P2 数据源)"),
    ]
    OPTIONAL_ENV_VARS = [
        ("TS_TOKEN", "Tushare 令牌 (国内期货/CPI)"),
        ("VOLCENGINE_API_KEY", "豆包 LLM (AI 分析)"),
        ("REPORT_OUTPUT_DIR", "报告输出目录"),
        ("LOG_LEVEL", "日志级别"),
        ("NO_PROXY", "国内金融 API 代理白名单"),
    ]

    def __init__(self, strict: bool = False, skip_datasource: bool = False):
        """
        Args:
            strict: 严格模式,任何 WARN FAIL 也算失败
            skip_datasource: 跳过数据源连通性检查 (加速启动)
        """
        self.strict = strict
        self.skip_datasource = skip_datasource
        self._results: List[CheckResult] = []

    # --------------------------------------------------------------------
    # 注册检查项
    # --------------------------------------------------------------------
    def _add(self, result: CheckResult) -> None:
        self._results.append(result)

    def _pass(self, code: str, name: str, level: CheckLevel,
              detail: str = "", elapsed_ms: float = 0.0) -> None:
        self._add(CheckResult(code=code, name=name, level=level,
                              status=CheckStatus.PASS,
                              detail=detail, elapsed_ms=elapsed_ms))

    def _fail(self, code: str, name: str, level: CheckLevel,
              detail: str = "", remediation: str = "",
              elapsed_ms: float = 0.0) -> None:
        self._add(CheckResult(code=code, name=name, level=level,
                              status=CheckStatus.FAIL,
                              detail=detail, remediation=remediation,
                              elapsed_ms=elapsed_ms))

    def _skip(self, code: str, name: str, level: CheckLevel,
              reason: str = "") -> None:
        self._add(CheckResult(code=code, name=name, level=level,
                              status=CheckStatus.SKIP,
                              detail=reason))

    # --------------------------------------------------------------------
    # C1. 关键文件存在性检查
    # --------------------------------------------------------------------
    def check_critical_files(self) -> None:
        """检查项目必须的关键文件与目录"""
        import time
        print("\n[C1] 关键文件存在性检查")
        print("-" * 60)

        for idx, (rel_path, desc) in enumerate(self.CRITICAL_FILES, 1):
            t0 = time.time()
            full_path = PROJECT_ROOT / rel_path
            code = f"C1.{idx}"
            try:
                exists = full_path.exists()
                elapsed = (time.time() - t0) * 1000
                if exists:
                    # 对目录额外统计文件数
                    if full_path.is_dir():
                        try:
                            file_count = len(list(full_path.glob("*.json")))
                            self._pass(code, f"{desc} ({rel_path})",
                                       CheckLevel.ERROR,
                                       f"目录存在, {file_count} JSON 文件", elapsed)
                        except Exception:
                            self._pass(code, f"{desc} ({rel_path})",
                                       CheckLevel.ERROR, "目录存在", elapsed)
                    else:
                        size = full_path.stat().st_size
                        self._pass(code, f"{desc} ({rel_path})",
                                   CheckLevel.ERROR,
                                   f"文件存在, {size} bytes", elapsed)
                else:
                    self._fail(code, f"{desc} ({rel_path})",
                               CheckLevel.ERROR,
                               detail=f"路径不存在: {full_path}",
                               remediation=f"请检查 {rel_path} 是否被意外删除或移动; "
                                           f"若是首次运行,请从模板/备份恢复",
                               elapsed_ms=elapsed)
            except Exception as e:
                elapsed = (time.time() - t0) * 1000
                self._fail(code, f"{desc} ({rel_path})", CheckLevel.ERROR,
                           detail=f"检查异常: {e}",
                           remediation="查看 system_check 日志排查",
                           elapsed_ms=elapsed)

    # --------------------------------------------------------------------
    # C2. 环境变量检查
    # --------------------------------------------------------------------
    def check_env_variables(self) -> None:
        """检查关键环境变量是否已设置"""
        print("\n[C2] 环境变量与凭证检查")
        print("-" * 60)

        # 必需变量
        for idx, (var_name, desc) in enumerate(self.CRITICAL_ENV_VARS, 1):
            code = f"C2.{idx}"
            value = os.environ.get(var_name, "").strip()
            if value:
                # 脱敏显示
                masked = value[:4] + "***" + value[-4:] if len(value) > 8 else "***"
                self._pass(code, f"{desc} ({var_name})", CheckLevel.ERROR,
                           detail=f"已设置: {masked}")
            else:
                self._fail(code, f"{desc} ({var_name})", CheckLevel.ERROR,
                           detail=f"环境变量未设置",
                           remediation=f"PowerShell: $env:{var_name}='your_key'\n"
                                       f"或写入系统环境变量 (永久生效)")

        # 可选变量
        offset = len(self.CRITICAL_ENV_VARS)
        for idx, (var_name, desc) in enumerate(self.OPTIONAL_ENV_VARS, 1):
            code = f"C2.{offset + idx}"
            value = os.environ.get(var_name, "").strip()
            if value:
                masked = value[:4] + "***" + value[-4:] if len(value) > 8 else "***"
                self._pass(code, f"{desc} ({var_name})", CheckLevel.WARN,
                           detail=f"已设置: {masked}")
            else:
                self._fail(code, f"{desc} ({var_name})", CheckLevel.WARN,
                           detail="未设置 (可选)",
                           remediation=f"建议设置 {var_name} 以启用相关功能")

    # --------------------------------------------------------------------
    # C3. 数据源连通性检查
    # --------------------------------------------------------------------
    def check_datasource_connectivity(self) -> None:
        """检查 Wind MCP / iFinD / TDX / AKShare 数据源连通性"""
        print("\n[C3] 数据源连通性检查")
        print("-" * 60)

        if self.skip_datasource:
            self._skip("C3.0", "数据源连通性 (跳过)", CheckLevel.WARN,
                       "skip_datasource=True")
            return

        sys.path.insert(0, str(PROJECT_ROOT))
        sys.path.insert(0, str(PROJECT_ROOT / "utils"))

        # C3.1 MarketDataProvider 整体初始化
        try:
            from data_provider import MarketDataProvider
            dp = MarketDataProvider()
            health = getattr(dp, "source_health", {})
            ok_count = sum(1 for h in health.values() if h.get("ok", False))
            total = len(health) if health else 0
            # 至少 Wind MCP 必须可用
            wind_ok = health.get("wind_mcp", {}).get("ok", False)
            if wind_ok:
                self._pass("C3.1", "Wind MCP 主数据源 (P1)", CheckLevel.ERROR,
                           detail=f"已加载, source_health: {ok_count}/{total} OK")
            else:
                wind_err = health.get("wind_mcp", {}).get("last_error", "unknown")
                self._fail("C3.1", "Wind MCP 主数据源 (P1)", CheckLevel.ERROR,
                           detail=f"Wind MCP 不可用: {wind_err}",
                           remediation="检查 WIND_API_KEY 环境变量; "
                                       "运行 python scripts/_diag_three_sources.py 排查")

            # C3.2 iFinD MCP
            ifind_ok = health.get("ifind_mcp", {}).get("ok", False)
            if ifind_ok:
                self._pass("C3.2", "iFinD MCP (P2)", CheckLevel.WARN,
                           detail="已加载")
            else:
                ifind_err = health.get("ifind_mcp", {}).get("last_error", "未配置")
                self._fail("C3.2", "iFinD MCP (P2)", CheckLevel.WARN,
                           detail=f"不可用: {ifind_err}",
                           remediation="检查 IFIND_TOKEN; 注意 iFinD 有日配额限制")

            # C3.3 TDX 通达信
            tdx_ok = health.get("tdx", {}).get("ok", False)
            if tdx_ok:
                self._pass("C3.3", "TDX 通达信 (P2.5)", CheckLevel.WARN,
                           detail="已连接")
            else:
                tdx_err = health.get("tdx", {}).get("last_error", "未连接")
                self._fail("C3.3", "TDX 通达信 (P2.5)", CheckLevel.WARN,
                           detail=f"不可用: {tdx_err}",
                           remediation="pip install pytdx2; 检查 7709 端口出站权限")

            # C3.4 AKShare
            ak_ok = health.get("akshare", {}).get("ok", False)
            if ak_ok:
                self._pass("C3.4", "AKShare (P3)", CheckLevel.WARN,
                           detail="已加载")
            else:
                ak_err = health.get("akshare", {}).get("last_error", "未加载")
                self._fail("C3.4", "AKShare (P3)", CheckLevel.WARN,
                           detail=f"不可用: {ak_err}",
                           remediation="pip install akshare; 检查 NO_PROXY 环境变量")

            # C3.5 数据源冗余度评估
            if ok_count < 2:
                self._fail("C3.5", "数据源冗余度", CheckLevel.ERROR,
                           detail=f"仅 {ok_count} 个数据源可用, 风险高",
                           remediation="至少需要 Wind MCP + 一个备选 (iFinD/TDX/AKShare)")
            else:
                self._pass("C3.5", "数据源冗余度", CheckLevel.INFO,
                           detail=f"{ok_count}/{total} 数据源可用")
        except ImportError as e:
            self._fail("C3.0", "MarketDataProvider 导入", CheckLevel.ERROR,
                       detail=f"导入失败: {e}",
                       remediation="检查 utils/data_provider.py 是否存在")
        except Exception as e:
            self._fail("C3.0", "MarketDataProvider 初始化", CheckLevel.ERROR,
                       detail=f"初始化异常: {e}",
                       remediation="运行 python scripts/_diag_three_sources.py 排查")

    # --------------------------------------------------------------------
    # C4. 配置文件 Schema 检查
    # --------------------------------------------------------------------
    def check_config_schema(self) -> None:
        """检查关键配置文件的字段完整性"""
        print("\n[C4] 配置文件 Schema 检查")
        print("-" * 60)

        # C4.1 positions.json (适配 28-终极量化交易系统8.4 实际格式:
        #     {meta: {...}, positions: {symbol: {code,name,shares,...}}, hedge_positions: {...}})
        positions_path = PROJECT_ROOT / "config" / "positions.json"
        try:
            if not positions_path.exists():
                self._fail("C4.1", "positions.json 存在性", CheckLevel.ERROR,
                           detail="文件不存在",
                           remediation="从备份恢复 config/positions.json")
            else:
                with open(positions_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                if not isinstance(data, dict):
                    self._fail("C4.1", "positions.json 格式", CheckLevel.ERROR,
                               detail=f"顶层应为 dict, 实际 {type(data).__name__}",
                               remediation="检查 JSON 格式")
                else:
                    pos_dict = data.get("positions", {})
                    if not isinstance(pos_dict, dict) or len(pos_dict) == 0:
                        self._fail("C4.1", "positions.json 内容", CheckLevel.ERROR,
                                   detail="positions 字段为空或非 dict",
                                   remediation="检查 positions.json 是否被清空")
                    else:
                        # 检查第一个持仓的字段完整性
                        first_sym = next(iter(pos_dict))
                        first = pos_dict[first_sym]
                        required_fields = ["code", "shares"]
                        missing = [f for f in required_fields if f not in first]
                        if missing:
                            self._fail("C4.1", "positions.json Schema", CheckLevel.ERROR,
                                       detail=f"{first_sym} 缺少字段: {missing}",
                                       remediation=f"补全持仓项的 {missing} 字段")
                        else:
                            # 检查 shares 是否为正数 (有持仓)
                            total_shares = sum(
                                p.get("shares", 0) for p in pos_dict.values()
                                if isinstance(p, dict)
                            )
                            if total_shares == 0:
                                self._fail("C4.1", "positions.json 持仓", CheckLevel.ERROR,
                                           detail=f"{len(pos_dict)} 个标的, 但总持仓为 0",
                                           remediation="检查是否所有 shares 都被误清零")
                            else:
                                self._pass("C4.1", "positions.json Schema", CheckLevel.ERROR,
                                           detail=f"{len(pos_dict)} 个标的, 总持仓 {total_shares} 股, 字段完整")
        except json.JSONDecodeError as e:
            self._fail("C4.1", "positions.json 解析", CheckLevel.ERROR,
                       detail=f"JSON 解析失败: {e}",
                       remediation="检查 JSON 语法 (括号/逗号)")
        except Exception as e:
            self._fail("C4.1", "positions.json 检查", CheckLevel.ERROR,
                       detail=f"异常: {e}",
                       remediation="查看 system_check 日志")

    # --------------------------------------------------------------------
    # C5. Python 依赖与模块导入检查
    # --------------------------------------------------------------------
    def check_python_dependencies(self) -> None:
        """检查关键 Python 依赖与模块可导入"""
        print("\n[C5] Python 依赖与关键模块导入检查")
        print("-" * 60)

        critical_modules = [
            ("pandas", "数据分析"),
            ("numpy", "数值计算"),
            ("requests", "HTTP 客户端"),
            ("yaml", "YAML 配置解析"),
        ]
        optional_modules = [
            ("akshare", "免费数据源 (P3)"),
            ("pytdx", "通达信数据源 (P2.5)"),
            ("lightgbm", "ML 模型"),
            ("sklearn", "ML 工具"),
        ]

        # 必需模块
        for idx, (mod_name, desc) in enumerate(critical_modules, 1):
            code = f"C5.{idx}"
            try:
                __import__(mod_name)
                self._pass(code, f"{desc} ({mod_name})", CheckLevel.ERROR,
                           detail="可导入")
            except ImportError:
                self._fail(code, f"{desc} ({mod_name})", CheckLevel.ERROR,
                           detail=f"模块未安装",
                           remediation=f"pip install {mod_name}")

        # 可选模块
        offset = len(critical_modules)
        for idx, (mod_name, desc) in enumerate(optional_modules, 1):
            code = f"C5.{offset + idx}"
            try:
                __import__(mod_name)
                self._pass(code, f"{desc} ({mod_name})", CheckLevel.WARN,
                           detail="可导入")
            except ImportError:
                self._fail(code, f"{desc} ({mod_name})", CheckLevel.WARN,
                           detail="未安装 (可选)",
                           remediation=f"pip install {mod_name}")

    # --------------------------------------------------------------------
    # C6. 目录权限与磁盘空间检查
    # --------------------------------------------------------------------
    def check_disk_and_permissions(self) -> None:
        """检查关键目录的写权限与磁盘空间"""
        print("\n[C6] 目录权限与磁盘空间检查")
        print("-" * 60)

        writable_dirs = [
            ("每日报告归档", "报告归档目录"),
            ("v8.3_institutional/trade_plans", "trade_plan 目录"),
            ("v8.3_institutional/reports", "报告目录"),
            ("data_cache", "数据缓存目录"),
        ]

        # C6.1 目录写权限
        for idx, (rel_path, desc) in enumerate(writable_dirs, 1):
            code = f"C6.{idx}"
            dir_path = PROJECT_ROOT / rel_path
            try:
                dir_path.mkdir(parents=True, exist_ok=True)
                # 测试写权限
                test_file = dir_path / ".system_check_test"
                test_file.write_text("test", encoding="utf-8")
                test_file.unlink()
                self._pass(code, f"{desc} 写权限", CheckLevel.ERROR,
                           detail=f"{rel_path} 可写")
            except Exception as e:
                self._fail(code, f"{desc} 写权限", CheckLevel.ERROR,
                           detail=f"无法写入 {rel_path}: {e}",
                           remediation=f"检查 {rel_path} 目录权限")

        # C6.5 磁盘空间
        code = "C6.5"
        try:
            disk_usage = shutil.disk_usage(str(PROJECT_ROOT))
            free_gb = disk_usage.free / (1024 ** 3)
            total_gb = disk_usage.total / (1024 ** 3)
            usage_pct = (disk_usage.used / disk_usage.total) * 100
            if free_gb < 1.0:
                self._fail(code, "磁盘剩余空间", CheckLevel.ERROR,
                           detail=f"仅剩 {free_gb:.2f} GB (使用率 {usage_pct:.1f}%)",
                           remediation="清理磁盘空间 (至少保留 1GB)")
            elif free_gb < 5.0:
                self._fail(code, "磁盘剩余空间", CheckLevel.WARN,
                           detail=f"剩余 {free_gb:.2f} GB (使用率 {usage_pct:.1f}%)",
                           remediation="建议清理磁盘空间")
            else:
                self._pass(code, "磁盘剩余空间", CheckLevel.INFO,
                           detail=f"{free_gb:.2f} GB 可用 / {total_gb:.2f} GB 总计 "
                                  f"(使用率 {usage_pct:.1f}%)")
        except Exception as e:
            self._fail(code, "磁盘空间检查", CheckLevel.WARN,
                       detail=f"检查异常: {e}",
                       remediation="手动检查磁盘空间")

    # --------------------------------------------------------------------
    # C7. 关键子模块 Smoke 测试
    # --------------------------------------------------------------------
    def check_subsystem_smoke(self) -> None:
        """关键业务子模块 smoke 测试 (轻量级,不修改数据)"""
        print("\n[C7] 关键子模块 Smoke 测试")
        print("-" * 60)

        sys.path.insert(0, str(PROJECT_ROOT))

        # C7.1 hedge_execution_engine 可导入 (28-终极量化交易系统8.4 实际模块名)
        try:
            from utils.hedge_execution_engine import HedgeExecutionEngine
            self._pass("C7.1", "HedgeExecutionEngine 模块", CheckLevel.ERROR,
                       detail="可导入")
        except Exception as e:
            self._fail("C7.1", "HedgeExecutionEngine 模块", CheckLevel.ERROR,
                       detail=f"导入失败: {e}",
                       remediation="检查 utils/hedge_execution_engine.py 完整性")

        # C7.2 signal_fusion 可导入
        try:
            from utils.signal_fusion import SignalFusionEngine
            self._pass("C7.2", "SignalFusionEngine 模块", CheckLevel.WARN,
                       detail="可导入")
        except Exception as e:
            self._fail("C7.2", "SignalFusionEngine 模块", CheckLevel.WARN,
                       detail=f"导入失败: {e}",
                       remediation="检查 utils/signal_fusion.py 完整性")

        # C7.3 logger 可用
        try:
            from utils.logger import get_logger
            test_logger = get_logger("system_check_test")
            self._pass("C7.3", "Logger 模块", CheckLevel.ERROR,
                       detail="可导入")
        except Exception as e:
            self._fail("C7.3", "Logger 模块", CheckLevel.ERROR,
                       detail=f"导入失败: {e}",
                       remediation="检查 utils/logger.py 完整性")

    # --------------------------------------------------------------------
    # C8. 历史数据完整性检查
    # --------------------------------------------------------------------
    def check_historical_data(self) -> None:
        """检查关键历史数据文件完整性"""
        print("\n[C8] 历史数据完整性检查")
        print("-" * 60)

        # C8.1 daily_returns.jsonl 末尾 N 行可解析
        returns_path = PROJECT_ROOT / "reports" / "shadow" / "daily_returns.jsonl"
        try:
            if not returns_path.exists():
                self._fail("C8.1", "daily_returns.jsonl 存在性", CheckLevel.WARN,
                           detail="文件不存在 (首次运行可接受)",
                           remediation="运行 shadow_admission_launcher.py 生成")
            else:
                # 读取末尾 5 行
                with open(returns_path, "r", encoding="utf-8") as f:
                    lines = f.readlines()[-5:]
                parsed = 0
                for line in lines:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        json.loads(line)
                        parsed += 1
                    except json.JSONDecodeError:
                        pass
                if parsed == len([l for l in lines if l.strip()]):
                    self._pass("C8.1", "daily_returns.jsonl 完整性", CheckLevel.WARN,
                               detail=f"末尾 {parsed} 行均可解析")
                else:
                    self._fail("C8.1", "daily_returns.jsonl 完整性", CheckLevel.WARN,
                               detail=f"末尾 {len(lines)} 行中仅 {parsed} 行可解析",
                               remediation="修复 JSONL 损坏行")
        except Exception as e:
            self._fail("C8.1", "daily_returns.jsonl 检查", CheckLevel.WARN,
                       detail=f"异常: {e}",
                       remediation="检查文件权限")

        # C8.2 watchdog heartbeat 存在 (近 24h)
        heartbeat_path = PROJECT_ROOT / "logs" / "shadow_watchdog_heartbeat.jsonl"
        try:
            if not heartbeat_path.exists():
                self._pass("C8.2", "Watchdog heartbeat", CheckLevel.INFO,
                           detail="heartbeat 文件不存在 (可能首次运行)")
            else:
                # 读取最后一行
                with open(heartbeat_path, "r", encoding="utf-8") as f:
                    lines = f.readlines()
                if lines:
                    last = json.loads(lines[-1].strip())
                    # heartbeat 格式: {"ts": "...", "outcome": "...", "exit_code": ...}
                    last_time = last.get("ts") or last.get("timestamp") or ""
                    if last_time:
                        outcome = last.get("outcome", "unknown")
                        self._pass("C8.2", "Watchdog heartbeat", CheckLevel.INFO,
                                   detail=f"最近 heartbeat: {last_time} (outcome={outcome})")
                    else:
                        self._fail("C8.2", "Watchdog heartbeat", CheckLevel.WARN,
                                   detail="末行无 ts/timestamp 字段")
                else:
                    self._fail("C8.2", "Watchdog heartbeat", CheckLevel.WARN,
                               detail="文件为空",
                               remediation="运行 shadow_admission_watchdog.py")
        except Exception as e:
            self._fail("C8.2", "Watchdog heartbeat 检查", CheckLevel.WARN,
                       detail=f"异常: {e}")

    # --------------------------------------------------------------------
    # C9. 兜底价格新鲜度检查 (R1 新增 2026-08-01)
    # --------------------------------------------------------------------
    # 触发场景: 所有数据源失效时, futures_prices.py 会回退到 DEFAULT_FUTURES_PRICES
    # 风险: 兜底价格过期会导致对冲计算失真, 名义价值错误, 保证金占用错算
    # 阈值: age > 7 天 WARN, age > 30 天 ERROR (fail-closed 阻断启动)
    # 依据: hedge_rebalance_v59.py 的 is_price_safe() 机制 (CR1 修复)
    # --------------------------------------------------------------------
    FALLBACK_PRICE_THRESHOLDS = {
        "warn_days": 7,    # 超过 7 天告警
        "error_days": 30,  # 超过 30 天阻断
    }
    FUTURES_PRICES_REL_PATH = "v8.3_institutional/src/data/futures_prices.py"

    def check_fallback_price_freshness(self) -> None:
        """检查兜底期货价格的新鲜度 (C9)

        读取 v8.3_institutional/src/data/futures_prices.py 的:
            - DEFAULT_FUTURES_PRICES: 兜底价格表
            - FALLBACK_PRICES_UPDATED: 最后更新日期 (YYYY-MM-DD)

        计算 age_days = (today - FALLBACK_PRICES_UPDATED).days, 分级判定:
            - age <= 7: INFO PASS
            - 7 < age <= 30: WARN FAIL (告警但不阻断)
            - age > 30: ERROR FAIL (fail-closed, 阻断启动)
        """
        import importlib.util
        import time
        from datetime import datetime

        print("\n[C9] 兜底价格新鲜度检查")
        print("-" * 60)

        t0 = time.time()
        fp_path = PROJECT_ROOT / self.FUTURES_PRICES_REL_PATH
        code_prefix = "C9.1"

        # C9.1 兜底价格模块存在性
        if not fp_path.exists():
            elapsed = (time.time() - t0) * 1000
            self._fail(code_prefix, "兜底价格模块存在性", CheckLevel.ERROR,
                       detail=f"文件不存在: {fp_path}",
                       remediation=f"恢复 {self.FUTURES_PRICES_REL_PATH}; "
                                   f"或检查项目结构是否变更",
                       elapsed_ms=elapsed)
            return

        # C9.2 加载模块并读取 FALLBACK_PRICES_UPDATED
        try:
            spec = importlib.util.spec_from_file_location(
                "_system_check_futures_prices", str(fp_path)
            )
            if spec is None or spec.loader is None:
                raise ImportError(f"无法创建模块 spec: {fp_path}")
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
        except Exception as e:
            elapsed = (time.time() - t0) * 1000
            self._fail(code_prefix, "兜底价格模块加载", CheckLevel.ERROR,
                       detail=f"模块加载失败: {e}",
                       remediation=f"检查 {self.FUTURES_PRICES_REL_PATH} 语法",
                       elapsed_ms=elapsed)
            return

        # 提取字段
        default_prices = getattr(mod, "DEFAULT_FUTURES_PRICES", None)
        updated_str = getattr(mod, "FALLBACK_PRICES_UPDATED", None)
        elapsed_load = (time.time() - t0) * 1000

        # C9.1 字段完整性 (覆盖原 code_prefix 含义: 模块+字段均存在)
        if not isinstance(default_prices, dict) or not default_prices:
            self._fail(code_prefix, "DEFAULT_FUTURES_PRICES 完整性", CheckLevel.ERROR,
                       detail="DEFAULT_FUTURES_PRICES 缺失或非非空 dict",
                       remediation="检查 futures_prices.py 顶部常量定义",
                       elapsed_ms=elapsed_load)
            return

        required_codes = ["IF", "IC", "IM", "IH"]
        missing_codes = [c for c in required_codes if c not in default_prices]
        if missing_codes:
            self._fail(code_prefix, "DEFAULT_FUTURES_PRICES 完整性", CheckLevel.ERROR,
                       detail=f"缺少期货品种: {missing_codes}",
                       remediation=f"补全 {missing_codes} 的兜底价格",
                       elapsed_ms=elapsed_load)
            return

        if not updated_str or not isinstance(updated_str, str):
            self._fail(code_prefix, "FALLBACK_PRICES_UPDATED 字段", CheckLevel.ERROR,
                       detail="FALLBACK_PRICES_UPDATED 缺失或非字符串",
                       remediation="在 futures_prices.py 添加 FALLBACK_PRICES_UPDATED = 'YYYY-MM-DD'",
                       elapsed_ms=elapsed_load)
            return

        # C9.1 字段完整性全部通过 (模块存在 + 4 品种齐全 + 日期字段存在)
        self._pass(code_prefix, "兜底价格字段完整性", CheckLevel.ERROR,
                   detail=f"DEFAULT_FUTURES_PRICES 含 {len(default_prices)} 品种, "
                          f"FALLBACK_PRICES_UPDATED={updated_str}",
                   elapsed_ms=elapsed_load)

        # C9.2 日期解析与年龄计算
        code_age = "C9.2"
        try:
            updated_date = datetime.strptime(updated_str, "%Y-%m-%d")
        except ValueError as e:
            self._fail(code_age, "FALLBACK_PRICES_UPDATED 格式", CheckLevel.ERROR,
                       detail=f"日期解析失败: '{updated_str}' (期望 YYYY-MM-DD): {e}",
                       remediation="修正为 'YYYY-MM-DD' 格式, 如 '2026-08-01'",
                       elapsed_ms=elapsed_load)
            return

        age_days = (datetime.now() - updated_date).days
        warn_days = self.FALLBACK_PRICE_THRESHOLDS["warn_days"]
        error_days = self.FALLBACK_PRICE_THRESHOLDS["error_days"]
        # 包含 4 个品种的简要价格摘要
        price_summary = ", ".join(f"{c}={default_prices[c]:.1f}" for c in required_codes)

        # 负年龄 (未来日期) — 视为配置错误, 不应出现
        if age_days < 0:
            self._fail(code_age, "兜底价格日期合法性", CheckLevel.ERROR,
                       detail=f"更新日期 {updated_str} 在未来 (age={age_days} 天), 配置错误",
                       remediation="检查 FALLBACK_PRICES_UPDATED 是否被误改为未来日期",
                       elapsed_ms=elapsed_load)
            return

        # 分级判定
        if age_days > error_days:
            self._fail(code_age, "兜底价格新鲜度", CheckLevel.ERROR,
                       detail=f"最后更新 {updated_str}, 已过期 {age_days} 天 "
                              f"(超 {error_days} 天阈值), 价格: {price_summary}",
                       remediation=f"立即更新 {self.FUTURES_PRICES_REL_PATH} "
                                   f"中的 DEFAULT_FUTURES_PRICES 与 FALLBACK_PRICES_UPDATED; "
                                   f"或临时调高 error_days 阈值 (不推荐)",
                       elapsed_ms=elapsed_load)
        elif age_days > warn_days:
            self._fail(code_age, "兜底价格新鲜度", CheckLevel.WARN,
                       detail=f"最后更新 {updated_str}, 已 {age_days} 天 "
                              f"(超 {warn_days} 天告警阈值), 价格: {price_summary}",
                       remediation=f"建议尽快更新 DEFAULT_FUTURES_PRICES "
                                   f"并刷新 FALLBACK_PRICES_UPDATED",
                       elapsed_ms=elapsed_load)
        else:
            self._pass(code_age, "兜底价格新鲜度", CheckLevel.INFO,
                       detail=f"最后更新 {updated_str}, {age_days} 天前 (阈值 {warn_days}/{error_days}), "
                              f"价格: {price_summary}",
                       elapsed_ms=elapsed_load)

    # --------------------------------------------------------------------
    # 主执行入口
    # --------------------------------------------------------------------
    def run_all(self) -> SystemCheckReport:
        """执行全部检查,返回汇总报告"""
        from datetime import datetime
        check_time = datetime.now().isoformat()

        print("=" * 70)
        print("P0 启动自检系统 (System Check) v8.6.13")
        print(f"项目根目录: {PROJECT_ROOT}")
        print(f"检查时间: {check_time}")
        print(f"模式: {'strict (严格)' if self.strict else 'normal (常规)'}"
              f"{', skip_datasource' if self.skip_datasource else ''}")
        print("=" * 70)

        self._results = []

        try:
            self.check_critical_files()           # C1
            self.check_env_variables()            # C2
            self.check_datasource_connectivity()  # C3
            self.check_config_schema()            # C4
            self.check_python_dependencies()      # C5
            self.check_disk_and_permissions()     # C6
            self.check_subsystem_smoke()          # C7
            self.check_historical_data()           # C8
            self.check_fallback_price_freshness() # C9 (R1 新增 2026-08-01)
        except Exception as e:
            # 自检本身异常
            self._add(CheckResult(
                code="SYS",
                name="自检脚本异常",
                level=CheckLevel.ERROR,
                status=CheckStatus.FAIL,
                detail=f"{e}\n{traceback.format_exc()}",
                remediation="修复 utils/system_check.py 本身",
            ))

        # 汇总
        report = self._build_report(check_time)
        return report

    def _build_report(self, check_time: str) -> SystemCheckReport:
        """构建汇总报告"""
        total = len(self._results)
        passed = sum(1 for r in self._results if r.status == CheckStatus.PASS)
        failed = sum(1 for r in self._results if r.status == CheckStatus.FAIL)
        skipped = sum(1 for r in self._results if r.status == CheckStatus.SKIP)
        warnings = sum(1 for r in self._results
                       if r.status == CheckStatus.FAIL and r.level == CheckLevel.WARN)
        blocking = sum(1 for r in self._results if r.is_blocking)

        # 严格模式: WARN FAIL 也算阻止性
        effective_blocking = blocking
        if self.strict:
            effective_blocking = sum(1 for r in self._results
                                     if r.status == CheckStatus.FAIL
                                     and r.level in (CheckLevel.ERROR, CheckLevel.WARN))

        exit_code = 0 if effective_blocking == 0 else 1

        error_summary = [
            f"[{r.code}] {r.name}: {r.detail}"
            for r in self._results
            if r.status == CheckStatus.FAIL and r.level == CheckLevel.ERROR
        ]

        return SystemCheckReport(
            check_time=check_time,
            project_root=str(PROJECT_ROOT),
            total=total,
            passed=passed,
            failed=failed,
            skipped=skipped,
            warnings=warnings,
            blocking_failures=effective_blocking,
            exit_code=exit_code,
            results=list(self._results),
            error_summary=error_summary,
        )

    # --------------------------------------------------------------------
    # 格式化报告输出
    # --------------------------------------------------------------------
    @staticmethod
    def format_report(report: SystemCheckReport) -> str:
        """格式化报告为可读字符串"""
        lines = []
        lines.append("")
        lines.append("=" * 70)
        lines.append("P0 启动自检 - 结果汇总")
        lines.append("=" * 70)
        lines.append(f"检查时间: {report.check_time}")
        lines.append(f"总检查项: {report.total}")
        lines.append(f"  ✅ PASS:     {report.passed}")
        lines.append(f"  ❌ FAIL:     {report.failed}")
        lines.append(f"  ⏭️  SKIP:     {report.skipped}")
        lines.append(f"  ⚠️  WARN FAIL: {report.warnings}")
        lines.append(f"  🚫 阻止性:    {report.blocking_failures}")
        lines.append("")

        # 列出所有失败项
        fail_results = [r for r in report.results
                        if r.status == CheckStatus.FAIL
                        and r.level == CheckLevel.ERROR]
        if fail_results:
            lines.append("-" * 70)
            lines.append("🚫 阻止性失败项 (必须修复后才能进入工作流):")
            lines.append("-" * 70)
            for r in fail_results:
                lines.append(f"  [{r.code}] {r.name}")
                lines.append(f"    详情: {r.detail}")
                if r.remediation:
                    lines.append(f"    修复: {r.remediation}")
                lines.append("")

        # 警告项
        warn_results = [r for r in report.results
                        if r.status == CheckStatus.FAIL
                        and r.level == CheckLevel.WARN]
        if warn_results:
            lines.append("-" * 70)
            lines.append("⚠️ 警告项 (不阻止,但建议修复):")
            lines.append("-" * 70)
            for r in warn_results:
                lines.append(f"  [{r.code}] {r.name}: {r.detail}")
                if r.remediation:
                    lines.append(f"    建议: {r.remediation}")
                lines.append("")

        # 最终结论
        lines.append("=" * 70)
        if report.exit_code == 0:
            lines.append("✅ 自检通过 - 可安全进入工作流")
        else:
            lines.append(f"❌ 自检失败 - 阻止性失败 {report.blocking_failures} 项, "
                         "必须人工干预")
        lines.append(f"   推荐退出码: {report.exit_code}")
        lines.append("=" * 70)

        return "\n".join(lines)

    @staticmethod
    def report_to_json(report: SystemCheckReport) -> str:
        """序列化报告为 JSON"""
        def _serialize(obj):
            if isinstance(obj, Enum):
                return obj.value
            if isinstance(obj, CheckResult):
                d = asdict(obj)
                d["level"] = obj.level.value
                d["status"] = obj.status.value
                return d
            if isinstance(obj, SystemCheckReport):
                return {
                    "check_time": obj.check_time,
                    "project_root": obj.project_root,
                    "total": obj.total,
                    "passed": obj.passed,
                    "failed": obj.failed,
                    "skipped": obj.skipped,
                    "warnings": obj.warnings,
                    "blocking_failures": obj.blocking_failures,
                    "exit_code": obj.exit_code,
                    "error_summary": obj.error_summary,
                    "results": [_serialize(r) for r in obj.results],
                }
            raise TypeError(f"无法序列化: {type(obj)}")

        return json.dumps(_serialize(report), ensure_ascii=False, indent=2)


# ============================================================================
# 便捷函数
# ============================================================================

def run_system_check(strict: bool = False,
                     skip_datasource: bool = False,
                     output_json: bool = False) -> SystemCheckReport:
    """运行系统自检 - 模块集成时的便捷入口

    Args:
        strict: 严格模式 (WARN FAIL 也算阻止性)
        skip_datasource: 跳过数据源连通性检查 (加速)
        output_json: 输出 JSON 报告 (而非文本)

    Returns:
        SystemCheckReport: 自检报告
    """
    checker = SystemChecker(strict=strict, skip_datasource=skip_datasource)
    report = checker.run_all()
    if output_json:
        print(SystemChecker.report_to_json(report))
    else:
        print(SystemChecker.format_report(report))
    return report


def assert_system_ready(strict: bool = False,
                       skip_datasource: bool = False) -> SystemCheckReport:
    """断言系统就绪 - 失败则 sys.exit(1)

    供入口点 (run_daily_eod.py / run_daily_morning.py 等) 在开头调用:

        from utils.system_check import assert_system_ready
        assert_system_ready()  # 失败立即退出

    Args:
        strict: 严格模式
        skip_datasource: 跳过数据源检查

    Returns:
        SystemCheckReport: 自检报告 (若失败已 sys.exit)

    Raises:
        SystemExit: 自检失败时退出 (exit_code 1)
    """
    report = run_system_check(strict=strict, skip_datasource=skip_datasource)
    if report.exit_code != 0:
        logger.error("P0 启动自检失败, 阻止工作流启动 (exit_code=%d)",
                     report.exit_code)
        sys.exit(report.exit_code)
    return report


# ============================================================================
# 模块自测
# ============================================================================

if __name__ == "__main__":
    # 直接运行此模块进行自检
    import argparse
    parser = argparse.ArgumentParser(description="P0 启动自检系统")
    parser.add_argument("--strict", action="store_true",
                        help="严格模式: WARN FAIL 也算阻止性")
    parser.add_argument("--skip-datasource", action="store_true",
                        help="跳过数据源连通性检查 (加速启动)")
    parser.add_argument("--json", action="store_true",
                        help="输出 JSON 格式报告")
    args = parser.parse_args()

    report = run_system_check(
        strict=args.strict,
        skip_datasource=args.skip_datasource,
        output_json=args.json,
    )
    sys.exit(report.exit_code)
