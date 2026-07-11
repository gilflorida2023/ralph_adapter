"""
Normalizer plugin registry.
Auto-discovers all normalizer .py files in this directory.
Each module must export NAME (str) and normalize(text: str) -> str.
"""
import importlib
import pathlib

_REGISTRY = None


def discover():
    global _REGISTRY
    if _REGISTRY is not None:
        return _REGISTRY
    _REGISTRY = {}
    pkg_dir = pathlib.Path(__file__).parent
    for f in sorted(pkg_dir.glob("*.py")):
        if f.name == "__init__.py":
            continue
        mod = importlib.import_module(f"normalizers.{f.stem}")
        if hasattr(mod, "NAME") and hasattr(mod, "normalize"):
            _REGISTRY[mod.NAME] = mod.normalize
    return _REGISTRY


def get(names):
    """Return ordered list of normalizer functions by name."""
    reg = discover()
    return [reg[n] for n in names if n in reg]


def apply_all(raw, names):
    """Apply normalizers in order, passing output of one to the next."""
    for fn in get(names):
        raw = fn(raw)
    return raw


def available():
    """Return list of normalizer names and descriptions."""
    discover()
    result = []
    pkg_dir = pathlib.Path(__file__).parent
    for f in sorted(pkg_dir.glob("*.py")):
        if f.name == "__init__.py":
            continue
        mod = importlib.import_module(f"normalizers.{f.stem}")
        result.append({
            "name": getattr(mod, "NAME", f.stem),
            "description": getattr(mod, "DESCRIPTION", ""),
        })
    return result
