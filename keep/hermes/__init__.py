"""Keep integration for Hermes Agent.

Provides a duck-typed MemoryProvider that Hermes discovers via its plugin
system.  No import of Hermes code — the provider protocol is satisfied
by implementing the expected methods.

Usage from the hermes plugin shim (plugins/memory/keep/__init__.py):

    def register(ctx):
        from keep.hermes import KeepMemoryProvider
        ctx.register_memory_provider(KeepMemoryProvider())
"""

from keep.hermes.provider import KeepMemoryProvider

__all__ = ["KeepMemoryProvider"]
