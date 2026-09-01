"""本地 LLM 模型选型器 — llmfit 集成 (P0).

通过调用外部工具 llmfit (https://github.com/AlexsJones/llmfit) 检测本机硬件
并推荐适合本地运行的 LLM 模型, 用于 GLM-5 决策链的 Ollama 回退选型.

集成位置:
    - 量化策略系统_统一入口_v8.6.py --check 自检阶段
    - utils/glm5_client.py Ollama 模式默认模型 (替代硬编码 "glm-5")

设计原则:
    - 零硬依赖: llmfit 未安装时优雅降级, 返回 None
    - 子进程隔离: 通过 subprocess 调用, 超时保护 30s
    - 结构化输出: 返回 dataclass, 便于上层消费
    - 幂等写配置: 多次运行结果一致, 仅更新 settings.yaml 的 local_llm 段

llmfit 1.x 输出格式:
    - `llmfit doctor`        : Markdown 报告 (不支持 --json), 用正则解析硬件
    - `llmfit recommend --json`: JSON, 评分在 score_components 子对象里

集成日期: 2026-08-23
"""

from __future__ import annotations

import json
import logging
import re
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

logger = logging.getLogger(__name__)

# llmfit 子进程调用超时 (秒)
_LLMFIT_TIMEOUT = 30
# llmfit 推荐用例 (与量化决策场景匹配: 代码生成/分析)
_DEFAULT_USE_CASE = "coding"
# 推荐结果最多取前 N 个
_TOP_N = 3


# ============================================================
# 数据结构
# ============================================================


@dataclass
class HardwareSpec:
    """本机硬件规格 (llmfit doctor 输出解析)."""

    os: str = ""
    cpu: str = ""
    ram_gb: float = 0.0
    gpu: str = ""
    vram_gb: float = 0.0
    backend: str = ""

    def summary(self) -> str:
        parts = [f"OS={self.os or '未知'}", f"RAM={self.ram_gb:.1f}GB"]
        if self.cpu:
            parts.append(f"CPU={self.cpu}")
        if self.gpu:
            parts.append(f"GPU={self.gpu}")
        if self.vram_gb > 0:
            parts.append(f"VRAM={self.vram_gb:.1f}GB")
        if self.backend:
            parts.append(f"backend={self.backend}")
        return " | ".join(parts)


@dataclass
class ModelRecommendation:
    """单个模型推荐项 (对应 llmfit recommend --json 的一个 model 条目)."""

    name: str = ""
    provider: str = ""
    score: float = 0.0  # 总分 (item.score)
    fit_score: float = 0.0  # 适配分 (score_components.fit)
    speed_score: float = 0.0  # 速度分 (score_components.speed)
    quality_score: float = 0.0  # 质量分 (score_components.quality)
    context_length: int = 0  # 有效上下文长度 (effective_context_length)
    size_gb: float = 0.0  # 运行所需内存 (total_memory_gb)
    quantization: str = ""  # 量化方式 (best_quant)
    estimated_tps: float = 0.0  # 估计速度 tok/s (estimated_tps)
    fit_label: str = ""  # 适配标签 Good/Fair/Poor (fit_label)
    run_mode: str = ""  # 运行模式 GPU/CPU (run_mode)
    runtime: str = ""  # 推理后端 vLLM/Ollama (runtime)

    def summary(self) -> str:
        parts = [self.name]
        if self.score > 0:
            parts.append(f"score={self.score:.1f}")
        if self.fit_label:
            parts.append(self.fit_label)
        if self.estimated_tps > 0:
            parts.append(f"{self.estimated_tps:.0f}tok/s")
        if self.run_mode:
            parts.append(self.run_mode)
        return " | ".join(parts)


@dataclass
class SelectionResult:
    """选型结果."""

    available: bool = False
    reason: str = ""
    hardware: HardwareSpec = field(default_factory=HardwareSpec)
    recommendations: list[ModelRecommendation] = field(default_factory=list)
    selected: ModelRecommendation | None = None

    def summary(self) -> str:
        if not self.available:
            return f"llmfit 不可用: {self.reason}"
        lines = [f"llmfit 可用 | 硬件: {self.hardware.summary()}"]
        if self.selected:
            lines.append(f"推荐模型: {self.selected.summary()}")
        return "\n".join(lines)


# ============================================================
# llmfit 子进程封装
# ============================================================


def is_llmfit_available() -> bool:
    """检测 llmfit 是否在 PATH 中可用."""
    return shutil.which("llmfit") is not None


def _run_llmfit_raw(args: list[str]) -> str | None:
    """调用 llmfit 子进程, 返回原始 stdout 文本.

    Args:
        args: llmfit 命令参数 (不含 "llmfit" 本身)

    Returns:
        stdout 文本, 失败返回 None
    """
    if not is_llmfit_available():
        return None
    cmd = ["llmfit"] + args
    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=_LLMFIT_TIMEOUT,
            check=False,
            encoding="utf-8",
        )
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError) as e:
        logger.warning(f"llmfit 调用失败 ({cmd}): {e}")
        return None
    if proc.returncode != 0:
        logger.warning(f"llmfit 返回非零 ({proc.returncode}): {proc.stderr[:200]}")
        return None
    return proc.stdout


def _run_llmfit_json(args: list[str]) -> dict | None:
    """调用 llmfit 子进程并解析 JSON 输出.

    Args:
        args: llmfit 命令参数 (不含 "llmfit" 本身)

    Returns:
        解析后的 JSON dict, 失败返回 None
    """
    text = _run_llmfit_raw(args)
    if text is None:
        return None
    try:
        return json.loads(text)
    except json.JSONDecodeError as e:
        logger.warning(f"llmfit 输出 JSON 解析失败: {e}")
        return None


# ============================================================
# 核心选型接口
# ============================================================


def detect_hardware() -> HardwareSpec | None:
    """调用 llmfit doctor 解析本机硬件.

    llmfit doctor 不支持 --json, 输出 Markdown 报告, 用正则解析关键字段.
    格式示例::

        - OS: windows (x86_64)
        SystemSpecs {
            total_ram_gb: 15.78,
            cpu_name: "Intel(R) Core(TM) i7-10870H CPU @ 2.20GHz",
            gpu_name: Some("NVIDIA GeForce RTX 3060 Laptop GPU"),
            gpu_vram_gb: Some(6.0),
            backend: Cuda,
        }

    Returns:
        HardwareSpec, llmfit 不可用或调用失败时返回 None
    """
    text = _run_llmfit_raw(["doctor"])
    if not text:
        return None
    spec = HardwareSpec()
    # 正则解析 llmfit doctor 的 Markdown 输出
    if m := re.search(r"- OS:\s*(\S+)", text):
        spec.os = m.group(1)
    if m := re.search(r"total_ram_gb:\s*([\d.]+)", text):
        spec.ram_gb = float(m.group(1))
    if m := re.search(r'cpu_name:\s*"([^"]+)"', text):
        spec.cpu = m.group(1)
    if m := re.search(r'gpu_name:\s*Some\(\s*"([^"]+)"', text):
        spec.gpu = m.group(1)
    if m := re.search(r"gpu_vram_gb:\s*Some\(\s*([\d.]+)", text):
        spec.vram_gb = float(m.group(1))
    if m := re.search(r"backend:\s*(\w+)", text):
        spec.backend = m.group(1)
    return spec


def recommend_models(
    use_case: str = _DEFAULT_USE_CASE, top_n: int = _TOP_N
) -> list[ModelRecommendation]:
    """调用 llmfit recommend 获取模型推荐列表.

    Args:
        use_case: 用例 (coding/general/agent 等)
        top_n: 最多返回前 N 个

    Returns:
        推荐模型列表 (按 score 降序), 空列表表示无推荐
    """
    data = _run_llmfit_json(["recommend", "--use-case", use_case, "--json"])
    if not data:
        return []
    raw_models = data.get("models", data.get("recommendations", []))
    if not isinstance(raw_models, list):
        return []
    recs: list[ModelRecommendation] = []
    for item in raw_models[:top_n]:
        if not isinstance(item, dict):
            continue
        sc = item.get("score_components") or {}
        rec = ModelRecommendation(
            name=str(item.get("name", "")),
            provider=str(item.get("provider", "")),
            score=float(item.get("score", 0) or 0),
            fit_score=float(sc.get("fit", 0) or 0),
            speed_score=float(sc.get("speed", 0) or 0),
            quality_score=float(sc.get("quality", 0) or 0),
            context_length=int(
                item.get("effective_context_length", item.get("context_length", 0)) or 0
            ),
            size_gb=float(
                item.get("total_memory_gb", item.get("disk_size_gb", 0)) or 0
            ),
            quantization=str(item.get("best_quant", "")),
            estimated_tps=float(item.get("estimated_tps", 0) or 0),
            fit_label=str(item.get("fit_label", item.get("fit_level", ""))),
            run_mode=str(item.get("run_mode", "")),
            runtime=str(item.get("runtime", "")),
        )
        if rec.name:
            recs.append(rec)
    recs.sort(key=lambda r: r.score, reverse=True)
    return recs


def select_local_model(use_case: str = _DEFAULT_USE_CASE) -> SelectionResult:
    """一站式选型: 检测硬件 + 推荐模型 + 选最优.

    Args:
        use_case: 用例 (coding/general/agent)

    Returns:
        SelectionResult, available=False 时表示 llmfit 不可用或无推荐
    """
    result = SelectionResult()
    if not is_llmfit_available():
        result.reason = (
            "llmfit 未安装 (Windows: scoop install llmfit / uv tool install llmfit)"
        )
        return result
    hw = detect_hardware()
    if hw:
        result.hardware = hw
    recs = recommend_models(use_case=use_case)
    if not recs:
        result.reason = "llmfit 未返回推荐模型"
        return result
    result.available = True
    result.recommendations = recs
    result.selected = recs[0]
    return result


# ============================================================
# 配置持久化
# ============================================================


def persist_to_settings(
    result: SelectionResult,
    settings_path: str | None = None,
) -> bool:
    """将选型结果写入 settings.yaml 的 local_llm 段.

    幂等: 多次写入结果一致, 仅更新 local_llm 段, 不触碰其他配置.

    Args:
        result: select_local_model 的结果
        settings_path: settings.yaml 路径, 默认 config/settings.yaml

    Returns:
        True 表示写入成功
    """
    if not result.available or not result.selected:
        return False
    if settings_path is None:
        base = Path(__file__).resolve().parent.parent
        settings_path = str(base / "config" / "settings.yaml")
    try:
        import yaml
    except ImportError:
        logger.warning("PyYAML 未安装, 无法写入 settings.yaml")
        return False
    path = Path(settings_path)
    if not path.exists():
        logger.warning(f"settings.yaml 不存在: {path}")
        return False
    try:
        with path.open("r", encoding="utf-8") as f:
            cfg = yaml.safe_load(f) or {}
        sel = result.selected
        cfg["local_llm"] = {
            "selected_model": sel.name,
            "provider": sel.provider,
            "score": round(sel.score, 2),
            "fit_score": round(sel.fit_score, 2),
            "speed_score": round(sel.speed_score, 2),
            "quality_score": round(sel.quality_score, 2),
            "context_length": sel.context_length,
            "size_gb": round(sel.size_gb, 2),
            "quantization": sel.quantization,
            "estimated_tps": round(sel.estimated_tps, 1),
            "fit_label": sel.fit_label,
            "run_mode": sel.run_mode,
            "runtime": sel.runtime,
            "use_case": _DEFAULT_USE_CASE,
            "hardware": {
                "os": result.hardware.os,
                "ram_gb": round(result.hardware.ram_gb, 1),
                "cpu": result.hardware.cpu,
                "gpu": result.hardware.gpu,
                "vram_gb": round(result.hardware.vram_gb, 1),
                "backend": result.hardware.backend,
            },
            "updated_by": "utils/local_model_selector.py",
        }
        with path.open("w", encoding="utf-8") as f:
            yaml.safe_dump(cfg, f, allow_unicode=True, sort_keys=False)
        logger.info(f"已写入 local_llm 段到 {path}")
        return True
    except (OSError, Exception) as e:  # noqa: BLE001  # fail-safe
        logger.warning(f"写入 settings.yaml 失败: {e}")
        return False


# ============================================================
# 自检快捷接口
# ============================================================


def quick_check() -> dict:
    """供 run_quick_check 调用的简化检查接口.

    Returns:
        dict 字段:
            available: bool - llmfit 是否可用
            reason: str - 不可用原因
            hardware: str - 硬件摘要
            selected: str - 推荐模型摘要 (或空串)
            recommendations: list[str] - 推荐摘要列表
    """
    result = select_local_model()
    return {
        "available": result.available,
        "reason": result.reason,
        "hardware": result.hardware.summary() if result.available else "",
        "selected": result.selected.summary() if result.selected else "",
        "recommendations": [r.summary() for r in result.recommendations],
    }


if __name__ == "__main__":
    # 直接运行: 执行选型并打印结果
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    res = select_local_model()
    print(res.summary())
    if res.available:
        print("\n前 3 推荐:")
        for i, rec in enumerate(res.recommendations, 1):
            print(f"  {i}. {rec.summary()}")
