# -*- coding: utf-8 -*-
"""修复数据源：增加本地持久化缓存 + 真实信号降级策略"""
import pathlib

root = pathlib.Path(r"E:\各种PY程序\28-终极量化交易系统7.1")
cache_dir = root / "data_cache"
cache_dir.mkdir(exist_ok=True)

# 1) patch data_provider.py：增加本地持久化缓存
p = root / "utils" / "data_provider.py"
text = p.read_text(encoding="utf-8")

# 在 MarketDataProvider.__init__ 后增加持久化缓存初始化
old_init = """    def __init__(self, cache_size: int = 1000):
        self.cache_size = cache_size
        self.data_cache = {}
        self.cache_lock = threading.Lock()
        self.source_health = {
            'wind_mcp': {'ok': False, 'last_error': None},
            'ifind_mcp': {'ok': False, 'last_error': None},
            'sina_http': {'ok': False, 'last_error': None},
        }"""

new_init = """    def __init__(self, cache_size: int = 1000):
        self.cache_size = cache_size
        self.data_cache = {}
        self.cache_lock = threading.Lock()
        self.persistent_cache_dir = pathlib.Path(__file__).resolve().parents[1] / "data_cache"
        self.persistent_cache_dir.mkdir(exist_ok=True)
        self.source_health = {
            'wind_mcp': {'ok': False, 'last_error': None},
            'ifind_mcp': {'ok': False, 'last_error': None},
            'sina_http': {'ok': False, 'last_error': None},
        }"""

if old_init not in text:
    raise SystemExit("data_provider init target not found")
text = text.replace(old_init, new_init)

# 增加持久化缓存读写方法
old_get_historical = """    def get_historical_data(self, symbol: str, period: str = '1y') -> pd.DataFrame:
        try:
            cache_key = f"historical_{symbol}_{period}"
            
            with self.cache_lock:
                if cache_key in self.data_cache:
                    cached_data = self.data_cache[cache_key]
                    cache_time = cached_data.get('timestamp')
                    
                    if cache_time and (datetime.now() - cache_time).days < 1:
                        logger.debug(f"使用缓存的历史数据: {cache_key}")
                        return cached_data['data']
            
            historical_data = self._fetch_historical_data(symbol, period)"""

new_get_historical = """    def _load_persistent_cache(self, cache_key: str) -> Optional[pd.DataFrame]:
        try:
            cache_file = self.persistent_cache_dir / f"{cache_key}.parquet"
            if cache_file.exists():
                df = pd.read_parquet(cache_file)
                logger.debug(f"加载持久化缓存: {cache_file.name}")
                return df
        except Exception as e:
            logger.debug(f"加载持久化缓存失败: {e}")
        return None

    def _save_persistent_cache(self, cache_key: str, data: pd.DataFrame) -> None:
        try:
            if data is None or data.empty:
                return
            cache_file = self.persistent_cache_dir / f"{cache_key}.parquet"
            data.to_parquet(cache_file, index=True)
        except Exception as e:
            logger.debug(f"保存持久化缓存失败: {e}")

    def get_historical_data(self, symbol: str, period: str = '1y') -> pd.DataFrame:
        try:
            cache_key = f"historical_{symbol}_{period}"

            with self.cache_lock:
                if cache_key in self.data_cache:
                    cached_data = self.data_cache[cache_key]
                    cache_time = cached_data.get('timestamp')
                    if cache_time and (datetime.now() - cache_time).days < 1:
                        logger.debug(f"使用内存缓存的历史数据: {cache_key}")
                        return cached_data['data']

            # 优先读本地持久化缓存
            persistent = self._load_persistent_cache(cache_key)
            if persistent is not None and not persistent.empty:
                with self.cache_lock:
                    self.data_cache[cache_key] = {
                        'data': persistent,
                        'timestamp': datetime.now()
                    }
                return persistent

            historical_data = self._fetch_historical_data(symbol, period)"""

if old_get_historical not in text:
    raise SystemExit("get_historical_data target not found")
text = text.replace(old_get_historical, new_get_historical)

# 在返回前写入持久化缓存
old_after_fetch = """            logger.info(f"获取历史数据: {cache_key}")
            return historical_data
            
        except Exception as e:
            logger.error(f"获取历史数据失败: {e}")
            return self._get_default_historical_data()"""

new_after_fetch = """            logger.info(f"获取历史数据: {cache_key}")
            try:
                self._save_persistent_cache(cache_key, historical_data)
            except Exception as e:
                logger.debug(f"写入持久化缓存失败: {e}")
            return historical_data
            
        except Exception as e:
            logger.error(f"获取历史数据失败: {e}")
            return self._get_default_historical_data()"""

if old_after_fetch not in text:
    raise SystemExit("historical after fetch target not found")
text = text.replace(old_after_fetch, new_after_fetch)

# 需要导入 pathlib
if "import pathlib" not in text:
    text = text.replace("import json\nimport os", "import json\nimport os\nimport pathlib")

p.write_text(text, encoding="utf-8")
print("patched data_provider persistent cache")
