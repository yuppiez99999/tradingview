p = "v8.3_institutional/daily_workflow.py"
f = open(p, encoding="utf-8")
c = f.read()
f.close()
old = "        if level >= CircuitLevel.LEVEL_3:"
new = """        try:
            _is_level3 = (hasattr(level, "value") and level.value >= 3) or (hasattr(level,
             "name") and "3" in str(level.name))
        except Exception as e:
            _is_level3 = False
        if _is_level3:"""
assert old in c, "old not found"
c = c.replace(old, new, 1)
f = open(p, "w", encoding="utf-8")
f.write(c)
f.close()
