#!/usr/bin/env python3
"""
config/settings.yaml 自动修复脚本 (P0 自检配套)
==================================================

用途:
    - 检测 config/settings.yaml 缺失时自动创建默认配置
    - 默认模板对齐 AGENTS.md v8.6 标准 (6 级数据源降级链, 剔除 iFinD)
    - 集成到 P0 启动自检系统 (C4 配置文件 Schema 维度)

触发场景:
    1. P0 启动自检报 "config/settings.yaml 缺失" 时人工执行
    2. cloned 新仓库后初始化配置
    3. settings.yaml 被误删后的灾难恢复
    4. 集成到 run_p0_startup_check.py 作为自动修复钩子

退出码 (对齐 P0 自检规范):
    0 = settings.yaml 已存在且必要字段完整 (无需修复)
    1 = 已修复 (新建或补全必要字段)
    2 = 脚本异常 (容错通过, 不阻断工作流)

用法:
    python scripts/fix_settings_yaml.py                       # 检测+自动修复 (默认)
    python scripts/fix_settings_yaml.py --check               # 仅检查, 不修改 (CI 模式)
    python scripts/fix_settings_yaml.py --force               # 强制覆盖为默认配置 (慎用)
    python scripts/fix_settings_yaml.py --dry-run             # 预览将创建的内容
    python scripts/fix_settings_yaml.py --migrate-from-v510   # 从 settings_v510.yaml 迁移自定义字段
    python scripts/fix_settings_yaml.py --json                # JSON 报告 (供其他程序消费)

设计原则 (对齐 AGENTS.md):
    - 零第三方依赖: 仅用 stdlib + PyYAML (项目已依赖)
    - 幂等: 重复运行不破坏已有配置
    - fail-safe: 异常容错, 不抛 SystemExit 阻断业务
    - 默认安全: 不覆盖已有 settings.yaml (除非 --force)
    - 可审计: 所有操作记录到日志 + 控制台

关联:
    - utils/config_manager.py: ConfigManager 注册 "settings" 短名
    - cli/modes/quick_check.py: --check 模式检查 config/settings.yaml 存在性
    - scripts/run_p0_startup_check.py: P0 启动自检 (C4 维度)
    - AGENTS.md §6 数据源优先级 (Wind Terminal → Wind MCP → TDX → AKShare → sina → cache → predefined)
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

# 项目根目录 (scripts/fix_settings_yaml.py 的上两级)
_PROJECT_ROOT: Path = Path(__file__).resolve().parent.parent
_SETTINGS_PATH: Path = _PROJECT_ROOT / "config" / "settings.yaml"
_V510_PATH: Path = _PROJECT_ROOT / "config" / "settings_v510.yaml"

# 退出码标准化 (对齐 P0 自检规范)
EXIT_OK = 0          # 已存在且完整
EXIT_FIXED = 1       # 已修复
EXIT_ERROR = 2       # 脚本异常

# 必要字段 (用于 --check 模式校验)
# 对齐 AGENTS.md v8.6 §6 数据源优先级 + utils/config_manager.py 注册的短名
REQUIRED_TOP_KEYS = {"system", "data_sources", "trading", "report", "cache", "monitoring", "hedge"}
REQUIRED_DATA_SOURCE_KEYS = {"primary", "secondary", "tertiary", "fallback_order"}

logger = logging.getLogger("fix_settings_yaml")


# ============================================================
# 默认配置模板 (对齐 AGENTS.md v8.6 标准)
# ============================================================
DEFAULT_SETTINGS: dict[str, Any] = {
    # --- 系统基本信息 ---
    "system": {
        "name": "量化策略系统 v8.6",
        "version": "8.6",
        "log_level": "INFO",
        "timezone": "Asia/Shanghai",
        "encoding": "utf-8",
    },
    # --- 数据源优先级 (AGENTS.md §6, 2026-08-03 已剔除 iFinD) ---
    # Wind 数据终端 (P0) → Wind MCP (P1) → TDX 通达信 (P2) → AKShare (P3)
    # → 新浪财经 (P4) → 本地缓存 (P5) → 兜底预定义价格 (P6)
    "data_sources": {
        "primary": "wind_terminal",       # P0: WindPy 原生客户端
        "secondary": "wind_mcp",          # P1: 强制回退
        "tertiary": "tdx",                # P2: 免费直连 (pytdx, TCP 7709)
        "quaternary": "akshare",          # P3: 免费回退
        "fallback_order": [
            "wind_terminal",
            "wind_mcp",
            "tdx",
            "akshare",
            "sina_api",
            "local_cache",
            "predefined_prices",
        ],
    },
    # --- 数据源详细配置 ---
    "wind_terminal": {
        "enabled": True,
        "priority": 0,
        "name": "Wind 数据终端",
        "module": "WindPy",
    },
    "wind_mcp": {
        "enabled": True,
        "priority": 1,
        "name": "Wind MCP",
        "api_key_env": "WIND_API_KEY",
    },
    "tdx": {
        "enabled": True,
        "priority": 2,
        "name": "TDX 通达信",
        "host": "127.0.0.1",
        "port": 7709,
        "market": "A_share",  # 仅支持沪深 A 股
    },
    "akshare": {
        "enabled": True,
        "priority": 3,
        "name": "AKShare",
    },
    "sina_api": {
        "enabled": True,
        "priority": 4,
        "name": "新浪财经API",
    },
    "local_cache": {
        "enabled": True,
        "priority": 5,
        "cache_dir": "data/cache",
        "cache_ttl_hours": 24,
    },
    "predefined_prices": {
        "enabled": True,
        "priority": 6,
        "source": "config/portfolio.yaml",  # fallback_prices 节
    },
    # --- 报告配置 ---
    "report": {
        "output_dir": "每日报告归档",
        "archive_format": "markdown",
        "enable_ai_analysis": True,
        "ai_model": "glm-5",
    },
    # --- 交易配置 ---
    "trading": {
        "enabled": False,           # 实盘交易总开关 (默认关闭, 安全)
        "paper_trading": True,      # 模拟交易
        "commission_rate": 0.0003,  # 佣金费率
        "stamp_duty": 0.001,        # 印花税 (卖出)
        "min_commission": 5,        # 最小佣金 (元)
    },
    # --- 缓存配置 ---
    "cache": {
        "enabled": True,
        "price_cache_ttl": 300,          # 价格缓存 5 分钟
        "fundamental_cache_ttl": 86400,   # 基本面缓存 1 天
    },
    # --- 监控配置 ---
    "monitoring": {
        "refresh_interval": 30,    # 刷新间隔 (秒)
        "alert_enabled": True,
        "alert_channels": ["console"],
    },
    # --- 券商配置 (环境变量占位, 不硬编码) ---
    "broker": {
        "type": "mock",
        "account_id": "${QMT_ACCOUNT_ID}",
        "trade_password": "${QMT_TRADE_PASSWORD}",
        "host": "127.0.0.1",
        "port": 10001,
        "qmt_path": "${QMT_PATH}",
        "futures": {
            "enabled": True,
            "account_id": "${QMT_FUTURES_ACCOUNT_ID}",
            "trade_password": "${QMT_FUTURES_TRADE_PASSWORD}",
        },
        "options": {
            "enabled": True,
            "account_id": "${QMT_OPTION_ACCOUNT_ID}",
            "trade_password": "${QMT_OPTION_TRADE_PASSWORD}",
            "commission_rate": 0.0005,
            "max_premium_pct": 0.02,
        },
    },
    # --- 对冲配置 (与 portfolio.yaml 的 hedge 节对齐) ---
    "hedge": {
        "enabled": True,
        "budget": 1000000,        # 100 万对冲账户
        "default_mode": "tail_only",
        "strategies": {
            "delta_hedge": {
                "allocation": 0.25,
                "target_delta": 0.0,
                "instrument": "index_futures",
            },
            "volatility_hedge": {
                "allocation": 0.30,
                "target_vega": 0.0,
                "instrument": "options",
            },
            "absolute_return": {
                "allocation": 0.25,
                "target_sharpe": 1.0,
                "instrument": "mixed",
            },
            "volatility_arbitrage": {
                "allocation": 0.15,
                "target_volatility": 0.15,
                "instrument": "options_futures",
            },
            "covered_write": {
                "allocation": 0.05,
                "premium_target": 0.02,
                "instrument": "options",
            },
        },
    },
}


# ============================================================
# YAML 序列化 (不依赖 PyYAML 的 dump, 用自定义以保证可读性)
# ============================================================
def render_yaml(data: dict[str, Any], indent: int = 0) -> str:
    """将 dict 渲染为可读的 YAML 字符串 (零依赖, 仅支持项目所需子集)

    支持: dict / list / str / int / float / bool / None
    不支持: 复杂对象 (datetime, tuple, set 等)

    Args:
        data: 配置字典
        indent: 当前缩进级别

    Returns:
        YAML 字符串
    """
    lines: list[str] = []
    spaces = "  " * indent

    for key, value in data.items():
        if isinstance(value, dict):
            lines.append(f"{spaces}{key}:")
            lines.append(render_yaml(value, indent + 1))
        elif isinstance(value, list):
            lines.append(f"{spaces}{key}:")
            for item in value:
                if isinstance(item, dict):
                    lines.append(f"{spaces}- ")
                    lines.append(render_yaml(item, indent + 1).rstrip())
                else:
                    lines.append(f"{spaces}  - {_yaml_scalar(item)}")
        else:
            lines.append(f"{spaces}{key}: {_yaml_scalar(value)}")

    return "\n".join(lines) + "\n"


def _yaml_scalar(value: Any) -> str:
    """标量值转 YAML 字符串"""
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return str(value)
    # 字符串: 含特殊字符的加引号
    s = str(value)
    if any(c in s for c in [":", "#", "{", "}", "[", "]", ",", "&", "*", "?", "|", "<", ">", "=", "%", "@", "`"]) or s.startswith("${"):
        # 保留 ${VAR} 形式 (环境变量占位符)
        if s.startswith("${") and s.endswith("}"):
            return s
        return f'"{s}"'
    return s


def build_default_yaml() -> str:
    """构建默认 settings.yaml 内容 (带头部注释)"""
    header = (
        "# 量化策略系统 - 全局设置 (自动生成)\n"
        f"# 版本: v8.6  | 生成时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n"
        "# 数据源优先级: Wind Terminal > Wind MCP > TDX > AKShare > sina > cache > predefined\n"
        "# 关联: utils/config_manager.py (ConfigManager 注册 \"settings\" 短名)\n"
        "# 生成工具: scripts/fix_settings_yaml.py (幂等, 可重复运行)\n"
        "#\n"
        "# ⚠ 本文件为自动生成的默认配置, 如需自定义请直接编辑.\n"
        "#    重新运行 fix_settings_yaml.py 不会覆盖 (除非 --force).\n"
        "\n"
    )
    return header + render_yaml(DEFAULT_SETTINGS)


# ============================================================
# 配置文件检查与修复
# ============================================================
def check_existing(path: Path) -> dict[str, Any]:
    """检查现有 settings.yaml 的字段完整性

    Returns:
        报告字典:
        {
            "exists": bool,
            "path": str,
            "missing_top_keys": list[str],
            "missing_data_source_keys": list[str],
            "has_ifind": bool,  # 是否仍引用已剔除的 iFinD
            "needs_fix": bool,
        }
    """
    report: dict[str, Any] = {
        "exists": path.is_file(),
        "path": str(path),
        "missing_top_keys": [],
        "missing_data_source_keys": [],
        "has_ifind": False,
        "needs_fix": False,
    }

    if not report["exists"]:
        report["needs_fix"] = True
        return report

    # 加载并检查字段
    try:
        import yaml  # 延迟导入, 失败时给出明确错误
    except ImportError:
        report["error"] = "PyYAML 未安装"
        report["needs_fix"] = False  # 不视为配置问题, 视为环境问题
        return report

    try:
        with open(path, encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
    except yaml.YAMLError as e:
        report["error"] = f"YAML 解析失败: {e}"
        report["needs_fix"] = True
        return report

    if not isinstance(data, dict):
        report["error"] = f"配置非 dict 类型: {type(data).__name__}"
        report["needs_fix"] = True
        return report

    # 检查必要顶层字段
    report["missing_top_keys"] = sorted(REQUIRED_TOP_KEYS - set(data.keys()))

    # 检查 data_sources 子字段
    ds = data.get("data_sources", {})
    if isinstance(ds, dict):
        report["missing_data_source_keys"] = sorted(REQUIRED_DATA_SOURCE_KEYS - set(ds.keys()))
        # 检查是否仍引用已剔除的 iFinD (2026-08-03 AGENTS.md)
        ds_text = json.dumps(ds, ensure_ascii=False)
        if "ifind" in ds_text.lower():
            report["has_ifind"] = True

    report["needs_fix"] = bool(
        report["missing_top_keys"]
        or report["missing_data_source_keys"]
        or report["has_ifind"]
    )

    return report


def write_settings(path: Path, content: str, dry_run: bool = False) -> bool:
    """写入 settings.yaml (创建父目录)"""
    if dry_run:
        print(f"[DRY-RUN] 将写入: {path}")
        print(f"[DRY-RUN] 内容预览 (前 50 行):")
        for i, line in enumerate(content.split("\n")[:50], 1):
            print(f"  {i:3d} | {line}")
        if len(content.split("\n")) > 50:
            print(f"  ... (共 {len(content.split(chr(10)))} 行)")
        return True

    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        return True
    except OSError as e:
        print(f"[ERROR] 写入失败: {path}, error={e}", file=sys.stderr)
        return False


def merge_v510_customizations(v510_path: Path) -> dict[str, Any]:
    """从 settings_v510.yaml 迁移自定义字段

    策略: 仅迁移 DEFAULT_SETTINGS 中未定义的字段 (用户自定义项),
    已存在的字段保留默认值 (避免引入旧版过时配置).

    Returns:
        迁移的自定义字段字典 (空字典表示无自定义项或迁移失败)
    """
    if not v510_path.is_file():
        return {}

    try:
        import yaml
        with open(v510_path, encoding="utf-8") as f:
            v510_data = yaml.safe_load(f) or {}
    except (ImportError, yaml.YAMLError, OSError) as e:
        print(f"[WARN] 读取 v510 配置失败, 跳过迁移: {e}", file=sys.stderr)
        return {}

    if not isinstance(v510_data, dict):
        return {}

    # 找出 v510 中存在但 DEFAULT 中不存在的顶层字段
    customizations: dict[str, Any] = {}
    for key, value in v510_data.items():
        if key not in DEFAULT_SETTINGS:
            customizations[key] = value

    return customizations


# ============================================================
# 主流程
# ============================================================
def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="config/settings.yaml 自动修复脚本 (P0 自检配套)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "退出码: 0=已存在且完整 | 1=已修复 | 2=脚本异常\n"
            "示例:\n"
            "  python scripts/fix_settings_yaml.py                 # 检测+自动修复\n"
            "  python scripts/fix_settings_yaml.py --check         # 仅检查\n"
            "  python scripts/fix_settings_yaml.py --dry-run        # 预览\n"
            "  python scripts/fix_settings_yaml.py --force          # 强制覆盖\n"
            "  python scripts/fix_settings_yaml.py --migrate-from-v510  # 迁移 v510 自定义字段\n"
        ),
    )
    parser.add_argument("--check", action="store_true", help="仅检查, 不修改 (CI 模式)")
    parser.add_argument("--force", action="store_true", help="强制覆盖为默认配置 (慎用, 已有配置会丢失)")
    parser.add_argument("--dry-run", action="store_true", help="预览将创建的内容, 不写入文件")
    parser.add_argument("--migrate-from-v510", action="store_true", help="从 settings_v510.yaml 迁移自定义字段")
    parser.add_argument("--json", action="store_true", help="JSON 报告 (供其他程序消费)")
    parser.add_argument("--verbose", "-v", action="store_true", help="详细日志")
    args = parser.parse_args(argv)

    # 日志配置
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s | %(name)s | %(levelname)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    # JSON 模式: 静默日志
    if args.json:
        logging.disable(logging.CRITICAL)

    # 1. 检查现有配置
    report = check_existing(_SETTINGS_PATH)

    # 2. 决定是否需要修复
    # 关键: --check 模式必须完全只读, 任何情况下都不修改文件 (CI 友好)
    needs_fix = report["needs_fix"] or not report["exists"]

    if args.check:
        # 仅检查模式: 报告状态, 不执行任何修改
        action = "report_only" if needs_fix else "none"
    elif args.force:
        action = "overwrite"
    elif not report["exists"]:
        action = "create"
    elif needs_fix:
        action = "repair"  # 补全缺失字段 (保留已有字段)
    else:
        action = "none"

    # 3. 执行修复
    fixed = False
    error_msg = ""

    if action in {"create", "overwrite"}:
        content = build_default_yaml()

        # 迁移 v510 自定义字段
        if args.migrate_from_v510:
            custom = merge_v510_customizations(_V510_PATH)
            if custom:
                logger.info(f"从 v510 迁移 {len(custom)} 个自定义字段: {list(custom.keys())}")
                content += "\n# --- 从 settings_v510.yaml 迁移的自定义字段 ---\n"
                content += render_yaml(custom)

        if write_settings(_SETTINGS_PATH, content, dry_run=args.dry_run):
            fixed = True
            report["action"] = action
        else:
            error_msg = "写入失败"
            report["action"] = "write_failed"

    elif action == "repair":
        # 补全缺失字段: 读取现有 + 合并默认 + 写回
        # 注意: 此分支保留用户已有配置, 仅补全缺失字段
        try:
            import yaml
            with open(_SETTINGS_PATH, encoding="utf-8") as f:
                existing = yaml.safe_load(f) or {}

            # 深度合并 (默认值作为底, 现有值覆盖)
            merged: dict[str, Any] = {}
            for key, default_val in DEFAULT_SETTINGS.items():
                if key in existing:
                    merged[key] = existing[key]
                else:
                    merged[key] = default_val
            # 保留用户自定义的额外顶层字段
            for key, value in existing.items():
                if key not in merged:
                    merged[key] = value

            content = build_default_yaml().split("\n", 7)[-1]  # 去掉头部注释
            content = (
                f"# 量化策略系统 - 全局设置 (自动修复)\n"
                f"# 版本: v8.6  | 修复时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n"
                f"# 修复工具: scripts/fix_settings_yaml.py\n"
                f"# ⚠ 已补全缺失字段, 用户自定义项已保留\n"
                "\n"
                + render_yaml(merged)
            )

            if write_settings(_SETTINGS_PATH, content, dry_run=args.dry_run):
                fixed = True
                report["action"] = "repair"
            else:
                error_msg = "写入失败"
                report["action"] = "write_failed"

        except Exception as e:
            error_msg = f"修复失败: {e}"
            report["action"] = "repair_failed"

    else:
        report["action"] = action

    # 4. 输出报告
    # 退出码语义:
    #   --check 模式 (CI 集成): 需要修复则 exit=1 (FAIL, CI 应 fail 或触发自动修复)
    #   默认模式 (人工执行): 已修复则 exit=1 (FIXED), 未修复则 exit=0 (OK)
    #   异常: exit=2 (ERROR)
    if error_msg:
        report["exit_code"] = EXIT_ERROR
    elif args.check:
        # --check 模式: 需要修复则 FAIL (exit=1), 否则 OK (exit=0)
        report["exit_code"] = EXIT_FIXED if needs_fix else EXIT_OK
    else:
        # 默认模式: 已修复则 FIXED (exit=1), 否则 OK (exit=0)
        report["exit_code"] = EXIT_FIXED if fixed else EXIT_OK

    if error_msg:
        report["error"] = error_msg

    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        _print_human_report(report)

    return report["exit_code"]


def _print_human_report(report: dict[str, Any]) -> None:
    """人类可读报告"""
    path = report["path"]
    print("=" * 70)
    print(f"  config/settings.yaml 修复报告")
    print("=" * 70)
    print(f"  路径: {path}")
    print(f"  存在: {'✅' if report['exists'] else '❌'}")
    print(f"  动作: {report.get('action', 'none')}")

    if report["exists"]:
        if report["missing_top_keys"]:
            print(f"  缺失顶层字段 ({len(report['missing_top_keys'])}): {report['missing_top_keys']}")
        else:
            print("  顶层字段: ✅ 完整")

        if report["missing_data_source_keys"]:
            print(f"  缺失 data_sources 子字段: {report['missing_data_source_keys']}")
        else:
            print("  data_sources 子字段: ✅ 完整")

        if report["has_ifind"]:
            print("  ⚠ 仍引用已剔除的 iFinD (AGENTS.md 2026-08-03)")

        if report["needs_fix"]:
            print("  状态: ⚠ 需修复")
        else:
            print("  状态: ✅ 字段完整")
    else:
        print("  状态: ❌ 文件缺失")

    if "error" in report:
        print(f"  错误: {report['error']}")

    exit_code = report.get("exit_code", EXIT_ERROR)
    exit_msg = {
        EXIT_OK: "✅ 无需修复 (exit=0)",
        EXIT_FIXED: "✅ 已修复 (exit=1)",
        EXIT_ERROR: "❌ 异常 (exit=2)",
    }.get(exit_code, f"未知 exit={exit_code}")
    print(f"  退出: {exit_msg}")
    print("=" * 70)


if __name__ == "__main__":
    try:
        sys.exit(main())
    except SystemExit:
        raise
    except Exception as e:
        print(f"[ERROR] 脚本异常: {e}", file=sys.stderr)
        import traceback
        traceback.print_exc(file=sys.stderr)
        sys.exit(EXIT_ERROR)
