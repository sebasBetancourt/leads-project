import logging

# Librerías que registran cada petición en INFO: solo se ven sus advertencias (o todo con --verbose).
NOISY_LOGGERS = ("LiteLLM", "litellm", "primp", "ddgs", "httpx", "httpcore")


def setup_logging(level: str = "INFO") -> None:
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    if level != "DEBUG":
        for name in NOISY_LOGGERS:
            logging.getLogger(name).setLevel(logging.WARNING)
        import litellm

        litellm.suppress_debug_info = True  # el banner "Give Feedback / Get Help" en cada error
