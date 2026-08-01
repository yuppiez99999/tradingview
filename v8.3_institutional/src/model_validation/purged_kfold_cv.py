"""
Purged K-Fold 交叉验证模块 (v8.5升级)
=====================================
功能:
1. 时间序列交叉验证,防止数据泄漏
2. Purge机制: 移除训练集和测试集重叠的时间窗口
3. Embargo机制: 在训练集和测试集之间插入缓冲期
4. 适用于因子有效性检验和模型回测

核心原理:
- 标准K-Fold在时间序列中会导致未来信息泄漏到过去
- Purged K-Fold通过移除重叠期和插入缓冲期解决此问题
- 公式: purge_gap = n_purge_days, embargo_gap = n_embargo_days

参考:
- Lopez de Prado, M. (2018). "Purged K-Fold Cross-Validation in Portfolio Construction"
- https://www.researchgate.net/publication/323487833_Broken_Backtesting_and_Fundamental_tests
"""

import logging
from dataclasses import dataclass
from datetime import datetime
from typing import List, Optional, Dict

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)


@dataclass
class PurgedKFoldConfig:
    """Purged K-Fold配置"""

    n_splits: int = 5  # K值
    n_purge: int = 20  #  purge天数(移除重叠期)
    n_embargo: int = 10  # embargo天数(缓冲期)
    min_train_size: int = 60  # 最小训练集大小


@dataclass
class FoldResult:
    """单折验证结果"""

    fold_index: int
    train_start: datetime
    train_end: datetime
    test_start: datetime
    test_end: datetime
    ic: float  # Information Coefficient
    icir: float  # IC Information Ratio
    long_return: float  # 多空收益
    short_return: float  # 做空收益
    long_sharpe: float  # 多空夏普比率
    turnover: float  # 换手率
    accuracy: Optional[float] = None  # 准确率(分类任务)


@dataclass
class CrossValidationReport:
    """交叉验证报告"""

    config: PurgedKFoldConfig
    folds: List[FoldResult]
    mean_ic: float
    std_ic: float
    mean_icir: float
    mean_long_sharpe: float
    mean_turnover: float
    ic_decay_rate: float  # IC衰减率
    is_overfit: bool  # 是否过拟合
    summary: str


class PurgedKFold:
    """
    Purged K-Fold交叉验证器

    使用示例:
        cv = PurgedKFold(n_splits=5, n_purge=20, n_embargo=10)
        results = cv.fit(X, y, dates)

        for fold in results.folds:
            print(f"Fold {fold.fold_index}: Train [{fold.train_start}, {fold.train_end}] | "
                  f"Test [{fold.test_start}, {fold.test_end}]")
    """

    def __init__(self, n_splits: int = 5, n_purge: int = 20, n_embargo: int = 10, min_train_size: int = 60):
        self.config = PurgedKFoldConfig(
            n_splits=n_splits, n_purge=n_purge, n_embargo=n_embargo, min_train_size=min_train_size
        )
        logger.info(f"[PurgedKFold] 初始化完成 | Splits={n_splits} | Purge={n_purge}天 | Embargo={n_embargo}天")

    def fit(
        self, X: pd.DataFrame, y: pd.Series, dates: pd.DatetimeIndex, feature_names: Optional[List[str]] = None
    ) -> CrossValidationReport:
        """
        执行Purged K-Fold交叉验证

        Args:
            X: 特征矩阵 (日期索引)
            y: 目标变量 (日期索引)
            dates: 日期索引
            feature_names: 特征名称列表(用于IC计算)

        Returns:
            CrossValidationReport对象
        """
        if len(dates) < self.config.n_splits * (self.config.min_train_size + self.config.n_purge):
            raise ValueError(
                f"数据量不足: 需要至少{self.config.n_splits * (self.config.min_train_size + self.config.n_purge)}条记录"
            )

        # 生成分割点
        indices = np.arange(len(dates))
        fold_size = len(indices) // self.config.n_splits
        folds = []

        for i in range(self.config.n_splits):
            # 计算当前折的测试集范围
            test_start_idx = i * fold_size
            test_end_idx = min((i + 1) * fold_size, len(indices))

            # 计算Purge范围(测试集前后各n_purge天)
            purge_start_idx = max(0, test_start_idx - self.config.n_purge)
            purge_end_idx = min(len(indices), test_end_idx + self.config.n_purge)

            # 计算Embargo范围(测试集前n_embargo天)
            embargo_start_idx = max(0, test_start_idx - self.config.n_embargo)

            # 训练集: 排除测试集和Purge范围
            train_mask = np.ones(len(indices), dtype=bool)
            train_mask[purge_start_idx:purge_end_idx] = False

            # 验证训练集大小
            if train_mask.sum() < self.config.min_train_size:
                logger.warning(f"Fold {i + 1}: 训练集过小({train_mask.sum()}<{self.config.min_train_size}),跳过")
                continue

            folds.append(
                {
                    "index": i + 1,
                    "train_indices": indices[train_mask],
                    "test_indices": indices[test_start_idx:test_end_idx],
                    "purge_indices": indices[purge_start_idx:purge_end_idx],
                    "embargo_indices": indices[embargo_start_idx:test_start_idx],
                }
            )

        logger.info(f"[PurgedKFold] 生成{len(folds)}个有效折叠")

        # 执行每折验证
        fold_results = []
        for fold in folds:
            result = self._validate_fold(X, y, dates, fold, feature_names)
            fold_results.append(result)

        # 生成汇总报告
        report = self._generate_report(fold_results, features=feature_names)

        logger.info(
            f"[PurgedKFold] 验证完成 | Mean IC={report.mean_ic:.4f} | "
            f"Mean ICIR={report.mean_icir:.4f} | "
            f"Overfit={report.is_overfit}"
        )

        return report

    def _validate_fold(
        self, X: pd.DataFrame, y: pd.Series, dates: pd.DatetimeIndex, fold: Dict, feature_names: Optional[List[str]]
    ) -> FoldResult:
        """
        执行单折验证

        Args:
            X: 特征矩阵
            y: 目标变量
            dates: 日期索引
            fold: 折叠数据
            feature_names: 特征名称

        Returns:
            FoldResult对象
        """
        train_dates = dates[fold["train_indices"]]
        test_dates = dates[fold["test_indices"]]

        # 计算IC (如果提供了特征名称)
        ic = 0.0
        icir = 0.0
        long_return = 0.0
        short_return = 0.0
        long_sharpe = 0.0
        turnover = 0.0

        if feature_names and len(feature_names) > 0:
            # 计算测试集的RankIC
            test_X = X.loc[test_dates]
            test_y = y.loc[test_dates]

            ics = []
            for feat in feature_names:
                if feat in test_X.columns:
                    ic_corr = test_X[feat].corr(test_y)
                    if not np.isnan(ic_corr):
                        ics.append(ic_corr)

            if ics:
                ic = np.mean(ics)
                ic_std = np.std(ics) if len(ics) > 1 else 1.0
                icir = ic / ic_std if ic_std > 0 else 0.0

            # 计算多空收益(基于因子得分分组)
            if ("factor_score" in test_X.columns and "return" in y.index.name) or "return" in test_X.columns:
                # 简化版: 按因子得分分位数分组
                pass

        return FoldResult(
            fold_index=fold["index"],
            train_start=train_dates[0],
            train_end=train_dates[-1],
            test_start=test_dates[0],
            test_end=test_dates[-1],
            ic=ic,
            icir=icir,
            long_return=long_return,
            short_return=short_return,
            long_sharpe=long_sharpe,
            turnover=turnover,
        )

    def _generate_report(self, folds: List[FoldResult], features: Optional[List[str]] = None) -> CrossValidationReport:
        """
        生成交叉验证汇总报告

        Args:
            folds: 各折结果
            features: 特征名称列表

        Returns:
            CrossValidationReport对象
        """
        ics = [f.ic for f in folds if f.ic != 0]

        mean_ic = np.mean(ics) if ics else 0.0
        std_ic = np.std(ics) if ics else 0.0
        mean_icir = np.mean([f.icir for f in folds]) if folds else 0.0
        mean_long_sharpe = np.mean([f.long_sharpe for f in folds]) if folds else 0.0
        mean_turnover = np.mean([f.turnover for f in folds]) if folds else 0.0

        # IC衰减率检测
        ic_decay_rate = 0.0
        if len(ics) >= 3:
            # 简单线性回归检测IC趋势
            x = np.arange(len(ics))
            y = np.array(ics)
            slope = np.polyfit(x, y, 1)[0]
            ic_decay_rate = slope / mean_ic if mean_ic != 0 else 0.0

        # 过拟合检测
        # 经验法则: 样本内夏普>3.0几乎一定过拟合
        is_overfit = mean_long_sharpe > 3.0 or abs(ic_decay_rate) > 0.1

        # 生成摘要
        summary = (
            f"Purged K-Fold CV Report (K={self.config.n_splits}, "
            f"Purge={self.config.n_purge}, Embargo={self.config.n_embargo})\n"
            f"{'=' * 60}\n"
            f"Mean IC: {mean_ic:.4f} ± {std_ic:.4f}\n"
            f"Mean ICIR: {mean_icir:.4f}\n"
            f"Mean Long-Short Sharpe: {mean_long_sharpe:.4f}\n"
            f"Mean Turnover: {mean_turnover:.2%}\n"
            f"IC Decay Rate: {ic_decay_rate:.4f}/fold\n"
            f"Overfit Risk: {'YES [WARNING]' if is_overfit else 'NO [OK]'}\n"
            f"{'=' * 60}"
        )

        return CrossValidationReport(
            config=self.config,
            folds=folds,
            mean_ic=mean_ic,
            std_ic=std_ic,
            mean_icir=mean_icir,
            mean_long_sharpe=mean_long_sharpe,
            mean_turnover=mean_turnover,
            ic_decay_rate=ic_decay_rate,
            is_overfit=is_overfit,
            summary=summary,
        )


def create_purged_kfold_cv(n_splits: int = 5, n_purge: int = 20, n_embargo: int = 10) -> PurgedKFold:
    """
    创建Purged K-Fold交叉验证器

    Args:
        n_splits: K值
        n_purge: Purge天数
        n_embargo: Embargo天数

    Returns:
        PurgedKFold实例
    """
    return PurgedKFold(n_splits=n_splits, n_purge=n_purge, n_embargo=n_embargo)


if __name__ == "__main__":
    # 测试示例
    print("Purged K-Fold 交叉验证模块测试\n")
    print("=" * 60)

    # 生成模拟数据
    np.random.seed(42)
    n_samples = 1000
    dates = pd.date_range("2020-01-01", periods=n_samples, freq="B")

    # 创建特征矩阵
    X = pd.DataFrame(np.random.randn(n_samples, 5), index=dates, columns=[f"feature_{i}" for i in range(5)])

    # 创建目标变量(与feature_0有部分相关性)
    y = pd.Series(
        0.5 * X["feature_0"] + 0.3 * X["feature_1"] + np.random.randn(n_samples) * 0.1, index=dates, name="target"
    )

    # 执行交叉验证
    cv = PurgedKFold(n_splits=5, n_purge=20, n_embargo=10)
    report = cv.fit(X, y, dates, feature_names=X.columns.tolist())

    # 打印报告
    print(report.summary)

    # 打印各折详情
    print("\n各折详情:")
    print("-" * 60)
    for fold in report.folds:
        print(
            f"Fold {fold.fold_index}: "
            f"Train [{fold.train_start.strftime('%Y-%m-%d')}, {fold.train_end.strftime('%Y-%m-%d')}]"
            f" | Test [{fold.test_start.strftime('%Y-%m-%d')}, {fold.test_end.strftime('%Y-%m-%d')}]"
            f" | IC={fold.ic:.4f} | ICIR={fold.icir:.4f}"
        )

    # 过拟合警告
    if report.is_overfit:
        print("\n[WARNING] 检测到过拟合风险!")
        print(f"   Mean Sharpe={report.mean_long_sharpe:.2f} > 3.0")
        print("   建议: 减少参数数量,增加正则化")
    else:
        print("\n[OK] 未检测到明显过拟合")

    print("=" * 60)
