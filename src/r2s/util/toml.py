"""TOML loading.

`tomllib` is stdlib from Python 3.11. On 3.10 the backport `tomli` is used, which is
declared as a conditional dependency for exactly that reason.

If neither is importable, parsing *fails loudly* rather than returning an empty dict.
Silently skipping `pyproject.toml` is the worst possible behaviour here: it makes the
tool miss every console script and every declared dependency, so a repo is
misclassified and its inventory comes back confidently wrong. That failure mode is
worse than refusing to run.
"""

try:
    import tomllib as _toml
except ModuleNotFoundError:  # pragma: no cover - Python 3.10 only
    try:
        import tomli as _toml
    except ModuleNotFoundError:
        _toml = None

TOML_AVAILABLE = _toml is not None

INSTALL_HINT = (
    "TOML parsing is unavailable: neither 'tomllib' (stdlib from Python 3.11) nor the "
    "'tomli' backport could be imported. Without it, pyproject.toml cannot be read, so "
    "console scripts and dependencies would be silently missed. Install tomli, or use "
    "Python 3.11+."
)


class TomlUnavailable(RuntimeError):
    """Raised when no TOML parser is importable."""


def loads(text):
    """Parse a TOML document. Raises TomlUnavailable if no parser exists."""
    if _toml is None:
        raise TomlUnavailable(INSTALL_HINT)
    return _toml.loads(text)
