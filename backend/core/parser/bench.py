"""Benchmark harness for the parser.

The point of this file is to make performance a *recorded* number rather than
an impression. `--record` appends one JSON object per run to a JSONL file, so
there is a history to diff against when a stage is rewritten — and, eventually,
when the hot loop moves to C++.

    python -m core.parser.bench all --lines 1000000
    python -m core.parser.bench gen --lines 1000000 --format jsonl --out logs.jsonl
    python -m core.parser.bench run --file logs.jsonl --record bench.jsonl --compare
    python -m core.parser.bench micro

The generated corpus is deterministic for a given seed and shaped like a real
incident: a steady baseline, a heartbeat, and a failure that starts partway
through and drags several services down with it. That matters more than raw
line count — a synthetic corpus of one repeated line would make the clustering
stage look free when it is not.
"""

from __future__ import annotations

import argparse
import json
import os
import random
import sys
import time
from typing import Iterator

from core.parser import ingest as ingest_mod
from core.parser import timestamps
from core.parser.ingest import ingest_file
from core.parser.model import severity_text
from core.parser.timing import Timings

# --------------------------------------------------------------------------
# Synthetic corpus
# --------------------------------------------------------------------------

#: (service, level, template). `{}` slots are filled from the pools below.
_BASELINE: tuple[tuple[str, str, str], ...] = (
    ("api-gateway", "INFO", "Request {method} {path} completed status=200 in {ms}ms"),
    ("api-gateway", "INFO", "Incoming request id={uuid} from {ip}"),
    ("api-gateway", "DEBUG", "Route {path} matched handler={word}Handler"),
    ("auth", "INFO", "User {uuid} authenticated via {word}"),
    ("auth", "DEBUG", "Session {uuid} refreshed ttl={n}s"),
    ("auth", "WARN", "Token for user {uuid} expires in {n}s"),
    ("cart", "DEBUG", "Cache lookup key=cart:{uuid} hit=true"),
    ("cart", "INFO", "Cart {uuid} updated items={n}"),
    ("checkout", "INFO", "Order {order} placed total={amount} currency=USD"),
    ("checkout", "DEBUG", "Validating order {order} against inventory"),
    ("inventory", "INFO", "Stock check sku={sku} available={n}"),
    ("inventory", "DEBUG", "Reserved {n} units of sku={sku}"),
    ("payment", "INFO", "Charge {uuid} authorized amount={amount}"),
    ("payment", "DEBUG", "Tokenizing card for order {order}"),
    ("db-proxy", "DEBUG", "Query executed in {ms}ms rows={n}"),
    ("db-proxy", "DEBUG", "Prepared statement cache hit for {word}_by_id"),
    ("db-proxy", "INFO", "Connection pool size=50 idle={n} active={n}"),
    ("notification", "INFO", "Queued email to user {uuid} template={word}"),
    ("notification", "DEBUG", "SMTP handshake with mail-{n}.internal ok"),
    ("search", "INFO", "Index query q={word} results={n} took={ms}ms"),
    ("search", "DEBUG", "Segment merge completed in {ms}ms"),
    ("recommendation", "INFO", "Scored {n} candidates for user {uuid}"),
)

#: Fired only during the injected incident window.
_INCIDENT: tuple[tuple[str, str, str], ...] = (
    ("db-proxy", "ERROR", "Connection to pg-primary timed out after {ms}ms"),
    ("db-proxy", "ERROR", "Pool exhausted, {n} requests waiting"),
    ("db-proxy", "WARN", "Query executed in {ms}ms rows={n} (slow)"),
    ("checkout", "ERROR", "Downstream call to payment failed: deadline exceeded"),
    ("checkout", "WARN", "Retrying order {order} attempt={n}"),
    ("payment", "ERROR", "Upstream timeout contacting acquirer host=acq-{n}.psp.net"),
    ("api-gateway", "WARN", "Request {method} {path} completed status=503 in {ms}ms"),
    ("cart", "ERROR", "Cache lookup key=cart:{uuid} failed: connection reset"),
)

#: One every `_HEARTBEAT_EVERY` records, so the cadence detector in stage 2 has
#: something with a known, exact period to find.
_HEARTBEAT = ("healthcheck", "INFO", "Health check OK")
_HEARTBEAT_EVERY = 200

_STACK = (
    "java.sql.SQLTransientConnectionException: HikariPool-1 - Connection is not available",
    "\tat com.zaxxer.hikari.pool.HikariPool.createTimeoutException(HikariPool.java:696)",
    "\tat com.zaxxer.hikari.pool.HikariPool.getConnection(HikariPool.java:197)",
    "\tat com.example.checkout.OrderRepository.save(OrderRepository.java:88)",
    "Caused by: java.net.SocketTimeoutException: connect timed out",
    "\tat java.base/java.net.Socket.connect(Socket.java:633)",
    "\t... 24 more",
)

_METHODS = ("GET", "POST", "PUT", "DELETE", "PATCH")
_PATHS = ("/api/cart", "/api/checkout", "/api/products", "/api/users/me", "/api/search", "/healthz")
_WORDS = ("oauth", "saml", "password", "welcome", "receipt", "order", "product", "user")


def _pools(rng: random.Random) -> dict[str, list[str]]:
    """Precomputed value pools.

    Generating a fresh UUID per line would make the *generator* the slowest
    thing in the benchmark; a pool sampled at random produces the same
    cardinality characteristics for a fraction of the cost.
    """
    return {
        "uuid": [
            "%08x-%04x-11ee-%04x-%012x"
            % (rng.getrandbits(32), rng.getrandbits(16), rng.getrandbits(16), rng.getrandbits(48))
            for _ in range(4096)
        ],
        "ip": ["10.%d.%d.%d" % (rng.randrange(256), rng.randrange(256), rng.randrange(256)) for _ in range(512)],
        "order": ["ORD-%07d" % rng.randrange(10_000_000) for _ in range(2048)],
        "sku": ["SKU-%05d" % rng.randrange(100_000) for _ in range(1024)],
    }


def _fill(template: str, rng: random.Random, pools: dict[str, list[str]]) -> str:
    if "{" not in template:
        return template
    return template.format(
        method=rng.choice(_METHODS),
        path=rng.choice(_PATHS),
        word=rng.choice(_WORDS),
        uuid=rng.choice(pools["uuid"]),
        ip=rng.choice(pools["ip"]),
        order=rng.choice(pools["order"]),
        sku=rng.choice(pools["sku"]),
        ms=rng.randrange(1, 5000),
        n=rng.randrange(1, 500),
        amount="%d.%02d" % (rng.randrange(5, 500), rng.randrange(100)),
    )


def generate(count: int, fmt: str = "text", seed: int = 1) -> Iterator[str]:
    """Yield `count` log lines (plus stack-trace continuations, which are extra).

    The incident starts at 60% through and ramps, so error volume climbs rather
    than stepping — closer to how a pool exhaustion actually looks, and harder
    for the later stages than a clean step change would be.
    """
    rng = random.Random(seed)
    pools = _pools(rng)
    start_ns = 1_756_512_000_000_000_000  # 2025-08-30T00:00:00Z, fixed for reproducibility
    # Spread the run over a plausible window rather than a fixed rate, so
    # bucketing has something realistic to bin.
    step_ns = max(1_000, 3_600 * 1_000_000_000 // max(count, 1))
    incident_at = int(count * 0.6)

    for i in range(count):
        ts = start_ns + i * step_ns + rng.randrange(step_ns or 1)

        if i % _HEARTBEAT_EVERY == 0:
            service, level, template = _HEARTBEAT
        elif i >= incident_at and rng.random() < 0.15 + 0.35 * (i - incident_at) / max(count - incident_at, 1):
            service, level, template = rng.choice(_INCIDENT)
        else:
            service, level, template = rng.choice(_BASELINE)

        message = _fill(template, rng, pools)
        yield _render(fmt, ts, level, service, message)

        # A stack trace every so often during the incident, to exercise the
        # continuation folding path.
        if level == "ERROR" and rng.random() < 0.05:
            if fmt == "text":
                for frame in _STACK:
                    yield frame + "\n"


def _iso(ts: int) -> str:
    secs, nanos = divmod(ts, 1_000_000_000)
    stamp = time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(secs))
    return f"{stamp}.{nanos // 1000:06d}Z"


def _render(fmt: str, ts: int, level: str, service: str, message: str) -> str:
    if fmt == "jsonl":
        return json.dumps(
            {
                "timestamp": _iso(ts),
                "level": level,
                "service.name": service,
                "message": message,
                "trace_id": "%032x" % (ts & ((1 << 128) - 1)),
                "host": "node-7",
            },
            separators=(",", ":"),
        ) + "\n"
    return f"{_iso(ts)} {level:<5} [{service}] {message}\n"


def write_corpus(path: str, count: int, fmt: str, seed: int = 1) -> tuple[int, int]:
    """Write a corpus to `path`. Returns (lines written, bytes)."""
    lines = 0
    with open(path, "w", encoding="utf-8") as handle:
        write = handle.write
        for line in generate(count, fmt, seed):
            write(line)
            lines += 1
    return lines, os.path.getsize(path)


def write_otlp(path: str, count: int, seed: int = 1) -> tuple[int, int]:
    """Write an OTLP/JSON document. One document, so this is memory-bound."""
    rng = random.Random(seed)
    pools = _pools(rng)
    start_ns = 1_756_512_000_000_000_000
    step_ns = max(1_000, 3_600 * 1_000_000_000 // max(count, 1))
    by_service: dict[str, list[dict]] = {}

    for i in range(count):
        ts = start_ns + i * step_ns
        if i % _HEARTBEAT_EVERY == 0:
            service, level, template = _HEARTBEAT
        elif i >= int(count * 0.6) and rng.random() < 0.3:
            service, level, template = rng.choice(_INCIDENT)
        else:
            service, level, template = rng.choice(_BASELINE)
        by_service.setdefault(service, []).append(
            {
                "timeUnixNano": str(ts),
                "severityNumber": ingest_mod._SEVERITY[level],
                "severityText": level,
                "body": {"stringValue": _fill(template, rng, pools)},
                "attributes": [{"key": "host", "value": {"stringValue": "node-7"}}],
                "traceId": "%032x" % (ts & ((1 << 128) - 1)),
            }
        )

    document = {
        "resourceLogs": [
            {
                "resource": {
                    "attributes": [{"key": "service.name", "value": {"stringValue": service}}]
                },
                "scopeLogs": [{"scope": {"name": service}, "logRecords": records}],
            }
            for service, records in by_service.items()
        ]
    }
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(document, handle, separators=(",", ":"))
    return count, os.path.getsize(path)


# --------------------------------------------------------------------------
# Runs
# --------------------------------------------------------------------------

def run_once(path: str, label: str = "", keep_attributes: bool = False) -> tuple[Timings, object]:
    timings = Timings(label or f"ingest {os.path.basename(path)}")
    batch = ingest_file(path, keep_attributes=keep_attributes, timings=timings)
    return timings, batch


def compare_legacy(path: str, limit: int) -> float | None:
    """Time `core.logs.normalize` on the same input, for a before/after number.

    Its 5,000-line cap is raised for the duration — the cap is a product
    decision about what to send a model, not a property of the parser, and
    leaving it in place would compare a full run against a truncated one.
    """
    try:
        from core import logs
    except Exception:
        return None

    with open(path, "r", encoding="utf-8", errors="replace") as handle:
        text = handle.read()

    saved = logs.MAX_LINES, logs.MAX_BYTES
    logs.MAX_LINES, logs.MAX_BYTES = limit * 4, len(text.encode()) + 1
    try:
        start = time.perf_counter_ns()
        logs.normalize(text)
        return (time.perf_counter_ns() - start) / 1e6
    finally:
        logs.MAX_LINES, logs.MAX_BYTES = saved


def micro(iterations: int = 200_000) -> None:
    """Per-extractor costs, measured in isolation.

    The main loop cannot report this — a timer pair around each field would
    cost more than the field extraction does. Running each extractor standalone
    over a representative line gives the attribution without the distortion.
    """
    line = "2025-08-30T10:00:01.123456Z ERROR [db-proxy] Connection to pg-primary timed out after 5000ms"
    body = "Connection to pg-primary timed out after 5000ms"
    payload = json.dumps(
        {
            "timestamp": "2025-08-30T10:00:01.123456Z",
            "level": "ERROR",
            "service.name": "db-proxy",
            "message": body,
            "trace_id": "0af7651916cd43dd8448eb211c80319c",
        },
        separators=(",", ":"),
    )

    from datetime import datetime

    cases = [
        ("fast prefix (ts+lvl+src)", lambda: ingest_mod._FAST_PREFIX.match(line)),
        ("timestamps.scan (iso)", lambda: timestamps.scan(line)),
        ("datetime.fromisoformat", lambda: datetime.fromisoformat("2025-08-30T10:00:01.123456+00:00")),
        ("level match (anchored)", lambda: ingest_mod._LEVEL_AT.match(line, 28)),
        ("source match (anchored)", lambda: ingest_mod._SOURCE_AT.match(line, 34)),
        ("level_in_head (fallback)", lambda: ingest_mod.level_in_head(body)),
        ("body slice", lambda: line[44:]),
        ("line.rstrip", lambda: line.rstrip()),
        ("json.loads", lambda: json.loads(payload)),
    ]

    print(f"{'operation':<28}{'ns/op':>10}{'M ops/s':>12}")
    print("-" * 50)
    for name, fn in cases:
        # One warm pass so the first-call overhead (regex cache, day cache) is
        # not attributed to the measurement.
        for _ in range(1000):
            fn()
        start = time.perf_counter_ns()
        for _ in range(iterations):
            fn()
        elapsed = time.perf_counter_ns() - start
        per_op = elapsed / iterations
        print(f"{name:<28}{per_op:>10.1f}{1000 / per_op:>12.2f}")


def _summarize(batch, timings: Timings) -> None:
    stats = batch.stats
    print(timings.report())
    print()
    print(f"{'format':<24}{stats.format:>10}")
    print(f"{'records':<24}{stats.records:>10,}")
    print(f"{'continuations folded':<24}{stats.lines_continuation:>10,}")
    print(f"{'unparsed':<24}{stats.lines_unparsed:>10,}")
    print(f"{'with timestamp':<24}{_pct(stats.with_timestamp, stats.records):>10}")
    print(f"{'with severity':<24}{_pct(stats.with_severity, stats.records):>10}")
    print(f"{'with trace id':<24}{_pct(stats.with_trace, stats.records):>10}")
    print(f"{'distinct sources':<24}{len(batch.sources):>10,}")
    print(f"{'retained columns':<24}{batch.memory_bytes() / 1e6:>9,.1f}M")

    first, last = batch.time_span()
    if first:
        span = (last - first) / 1e9
        print(f"{'time span':<24}{span:>9,.1f}s")

    if len(batch):
        print()
        print("first record:", _one(batch, 0))
        print("last  record:", _one(batch, len(batch) - 1))


def _one(batch, i: int) -> str:
    r = batch.record(i)
    return f"[{r.timestamp}] {severity_text(r.severity):<5} {r.source}: {r.body[:70]}"


def _pct(part: int, whole: int) -> str:
    return f"{part:,} ({part * 100 // whole if whole else 0}%)"


def _record_result(path: str, timings: Timings, batch, extra: dict) -> None:
    row = timings.to_dict()
    row["ts"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    row["stats"] = batch.stats.as_dict()
    row.update(extra)
    with open(path, "a", encoding="utf-8") as handle:
        handle.write(json.dumps(row) + "\n")
    print(f"\nappended to {path}")


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="core.parser.bench", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    gen = sub.add_parser("gen", help="write a synthetic corpus")
    gen.add_argument("--lines", type=int, default=1_000_000)
    gen.add_argument("--format", choices=("text", "jsonl", "otlp"), default="text")
    gen.add_argument("--out", required=True)
    gen.add_argument("--seed", type=int, default=1)

    run = sub.add_parser("run", help="ingest a file and report timings")
    run.add_argument("--file", required=True)
    run.add_argument("--format", default="")
    run.add_argument("--attributes", action="store_true", help="keep the attributes column")
    run.add_argument("--record", default="", help="append the result to this JSONL file")
    run.add_argument("--compare", action="store_true", help="also time core.logs.normalize")
    run.add_argument("--repeat", type=int, default=1)

    sub.add_parser("micro", help="per-extractor costs")

    every = sub.add_parser("all", help="generate and run every format")
    every.add_argument("--lines", type=int, default=1_000_000)
    every.add_argument("--dir", default="")
    every.add_argument("--record", default="")
    every.add_argument("--compare", action="store_true")

    args = parser.parse_args(argv)

    if args.command == "gen":
        start = time.perf_counter_ns()
        if args.format == "otlp":
            lines, size = write_otlp(args.out, args.lines, args.seed)
        else:
            lines, size = write_corpus(args.out, args.lines, args.format, args.seed)
        elapsed = (time.perf_counter_ns() - start) / 1e9
        print(f"wrote {lines:,} lines / {size / 1e6:.1f} MB to {args.out} in {elapsed:.1f}s")
        return 0

    if args.command == "micro":
        micro()
        return 0

    if args.command == "run":
        for attempt in range(args.repeat):
            label = f"ingest {os.path.basename(args.file)}"
            if args.repeat > 1:
                label += f" (run {attempt + 1}/{args.repeat})"
            timings = Timings(label)
            batch = ingest_file(
                args.file, fmt=args.format, keep_attributes=args.attributes, timings=timings
            )
            _summarize(batch, timings)
            if args.compare:
                legacy = compare_legacy(args.file, batch.stats.records)
                if legacy is not None:
                    new_ms = timings.total_ns / 1e6
                    print()
                    print(f"{'core.logs.normalize':<24}{legacy:>9,.1f}ms")
                    print(f"{'core.parser.ingest':<24}{new_ms:>9,.1f}ms")
                    print(f"{'speedup':<24}{legacy / new_ms:>9,.2f}x")
            if args.record:
                _record_result(args.record, timings, batch, {"file": args.file})
            print()
        return 0

    if args.command == "all":
        import subprocess

        directory = args.dir or os.environ.get("TMPDIR", "/tmp")
        for fmt in ("text", "jsonl"):
            path = os.path.join(directory, f"bench-{fmt}-{args.lines}.log")
            if not os.path.exists(path):
                print(f"generating {fmt}...", flush=True)
                write_corpus(path, args.lines, fmt)
            # Each format runs in its own interpreter. Peak RSS is a process
            # high-water mark, so a second run in the same process would report
            # a memory delta of zero no matter what it allocated.
            command = [sys.executable, "-m", "core.parser.bench", "run", "--file", path]
            if args.record:
                command += ["--record", args.record]
            if args.compare and fmt == "text":
                command += ["--compare"]
            subprocess.run(command, check=False)
        return 0

    return 1


if __name__ == "__main__":
    sys.exit(main())
