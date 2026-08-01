import py_compile
import os
base = r'e:\各种PY程序\28-终极量化交易系统7.1\v7.5_institutional\src'
errors = 0
for root, _dirs, files in os.walk(base):
    for f in files:
        if not f.endswith('.py'):
            continue
        full = os.path.join(root, f)
        rel = os.path.relpath(full, base)
        try:
            py_compile.compile(full, doraise=True)
        except py_compile.PyCompileError as e:
            errors += 1
            print(f'  SYNTAX ERROR: {rel}: {e}')
        except SyntaxError as e:
            errors += 1
            print(f'  SYNTAX ERROR: {rel}: {e}')
if errors == 0:
    print('  ALL CLEAN - No syntax errors found')
else:
    print(f'  {errors} files with syntax errors')
