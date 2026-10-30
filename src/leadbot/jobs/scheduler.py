"""Scheduler del worker (una sola instancia, como en AgentSDR: no hay lock distribuido).

| Tarea                  | Frecuencia     | Fase |
|------------------------|----------------|------|
| discover (gosom)       | diario 6:00    | F1   |
| enrich                 | cada 10 min    | F1   |
| sheets push            | cada 5 min     | F1   |
| qualify + draft        | cada 10 min    | F2   |
| sheets pull_decisions  | cada 5 min     | F2   |
| send email             | cada 1 min     | F3   |
| fetch replies (IMAP)   | cada 15 min    | F3   |
| follow-ups             | diario 7:00    | F3   |
"""


def run_worker() -> None:
    raise NotImplementedError("F1")
