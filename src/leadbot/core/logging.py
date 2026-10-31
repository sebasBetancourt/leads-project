import json
import logging

# Librerías que registran cada petición en INFO: solo se ven sus advertencias (o todo con --verbose).
NOISY_LOGGERS = ("LiteLLM", "litellm", "primp", "ddgs", "httpx", "httpcore")
STANDARD_ATTRS = set(logging.LogRecord("", 0, "", 0, "", (), None).__dict__) | {"message", "asctime", "taskName"}


class JsonFormatter(logging.Formatter):
    """Un objeto JSON por línea. Lo que se pasa en `extra=` (company_id, message_id, job_id...) queda como campo."""

    def format(self, record: logging.LogRecord) -> str:
        entry = {
            "ts": self.formatTime(record, "%Y-%m-%dT%H:%M:%S%z"),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        entry |= {k: v for k, v in record.__dict__.items() if k not in STANDARD_ATTRS}
        if record.exc_info:
            entry["exc"] = self.formatException(record.exc_info)
        return json.dumps(entry, ensure_ascii=False, default=str)


def setup_logging(level: str = "INFO", fmt: str = "text") -> None:
    handler = logging.StreamHandler()
    if fmt == "json":
        handler.setFormatter(JsonFormatter())
    else:
        handler.setFormatter(
            logging.Formatter("%(asctime)s %(levelname)-7s %(name)s: %(message)s", datefmt="%Y-%m-%d %H:%M:%S")
        )
    logging.basicConfig(level=level, handlers=[handler], force=True)
    if level != "DEBUG":
        for name in NOISY_LOGGERS:
            logging.getLogger(name).setLevel(logging.WARNING)
        import litellm

        litellm.suppress_debug_info = True  # el banner "Give Feedback / Get Help" en cada error
