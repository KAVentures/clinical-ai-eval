"""Web product shell — a local operator console over the existing kernel.

Deliberately small and deliberately honest about what it is:

  * **Local, single-tenant, stdlib only.** It binds 127.0.0.1 by default and has no
    accounts, no roles, and no authentication. That is a real limitation, stated
    here rather than implied away: anyone who can reach the port can read every run
    and submit review answers as any reviewer whose packet they hold. Do not expose
    it to a network and do not describe it as multi-tenant or access-controlled.
  * **Read-mostly.** The shell renders artifacts the CLI produced and accepts
    review submissions. It cannot start a run, raise a maturity level, or alter a
    claim — every guard stays in the kernel, where it is unit-tested, rather than
    being re-implemented in a view.
  * **Every number carries its authority.** A rate rendered without its maturity,
    conformance level and claim scope is how an experimental fixture ends up in a
    slide deck. The templates refuse to render a headline without them.
"""
from .server import build_app, serve  # noqa: F401
