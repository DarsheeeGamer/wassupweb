from __future__ import annotations

import logging
from typing import Any


class WassupLogger(logging.LoggerAdapter):
    def child(self, obj: dict[str, Any]) -> "WassupLogger":
        merged = dict(self.extra)
        merged.update(obj)
        return WassupLogger(self.logger, merged)

    def process(self, msg: Any, kwargs: dict[str, Any]) -> tuple[Any, dict[str, Any]]:
        extra = kwargs.get("extra", {})
        merged = dict(self.extra)
        merged.update(extra)
        kwargs["extra"] = merged
        return msg, kwargs


def get_logger(name: str = "wassupweb", level: str = "INFO") -> WassupLogger:
    logger = logging.getLogger(name)
    if not logger.handlers:
        handler = logging.StreamHandler()
        formatter = logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s")
        handler.setFormatter(formatter)
        logger.addHandler(handler)
    logger.setLevel(level.upper())
    return WassupLogger(logger, {})


logger = get_logger()
