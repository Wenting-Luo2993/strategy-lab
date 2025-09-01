from enum import Enum
from typing import Dict, Type

class DataSource(Enum):
    YAHOO = "yahoo"
    INTERACTIVE_BROKERS = "ib"
    POLYGON = "polygon"


class Timeframe(Enum):
    MIN_1 = "1m"
    MIN_5 = "5m"
    MIN_15 = "15m"
    HOUR_1 = "1h"
    DAY_1 = "1d"
    WEEK_1 = "1w"


class DataLoader:
    def fetch(self, symbol: str, start: str, end: str):
        raise NotImplementedError


# Global registry
_data_registry: Dict[str, Type[DataLoader]] = {}


def register_data_source(name: DataSource):
    """Decorator for registering a new data loader class"""
    def wrapper(cls: Type[DataLoader]):
        _data_registry[name] = cls
        return cls
    return wrapper


class DataLoaderFactory:
    @staticmethod
    def create(source: DataSource, **kwargs) -> DataLoader:
        if source.value not in _data_registry:
            raise ValueError(f"Data source {source} is not registered")
        return _data_registry[source.value](**kwargs)

# Alias for easier decorator usage
register_loader = register_data_source