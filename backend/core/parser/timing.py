"""Stage timing for the parser pipeline.

Every stage — ingest, mask, cluster, bucket, matrix — reports through one
`Timings` object, so a run produces a single comparable record rather than
five ad-hoc `time.time()` prints. `to_dict()` is the machine-readable form;
append it to a JSONL file and you have a performance history to regress
against when the C++ port lands.

Deliberately coarse: timers wrap whole stages, never individual records. At a
million records a `perf_counter_ns()` pair per line would cost more than the
parsing it claims to measure. Per-field attribution comes from the micro
benchmarks in `bench.py`, which measure extractors in isolation instead.
"""

from __future__ import annotations

import platform
import resource
import sys
import time
from contextlib import contextmanager
from typing import Any, Iterator

#: macOS reports ru_maxrss in bytes, Linux in kilobytes.
_RSS_SCALE = 1 if sys.platform == "darwin" else 1024


def peak_rss_bytes() -> int:
    """Peak resident set size for this process, in bytes.

    Cheap enough to call freely (one getrusage syscall), unlike `tracemalloc`,
    which roughly halves throughput on an allocation-heavy loop like ingest.
    """
    return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss * _RSS_SCALE


class Timings:
    """Wall time per named stage, plus counters, for one pipeline run."""

    __slots__ = ("label", "stages", "order", "counters", "_rss_start")

    def __init__(self, label: str = "") -> None:
        self.label = label
        self.stages: dict[str, int] = {}  # name -> nanoseconds
        self.order: list[str] = []  # first-seen order, for reporting
        self.counters: dict[str, int] = {}
        self._rss_start = peak_rss_bytes()

    @contextmanager
    def stage(self, name: str) -> Iterator[None]:
        """Time a block. Re-entering the same name accumulates."""
        if name not in self.stages:
            self.stages[name] = 0
            self.order.append(name)
        start = time.perf_counter_ns()
        try:
            yield
        finally:
            self.stages[name] += time.perf_counter_ns() - start

    def count(self, name: str, n: int = 1) -> None:
        self.counters[name] = self.counters.get(name, 0) + n

    def set(self, name: str, n: int) -> None:
        self.counters[name] = n

    @property
    def total_ns(self) -> int:
        return sum(self.stages.values())

    @property
    def rss_delta_bytes(self) -> int:
        """Peak RSS growth since this object was created.

        Not the size of the result — the interpreter's high-water mark, which
        includes transient garbage. It is the number that predicts whether a
        given input size fits in a container, which is what matters here.
        """
        return max(0, peak_rss_bytes() - self._rss_start)

    def rate(self, counter: str, stage: str | None = None) -> float:
        """Counter units per second, over one stage or the whole run."""
        ns = self.stages.get(stage, 0) if stage else self.total_ns
        if not ns:
            return 0.0
        return self.counters.get(counter, 0) * 1e9 / ns

    def to_dict(self) -> dict[str, Any]:
        """A flat record suitable for appending to a results JSONL file."""
        records = self.counters.get("records", 0)
        total_ms = self.total_ns / 1e6
        return {
            "label": self.label,
            "python": platform.python_version(),
            "platform": f"{platform.system()}-{platform.machine()}",
            "total_ms": round(total_ms, 3),
            "stages_ms": {n: round(self.stages[n] / 1e6, 3) for n in self.order},
            "counters": dict(self.counters),
            "records_per_sec": round(self.rate("records"), 1),
            "mb_per_sec": round(self.rate("bytes") / 1e6, 2),
            "peak_rss_mb": round(self.rss_delta_bytes / 1e6, 1),
            "bytes_per_record": round(self.rss_delta_bytes / records, 1) if records else 0,
        }

    def report(self) -> str:
        """A human-readable block, for the terminal."""
        total = self.total_ns or 1
        lines = []
        if self.label:
            lines.append(self.label)
            lines.append("=" * max(len(self.label), 52))

        lines.append(f"{'stage':<24}{'ms':>10}{'%':>8}")
        lines.append("-" * 42)
        for name in self.order:
            ns = self.stages[name]
            lines.append(f"{name:<24}{ns / 1e6:>10.2f}{ns * 100 / total:>7.1f}%")
        lines.append("-" * 42)
        lines.append(f"{'total':<24}{total / 1e6:>10.2f}{100.0:>7.1f}%")

        if self.counters:
            lines.append("")
            for name in sorted(self.counters):
                lines.append(f"{name:<24}{self.counters[name]:>10,}")

        records = self.counters.get("records", 0)
        if records:
            lines.append("")
            lines.append(f"{'throughput':<24}{self.rate('records'):>10,.0f} rec/s")
            if "bytes" in self.counters:
                lines.append(f"{'':<24}{self.rate('bytes') / 1e6:>10,.1f} MB/s")
            lines.append(f"{'ns per record':<24}{total / records:>10,.0f} ns")
            rss = self.rss_delta_bytes
            lines.append(f"{'peak rss':<24}{rss / 1e6:>10,.1f} MB")
            lines.append(f"{'bytes per record':<24}{rss / records:>10,.0f} B")

        return "\n".join(lines)
