"""Load processing settings only when requested by the modern environment.

The Python 3.10 recorder imports secrets without importing tomllib/settings.
"""


def __getattr__(name):
    if name in {'Settings', 'load_settings'}:
        from bilive.config import settings
        return getattr(settings, name)
    raise AttributeError(name)

__all__ = ["Settings", "load_settings"]
