"""textkit: tiny text utilities (the Code Tower sandbox's practice codebase).

Every module in this package lists its public functions in its own __all__, and this file exports them
automatically. So a new function is a new module plus its tests, and parallel rows never edit the same file.
"""
import importlib
import pkgutil

__all__ = []

for _info in sorted(pkgutil.iter_modules(__path__), key=lambda m: m.name):
    _module = importlib.import_module(f"{__name__}.{_info.name}")
    for _name in getattr(_module, "__all__", ()):
        globals()[_name] = getattr(_module, _name)
        __all__.append(_name)
