import sys

sys.path.insert(0, r'e:\各种PY程序\28-终极量化交易系统7.1\v7.5_institutional')

tests = [
    # (module, class, label)
    ('src.hedging', 'HedgeEngine', 'HedgeEngine'),
    ('src.hedging', 'HedgeRebalanceIntegrator', 'HedgeRebalanceIntegrator'),
    ('src.hedging', 'MultiLayerHedgeManager', 'MultiLayerHedgeManager'),
    ('src.hedging', 'TailRiskHedge', 'TailRiskHedge'),
    ('src.hedging', 'VolatilityHedge', 'VolatilityHedge'),
    ('src.hedging', 'EnhancedDeltaHedge', 'EnhancedDeltaHedge'),
    ('src.hedging', 'SmartHedgeTrigger', 'SmartHedgeTrigger'),
    ('src.hedging', 'BetaHedger', 'BetaHedger'),
    ('src.hedging', 'HedgeCoordinator', 'HedgeCoordinator'),

    ('src.risk', 'CircuitBreaker', 'CircuitBreaker'),
    ('src.risk', 'RiskBudgeter', 'RiskBudgeter'),
    ('src.risk', 'RiskManager', 'RiskManager'),
    ('src.risk', 'DynamicRiskBudget', 'DynamicRiskBudget'),
    ('src.risk', 'PSIMonitor', 'PSIMonitor'),
    ('src.risk', 'ConcentrationRiskMonitor', 'ConcentrationRiskMonitor'),
    ('src.risk', 'StressTestEngine', 'StressTestEngine'),
    ('src.risk', 'RiskControls', 'RiskControls'),
    ('src.risk', 'CorrelationMonitor', 'CorrelationMonitor'),

    ('src.macro', 'KondratievCycleAnalyzer', 'KondratievCycleAnalyzer'),
    ('src.macro', 'FifteenFivePlanAnalyzer', 'FifteenFivePlanAnalyzer'),
    ('src.macro', 'SocialSecurityETFTracker', 'SocialSecurityETFTracker'),

    ('src.validation', 'PurgedWalkForward', 'PurgedWalkForward'),
    ('src.validation', 'DeflatedSharpeResult', 'DeflatedSharpeResult'),
    ('src.validation', 'WalkForwardValidator', 'WalkForwardValidator'),
    ('src.validation', 'PITChecker', 'PITChecker'),
    ('src.validation', 'PreDeploymentValidator', 'PreDeploymentValidator'),
    ('src.validation', 'StatisticalSignificance', 'StatisticalSignificance'),

    ('src.signals', 'SignalFusionEngine', 'SignalFusionEngine'),
    ('src.signals', 'EnhancedSignalFusion', 'EnhancedSignalFusion'),
    ('src.signals', 'SignalIndependenceAnalyzer', 'SignalIndependenceAnalyzer'),
    ('src.signals', 'SignalAuditor', 'SignalAuditor'),
    ('src.signals', 'RuleEngine', 'RuleEngine'),
    ('src.signals', 'SignalCrowdingDetector', 'SignalCrowdingDetector'),

    ('src.ai', 'ModelRouter', 'ModelRouter'),
    ('src.ai', 'GLM5DecisionEngine', 'GLM5DecisionEngine'),
    ('src.ai', 'AICoordinator', 'AICoordinator'),
    ('src.ai', 'LLMClient', 'LLMClient'),

    ('src.nlp', 'FinSentimentAnalyzer', 'FinSentimentAnalyzer'),
    ('src.nlp', 'EventDrivenFactor', 'EventDrivenFactor'),
    ('src.nlp', 'SentimentHub', 'SentimentHub'),

    ('src.factors', 'FiveFactorModel', 'FiveFactorModel'),
    ('src.factors', 'DynamicPositionSizer', 'DynamicPositionSizer'),
    ('src.factors', 'DecisionTheoryEngine', 'DecisionTheoryEngine'),
    ('src.factors', 'MarketImpactModel', 'MarketImpactModel'),

    ('src.derivatives', 'GreeksCalculator', 'GreeksCalculator'),
    ('src.derivatives', 'FuturesQuote', 'FuturesQuote'),
    ('src.derivatives', 'OptionsSnapshot', 'OptionsSnapshot'),

    ('src.config', 'ConfigHub', 'ConfigHub'),
    ('src.config', 'ConfigValidator', 'ConfigValidator'),
    ('src.config', 'PerformanceAttribution', 'PerformanceAttribution'),

    ('src.ml', 'MLPredictor', 'MLPredictor'),
    ('src.ml', 'EnhancedTrainer', 'EnhancedTrainer'),
    ('src.ml', 'OptunaTrainer', 'OptunaTrainer'),
    ('src.ml', 'MLflowTracker', 'MLflowTracker'),
]

ok = fail = 0
for mod, cls, lbl in tests:
    try:
        m = __import__(mod, fromlist=[cls])
        got = getattr(m, cls, None)
        if got is not None:
            ok += 1
            print(f'  OK  {lbl}')
        else:
            fail += 1
            print(f'  MISS {lbl}')
    except Exception as e:
        fail += 1
        print(f'  FAIL {lbl}: {str(e)[:80]}')

print(f'  {"="*40}')
print(f'  {ok}/{ok+fail} classes verified')
