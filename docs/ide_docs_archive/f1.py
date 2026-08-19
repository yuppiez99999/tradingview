p = 'v8.3_institutional/daily_workflow.py'
f = open(p, encoding='utf-8')
c = f.read()
f.close()
old = 'from risk.circuit_breaker import CircuitBreaker, CircuitLevel'
new = '''from risk.circuit_breaker import CircuitBreaker
class CircuitLevel:
    LEVEL_1 = 1; LEVEL_2 = 2; LEVEL_3 = 3; LEVEL_4 = 4'''
assert old in c, 'old not found'
c = c.replace(old, new, 1)
f = open(p, 'w', encoding='utf-8')
f.write(c)
f.close()
