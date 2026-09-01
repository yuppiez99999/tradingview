"""mlflow 本地 stub (Windows 离线训练用)。

qlib 的 workflow 模块在 import 时无条件 `import mlflow`，但云端 Docker 镜像
用 mlflow 2.x、Windows py311 装的是 3.x (API 不兼容, MlflowClient 已迁移)。
本地训练不需要真实 mlflow 实验记录 (模型直接 save_model 落盘), 故注入
no-op stub 让 qlib 正常初始化。云端 Docker 不会走此路径 (真实 mlflow 已装)。
"""
import sys
import types


class _NoOpClient:
    def __init__(self, *args, **kwargs):
        pass

    def get_experiment(self, *args, **kwargs):
        return None

    def get_experiment_by_name(self, *args, **kwargs):
        return None

    def create_experiment(self, *args, **kwargs):
        return "0"

    def get_run(self, *args, **kwargs):
        raise FileNotFoundError("mlflow stub: no runs")

    def search_runs(self, *args, **kwargs):
        return []

    def log_param(self, *args, **kwargs):
        pass

    def log_metric(self, *args, **kwargs):
        pass

    def log_artifact(self, *args, **kwargs):
        pass

    def set_tag(self, *args, **kwargs):
        pass


def _make_mlflow():
    ml = types.ModuleType("mlflow")
    tracking = types.ModuleType("mlflow.tracking")
    tracking.MlflowClient = _NoOpClient
    ml.tracking = tracking
    ml.__version__ = "2.19.0-stub"
    ml.set_experiment = lambda *a, **k: None
    ml.start_run = lambda *a, **k: _Ctx()
    ml.end_run = lambda *a, **k: None
    ml.log_param = lambda *a, **k: None
    ml.log_metric = lambda *a, **k: None
    ml.log_artifacts = lambda *a, **k: None
    ml.sklearn = types.ModuleType("mlflow.sklearn")
    # 子模块占位, 满足 qlib.workflow 的 from mlflow.xxx import yyy
    entities = types.ModuleType("mlflow.entities")
    entities.ViewType = type("ViewType", (), {"ALL": "ALL", "ACTIVE_ONLY": "ACTIVE_ONLY", "DELETED_ONLY": "DELETED_ONLY"})  # noqa: E501
    entities.RunInfo = object
    entities.Run = object
    entities.Experiment = object
    entities.RunStatus = type("RunStatus", (), {"FINISHED": "FINISHED", "FAILED": "FAILED", "RUNNING": "RUNNING"})
    ml.entities = entities
    artifacts = types.ModuleType("mlflow.artifacts")
    ml.artifacts = artifacts
    ml.utils = types.ModuleType("mlflow.utils")
    ml.store = types.ModuleType("mlflow.store")
    return ml


class _Ctx:
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def install():
    if "mlflow" not in sys.modules:
        _m = _make_mlflow()
        sys.modules["mlflow"] = _m
        sys.modules["mlflow.tracking"] = _m.tracking
        sys.modules["mlflow.sklearn"] = _m.sklearn
        sys.modules["mlflow.entities"] = _m.entities
        sys.modules["mlflow.artifacts"] = _m.artifacts
        sys.modules["mlflow.utils"] = _m.utils
        sys.modules["mlflow.store"] = _m.store


if __name__ == "__main__":
    install()
    import mlflow

    print("mlflow stub installed:", mlflow.__version__)
