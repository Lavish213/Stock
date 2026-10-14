"""Environment-backed settings with mapping and attribute compatibility."""
import os
from pathlib import Path
from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parents[2] / '.env')
_runtime_overrides = {}

class Settings(dict):
    def __getattr__(self, name):
        try:
            return self[name]
        except KeyError as exc:
            raise AttributeError(name) from exc

    def __setattr__(self, name, value):
        self[name] = value

def set_runtime_overrides(**values):
    _runtime_overrides.update(values)

def get_settings():
    settings = Settings(
        ENV=os.getenv('ENV','dev'),
        TRADE_MODE=os.getenv('TRADE_MODE','dry'),
        DB_URL=os.getenv('DB_URL','sqlite:///lavish_store.db'),
        DEBUG=os.getenv('DEBUG','false').lower() in ('1','true','yes','on'),
        ALPACA_BASE_URL=os.getenv('ALPACA_BASE_URL','https://paper-api.alpaca.markets'),
        ALPACA_API_KEY=os.getenv('ALPACA_API_KEY',''),
        ALPACA_SECRET_KEY=os.getenv('ALPACA_SECRET_KEY',''),
        NEWS_MIN_ROWS=int(os.getenv('NEWS_MIN_ROWS','120')),
        NEWS_TIMEOUT=int(os.getenv('NEWS_TIMEOUT','10')),
        NEWS_FAIL_CUTOFF=int(os.getenv('NEWS_FAIL_CUTOFF','3')),
        CSV_ORDERS=Path(os.getenv('CSV_ORDERS','data/trades.csv')),
        CSV_SIGNALS=Path(os.getenv('CSV_SIGNALS','data/signal_scores.csv')),
    )
    settings.update(_runtime_overrides)
    if not settings.DB_URL:
        raise ValueError('Missing DB_URL')
    for name in ('NEWS_MIN_ROWS','NEWS_TIMEOUT','NEWS_FAIL_CUTOFF'):
        value = settings[name]
        if isinstance(value,bool) or not isinstance(value,int) or value <= 0:
            raise ValueError(f'{name} must be a positive integer')
    return settings
