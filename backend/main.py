"""FastAPI app: REST routes, WebSocket endpoint, ConnectionManager.

Populated in T3.3–T3.4. Glue only: calls the engine on incoming messages,
hands scheduling to TimerService, broadcasts state snapshots after changes.
"""

from __future__ import annotations
