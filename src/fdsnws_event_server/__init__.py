"""FDSNWS Event MCP Server for INGV earthquake data."""

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("mcp-fdsnws-event-server")
except PackageNotFoundError:  # package not installed (e.g. running from source tree)
    __version__ = "0.0.0+unknown"
