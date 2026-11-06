"""A-share intraday real-time quote MCP server.

The package has no third-party runtime dependencies: it talks to public quote
endpoints over HTTPS with ``urllib`` and speaks the MCP stdio protocol with
newline-delimited JSON-RPC 2.0.
"""

__version__ = "0.1.0"

__all__ = ["__version__"]
