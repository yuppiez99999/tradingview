import pytest

"""
Fallback `benchmark` fixture for environments without `pytest-benchmark`.

Some CI or dev environments may not have the `pytest-benchmark` plugin
installed. Tests in `tests/perf/` use the `benchmark` fixture; when the
plugin is absent, provide a no-op replacement that simply calls the
target function to allow test execution to continue.
"""

try:
    # If pytest-benchmark is available it will provide the `benchmark`
    # fixture; in that case do nothing and let the plugin handle it.
    pass  # type: ignore
except Exception:

    @pytest.fixture
    def benchmark():
        """No-op benchmark: run the callable and return its result.

        Usage in tests remains the same: `benchmark(fn, *args, **kwargs)`.
        """

        def _bench(func, *args, **kwargs):
            return func(*args, **kwargs)

        return _bench
