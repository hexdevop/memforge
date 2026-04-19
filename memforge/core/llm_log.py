"""LLM call logger — writes structured records to logs/llm.jsonl."""

from __future__ import annotations

import json
import logging
import time
from collections.abc import Generator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path

log = logging.getLogger(__name__)

_COSTS_PER_1M: dict[str, tuple[float, float]] = {
    "claude-opus-4-7": (5.0, 25.0),
    "claude-opus-4-6": (5.0, 25.0),
    "claude-sonnet-4-6": (3.0, 15.0),
    "claude-haiku-4-5": (1.0, 5.0),
}


def _cost_usd(model: str, input_tokens: int, output_tokens: int) -> float:
    in_rate, out_rate = _COSTS_PER_1M.get(model, (3.0, 15.0))
    return (input_tokens * in_rate + output_tokens * out_rate) / 1_000_000


@contextmanager
def record_call(
    log_dir: Path,
    model: str,
    prompt_version: str,
    operation: str = "extract",
) -> Generator[dict, None, None]:  # type: ignore[type-arg]
    """Context manager that records a timed LLM call to llm.jsonl.

    Usage:
        with record_call(store.logs, model, "v1") as meta:
            response = client.messages.create(...)
            meta["input_tokens"] = response.usage.input_tokens
            meta["output_tokens"] = response.usage.output_tokens
    """
    entry: dict = {  # type: ignore[type-arg]
        "timestamp": datetime.now(UTC).isoformat(),
        "operation": operation,
        "model": model,
        "prompt_version": prompt_version,
        "input_tokens": 0,
        "output_tokens": 0,
        "cost_usd": 0.0,
        "latency_ms": 0,
        "error": None,
    }
    t0 = time.monotonic()
    try:
        yield entry
    except Exception as exc:
        entry["error"] = str(exc)
        raise
    finally:
        entry["latency_ms"] = int((time.monotonic() - t0) * 1000)
        entry["cost_usd"] = _cost_usd(model, entry["input_tokens"], entry["output_tokens"])
        _append(log_dir, entry)


def _append(log_dir: Path, entry: dict) -> None:  # type: ignore[type-arg]
    try:
        log_dir.mkdir(parents=True, exist_ok=True)
        jsonl_path = log_dir / "llm.jsonl"
        with jsonl_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except Exception as exc:
        log.warning("Failed to write llm.jsonl: %s", exc)


def read_log(log_dir: Path) -> list[dict]:  # type: ignore[type-arg]
    p = log_dir / "llm.jsonl"
    if not p.exists():
        return []
    entries = []
    for line in p.read_text("utf-8").splitlines():
        line = line.strip()
        if line:
            try:
                entries.append(json.loads(line))
            except json.JSONDecodeError:
                pass
    return entries


def summarise(entries: list[dict], since_days: int = 30) -> dict:  # type: ignore[type-arg]
    """Return aggregate stats for UI / mem stats display."""
    from datetime import timedelta

    cutoff = datetime.now(UTC) - timedelta(days=since_days)
    recent = [e for e in entries if datetime.fromisoformat(e["timestamp"]) >= cutoff]
    total_cost = sum(e.get("cost_usd", 0) for e in recent)
    total_calls = len(recent)
    total_tokens = sum(e.get("input_tokens", 0) + e.get("output_tokens", 0) for e in recent)
    by_op: dict[str, int] = {}
    for e in recent:
        op = e.get("operation", "unknown")
        by_op[op] = by_op.get(op, 0) + 1
    return {
        "calls": total_calls,
        "tokens": total_tokens,
        "cost_usd": round(total_cost, 4),
        "by_operation": by_op,
    }
