"""运行 lgb_enhanced_trainer 全量强制重训"""
import os, sys, traceback, io

# 项目根目录路径设置
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE_DIR)
os.chdir(BASE_DIR)

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8')

try:
    sys.argv = ['lgb_enhanced_trainer.py', '--force-retrain']
    from lgb_enhanced_trainer import main
    main()
except SystemExit as e:
    print(f"\n[训练完成] code={e.code}")
except (ValueError, TypeError, KeyError, AttributeError, RuntimeError, OSError, TimeoutError, ConnectionError) as e:
    # 数据处理/计算/IO 异常: 格式/类型/字段/属性/运行时/网络/超时
    print(f"\n[异常] {type(e).__name__}: {e}")
    traceback.print_exc()
