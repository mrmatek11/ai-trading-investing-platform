from .market_scanner import MarketScanner

# Optional imports — fail gracefully if dependencies missing
try:
    from .news_monitor import NewsMonitor
except ImportError:
    pass

try:
    from .fear_greed import FearGreedMonitor
except ImportError:
    pass

try:
    from .funding_rate import FundingRateMonitor
except ImportError:
    pass

try:
    from .whale_alerts import WhaleLiquidationMonitor
except ImportError:
    pass

try:
    from .economic_calendar import EconomicCalendar
except ImportError:
    pass
