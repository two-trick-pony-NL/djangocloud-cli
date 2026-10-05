"""Command-line client for DjangoCloud."""

try:
    from ._version import __version__  # written at build time from the git tag
except ImportError:  # running from a checkout that was never built
    __version__ = "0.0.0+unknown"
