"""Parallel fan-out over fleet hosts."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Callable, TypeVar

from .inventory import FleetHost

T = TypeVar("T")


def fanout(
    hosts: list[FleetHost],
    worker: Callable[[FleetHost], T],
    *,
    concurrency: int = 8,
) -> list[T]:
    """Run ``worker`` on each host; preserve inventory order in the result list.

    ``concurrency`` is clamped to at least 1. Exceptions from ``worker`` propagate
    once all outstanding futures finish (the pool's context manager waits, it
    does not cancel them) — in practice ``worker`` callables here never raise,
    since ``run_remote`` folds SSH/timeout/parse failures into a result value.
    """
    if not hosts:
        return []
    workers = max(1, min(concurrency, len(hosts)))
    if workers == 1 or len(hosts) == 1:
        return [worker(h) for h in hosts]

    results: dict[str, T] = {}
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(worker, h): h.id for h in hosts}
        for fut in as_completed(futures):
            host_id = futures[fut]
            results[host_id] = fut.result()
    return [results[h.id] for h in hosts]
