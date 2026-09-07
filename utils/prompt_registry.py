"""Prompt 版本注册中心 (Prompt Registry).

借鉴 google-skills/agent-platform-prompt-management 的 prompt 版本化 + 模板变量 +
Tier M/D 确认门禁模式, 为本系统 GLM-5/DeepSeek 等模型的 prompt 提供集中治理.

核心能力:
    1. 注册: prompt 模板 + 变量声明 + 目标模型 + 版本号.
    2. 版本化: 同名 prompt 多版本共存, 默认拉取最新, 可指定版本回滚.
    3. 模板变量: {{var}} 语法, assemble 时替换; 缺失变量告警但不崩溃.
    4. 安全分级: register/update 走 Tier M, delete 走 Tier D, get/list 走 Tier R.
    5. 持久化: config/prompts.yaml 单一事实源, 人类可读可 diff.
    6. 向后兼容: glm5_client 的 system_prompt 字符串参数可零改造成 prompt_name+variables.

存储格式 (config/prompts.yaml):
    prompts:
      glm5_daily_decision:
        description: GLM-5 盘中决策 prompt
        target_model: glm-5
        variables: [symbol, position, market_state]
        versions:
          "1":
            template: |
              你是量化交易决策助手. 当前标的: {{symbol}}...
            created_at: 2026-08-25T10:00:00
          "2":
            template: |
              ...
            created_at: 2026-08-25T12:00:00
        latest: 2

用法:
    from utils.prompt_registry import PromptRegistry
    reg = PromptRegistry()
    reg.register("glm5_daily", "你是量化助手. 标的={{symbol}}", variables=["symbol"], target_model="glm-5")
    assembled = reg.assemble("glm5_daily", symbol="600519")  # 自动拉最新版本

集成日期: 2026-08-25 (借鉴 google-skills prompt-management 模式)
"""

from __future__ import annotations

import logging
import re
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

try:
    import yaml
except ImportError:
    yaml = None

from .tier_safety import tier_d, tier_m, tier_r

logger = logging.getLogger("prompt_registry")

_BASE_DIR = Path(__file__).resolve().parent.parent
_DEFAULT_STORE = _BASE_DIR / "config" / "prompts.yaml"

_VAR_PATTERN = re.compile(r"\{\{\s*(\w+)\s*\}\}")


# ============================================================
# 数据结构
# ============================================================


@dataclass
class PromptVersion:
    """prompt 单版本."""

    template: str
    created_at: str = field(
        default_factory=lambda: datetime.now().isoformat(timespec="seconds")
    )
    notes: str = ""


@dataclass
class PromptRecord:
    """prompt 注册记录 (含全部版本)."""

    name: str
    description: str = ""
    target_model: str = ""
    variables: list[str] = field(default_factory=list)
    versions: dict[str, PromptVersion] = field(default_factory=dict)
    latest: int = 0

    def get_version(self, version: int | None = None) -> PromptVersion:
        """取指定版本, None 取最新."""
        v = version if version is not None else self.latest
        if v == 0 or str(v) not in self.versions:
            raise KeyError(f"prompt '{self.name}' 无版本 {v}")
        return self.versions[str(v)]

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["versions"] = {k: asdict(v) for k, v in self.versions.items()}
        return d


# ============================================================
# 注册中心
# ============================================================


class PromptRegistry:
    """Prompt 版本注册中心.

    Args:
        store_path: YAML 存储路径, 默认 config/prompts.yaml.
        auto_confirm: True 时跳过 Tier M/D 交互确认 (CI/测试用).
    """

    def __init__(
        self, store_path: Path | None = None, *, auto_confirm: bool = False
    ):
        self.store_path = Path(store_path) if store_path else _DEFAULT_STORE
        self.auto_confirm = auto_confirm
        self._records: dict[str, PromptRecord] = {}
        self._load()

    # ------------------------------------------------------------
    # 持久化
    # ------------------------------------------------------------

    def _load(self) -> None:
        if not self.store_path.exists():
            return
        if yaml is None:
            logger.warning("PyYAML 未安装, prompt 注册中心无法加载 %s", self.store_path)
            return
        try:
            data = yaml.safe_load(self.store_path.read_text(encoding="utf-8")) or {}
        except (OSError, yaml.YAMLError) as exc:
            logger.error("加载 prompts.yaml 失败: %s", exc)
            return
        for name, rec in (data.get("prompts") or {}).items():
            versions = {
                str(v): PromptVersion(
                    template=vd.get("template", ""),
                    created_at=vd.get("created_at", ""),
                    notes=vd.get("notes", ""),
                )
                for v, vd in (rec.get("versions") or {}).items()
            }
            self._records[name] = PromptRecord(
                name=name,
                description=rec.get("description", ""),
                target_model=rec.get("target_model", ""),
                variables=rec.get("variables", []),
                versions=versions,
                latest=int(rec.get("latest", 0)),
            )

    def _save(self) -> None:
        if yaml is None:
            logger.warning("PyYAML 未安装, 跳过持久化")
            return
        self.store_path.parent.mkdir(parents=True, exist_ok=True)
        data = {"prompts": {n: r.to_dict() for n, r in self._records.items()}}
        self.store_path.write_text(
            yaml.safe_dump(data, allow_unicode=True, sort_keys=False), encoding="utf-8"
        )

    # ------------------------------------------------------------
    # CRUD (带 Tier 分级)
    # ------------------------------------------------------------

    @tier_m(
        "注册 prompt", params_extractor=lambda self, name, **kw: {"name": name, **kw}
    )
    def register(
        self,
        name: str,
        template: str,
        *,
        variables: list[str] | None = None,
        target_model: str = "",
        description: str = "",
        notes: str = "",
        bump_version: bool = True,
    ) -> int:
        """注册新 prompt 或追加新版本 (Tier M).

        Returns:
            新版本号.
        """
        variables = variables or self._extract_variables(template)
        if name in self._records:
            rec = self._records[name]
            if not bump_version:
                # BUG FIX (2026-08-25): latest=0 或版本被清空时,
                # rec.versions[str(rec.latest)] 会 KeyError, 回退为新增版本。
                cur = rec.versions.get(str(rec.latest))
                if cur is not None:
                    cur.template = template
                    cur.notes = notes
                else:
                    rec.latest += 1
                    rec.versions[str(rec.latest)] = PromptVersion(
                        template=template, notes=notes
                    )
            else:
                rec.latest += 1
                rec.versions[str(rec.latest)] = PromptVersion(
                    template=template, notes=notes
                )
            rec.variables = variables or rec.variables
            rec.target_model = target_model or rec.target_model
            rec.description = description or rec.description
            new_v = rec.latest
        else:
            rec = PromptRecord(
                name=name,
                description=description,
                target_model=target_model,
                variables=variables,
                versions={"1": PromptVersion(template=template, notes=notes)},
                latest=1,
            )
            self._records[name] = rec
            new_v = 1
        self._save()
        logger.info("prompt '%s' 注册版本 %s", name, new_v)
        return new_v

    @tier_r("获取 prompt", params_extractor=lambda self, name, **kw: {"name": name})
    def get(self, name: str, version: int | None = None) -> PromptRecord:
        """获取 prompt 记录 (Tier R)."""
        if name not in self._records:
            raise KeyError(f"prompt '{name}' 不存在")
        return self._records[name]

    @tier_r("列出全部 prompt")
    def list(self) -> list[dict[str, Any]]:
        """列出全部 prompt 摘要 (Tier R)."""
        return [
            {
                "name": r.name,
                "description": r.description,
                "target_model": r.target_model,
                "variables": r.variables,
                "latest_version": r.latest,
                "version_count": len(r.versions),
            }
            for r in self._records.values()
        ]

    @tier_d("删除 prompt", params_extractor=lambda self, name: {"name": name})
    def delete(self, name: str) -> bool:
        """永久删除 prompt 及全部版本 (Tier D)."""
        if name in self._records:
            del self._records[name]
            self._save()
            logger.warning("prompt '%s' 已永久删除", name)
            return True
        return False

    # ------------------------------------------------------------
    # 模板组装
    # ------------------------------------------------------------

    @tier_r(
        "组装 prompt",
        params_extractor=lambda self, name, **kw: {"name": name, "vars": kw},
    )
    def assemble(
        self, name: str, version: int | None = None, **variables: Any
    ) -> str:
        """组装 prompt: 拉取模板 + 替换变量 (Tier R).

        缺失变量保留原 {{var}} 占位并告警, 不抛异常.
        """
        rec = self.get(name)
        ver = rec.get_version(version)
        template = ver.template
        declared = set(rec.variables)
        provided = set(variables.keys())
        missing = declared - provided
        extra = provided - declared
        if missing:
            logger.warning("prompt '%s' 缺失变量: %s (将保留占位符)", name, missing)
        if extra:
            logger.debug("prompt '%s' 额外变量: %s", name, extra)

        def _replace(match: re.Match) -> str:
            key = match.group(1)
            if key in variables:
                return str(variables[key])
            return match.group(0)

        return _VAR_PATTERN.sub(_replace, template)

    # ------------------------------------------------------------
    # 辅助
    # ------------------------------------------------------------

    @staticmethod
    def _extract_variables(template: str) -> list[str]:
        """从模板中提取 {{var}} 变量名."""
        return sorted({m.group(1) for m in _VAR_PATTERN.finditer(template)})

    def history(self, name: str) -> list[dict[str, Any]]:
        """查看 prompt 版本历史 (Tier R)."""
        rec = self.get(name)
        return [
            {
                "version": int(v),
                "created_at": vd.created_at,
                "notes": vd.notes,
                "template_preview": vd.template[:80],
            }
            for v, vd in sorted(rec.versions.items(), key=lambda x: int(x[0]))
        ]

    def migrate_from_string(
        self, name: str, template: str, *, target_model: str = ""
    ) -> int:
        """从硬编码字符串迁移到注册中心 (幂等, 同内容不新增版本)."""
        if name in self._records:
            rec = self._records[name]
            try:
                latest = rec.get_version()
                if latest.template == template:
                    return rec.latest
            except KeyError:
                pass
        return self.register(
            name, template, target_model=target_model, description="从硬编码迁移"
        )


__all__ = ["PromptRegistry", "PromptRecord", "PromptVersion"]
