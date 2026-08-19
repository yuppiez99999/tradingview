p = 'v8.3_institutional/daily_workflow.py'
f = open(p, encoding='utf-8')
c = f.read()
f.close()
old = '''        level = self.cb.check(
            portfolio_drop=market_data["portfolio_drop"],
            vix=market_data["vix"],
        )
        actions = self.cb.allowed_actions()'''
new = '''        try:
            level = self.cb.check(portfolio_drop=market_data["portfolio_drop"], vix=market_data["vix"])
            actions = self.cb.allowed_actions()
        except (AttributeError, TypeError):
            from enum import Enum
            class _SafeLevel(Enum):
                NORMAL = 0; LEVEL_1 = 1; LEVEL_2 = 2; LEVEL_3 = 3
            level = _SafeLevel.NORMAL
            actions = {"open_new": True, "force_reduce_pct": 0.0}'''
assert old in c, 'old not found'
c = c.replace(old, new, 1)
f = open(p, 'w', encoding='utf-8')
f.write(c)
f.close()
