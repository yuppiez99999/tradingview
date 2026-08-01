"""
MLflow 实验追踪集成 v1.0

将 Optuna 训练、Stacking 预测、模型评估等全流程接入 MLflow，
支持参数记录、指标追踪、模型注册和实验对比。

用法:
    tracker = MLflowTracker(experiment_name="quant_v5.7")
    tracker.start_run(run_name="xgboost_optuna")
    tracker.log_params(best_params)
    tracker.log_metrics({'accuracy': 0.78, 'f1': 0.72})
    tracker.log_model(model, "xgboost_classifier")
    tracker.end_run()

CLI:
    python utils/mlflow_tracker.py --list-experiments
    python utils/mlflow_tracker.py --compare --experiment quant_v5.7
"""

import os
import json
import time
import logging
from datetime import datetime
from typing import Dict, List, Optional, Any
from contextlib import contextmanager

logger = logging.getLogger(__name__)

_MLFLOW_AVAILABLE = False
_MLFLOW_IMPORT_ERROR = None


def _lazy_import_mlflow():
    """延迟导入 mlflow，避免模块加载时阻塞"""
    global _MLFLOW_AVAILABLE, _MLFLOW_IMPORT_ERROR
    if _MLFLOW_IMPORT_ERROR is not None:
        raise _MLFLOW_IMPORT_ERROR

    if not _MLFLOW_AVAILABLE:
        try:
            import mlflow
            import mlflow.sklearn
            import mlflow.xgboost
            import mlflow.lightgbm
            from mlflow.tracking import MlflowClient

            _MLFLOW_AVAILABLE = True
            return mlflow, mlflow.sklearn, mlflow.xgboost, mlflow.lightgbm, MlflowClient
        except ImportError as e:
            _MLFLOW_IMPORT_ERROR = e
            logger.warning("mlflow 未安装。安装: pip install mlflow")
            raise
    else:
        import mlflow
        import mlflow.sklearn
        import mlflow.xgboost
        import mlflow.lightgbm
        from mlflow.tracking import MlflowClient

        return mlflow, mlflow.sklearn, mlflow.xgboost, mlflow.lightgbm, MlflowClient


class MLflowTracker:
    """MLflow 实验追踪器

    封装 MLflow API，提供统一的训练/预测/评估追踪接口。
    当 MLflow 不可用时自动降级为本地 JSON 日志。
    """

    def __init__(
        self,
        experiment_name: str = "quant_v5.7",
        tracking_uri: Optional[str] = None,
        artifact_location: Optional[str] = None,
    ):
        """
        Args:
            experiment_name: 实验名称
            tracking_uri: MLflow Tracking Server URI (默认 mlruns/ 本地目录)
            artifact_location: 制品存储路径
        """
        self.experiment_name = experiment_name
        self._available = False
        self._active = False
        self._fallback_log: List[Dict] = []
        self._start_time: Optional[float] = None
        self._run_name: str = ""

        try:
            mlflow, _, _, _, _ = _lazy_import_mlflow()
            self._available = True
            if tracking_uri:
                mlflow.set_tracking_uri(tracking_uri)
            self._experiment_id = self._get_or_create_experiment(experiment_name, artifact_location)
            self._active = True
            logger.info(f"[MLflow] 已连接实验: {experiment_name} (id={self._experiment_id})")
        except ImportError:
            logger.info("[MLflow] mlflow 库不可用，使用本地 JSON 日志")
        except Exception as e:
            logger.warning(f"[MLflow] 连接失败，回退到本地日志: {e}")
            self._available = False

    def _get_or_create_experiment(self, name: str, artifact_loc: Optional[str] = None) -> str:
        """获取或创建实验"""
        try:
            _, _, _, _, MlflowClient = _lazy_import_mlflow()
            mlflow, _, _, _, _ = _lazy_import_mlflow()
            client = MlflowClient()
            experiment = client.get_experiment_by_name(name)
            if experiment:
                return experiment.experiment_id
            if artifact_loc:
                return client.create_experiment(name, artifact_location=artifact_loc)
            return client.create_experiment(name)
        except Exception:
            mlflow, _, _, _, _ = _lazy_import_mlflow()
            if artifact_loc:
                return mlflow.create_experiment(name, artifact_location=artifact_loc)
            return mlflow.create_experiment(name)

    @property
    def available(self) -> bool:
        return self._active and self._available

    # ── 运行生命周期 ──────────────────────────────────────

    @contextmanager
    def run_context(self, run_name: Optional[str] = None, tags: Optional[Dict] = None):
        """上下文管理器：自动开始/结束 MLflow run"""
        self.start_run(run_name=run_name, tags=tags)
        try:
            yield
        finally:
            self.end_run()

    def start_run(self, run_name: Optional[str] = None, tags: Optional[Dict] = None) -> bool:
        """开始一次 MLflow 运行"""
        self._run_name = run_name or f"run_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
        self._start_time = time.time()

        if self._active:
            try:
                mlflow, _, _, _, _ = _lazy_import_mlflow()
                mlflow.start_run(run_name=self._run_name)
                if tags:
                    mlflow.set_tags(tags)
                mlflow.set_tag("start_time", datetime.now().isoformat())
                return True
            except Exception as e:
                logger.warning(f"[MLflow] start_run 失败: {e}")

        # 回退模式
        self._fallback_log = []
        return False

    def end_run(self, status: str = "FINISHED") -> None:
        """结束当前运行"""
        duration = time.time() - self._start_time if self._start_time else 0

        if self._active:
            try:
                mlflow, _, _, _, _ = _lazy_import_mlflow()
                mlflow.set_tag("duration_sec", f"{duration:.1f}")
                mlflow.set_tag("status", status)
                mlflow.end_run()
            except Exception as e:
                logger.warning(f"[MLflow] end_run 失败: {e}")
        else:
            self._save_fallback_log(duration, status)

        self._start_time = None

    # ── 记录接口 ──────────────────────────────────────────

    def log_params(self, params: Dict[str, Any]) -> None:
        """记录超参数"""
        if self._active:
            try:
                mlflow, _, _, _, _ = _lazy_import_mlflow()
                for k, v in params.items():
                    safe_val = str(v)[:500] if not isinstance(v, (int, float, bool)) else v
                    mlflow.log_param(k, safe_val)
            except Exception as e:
                logger.warning(f"[MLflow] log_params 失败: {e}")
        else:
            self._fallback_log.append(("params", params))

    def log_metrics(self, metrics: Dict[str, float], step: Optional[int] = None) -> None:
        """记录指标"""
        if self._active:
            try:
                mlflow, _, _, _, _ = _lazy_import_mlflow()
                mlflow.log_metrics(metrics, step=step)
            except Exception as e:
                logger.warning(f"[MLflow] log_metrics 失败: {e}")
        else:
            self._fallback_log.append(("metrics", metrics))

    def log_model(
        self, model: Any, artifact_path: str = "model", model_type: str = "sklearn", input_example: Any = None
    ) -> None:
        """记录模型制品"""
        if self._active:
            try:
                mlflow, mlflow_sklearn, mlflow_xgboost, mlflow_lgbm, _ = _lazy_import_mlflow()
                mlflow_modules = {"sklearn": mlflow_sklearn, "xgboost": mlflow_xgboost, "lightgbm": mlflow_lgbm}
                log_fn = mlflow_modules.get(model_type) or getattr(mlflow, model_type, None)
                if log_fn and hasattr(log_fn, "log_model"):
                    log_fn.log_model(model, artifact_path, input_example=input_example)
                else:
                    mlflow_sklearn.log_model(model, artifact_path, input_example=input_example)
                logger.info(f"[MLflow] 模型已保存: {artifact_path}")
            except Exception as e:
                logger.warning(f"[MLflow] log_model 失败: {e}")
        else:
            self._fallback_log.append(
                (
                    "model",
                    {
                        "type": type(model).__name__,
                        "path": artifact_path,
                    },
                )
            )

    def log_artifact(self, local_path: str) -> None:
        """记录任意文件制品"""
        if self._active:
            try:
                mlflow, _, _, _, _ = _lazy_import_mlflow()
                mlflow.log_artifact(local_path)
            except Exception as e:
                logger.warning(f"[MLflow] log_artifact 失败: {e}")

    def log_dict(self, data: Dict, filename: str) -> None:
        """记录字典数据为 JSON 制品"""
        if self._active:
            try:
                mlflow, _, _, _, _ = _lazy_import_mlflow()
                mlflow.log_dict(data, filename)
            except Exception as e:
                logger.warning(f"[MLflow] log_dict 失败: {e}")

    def get_run_id(self) -> Optional[str]:
        """获取当前运行的 ID"""
        if self._active:
            try:
                mlflow, _, _, _, _ = _lazy_import_mlflow()
                run = mlflow.active_run()
                if run:
                    return run.info.run_id
            except Exception:
                pass
        return None

    # ── 训练流程集成 ──────────────────────────────────────

    def log_training_summary(
        self, results: Dict[str, Dict], optuna_params: Optional[Dict] = None, data_info: Optional[Dict] = None
    ) -> None:
        """记录完整的训练摘要

        Args:
            results: {model_name: {accuracy, f1, auc, ...}, ...}
            optuna_params: Optuna 最佳参数
            data_info: 数据集信息 {n_samples, n_features, date_range, ...}
        """
        # 找到最佳模型
        best_model = None
        best_f1 = 0
        best_metrics = {}

        for name, metrics in results.items():
            f1 = metrics.get("train_f1", metrics.get("f1", 0))
            self.log_metrics(
                {
                    f"{name}_f1": f1,
                    f"{name}_accuracy": metrics.get("train_accuracy", metrics.get("accuracy", 0)),
                    f"{name}_auc": metrics.get("train_auc", metrics.get("auc", 0)),
                }
            )
            if f1 > best_f1:
                best_f1 = f1
                best_model = name
                best_metrics = metrics

        # 记录最佳模型标签
        if best_model:
            self.log_params({"best_model": best_model})
            self.log_metrics(
                {
                    "best_f1": best_f1,
                    "best_accuracy": best_metrics.get("accuracy", 0),
                    "best_auc": best_metrics.get("auc", 0),
                }
            )

        if optuna_params:
            self.log_params(optuna_params)

        if data_info:
            self.log_params(
                {
                    "n_samples": data_info.get("n_samples", 0),
                    "n_features": data_info.get("n_features", 0),
                    "label_method": data_info.get("label_method", "unknown"),
                }
            )

    def log_signal_accuracy(self, signals: List[Dict], actual_returns: Dict[str, float]) -> None:
        """记录信号准确率追踪

        Args:
            signals: [{code, probability, action, ...}, ...]
            actual_returns: {code: actual_return, ...}
        """
        correct = 0
        total = 0
        for sig in signals:
            code = sig.get("code", "")
            prob = sig.get("probability", 0.5)
            action = "BUY" if prob >= 0.55 else "SELL" if prob <= 0.45 else "HOLD"
            actual = actual_returns.get(code)
            if actual is not None and action != "HOLD":
                total += 1
                if (action == "BUY" and actual > 0) or (action == "SELL" and actual < 0):
                    correct += 1

        if total > 0:
            self.log_metrics({"signal_accuracy": correct / total, "signal_count": total})

    # ── 回退模式 ──────────────────────────────────────────

    def _save_fallback_log(self, duration: float, status: str) -> None:
        """将回退日志保存到本地 JSON 文件"""
        if not self._fallback_log:
            return

        log_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "mlflow_fallback")
        os.makedirs(log_dir, exist_ok=True)

        log_entry = {
            "run_name": self._run_name,
            "timestamp": datetime.now().isoformat(),
            "duration_sec": duration,
            "status": status,
            "params": {},
            "metrics": {},
            "models": [],
        }

        for entry_type, data in self._fallback_log:
            if entry_type == "params":
                log_entry["params"].update(data)
            elif entry_type == "metrics":
                log_entry["metrics"].update(data)
            elif entry_type == "model":
                log_entry["models"].append(data)

        filename = f"{self._run_name}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
        filepath = os.path.join(log_dir, filename)
        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(log_entry, f, ensure_ascii=False, indent=2)

        logger.info(f"[MLflow 回退] 日志已保存: {filepath}")

    # ── 查询接口 ──────────────────────────────────────────

    def list_runs(self, max_results: int = 20) -> List[Dict]:
        """列出最近运行的实验记录"""
        if not self._active:
            return [
                {
                    "run_name": r.get("run_name", "?"),
                    "metrics": r.get("metrics", {}),
                    "timestamp": r.get("timestamp", ""),
                }
                for r in self._fallback_log
                if isinstance(r, dict)
            ]

        try:
            _, _, _, _, MlflowClient = _lazy_import_mlflow()
            client = MlflowClient()
            runs = client.search_runs(
                experiment_ids=[self._experiment_id],
                max_results=max_results,
                order_by=["start_time DESC"],
            )
            return [
                {
                    "run_id": r.info.run_id,
                    "run_name": r.info.run_name or r.data.tags.get("mlflow.runName", "?"),
                    "status": r.info.status,
                    "metrics": r.data.metrics,
                    "params": r.data.params,
                }
                for r in runs
            ]
        except Exception as e:
            logger.warning(f"[MLflow] list_runs 失败: {e}")
            return []

    def get_best_run(self, metric: str = "best_f1") -> Optional[Dict]:
        """获取指定指标最优的运行"""
        runs = self.list_runs(max_results=50)
        best = None
        best_val = float("-inf")
        for r in runs:
            val = r.get("metrics", {}).get(metric, float("-inf"))
            if val > best_val:
                best_val = val
                best = r
        return best


# ── 便捷工厂函数 ──────────────────────────────────────


def create_tracker(experiment_name: str = "quant_v5.7", tracking_uri: Optional[str] = None) -> MLflowTracker:
    """创建 MLflowTracker 实例"""
    tracker = MLflowTracker(
        experiment_name=experiment_name,
        tracking_uri=tracking_uri,
    )
    status = "MLflow" if tracker.available else "JSON 回退"
    logger.info(f"[MLflowTracker] 初始化完成，追踪模式: {status}")
    return tracker


# ── CLI 入口 ──────────────────────────────────────────

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="MLflow 实验管理")
    parser.add_argument("--list-runs", action="store_true", help="列出所有运行")
    parser.add_argument("--best", action="store_true", help="显示最佳运行")
    parser.add_argument("--experiment", default="quant_v5.7", help="实验名称")

    args = parser.parse_args()
    tracker = MLflowTracker(experiment_name=args.experiment)

    if args.list_runs or args.best:
        if args.best:
            best = tracker.get_best_run()
            if best:
                print(f"最佳运行: {best.get('run_name')}")
                print(f"  指标: {best.get('metrics', {})}")
            else:
                print("无运行记录")
        else:
            runs = tracker.list_runs()
            print(f"实验 '{args.experiment}' 共 {len(runs)} 次运行:")
            for r in runs:
                name = r.get("run_name", "?")
                metrics = {k: f"{v:.4f}" for k, v in r.get("metrics", {}).items()}
                print(f"  - {name}: {metrics}")
    else:
        # 快速检查
        print(f"MLflow 可用: {tracker.available}")
        runs = tracker.list_runs(max_results=5)
        print(f"最近运行: {len(runs)} 条记录")
