"""A tiny, dependency-free Prometheus-style metrics registry.

Avoids pulling in prometheus_client for a handful of series. Supports counters
and summaries (sum + count); ``render()`` emits the text exposition format
scrapers expect at /metrics. Process-local (like the default rate limiter); a
multi-worker deployment should scrape each worker or move to a shared exporter.
"""

from __future__ import annotations

import threading

_lock = threading.Lock()
_counters: dict[tuple[str, tuple], float] = {}
_summaries: dict[tuple[str, tuple], tuple[float, int]] = {}  # (sum, count)
_HELP = {
    "ascore_http_requests_total": "HTTP requests handled, by method and status.",
    "ascore_http_request_duration_seconds": "HTTP request duration in seconds.",
    "ascore_runs_total": "Completed scoring runs (scorecards), by outcome.",
    "ascore_llm_cost_usd_total": "Total LLM spend recorded (execution + scoring).",
    "ascore_llm_tokens_total": "LLM tokens used, by component (agent/judge) and kind.",
}


def _labels(d: dict | None) -> tuple:
    return tuple(sorted((d or {}).items()))


def inc_counter(name: str, labels: dict | None = None, value: float = 1.0) -> None:
    with _lock:
        key = (name, _labels(labels))
        _counters[key] = _counters.get(key, 0.0) + value


def observe(name: str, value: float, labels: dict | None = None) -> None:
    with _lock:
        key = (name, _labels(labels))
        s, c = _summaries.get(key, (0.0, 0))
        _summaries[key] = (s + value, c + 1)


# -- domain helpers ---------------------------------------------------------

def record_http(method: str, status: int, duration_s: float) -> None:
    inc_counter("ascore_http_requests_total",
                {"method": method, "status": str(status)})
    observe("ascore_http_request_duration_seconds", duration_s,
            {"method": method})


def record_run(status: str) -> None:
    inc_counter("ascore_runs_total", {"status": status})


def record_cost(usd: float) -> None:
    if usd:
        inc_counter("ascore_llm_cost_usd_total", None, usd)


def record_tokens(component: str, tokens_in: int | None,
                  tokens_out: int | None) -> None:
    if tokens_in:
        inc_counter("ascore_llm_tokens_total",
                    {"component": component, "kind": "input"}, tokens_in)
    if tokens_out:
        inc_counter("ascore_llm_tokens_total",
                    {"component": component, "kind": "output"}, tokens_out)


def reset() -> None:  # tests
    with _lock:
        _counters.clear()
        _summaries.clear()


def _fmt_labels(labels: tuple) -> str:
    if not labels:
        return ""
    inner = ",".join(f'{k}="{v}"' for k, v in labels)
    return "{" + inner + "}"


def render() -> str:
    lines: list[str] = []
    with _lock:
        counters = dict(_counters)
        summaries = dict(_summaries)
    emitted: set[str] = set()
    for (name, labels), val in sorted(counters.items()):
        if name not in emitted and name in _HELP:
            lines.append(f"# HELP {name} {_HELP[name]}")
            lines.append(f"# TYPE {name} counter")
            emitted.add(name)
        lines.append(f"{name}{_fmt_labels(labels)} {val}")
    for (name, labels), (s, c) in sorted(summaries.items()):
        if name not in emitted and name in _HELP:
            lines.append(f"# HELP {name} {_HELP[name]}")
            lines.append(f"# TYPE {name} summary")
            emitted.add(name)
        lines.append(f"{name}_sum{_fmt_labels(labels)} {s}")
        lines.append(f"{name}_count{_fmt_labels(labels)} {c}")
    return "\n".join(lines) + "\n"
