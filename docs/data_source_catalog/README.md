# 数据源目录索引 (Data Source Catalog)

> 源自 [FinceptTerminal](https://github.com/Fincept-Corporation/FinceptTerminal) v4.0.3 (AGPL-3.0)
> 整理日期: 2026-08-22

本目录收录 100+ 金融数据源的参考文档，按地域/类别组织，供主系统数据连接器层
（`quant_modules/data_layer.py`）选型参考。主系统数据源优先级链为：

```
Wind 数据终端 (P0) → Wind MCP (P1) → TDX 通达信 (P2) → AKShare (P3)
→ 新浪财经 (P4) → 本地缓存 (P5) → 兜底预定义价格 (P6)
```

## 目录

| 文档 | 覆盖范围 | 与主系统关系 |
|------|----------|-------------|
| [CHINA_DATA_SOURCES.md](CHINA_DATA_SOURCES.md) | A 股/港股/期货/期权/宏观经济 (AkShare 8模块 + 国家统计局) | **直接相关** — AkShare 已是 P3 数据源 |
| [ECONOMIC_DATA_SOURCES.md](ECONOMIC_DATA_SOURCES.md) | FRED/World Bank/IMF/OECD/BIS/ECB/BOJ | 宏观综合分析页可复用 |
| [GOVERNMENT_DATA_SOURCES.md](GOVERNMENT_DATA_SOURCES.md) | 各国政府统计机构 | 政策/十五五规划分析参考 |
| [MARKET_DATA_SOURCES.md](MARKET_DATA_SOURCES.md) | AkShare/Polygon/Yahoo/Baostock/Tushare/Wind | **直接相关** — Wind/AKShare 已接入 |
| [REGIONAL_DATA_SOURCES.md](REGIONAL_DATA_SOURCES.md) | 区域性数据源 | 备选 |
| [SATELLITE_GEO_DATA_SOURCES.md](SATELLITE_GEO_DATA_SOURCES.md) | 卫星/遥感/海运 | 另类数据，暂不接入 |
| [SPECIALTY_DATA_SOURCES.md](SPECIALTY_DATA_SOURCES.md) | 链上/情绪/气候 | 另类数据，暂不接入 |
| [US_FINANCIAL_DATA_SOURCES.md](US_FINANCIAL_DATA_SOURCES.md) | 美股专用数据源 | 跨市场研究参考 |

## 使用建议

1. **A 股核心**: 优先 `CHINA_DATA_SOURCES.md` 中 AkShare 模块（已通过 P3 接入）
2. **宏观补充**: `ECONOMIC_DATA_SOURCES.md` 中 FRED/IMF 可作为康波周期分析的数据补充
3. **政策分析**: `GOVERNMENT_DATA_SOURCES.md` 中国家统计局可增强十五五规划评分
4. **另类数据**: 卫星/链上/气候等暂不接入，留作未来扩展参考

## 备注

FinceptTerminal 本体为 C++20/Qt6 桌面应用（非 Python 包），无法直接 pip install。
本目录仅收录其数据源文档作为选型参考。如需 FinceptTerminal 完整功能，
请下载其 [桌面安装包](https://github.com/Fincept-Corporation/FinceptTerminal/releases)。
