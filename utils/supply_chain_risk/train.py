"""
供应链综合风险智能决策模型 - 训练管道
==============================================

核心模型：
  1. 金融风控评分卡 (FinancialRiskScorer)
     - 输入: 5维度质量评分 + category + data_type
     - 输出: 0-100 风险评分 (越高越安全)
     - 应用: 供应商信用评估、交易限额、合同审批

  2. 能源成本预警模型 (EnergyCostModel)
     - 输入: 能源领域各维度数据
     - 输出: 成本波动预警等级 (平稳/关注/预警/紧急)
     - 应用: 采购决策、库存管理、价格谈判

  3. 综合决策引擎 (CombinedDecisionEngine)
     - 融合模型 1 + 2 的输出
     - 输出: 结构化决策建议 + 风险等级
     - 应用: 日常供应链决策辅助

技术栈:
  - sklearn: 逻辑回归、随机森林、XGBoost
  - 规则引擎: 专家规则 + 机器学习混合
  - 模型持久化: joblib/pickle
"""

import json
import logging
import os
import pickle
import warnings
from collections import Counter
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.ensemble import RandomForestClassifier
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import accuracy_score
from sklearn.model_selection import train_test_split
from sklearn.preprocessing import StandardScaler

from utils.datetime_utils import now_bj
from utils.safe_pickle import load_pickle, sha256_file

warnings.filterwarnings("ignore")

logger = logging.getLogger(__name__)


# ============================================================
# 安全加固: pickle 完整性校验 (CWE-502)
# ============================================================


def _sha256_file(path):
    """流式计算文件 SHA256 — 委托收口实现 (侧车写入复用同一算法)."""
    return sha256_file(path)


def load_model_safe(path, expected_sha256=None):
    """安全加载 pickle 模型, 防供应链投毒 (CWE-502).

    2026-09-12 (Issue #30 二次复扫): 改为委托全项目唯一收口
    `utils.safe_pickle.load_pickle` —— 侧车比对、无侧车时的审计记录与
    「强制校验」策略 (`QUANT_REQUIRE_PICKLE_INTEGRITY=1` → fail-closed)
    都只保留这一处实现。原实现把「无侧车」静默放行, 与其它 4 个 pickle
    入口同属一类结构性缺口 (审计口径达标、实质防护缺位)。

    Raises:
        FileNotFoundError: 路径不存在.
        ValueError: 路径不是文件.
        PickleIntegrityError: 哈希不一致, 或强制校验下缺侧车.
    """
    return load_pickle(path, expected_sha256=expected_sha256)


DATA_DIR = Path(
    os.environ.get(
        "SUPPLY_CHAIN_DATA_DIR",
        str(
            Path(__file__).resolve().parents[3]
            / "01_数据源与数据处理"
            / "北数所A级Token_五领域上架"
            / "6.16"
            / "北数所上架包"
            / "01数据文件"
        ),
    )
)
BASE_DIR = Path(__file__).resolve().parent
MODEL_DIR = BASE_DIR / "models"
MODEL_DIR.mkdir(parents=True, exist_ok=True)
REPORT_DIR = BASE_DIR / "reports"
REPORT_DIR.mkdir(parents=True, exist_ok=True)


# ============================================================
# 工具函数
# ============================================================


def load_data():
    """加载金融和能源数据"""
    finance_df = pd.read_csv(DATA_DIR / "finance_token_A_B_20260616_205603.csv")
    energy_df = pd.read_csv(DATA_DIR / "energy_token_A_B_20260616_205603.csv")
    return finance_df, energy_df


def create_features(df):
    """
    特征工程 - 从原始数据创建机器学习特征

    特征包括:
    - 数值特征: 5维度质量评分 (data_quality_score, completeness, accuracy, timeliness, compliance_score)
    - 类别特征: category, data_type (One-Hot编码)
    - 组合特征: 各维度均值、方差、极值
    """
    features = pd.DataFrame()

    # 1. 基础数值特征
    features["quality_score"] = df["data_quality_score"]
    features["completeness"] = df["completeness"]
    features["accuracy"] = df["accuracy"]
    features["timeliness"] = df["timeliness"]
    features["compliance"] = df["compliance_score"]

    # 2. 衍生特征
    features["avg_dimension"] = features[
        ["quality_score", "completeness", "accuracy", "timeliness"]
    ].mean(axis=1)
    features["dim_std"] = features[
        ["quality_score", "completeness", "accuracy", "timeliness"]
    ].std(axis=1)
    features["quality_gap"] = features["quality_score"] - features["avg_dimension"]

    # 3. 风险相关特征
    features["risk_factor"] = (100 - features["quality_score"]) / 100  # 归一化风险因子
    features["compliance_risk"] = (100 - features["compliance"]) / 100
    features["timeliness_risk"] = (100 - features["timeliness"]) / 100

    # 4. 类别特征 (One-Hot 编码)
    cat_dummies = pd.get_dummies(df["category"], prefix="cat", dummy_na=False)
    dtype_dummies = pd.get_dummies(df["data_type"], prefix="dtype", dummy_na=False)

    features = pd.concat([features, cat_dummies, dtype_dummies], axis=1)

    return features


def create_labels(df):
    """
    创建训练标签 (多策略)

    标签 1: 二分类 (A级=1, B级=0) - 用于基础质量评估
    标签 2: 多分类 (基于质量分的5级分箱) - 用于精细风险等级
    标签 3: 回归 (直接预测质量分) - 用于评分卡
    """
    labels = {}

    # 二分类标签
    labels["binary"] = (df["token_level"] == "A").astype(int).values

    # 多分类标签 (5级风险)
    labels["multiclass"] = (
        pd.cut(
            df["data_quality_score"],
            bins=[0, 94, 96, 98, 99.5, 100],
            labels=[0, 1, 2, 3, 4],  # 0=高危, 4=优质
            include_lowest=True,
        )
        .astype(int)
        .values
    )

    # 回归标签 (0-100 质量分)
    labels["regression"] = df["data_quality_score"].values

    # 业务风险标签 (用于供应链决策)
    # 低于 95 分 = 高风险, 95-97 = 中等, 97-99 = 良好, 99+ = 优秀
    labels["business_risk"] = (
        pd.cut(
            df["data_quality_score"],
            bins=[0, 95, 97, 99, 100],
            labels=[3, 2, 1, 0],  # 3=高风险, 0=优秀
            include_lowest=True,
        )
        .astype(int)
        .values
    )

    return labels


# ============================================================
# 模型 1: 金融风控评分卡 (Rule-based + ML hybrid)
# ============================================================


class FinancialRiskScorecard:
    """
    金融风控评分卡模型

    混合架构:
    - 规则层: 专家规则 (权重评分)
    - 机器学习层: 逻辑回归 + 随机森林
    - 融合层: 加权融合输出最终评分

    输出: 0-100 评分, 越高越安全

    供应链应用场景:
    - 供应商信用评估 (采购决策)
    - 客户风险评级 (合同审批)
    - 交易限额计算 (付款条件)
    - 合同条款自动建议 (价格调整)
    """

    def __init__(self):
        self.rule_weights = None
        self.ml_model = None
        self.scaler = None
        self.feature_cols = None
        self.category_map = {
            "banking": 1.2,
            "securities": 1.1,
            "insurance": 1.0,
            "funds": 1.1,
            "trust": 1.15,
            "consumer_finance": 1.0,
            "fintech": 0.95,
            "asset_management": 1.05,
        }
        self.data_type_map = {
            "risk_control": 1.1,
            "credit_report": 1.15,
            "transaction_record": 1.0,
            "customer_profile": 0.9,
            "anti_fraud": 1.2,
            "credit_score": 1.25,
            "transaction_monitoring": 1.0,
            "portfolio_data": 1.05,
        }

    def rule_score(self, row):
        """规则引擎评分"""
        base_score = row["data_quality_score"]
        completeness = row["completeness"]
        accuracy = row["accuracy"]
        timeliness = row["timeliness"]
        compliance = row["compliance_score"]

        # 加权评分
        score = (
            base_score * 0.3
            + completeness * 0.2
            + accuracy * 0.2
            + timeliness * 0.15
            + compliance * 0.15
        )

        # 类别权重调整
        if row["category"] in self.category_map:
            score *= self.category_map[row["category"]]

        # 数据类型权重调整
        if row["data_type"] in self.data_type_map:
            score *= self.data_type_map[row["data_type"]]

        return min(100, max(0, score))

    def train_ml(self, features, labels):
        """训练机器学习子模型"""
        X = features.drop(columns=["category", "data_type"], errors="ignore")
        y = labels["business_risk"]

        # 划分数据集
        X_train, X_test, y_train, y_test = train_test_split(
            X, y, test_size=0.2, random_state=42, stratify=y
        )

        # 标准化
        self.scaler = StandardScaler()
        X_train_scaled = self.scaler.fit_transform(X_train)
        X_test_scaled = self.scaler.transform(X_test)

        # 逻辑回归
        lr = LogisticRegression(max_iter=1000, C=1.0, random_state=42)
        lr.fit(X_train_scaled, y_train)

        # 随机森林
        rf = RandomForestClassifier(
            n_estimators=200, max_depth=10, random_state=42, n_jobs=-1
        )
        rf.fit(X_train, y_train)

        # 评估
        lr_pred = lr.predict(X_test_scaled)
        rf_pred = rf.predict(X_test)

        self.lr_model = lr
        self.rf_model = rf
        self.lr_acc = accuracy_score(y_test, lr_pred)
        self.rf_acc = accuracy_score(y_test, rf_pred)
        self.feature_cols = X.columns.tolist()

        return {
            "logistic_regression_accuracy": self.lr_acc,
            "random_forest_accuracy": self.rf_acc,
            "feature_importance": dict(
                zip(X.columns, rf.feature_importances_, strict=True)
            ),
        }

    def _build_single_feature_vector(self, row):
        """为单条数据构建与训练时一致的特征向量"""
        if self.feature_cols is None:
            return None

        # 初始化所有特征为 0
        features = {}

        # 1. 数值特征
        features["quality_score"] = row["data_quality_score"]
        features["completeness"] = row["completeness"]
        features["accuracy"] = row["accuracy"]
        features["timeliness"] = row["timeliness"]
        features["compliance"] = row["compliance_score"]

        # 2. 衍生特征
        quality_vals = [
            features["quality_score"],
            features["completeness"],
            features["accuracy"],
            features["timeliness"],
        ]
        features["avg_dimension"] = np.mean(quality_vals)
        features["dim_std"] = np.std(quality_vals)
        features["quality_gap"] = features["quality_score"] - features["avg_dimension"]
        features["risk_factor"] = (100 - features["quality_score"]) / 100
        features["compliance_risk"] = (100 - features["compliance"]) / 100
        features["timeliness_risk"] = (100 - features["timeliness"]) / 100

        # 3. One-hot 编码 - 遍历所有训练时的列
        for col in self.feature_cols:
            if col not in features:
                if col.startswith("cat_"):
                    cat_val = col.replace("cat_", "")
                    features[col] = 1 if row.get("category") == cat_val else 0
                elif col.startswith("dtype_"):
                    dtype_val = col.replace("dtype_", "")
                    features[col] = 1 if row.get("data_type") == dtype_val else 0

        # 按 feature_cols 顺序构建向量
        X = np.array([features[col] for col in self.feature_cols]).reshape(1, -1)
        return X

    def predict_score(self, row, features_row=None):
        """预测单个样本的风险评分 (0-100)"""
        # 规则评分
        rule_score_val = self.rule_score(row)

        # ML 评分 (如果已训练)
        ml_score = 50.0
        if self.lr_model is not None and self.feature_cols is not None:
            # 优先使用传入的 features_row，否则自行构建
            if features_row is not None and all(
                col in features_row.index for col in self.feature_cols
            ):
                X = features_row[self.feature_cols].values.reshape(1, -1)
            else:
                X = self._build_single_feature_vector(row)

            # scaler 未训练(弱化)时不参与融合, ml_score 保持默认 50.0
            if X is not None and self.scaler is not None:
                X_scaled = self.scaler.transform(X)

                lr_probs = self.lr_model.predict_proba(X_scaled)[0]
                rf_probs = self.rf_model.predict_proba(X)[0]

                # 将类别概率转换为评分 (0=优秀, 3=高风险)
                lr_score = sum(
                    (3 - i) * prob * (100 / 3) for i, prob in enumerate(lr_probs)
                )
                rf_score = sum(
                    (3 - i) * prob * (100 / 3) for i, prob in enumerate(rf_probs)
                )

                # 融合ML评分
                ml_score = (lr_score * self.lr_acc + rf_score * self.rf_acc) / (
                    self.lr_acc + self.rf_acc
                )

        # 最终评分: 70% 规则 + 30% ML
        final_score = rule_score_val * 0.7 + ml_score * 0.3

        return round(final_score, 2)

    def predict_batch(self, df, features_df):
        """批量预测"""
        scores = []
        for i, (_, row) in enumerate(df.iterrows()):
            features_row = features_df.iloc[i] if features_df is not None else None
            scores.append(self.predict_score(row, features_row))
        return np.array(scores)

    def get_risk_level(self, score):
        """根据评分返回风险等级"""
        if score >= 85:
            return "优秀", "💚", "可授予最高信用额度，最优付款条件"
        if score >= 75:
            return "良好", "🟢", "正常信用额度，标准付款条件"
        if score >= 65:
            return "中等", "🟡", "建议加强尽职调查，缩短付款周期"
        if score >= 55:
            return "关注", "🟠", "限制交易金额，要求预付款或担保"
        return "高风险", "🔴", "建议暂缓合作或要求 100% 预付"


# ============================================================
# 模型 2: 能源成本预警模型
# ============================================================


class EnergyCostAlertModel:
    """
    能源成本预警模型

    分析能源领域数据的多维特征，识别成本波动风险信号:
    - 电力生产/消费异常
    - 碳排放指标变化
    - 电网调度稳定性
    - 水电/新能源出力波动

    输出四个预警等级:
    - 绿色 (0): 正常, 无需关注
    - 蓝色 (1): 关注, 常规监控
    - 黄色 (2): 预警, 启动应急预案
    - 红色 (3): 紧急, 立即调整采购策略
    """

    def __init__(self):
        self.category_weights = {
            "coal": 1.0,  # 煤炭 - 基础能源
            "electricity": 1.2,  # 电力 - 核心成本
            "oil_gas": 1.1,  # 油气 - 价格敏感
            "renewable": 0.8,  # 可再生 - 波动较大
            "storage": 0.9,  # 储能 - 稳定因素
            "nuclear": 1.3,  # 核电 - 安全敏感
            "hydro": 0.85,  # 水电 - 季节性
            "smart_grid": 1.0,  # 智能电网 - 技术因素
        }

        self.data_type_weights = {
            "production": 1.1,
            "consumption": 1.15,
            "grid_dispatch": 1.0,
            "carbon_emission": 1.2,
            "maintenance": 0.85,
            "radiation_monitor": 1.3,
            "dam_level": 0.9,
            "load_forecast": 1.05,
        }

    def calculate_cost_risk(self, row):
        """计算能源成本风险指数"""
        # 基础分
        base_risk = (100 - row["data_quality_score"]) * 0.5
        timeliness_risk = (100 - row["timeliness"]) * 0.3
        completeness_risk = (100 - row["completeness"]) * 0.2

        # 类别权重
        cat_weight = self.category_weights.get(row["category"], 1.0)
        dtype_weight = self.data_type_weights.get(row["data_type"], 1.0)

        # 综合成本风险
        cost_risk = (
            (base_risk + timeliness_risk + completeness_risk)
            * cat_weight
            * dtype_weight
        )

        return min(100, cost_risk)

    def train(self, energy_df):
        """训练能源成本模型 (统计基准 + 规则)"""
        create_features(energy_df)

        # 计算基准分布
        cost_risk_list: list[float] = []
        for _, row in energy_df.iterrows():
            cost_risk_list.append(self.calculate_cost_risk(row))

        cost_risks = np.asarray(cost_risk_list)

        # 设定分位数阈值
        self.p25 = np.percentile(cost_risks, 25)
        self.p50 = np.percentile(cost_risks, 50)
        self.p75 = np.percentile(cost_risks, 75)
        self.p90 = np.percentile(cost_risks, 90)
        self.mean_risk = cost_risks.mean()
        self.std_risk = cost_risks.std()

        # 类别基准统计
        self.category_stats = {}
        for cat in energy_df["category"].unique():
            subset = energy_df[energy_df["category"] == cat]
            risks = [self.calculate_cost_risk(r) for _, r in subset.iterrows()]
            self.category_stats[cat] = {
                "mean": np.mean(risks),
                "std": np.std(risks),
                "count": len(risks),
            }

        return {
            "mean_cost_risk": self.mean_risk,
            "std_cost_risk": self.std_risk,
            "thresholds": {
                "p25": self.p25,
                "p50": self.p50,
                "p75": self.p75,
                "p90": self.p90,
            },
            "category_stats": self.category_stats,
        }

    def predict_alert(self, row):
        """预测单个样本的预警等级"""
        cost_risk = self.calculate_cost_risk(row)

        # 动态阈值判断
        if cost_risk >= self.p90:
            alert_level = 3
            status = "🔴 紧急"
            action = "立即调整采购策略，考虑应急储备，启动价格谈判"
        elif cost_risk >= self.p75:
            alert_level = 2
            status = "🟡 预警"
            action = "加强监控，启动应急预案，准备调整库存"
        elif cost_risk >= self.p50:
            alert_level = 1
            status = "🔵 关注"
            action = "常规监控，关注同类数据变化趋势"
        else:
            alert_level = 0
            status = "🟢 正常"
            action = "无需关注，维持现有策略"

        return {
            "cost_risk_index": round(cost_risk, 2),
            "alert_level": alert_level,
            "status": status,
            "recommended_action": action,
            "risk_vs_mean": round(cost_risk - self.mean_risk, 2),
            "category": row["category"],
            "data_type": row["data_type"],
        }

    def predict_batch(self, df):
        """批量预测"""
        return [self.predict_alert(row) for _, row in df.iterrows()]


# ============================================================
# 模型 3: 综合决策引擎
# ============================================================


class CombinedDecisionEngine:
    """
    供应链综合决策引擎

    融合:
    - 金融风控评分 (0-100)
    - 能源成本预警 (0-3)
    - 业务规则 (自定义权重)

    输出:
    - 综合风险评分 (0-100)
    - 决策建议 (通过/关注/拒绝)
    - 行动指南 (具体操作建议)
    """

    def __init__(self, finance_weight=0.6, energy_weight=0.4):
        self.finance_weight = finance_weight
        self.energy_weight = energy_weight
        self.finance_model = FinancialRiskScorecard()
        self.energy_model = EnergyCostAlertModel()
        self.trained = False

    def train(self, finance_df, energy_df):
        """训练所有子模型"""
        finance_features = create_features(finance_df)
        finance_labels = create_labels(finance_df)
        finance_metrics = self.finance_model.train_ml(finance_features, finance_labels)

        energy_metrics = self.energy_model.train(energy_df)

        self.trained = True

        return {
            "finance_model": finance_metrics,
            "energy_model": energy_metrics,
            "weights": {"finance": self.finance_weight, "energy": self.energy_weight},
        }

    def make_decision(self, finance_score, energy_alert, context=None):
        """
        综合决策

        Args:
            finance_score: 金融风控评分 (0-100)
            energy_alert: 能源预警结果 dict
            context: 可选上下文信息 (如交易金额、合作历史等)

        Returns:
            dict: 决策结果
        """
        # 将能源预警转为 0-100 分
        alert_level = energy_alert["alert_level"]
        energy_score = 100 - (alert_level * 25)  # 0→100, 1→75, 2→50, 3→25

        # 综合评分
        combined_score = (
            finance_score * self.finance_weight + energy_score * self.energy_weight
        )

        # 决策逻辑
        if combined_score >= 80:
            decision = "✅ 通过"
            decision_level = 0
            priority = "普通"
        elif combined_score >= 65:
            decision = "🟡 关注"
            decision_level = 1
            priority = "关注"
        elif combined_score >= 50:
            decision = "🟠 限制"
            decision_level = 2
            priority = "优先"
        else:
            decision = "🔴 拒绝"
            decision_level = 3
            priority = "紧急"

        # 生成详细建议
        suggestions = []

        # 金融相关建议
        risk_level, risk_emoji, risk_advice = self.finance_model.get_risk_level(
            finance_score
        )
        suggestions.append(
            f"金融风险: {risk_emoji} {risk_level} ({finance_score:.1f}分)"
        )
        suggestions.append(f"  → {risk_advice}")

        # 能源相关建议
        suggestions.append(f"能源成本: {energy_alert['status']}")
        suggestions.append(f"  → {energy_alert['recommended_action']}")

        # 综合建议
        if decision_level == 0:
            suggestions.append(
                "\n📋 综合建议: 可按常规流程推进，建议合同付款条件 30-60 天"
            )
        elif decision_level == 1:
            suggestions.append(
                "\n📋 综合建议: 建议增加额外尽职调查，付款条件缩短至 15-30 天"
            )
        elif decision_level == 2:
            suggestions.append(
                "\n📋 综合建议: 建议限制单笔交易金额，要求 50% 预付款或第三方担保"
            )
        else:
            suggestions.append(
                "\n📋 综合建议: 强烈建议暂缓合作或要求 100% 预付，待风险因素改善后重新评估"
            )

        return {
            "combined_score": round(combined_score, 2),
            "finance_score": finance_score,
            "energy_score": energy_score,
            "energy_alert": energy_alert,
            "decision": decision,
            "decision_level": decision_level,
            "priority": priority,
            "suggestions": suggestions,
            "timestamp": now_bj().isoformat(),
        }

    def evaluate_supplier(self, finance_row, energy_rows=None):
        """
        评估供应商 (支持单条或多条能源数据聚合)

        Args:
            finance_row: 金融数据单行 (Series)
            energy_rows: 可选的能源数据多行

        Returns:
            dict: 供应商综合评估报告
        """
        # 金融评分
        finance_features = create_features(pd.DataFrame([finance_row]))
        finance_score = self.finance_model.predict_score(
            finance_row,
            finance_features.iloc[0] if not finance_features.empty else None,
        )

        # 能源预警
        if energy_rows is not None and len(energy_rows) > 0:
            # 多条能源数据取最严重的
            alerts = [
                self.energy_model.predict_alert(row)
                for _, row in energy_rows.iterrows()
            ]
            energy_alert = max(alerts, key=lambda x: x["alert_level"])
        else:
            # 无能源数据时使用中性评分
            energy_alert = {
                "cost_risk_index": 50.0,
                "alert_level": 1,
                "status": "🔵 关注",
                "recommended_action": "无能源维度数据，建议补充",
                "risk_vs_mean": 0,
                "category": "unknown",
                "data_type": "unknown",
            }

        return self.make_decision(finance_score, energy_alert)

    def save(self, path):
        """保存模型"""
        model_data = {
            "finance_model": {
                "lr_model": self.finance_model.lr_model,
                "rf_model": self.finance_model.rf_model,
                "scaler": self.finance_model.scaler,
                "feature_cols": self.finance_model.feature_cols,
                "lr_acc": self.finance_model.lr_acc,
                "rf_acc": self.finance_model.rf_acc,
            },
            "energy_model": {
                "p25": self.energy_model.p25,
                "p50": self.energy_model.p50,
                "p75": self.energy_model.p75,
                "p90": self.energy_model.p90,
                "mean_risk": self.energy_model.mean_risk,
                "std_risk": self.energy_model.std_risk,
                "category_stats": self.energy_model.category_stats,
            },
            "weights": {"finance": self.finance_weight, "energy": self.energy_weight},
            "trained": self.trained,
            "training_time": now_bj().isoformat(),
        }

        with open(path, "wb") as f:
            pickle.dump(model_data, f)
        # CWE-502 加固: 同步写 SHA256 侧车, 供 load_model_safe 完整性校验
        sidecar = str(path) + ".sha256"
        with open(sidecar, "w", encoding="utf-8") as f:
            f.write(_sha256_file(path))

    @classmethod
    def load(cls, path):
        """加载模型 (CWE-502 加固: 经 load_model_safe 做 SHA256 侧车校验)"""
        model_data = load_model_safe(path)

        engine = cls(
            finance_weight=model_data["weights"]["finance"],
            energy_weight=model_data["weights"]["energy"],
        )

        # 恢复金融模型
        engine.finance_model.lr_model = model_data["finance_model"]["lr_model"]
        engine.finance_model.rf_model = model_data["finance_model"]["rf_model"]
        engine.finance_model.scaler = model_data["finance_model"]["scaler"]
        engine.finance_model.feature_cols = model_data["finance_model"]["feature_cols"]
        engine.finance_model.lr_acc = model_data["finance_model"]["lr_acc"]
        engine.finance_model.rf_acc = model_data["finance_model"]["rf_acc"]

        # 恢复能源模型
        engine.energy_model.p25 = model_data["energy_model"]["p25"]
        engine.energy_model.p50 = model_data["energy_model"]["p50"]
        engine.energy_model.p75 = model_data["energy_model"]["p75"]
        engine.energy_model.p90 = model_data["energy_model"]["p90"]
        engine.energy_model.mean_risk = model_data["energy_model"]["mean_risk"]
        engine.energy_model.std_risk = model_data["energy_model"]["std_risk"]
        engine.energy_model.category_stats = model_data["energy_model"][
            "category_stats"
        ]

        engine.trained = model_data["trained"]
        return engine


# ============================================================
# 主训练流程
# ============================================================


def main():

    # 步骤 1: 加载数据
    finance_df, energy_df = load_data()

    # 步骤 2: 特征工程
    finance_features = create_features(finance_df)
    create_features(energy_df)

    create_labels(finance_df)
    create_labels(energy_df)

    # 步骤 3: 训练综合决策引擎
    engine = CombinedDecisionEngine(finance_weight=0.6, energy_weight=0.4)
    metrics = engine.train(finance_df, energy_df)

    # 步骤 4: 模型验证 - 抽样测试

    # 测试金融评分
    test_finance_rows = finance_df.sample(5, random_state=42)
    test_finance_features = finance_features.iloc[test_finance_rows.index]

    for i, (_, row) in enumerate(test_finance_rows.iterrows()):
        score = engine.finance_model.predict_score(row, test_finance_features.iloc[i])
        level, emoji, advice = engine.finance_model.get_risk_level(score)

    # 测试能源预警
    test_energy_rows = energy_df.sample(5, random_state=42)
    for _, (_, row) in enumerate(test_energy_rows.iterrows()):
        engine.energy_model.predict_alert(row)

    # 测试综合决策
    test_indices = [0, len(finance_df) // 2, len(finance_df) - 1]
    for _, idx in enumerate(test_indices):
        finance_row = finance_df.iloc[idx]
        energy_sample = energy_df.sample(min(3, len(energy_df)), random_state=idx + 42)
        result = engine.evaluate_supplier(finance_row, energy_sample)

        for _ in result["suggestions"]:
            pass

    # 步骤 5: 批量评分统计

    # 金融评分批量计算
    engine.finance_model.predict_batch(finance_df, finance_features)

    # 能源预警批量计算
    energy_alerts = engine.energy_model.predict_batch(energy_df)
    alert_levels = [a["alert_level"] for a in energy_alerts]
    alert_dist = Counter(alert_levels)
    for level in sorted(alert_dist.keys()):
        alert_dist[level]

    # 步骤 6: 保存模型

    model_path = MODEL_DIR / "combined_risk_model_v1.0.pkl"
    engine.save(model_path)

    # 保存元数据
    metadata = {
        "version": "1.0.0",
        "training_date": now_bj().isoformat(),
        "training_data": {
            "finance_records": len(finance_df),
            "energy_records": len(energy_df),
            "finance_categories": finance_df["category"].nunique(),
            "energy_categories": energy_df["category"].nunique(),
            "finance_data_types": finance_df["data_type"].nunique(),
            "energy_data_types": energy_df["data_type"].nunique(),
        },
        "metrics": metrics,
        "feature_columns": finance_features.columns.tolist(),
        "model_architecture": {
            "name": "HybridRuleML",
            "components": [
                "RuleBasedScorecard",
                "LogisticRegression",
                "RandomForest",
                "EnergyCostAlert",
            ],
            "finance_ml_accuracy": metrics["finance_model"]["random_forest_accuracy"],
            "fusion_weights": metrics["weights"],
        },
        "thresholds": {
            "finance_score_levels": {
                "excellent": 85,
                "good": 75,
                "medium": 65,
                "watch": 55,
            },
            "energy_alert_levels": {
                "normal": 0,
                "watch": 1,
                "warning": 2,
                "critical": 3,
            },
            "combined_decision_levels": {"approve": 80, "watch": 65, "restrict": 50},
        },
    }

    metadata_path = MODEL_DIR / "model_metadata.json"
    with open(metadata_path, "w", encoding="utf-8") as f:
        json.dump(metadata, f, ensure_ascii=False, indent=2)

    # 保存类别映射
    category_mapping = {
        "finance_categories": sorted(finance_df["category"].unique().tolist()),
        "energy_categories": sorted(energy_df["category"].unique().tolist()),
        "finance_data_types": sorted(finance_df["data_type"].unique().tolist()),
        "energy_data_types": sorted(energy_df["data_type"].unique().tolist()),
    }
    mapping_path = MODEL_DIR / "category_mapping.json"
    with open(mapping_path, "w", encoding="utf-8") as f:
        json.dump(category_mapping, f, ensure_ascii=False, indent=2)

    # 步骤 7: 输出使用指南

    return engine, metadata


if __name__ == "__main__":
    engine, metadata = main()
