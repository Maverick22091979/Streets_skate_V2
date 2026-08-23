import copy
import logging
import os

from dotenv import load_dotenv
import uvicorn
from uvicorn.config import LOGGING_CONFIG


class _LocalhostStartupFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        msg = record.getMessage()
        if "Uvicorn running on http://0.0.0.0:" in msg:
            record.msg = msg.replace("http://0.0.0.0:", "http://localhost:")
            record.args = ()
        return True

load_dotenv()


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}

if __name__ == "__main__":
    host = os.getenv("HOST", "127.0.0.1")
    port = int(os.getenv("PORT", "5000"))
    reload_enabled = _env_bool("UVICORN_RELOAD", True)
    log_config = copy.deepcopy(LOGGING_CONFIG)
    log_config.setdefault("filters", {})
    log_config["filters"]["localhost_startup"] = {"()": _LocalhostStartupFilter}
    for h in ("default",):
        if h in log_config.get("handlers", {}):
            log_config["handlers"][h]["filters"] = ["localhost_startup"]
    uvicorn.run(
        "app.main:app",
        host=host,
        port=port,
        reload=reload_enabled,
        reload_dirs=["app"] if reload_enabled else None,
        log_config=log_config,
    )
