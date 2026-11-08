"""textkit: tiny text utilities (the Code Tower sandbox's practice codebase).

Every module in this package lists its public functions in its own __all__, and this file exports them
automatically. So a new function is a new module plus its tests, and parallel rows never edit the same file.
"""
__all__ = []


def _export_all():
    # The import machinery stays local to this function, so nothing but the exported functions (and the submodules)
    # is left on the package for row code to reach.
    from importlib import import_module
    from pkgutil import iter_modules

    for info in sorted(iter_modules(__path__), key=lambda m: m.name):
        module = import_module(f"{__name__}.{info.name}")
        for name in getattr(module, "__all__", ()):
            globals()[name] = getattr(module, name)
            __all__.append(name)


_export_all()
del _export_all
