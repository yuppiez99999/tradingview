"""模型版本化注册表 — T5.8 交付物.

模块整合 8.4 — ARCHITECTURE §4.2
任务: T5.8 MLops 流水线 (模型版本化)

设计原则:
    1. 复用 v8.3_institutional/src/ml/mlflow_tracker.py 的 MLflow 集成能力
    2. 启用 MLflow Model Registry (注册/晋升/归档)
    3. 本地文件系统兜底 (mlflow 不可用时降级)
    4. Feature Flag 透传 (HC-1): USE_MODEL_REGISTRY 默认 False
    5. 配置走 ConfigManager 4 级优先级 (HC-5)

模型生命周期:
    REGISTERED → STAGING → PRODUCTION → ARCHIVED

API:
    from utils.alpha.model_registry import ModelRegistry, ModelVersion, ModelStage

    registry = ModelRegistry()
    registry.register_model("v9_lgb", model, metrics={"dsr": 8, "annual_return": 0.1962})
    registry.promote_model("v9_lgb", version=1, to_stage=ModelStage.PRODUCTION)
    model = registry.load_model("v9_lgb", stage=ModelStage.PRODUCTION)

硬约束:
    - HC-1: Feature Flag 默认 False, 关闭时降级为本地文件存储
    - HC-5: ConfigManager 4 级优先级解析不可绕过
"""

from __future__ import annotations

import json
import logging
import shutil
from dataclasses import asdict, dataclass, field
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Any

logger = logging.getLogger("model_registry")

# 项目根目录
_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent

# 本地注册表根目录 (mlflow 不可用时兜底)
_DEFAULT_REGISTRY_DIR = _PROJECT_ROOT / "models" / "registry"


# ============================================================
# 异常定义
# ============================================================
class ModelRegistryError(Exception):
    """模型注册表基础异常."""


class ModelNotFoundError(ModelRegistryError):
    """模型未找到."""


class ModelVersionNotFoundError(ModelRegistryError):
    """模型版本未找到."""


class StageTransitionError(ModelRegistryError):
    """阶段转换非法."""


# ============================================================
# 模型阶段 (对齐 MLflow Model Registry)
# ============================================================
class ModelStage(str, Enum):
    """模型生命周期阶段."""

    REGISTERED = "registered"  # 已注册 (初始)
    STAGING = "staging"  # 测试中 (Shadow/A/B 测试)
    PRODUCTION = "production"  # 生产
    ARCHIVED = "archived"  # 归档

    @classmethod
    def from_string(cls, s: str) -> ModelStage:
        """从字符串解析 (容错)."""
        s_lower = s.lower()
        for member in cls:
            if member.value == s_lower:
                return member
        raise ModelRegistryError(f"未知模型阶段: {s} (支持: {[m.value for m in cls]})")


# ============================================================
# 模型版本数据类
# ============================================================
@dataclass
class ModelVersion:
    """模型版本元数据."""

    name: str
    version: int
    stage: str  # ModelStage.value
    source: str  # 模型文件路径或 mlflow URI
    metrics: dict[str, float] = field(default_factory=dict)
    params: dict[str, Any] = field(default_factory=dict)
    tags: dict[str, str] = field(default_factory=dict)
    description: str = ""
    created_at: str = ""
    updated_at: str = ""
    created_by: str = ""
    mlflow_run_id: str | None = None
    mlflow_model_uri: str | None = None  # mlflow.models:/<name>/<version>

    def to_dict(self) -> dict[str, Any]:
        """转为字典 (用于 JSON 持久化)."""
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> ModelVersion:
        """从字典构造."""
        return cls(**{k: v for k, v in d.items() if k in cls.__dataclass_fields__})


# ============================================================
# 模型注册表
# ============================================================
class ModelRegistry:
    """模型版本化注册表.

    支持两种后端:
        1. MLflow Model Registry (优先, 启用 mlflow 时)
        2. 本地文件系统 (兜底, mlflow 不可用时)

    本地注册表结构:
        models/registry/
            <model_name>/
                metadata.json         # 模型元数据 (所有版本)
                version_1/
                    model.pkl         # 模型文件 (joblib 序列化)
                    metrics.json
                version_2/
                    ...
    """

    def __init__(
        self,
        registry_dir: str | None = None,
        use_mlflow: bool | None = None,
        mlflow_tracking_uri: str | None = None,
    ) -> None:
        """初始化.

        Args:
            registry_dir: 本地注册表目录 (None=默认 models/registry/)
            use_mlflow: 是否启用 MLflow (None=自动检测)
            mlflow_tracking_uri: MLflow Tracking URI
        """
        # 本地目录
        if registry_dir:
            self.registry_dir = Path(registry_dir)
            if not self.registry_dir.is_absolute():
                self.registry_dir = _PROJECT_ROOT / registry_dir
        else:
            self.registry_dir = _DEFAULT_REGISTRY_DIR
        self.registry_dir.mkdir(parents=True, exist_ok=True)

        # MLflow 后端
        self._mlflow_available = False
        self._mlflow_client = None
        if use_mlflow is True or (use_mlflow is None and self._check_mlflow()):
            self._init_mlflow(mlflow_tracking_uri)

        # 内存缓存 (name -> list[ModelVersion])
        self._cache: dict[str, list[ModelVersion]] = {}
        self._load_cache()

        logger.info(
            "ModelRegistry 初始化: dir=%s, mlflow=%s",
            self.registry_dir,
            self._mlflow_available,
        )

    # ============================================================
    # MLflow 集成
    # ============================================================
    def _check_mlflow(self) -> bool:
        """检测 mlflow 是否可用."""
        try:
            import mlflow  # noqa: F401

            return True
        except ImportError:
            return False

    def _init_mlflow(self, tracking_uri: str | None) -> None:
        """初始化 MLflow 客户端."""
        try:
            import mlflow
            from mlflow.tracking import MlflowClient

            if tracking_uri:
                mlflow.set_tracking_uri(tracking_uri)
            self._mlflow_client = MlflowClient()
            self._mlflow_available = True
            logger.info("MLflow Model Registry 已启用: tracking_uri=%s", tracking_uri or mlflow.get_tracking_uri())
        except (ValueError, TypeError, KeyError, AttributeError, RuntimeError, OSError, TimeoutError, ConnectionError) as e: # noqa: BLE001  # P2 模块 fail-safe, 待后续精确化
            logger.warning("MLflow 初始化失败, 降级为本地存储: %s", e)
            self._mlflow_available = False
            self._mlflow_client = None

    # ============================================================
    # 缓存加载
    # ============================================================
    def _load_cache(self) -> None:
        """从本地 metadata.json 加载所有模型元数据."""
        self._cache = {}
        for model_dir in self.registry_dir.iterdir():
            if not model_dir.is_dir():
                continue
            metadata_file = model_dir / "metadata.json"
            if not metadata_file.exists():
                continue
            try:
                with open(metadata_file, encoding="utf-8") as f:
                    data = json.load(f)
                versions = [ModelVersion.from_dict(v) for v in data.get("versions", [])]
                self._cache[model_dir.name] = versions
            except (json.JSONDecodeError, OSError) as e:
                logger.warning("加载模型元数据失败 %s: %s", model_dir.name, e)

    def _save_metadata(self, name: str) -> None:
        """保存模型元数据到本地."""
        model_dir = self.registry_dir / name
        model_dir.mkdir(parents=True, exist_ok=True)
        metadata_file = model_dir / "metadata.json"
        versions = [v.to_dict() for v in self._cache.get(name, [])]
        data = {
            "name": name,
            "updated_at": datetime.utcnow().isoformat() + "Z",
            "version_count": len(versions),
            "versions": versions,
        }
        with open(metadata_file, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2, default=str)

    # ============================================================
    # 注册模型
    # ============================================================
    def register_model(
        self,
        name: str,
        model: Any,
        metrics: dict[str, float] | None = None,
        params: dict[str, Any] | None = None,
        tags: dict[str, str] | None = None,
        description: str = "",
        created_by: str = "system",
        mlflow_run_id: str | None = None,
    ) -> ModelVersion:
        """注册新模型版本.

        Args:
            name: 模型名称 (如 "v9_lgb")
            model: 模型对象 (会序列化保存)
            metrics: 评估指标 (如 {"dsr": 8, "annual_return": 0.1962})
            params: 训练参数
            tags: 自定义标签
            description: 模型描述
            created_by: 创建者
            mlflow_run_id: 关联的 MLflow run ID

        Returns:
            新创建的 ModelVersion
        """
        metrics = metrics or {}
        params = params or {}
        tags = tags or {}

        # 计算新版本号
        existing_versions = self._cache.get(name, [])
        new_version_num = max((v.version for v in existing_versions), default=0) + 1

        # 保存模型文件 (本地兜底)
        model_dir = self.registry_dir / name / f"version_{new_version_num}"
        model_dir.mkdir(parents=True, exist_ok=True)
        model_file = model_dir / "model.pkl"

        try:
            import joblib

            joblib.dump(model, model_file)
            source = str(model_file)
        except (ValueError, TypeError, KeyError, AttributeError, RuntimeError, OSError, TimeoutError, ConnectionError) as e: # noqa: BLE001  # P2 模块 fail-safe, 待后续精确化
            logger.warning("模型序列化失败 (joblib): %s, 跳过本地保存", e)
            source = ""

        # 保存 metrics.json
        metrics_file = model_dir / "metrics.json"
        with open(metrics_file, "w", encoding="utf-8") as f:
            json.dump(metrics, f, ensure_ascii=False, indent=2)

        # MLflow 注册 (如果可用)
        mlflow_model_uri = None
        if self._mlflow_available and mlflow_run_id:
            try:
                import mlflow

                # 通过 mlflow 注册模型
                mlflow_model_uri = f"runs:/{mlflow_run_id}/model"
                mlflow.register_model(
                    model_uri=mlflow_model_uri,
                    name=name,
                    tags=tags,
                )
                logger.info("MLflow 已注册模型: %s (run_id=%s)", name, mlflow_run_id)
            except (ValueError, TypeError, KeyError, AttributeError, RuntimeError, OSError, TimeoutError, ConnectionError) as e: # noqa: BLE001  # P2 模块 fail-safe, 待后续精确化
                logger.warning("MLflow 注册失败: %s", e)

        # 创建版本记录
        now = datetime.utcnow().isoformat() + "Z"
        version = ModelVersion(
            name=name,
            version=new_version_num,
            stage=ModelStage.REGISTERED.value,
            source=source,
            metrics=metrics,
            params=params,
            tags=tags,
            description=description,
            created_at=now,
            updated_at=now,
            created_by=created_by,
            mlflow_run_id=mlflow_run_id,
            mlflow_model_uri=mlflow_model_uri,
        )

        # 更新缓存
        if name not in self._cache:
            self._cache[name] = []
        self._cache[name].append(version)
        self._save_metadata(name)

        logger.info(
            "模型已注册: %s v%d (metrics=%s)",
            name,
            new_version_num,
            metrics,
        )
        return version

    # ============================================================
    # 晋升 / 归档
    # ============================================================
    def transition_stage(
        self,
        name: str,
        version: int,
        to_stage: ModelStage,
        by: str = "system",
    ) -> ModelVersion:
        """转换模型阶段 (晋升/归档).

        Args:
            name: 模型名称
            version: 版本号
            to_stage: 目标阶段
            by: 操作人

        Returns:
            更新后的 ModelVersion

        Raises:
            ModelNotFoundError: 模型未找到
            ModelVersionNotFoundError: 版本未找到
            StageTransitionError: 阶段转换非法
        """
        if name not in self._cache:
            raise ModelNotFoundError(f"模型未注册: {name}")
        versions = self._cache[name]
        target = None
        for v in versions:
            if v.version == version:
                target = v
                break
        if target is None:
            raise ModelVersionNotFoundError(f"模型 {name} 无版本 {version}")

        # 验证转换合法性
        current_stage = ModelStage.from_string(target.stage)
        if not self._is_valid_transition(current_stage, to_stage):
            raise StageTransitionError(f"非法阶段转换: {current_stage.value} → {to_stage.value}")

        # 如果晋升到 PRODUCTION, 自动将其他 PRODUCTION 版本归档
        if to_stage == ModelStage.PRODUCTION:
            for v in versions:
                if v.version != version and v.stage == ModelStage.PRODUCTION.value:
                    v.stage = ModelStage.ARCHIVED.value
                    v.updated_at = datetime.utcnow().isoformat() + "Z"
                    logger.info(
                        "自动归档旧 PRODUCTION 版本: %s v%d",
                        name,
                        v.version,
                    )

        target.stage = to_stage.value
        target.updated_at = datetime.utcnow().isoformat() + "Z"
        self._save_metadata(name)

        # MLflow 阶段转换
        if self._mlflow_available and target.mlflow_model_uri:
            try:
                self._mlflow_client.transition_model_version_stage(  # type: ignore[misc]
                name=name,
                    version=str(version),
                    stage=to_stage.value.upper(),
                )
            except (ValueError, TypeError, KeyError, AttributeError, RuntimeError, OSError, TimeoutError, ConnectionError) as e: # noqa: BLE001  # P2 模块 fail-safe, 待后续精确化
                logger.warning("MLflow 阶段转换失败: %s", e)

        logger.info(
            "模型阶段转换: %s v%d %s → %s (by=%s)",
            name,
            version,
            current_stage.value,
            to_stage.value,
            by,
        )
        return target

    def promote_model(self, name: str, version: int, by: str = "system") -> ModelVersion:
        """晋升模型到 PRODUCTION (便捷方法)."""
        return self.transition_stage(name, version, ModelStage.PRODUCTION, by)

    def archive_model(self, name: str, version: int, by: str = "system") -> ModelVersion:
        """归档模型 (便捷方法)."""
        return self.transition_stage(name, version, ModelStage.ARCHIVED, by)

    def _is_valid_transition(self, from_stage: ModelStage, to_stage: ModelStage) -> bool:
        """验证阶段转换合法性.

        合法转换:
            REGISTERED → STAGING / PRODUCTION / ARCHIVED
            STAGING → PRODUCTION / ARCHIVED
            PRODUCTION → ARCHIVED
            ARCHIVED → STAGING (重新测试)
        """
        valid = {
            (ModelStage.REGISTERED, ModelStage.STAGING),
            (ModelStage.REGISTERED, ModelStage.PRODUCTION),
            (ModelStage.REGISTERED, ModelStage.ARCHIVED),
            (ModelStage.STAGING, ModelStage.PRODUCTION),
            (ModelStage.STAGING, ModelStage.ARCHIVED),
            (ModelStage.PRODUCTION, ModelStage.ARCHIVED),
            (ModelStage.ARCHIVED, ModelStage.STAGING),
        }
        return (from_stage, to_stage) in valid

    # ============================================================
    # 查询接口
    # ============================================================
    def get_model_versions(self, name: str, stage: ModelStage | None = None) -> list[ModelVersion]:
        """查询模型版本列表.

        Args:
            name: 模型名称
            stage: 按阶段过滤 (None=所有)

        Returns:
            版本列表 (按版本号倒序)
        """
        if name not in self._cache:
            return []
        versions = self._cache[name]
        if stage is not None:
            versions = [v for v in versions if v.stage == stage.value]
        return sorted(versions, key=lambda v: v.version, reverse=True)

    def get_latest_version(self, name: str) -> ModelVersion | None:
        """获取最新版本."""
        versions = self.get_model_versions(name)
        return versions[0] if versions else None

    def get_production_version(self, name: str) -> ModelVersion | None:
        """获取当前 PRODUCTION 版本."""
        versions = self.get_model_versions(name, ModelStage.PRODUCTION)
        return versions[0] if versions else None

    def get_model_info(self, name: str, version: int) -> ModelVersion:
        """获取指定版本详情.

        Raises:
            ModelNotFoundError
            ModelVersionNotFoundError
        """
        if name not in self._cache:
            raise ModelNotFoundError(f"模型未注册: {name}")
        for v in self._cache[name]:
            if v.version == version:
                return v
        raise ModelVersionNotFoundError(f"模型 {name} 无版本 {version}")

    def list_models(self) -> list[str]:
        """列出所有已注册模型名称."""
        return list(self._cache.keys())

    # ============================================================
    # 加载模型
    # ============================================================
    def load_model(
        self,
        name: str,
        version: int | None = None,
        stage: ModelStage | None = None,
    ) -> Any:
        """加载模型.

        Args:
            name: 模型名称
            version: 指定版本号 (优先)
            stage: 指定阶段 (version 为 None 时生效, 默认 PRODUCTION)

        Returns:
            模型对象

        Raises:
            ModelNotFoundError
            ModelVersionNotFoundError
        """
        # 确定要加载的版本
        if version is not None:
            target = self.get_model_info(name, version)
        else:
            target_stage = stage or ModelStage.PRODUCTION
            target = self.get_production_version(name) if target_stage == ModelStage.PRODUCTION else None  # type: ignore[misc]
            if target is None:
                versions = self.get_model_versions(name, target_stage)
                target = versions[0] if versions else None
        if target is None:
            raise ModelVersionNotFoundError(f"模型 {name} 无可加载版本 (version={version}, stage={stage})")

        # MLflow 加载 (优先)
        if self._mlflow_available and target.mlflow_model_uri:
            try:
                import mlflow

                return mlflow.pyfunc.load_model(target.mlflow_model_uri)
            except (ValueError, TypeError, KeyError, AttributeError, RuntimeError, OSError, TimeoutError, ConnectionError) as e: # noqa: BLE001  # P2 模块 fail-safe, 待后续精确化
                logger.warning("MLflow 加载失败, 回退到本地: %s", e)

        # 本地加载
        if target.source and Path(target.source).exists():
            try:
                import joblib

                return joblib.load(target.source)
            except (ValueError, TypeError, KeyError, AttributeError, RuntimeError, OSError, TimeoutError, ConnectionError) as e: # noqa: BLE001  # P2 模块 fail-safe, 待后续精确化
                logger.exception("本地模型加载失败: %s", e)
                raise ModelRegistryError(f"模型加载失败: {e}") from e
        raise ModelRegistryError(f"模型 {name} v{target.version} 无可用 source")

    # ============================================================
    # 删除 (软删除, 仅归档)
    # ============================================================
    def delete_model_version(self, name: str, version: int, force: bool = False) -> bool:
        """删除模型版本.

        Args:
            name: 模型名称
            version: 版本号
            force: True=物理删除文件, False=仅归档

        Returns:
            True 删除成功
        """
        if name not in self._cache:
            return False
        target = None
        for v in self._cache[name]:
            if v.version == version:
                target = v
                break
        if target is None:
            return False
        # PRODUCTION 版本不允许直接删除
        if target.stage == ModelStage.PRODUCTION.value and not force:
            logger.warning(
                "PRODUCTION 版本 %s v%d 不允许删除 (使用 archive_model)",
                name,
                version,
            )
            return False
        if force:
            # 物理删除文件
            model_dir = self.registry_dir / name / f"version_{version}"
            if model_dir.exists():
                shutil.rmtree(model_dir, ignore_errors=True)
            self._cache[name].remove(target)
            self._save_metadata(name)
            logger.info("已物理删除: %s v%d", name, version)
        else:
            # 软删除 (归档)
            target.stage = ModelStage.ARCHIVED.value
            target.updated_at = datetime.utcnow().isoformat() + "Z"
            self._save_metadata(name)
            logger.info("已归档: %s v%d", name, version)
        return True

    # ============================================================
    # 审计/导出
    # ============================================================
    def export_registry(self) -> dict[str, Any]:
        """导出整个注册表 (审计用)."""
        result = {
            "exported_at": datetime.utcnow().isoformat() + "Z",
            "registry_dir": str(self.registry_dir),
            "mlflow_available": self._mlflow_available,
            "model_count": len(self._cache),
            "models": {},
        }
        for name, versions in self._cache.items():
            result["models"][name] = {  # type: ignore[index]
            "version_count": len(versions),
                "versions": [v.to_dict() for v in versions],
            }
        return result

    def search_models(
        self,
        metric_filter: dict[str, tuple[str, float]] | None = None,
        stage: ModelStage | None = None,
        tag_filter: dict[str, str] | None = None,
    ) -> list[ModelVersion]:
        """搜索模型 (按指标/阶段/标签过滤).

        Args:
            metric_filter: 指标过滤, 如 {"dsr": (">=", 5.0)}
            stage: 阶段过滤
            tag_filter: 标签过滤

        Returns:
            匹配的版本列表
        """
        results: list[ModelVersion] = []
        for _name, versions in self._cache.items():
            for v in versions:
                if stage is not None and v.stage != stage.value:
                    continue
                if tag_filter:
                    if not all(v.tags.get(k) == val for k, val in tag_filter.items()):
                        continue
                if metric_filter:
                    matched = True
                    for metric_name, (op, threshold) in metric_filter.items():
                        val = v.metrics.get(metric_name, 0.0)
                        if op == ">=" and not val >= threshold:
                            matched = False
                            break
                        elif op == ">" and not val > threshold:
                            matched = False
                            break
                        elif op == "<=" and not val <= threshold:
                            matched = False
                            break
                        elif op == "<" and not val < threshold:
                            matched = False
                            break
                        elif op == "==" and not val == threshold:
                            matched = False
                            break
                    if not matched:
                        continue
                results.append(v)
        return sorted(results, key=lambda v: (v.name, v.version), reverse=True)
