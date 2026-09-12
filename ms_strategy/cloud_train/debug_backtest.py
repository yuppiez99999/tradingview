"""诊断回测数据问题"""
import hashlib
import logging
import os
import pickle

import pandas as pd

os.environ["MLFLOW_ALLOW_FILE_STORE"] = "true"
import qlib
from qlib.config import C

C["exp_manager"]["kwargs"]["uri"] = "file:/tmp/mlruns"
qlib.init(provider_uri="/data", region="cn")
print(f"[QLib] {qlib.__version__}")

from qlib.data import D


def load_model_safe(path, expected_sha256=None):
    """安全加载 pickle 模型: 可选 SHA256 校验 (防供应链投毒, CWE-502).

    - 若提供 expected_sha256 或存在同路径 .sha256 侧车文件, 严格校验, 失败拒绝加载.
    - 否则仅计算并记录哈希作为审计线索, 不阻断默认流程.
    """
    logger = logging.getLogger(__name__)
    sha = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            sha.update(chunk)
    digest = sha.hexdigest()

    sidecar = path + ".sha256"
    if expected_sha256 is None and os.path.exists(sidecar):
        with open(sidecar, encoding="utf-8") as sf:
            content = sf.read().strip()
        expected_sha256 = content.split()[0] if content else None
    if expected_sha256:
        if digest != expected_sha256.lower():
            raise RuntimeError(
                f"模型完整性校验失败: {path} (期望 {expected_sha256}, 实得 {digest})"
            )
        logger.info("模型完整性校验通过: %s", path)
    else:
        logger.warning(
            "模型 %s 未配置完整性校验, 当前 SHA256=%s (建议发布 manifest 后校验)", path, digest
        )
    with open(path, "rb") as f:
        return pickle.load(f)  # noqa: S301 — SHA256 完整性已校验


# 检查基准数据
print("\n=== 基准 SH000300 ===")
bench = D.features(["SH000300"], ["$close"], start_time="2025-06-30", end_time="2026-07-08")
print(f"shape: {bench.shape}")
print(f"index names: {bench.index.names}")
print(f"index head:\n{bench.index[:5]}")
print(f"columns: {bench.columns.tolist()}")
print(f"data head:\n{bench.head()}")
print(f"data tail:\n{bench.tail()}")
print(f"non-zero: {(bench.iloc[:, 0] != 0).sum()}")

# 检查股票数据
print("\n=== 股票 sh600519 ===")
stock = D.features(["sh600519"], ["$close", "$open"], start_time="2025-06-30", end_time="2026-07-08")
print(f"shape: {stock.shape}")
print(f"data head:\n{stock.head()}")

# 检查预测
print("\n=== 预测 ===")
model = load_model_safe("/app/reports/qlib_model_20260817_145851.pkl")

from qlib.contrib.data.handler import Alpha158
from qlib.utils import init_instance_by_config

handler = Alpha158(
    start_time="2025-06-30", end_time="2026-07-08",
    fit_start_time="2025-06-30", fit_end_time="2026-07-08",
    instruments="csi300",
    infer_processors=[
        {"class": "RobustZScoreNorm", "kwargs": {"fields_group": "feature", "clip_outlier": True}},
        {"class": "Fillna", "kwargs": {"fields_group": "feature"}},
    ],
    learn_processors=[{"class": "DropnaLabel"}, {"class": "CSRankNorm", "kwargs": {"fields_group": "label"}}],
    label=["Ref($close, -2) / Ref($close, -1) - 1"],
)
dataset = init_instance_by_config({
    "class": "DatasetH", "module_path": "qlib.data.dataset",
    "kwargs": {"handler": handler, "segments": {"test": ("2025-06-30", "2026-07-08")}},
})
pred = model.predict(dataset, segment="test")
if isinstance(pred, pd.DataFrame):
    pred = pred.iloc[:, 0]
print(f"pred shape: {pred.shape}")
print(f"pred index names: {pred.index.names}")
print(f"pred index head:\n{pred.index[:5]}")
print(f"pred head:\n{pred.head(10)}")

# 检查某一天的预测
dates = sorted(pred.index.get_level_values(0).unique())
print(f"\n预测日期数: {len(dates)}")
print(f"第一个日期: {dates[0]}, type={type(dates[0])}")
first_pred = pred.xs(dates[0], level=0)
print(f"第一天预测: {len(first_pred)} 只股票")
print(f"Top10:\n{first_pred.nlargest(10)}")

# 检查这些 Top10 股票的价格
top10 = first_pred.nlargest(10).index.tolist()
print(f"\nTop10 股票: {top10}")
prices = D.features(top10, ["$close"], start_time="2025-06-30", end_time="2026-07-08")
print(f"价格 shape: {prices.shape}")
print(f"价格 head:\n{prices.head(15)}")

# 尝试用日期索引
test_date = dates[0]
print(f"\n尝试索引 ({test_date}, {top10[0]}):")
try:
    val = prices.loc[(test_date, top10[0])]
    print(f"  找到: {val}")
except KeyError as e:
    print(f"  KeyError: {e}")
    print(f"  prices.index[0] = {prices.index[0]}")
    print(f"  类型: date={type(test_date)}, stock={type(top10[0])}")
    print(f"  idx0: date={type(prices.index[0][0])}, stock={type(prices.index[0][1])}")
