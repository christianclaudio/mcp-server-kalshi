"""MCP Server implementation for Kalshi prediction market endpoints."""

from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as _dist_version

# The git tag is the version (uv-dynamic-versioning); never hard-code it here.
try:
    __version__ = _dist_version("mcp-server-kalshi")
except PackageNotFoundError:  # source tree without an installed distribution
    __version__ = "0.0.0"
