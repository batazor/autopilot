"""Entry point: `python -m modules.notify` (or `uv run python -m modules.notify`).

Starts the FastAPI app via uvicorn. The monitor thread is launched in the
app's lifespan startup hook, so this single process runs both the poller and
the web UI.
"""

from __future__ import annotations

import argparse
import os

import uvicorn

from .logging_setup import setup_logging


def main() -> None:
    setup_logging()
    parser = argparse.ArgumentParser(description="Notification monitoring service")
    parser.add_argument("--host", default=os.environ.get("NM_HOST", "127.0.0.1"))
    parser.add_argument("--port", type=int, default=int(os.environ.get("NM_PORT", "8800")))
    parser.add_argument("--reload", action="store_true", help="dev auto-reload")
    args = parser.parse_args()

    uvicorn.run(
        "modules.notify.app:app",
        host=args.host,
        port=args.port,
        reload=args.reload,
        log_level="info",
    )


if __name__ == "__main__":
    main()
