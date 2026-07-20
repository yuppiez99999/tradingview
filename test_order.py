import logging
import sys
import importlib
import importlib.util

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("test_order")

spec = importlib.util.spec_from_file_location(
    "ths_real_broker", 
    "v7.5_institutional/ths_real_broker.py"
)
ths_module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ths_module)
THSRealBroker = ths_module.THSRealBroker

class MockAccount:
    def __init__(self):
        self.positions = {}
        self.available_cash = 1000000

account = MockAccount()
broker = THSRealBroker(account, mode="real")

print("尝试连接期货通...")
result = broker.connect()
print(f"连接结果: {result}")
print(f"连接状态: {broker.is_connected}")

if broker.is_connected:
    print("\n连接成功，准备下单...")
    order_result = broker.place_order("y2608-C-9000", 1, "BUY_OPEN", 512.5)
    print(f"订单结果: {order_result}")
else:
    print("\n连接失败")
