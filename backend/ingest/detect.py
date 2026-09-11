"""Stage 2 — decide what format a file is in.

Scoring, not first-match. Several patterns overlap: a Postgres line and a
plain ISO-timestamped line share a prefix, and CRI-wrapped output is a
timestamp followed by whatever the container actually wrote. Every candidate
scores itself over the same sample and the highest wins, which keeps the
tie-breaks in one readable table instead of spread across the order of a
chain of `if` statements.

Detection is a guess with a confidence attached, and the UI shows it as one.
The user can always override; `SourceFile.format` short-circuits everything
here.
"""

import json
import re
from dataclasses import dataclass

from ingest.limits import DETECT_SAMPLE_LINES

#: Every format a parser exists for. "plain" is the fallback and always scores
#: above zero, so detection never fails outright.
FORMATS: tuple[str, ...] = (
    "json",
    "cri",
    "postgres",
    "clf",
    "syslog5424",
    "syslog",
    "logfmt",
    "plain",
)

# --- Format signatures -------------------------------------------------------

#: `2026-08-04T14:58:11.004Z stdout F the actual line` — what `kubectl logs`
#: writes to disk under CRI, and what you get out of a node-level collector.
_CRI_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T[\d:.]+(?:Z|[+-]\d{2}:\d{2})\s+(?:stdout|stderr)\s+[FP]\s")

#: Postgres with the stock `log_line_prefix = '%m [%p] '`. The bracketed PID is
#: the whole reason to special-case this format: it is a session join key, and
#: it is the only one an uninstrumented database gives you.
_POSTGRES_RE = re.compile(
    r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}(?:[.,]\d+)?(?:\s*[A-Z]{2,5}|\s*[+-]\d{2})?\s*\[\d+\]"
)
_POSTGRES_TAG_RE = re.compile(
    r"\b(?:LOG|ERROR|FATAL|PANIC|WARNING|NOTICE|DETAIL|HINT|STATEMENT|CONTEXT):\s"
)

#: Common / combined log format: `host ident user [date] "METHOD path proto" status bytes`
_CLF_RE = re.compile(
    r'^\S+ \S+ \S+ \[[^\]]+\] "(?:GET|POST|PUT|PATCH|DELETE|HEAD|OPTIONS|TRACE|CONNECT)[^"]*" \d{3} '
)

#: RFC 5424 always starts with a priority and the version digit `1`.
_SYSLOG5424_RE = re.compile(r"^<\d{1,3}>1 ")

#: RFC 3164: optional priority, then `Aug  4 15:04:05 host tag[pid]:`
_SYSLOG3164_RE = re.compile(
    r"^(?:<\d{1,3}>)?[A-Z][a-z]{2}\s+\d{1,2} \d{2}:\d{2}:\d{2} \S+ [^\s:\[]+(?:\[\d+\])?:"
)

#: logfmt is a run of bare `key=value` pairs. Requiring three keeps it from
#: claiming plain lines that merely happen to mention `status=200`.
_LOGFMT_PAIR_RE = re.compile(r'(?:^|\s)([A-Za-z_][\w.\-]*)=("[^"]*"|\'[^\']*\'|[^\s"\']*)')


@dataclass(slots=True)
class Detection:
    format: str
    #: 0.0–1.0. Roughly "what share of the sample matched this format", so a
    #: file of mixed output detects as its majority format with a confidence
    #: that honestly reflects the mix.
    confidence: float
    #: For `format="json"`, which producer wrote it — this picks the field
    #: mapping. `None` for every other format.
    flavor: str | None = None
    #: A sample line that matched, for the "why did you think that" tooltip.
    evidence: str | None = None


# --- JSON flavors ------------------------------------------------------------


def _json_flavor(obj: dict) -> str:
    """Identify a JSON log producer from its key names.

    Ordered most specific → least: OTLP's envelope and GCP's fully-qualified
    keys are unmistakable, whereas `level`/`msg` is shared by half the
    ecosystem and has to come last.
    """
    if "resourceLogs" in obj or "resource_logs" in obj:
        return "otlp"
    if "logging.googleapis.com/trace" in obj or "insertId" in obj or "jsonPayload" in obj:
        return "gcp"
    if "severity" in obj and "resource" in obj and isinstance(obj.get("resource"), dict):
        return "gcp"
    if "dd" in obj or "dd.trace_id" in obj or "ddsource" in obj:
        return "datadog"
    if "ecs" in obj or "@timestamp" in obj and ("log.level" in obj or "log" in obj):
        return "ecs"
    # OTLP flattened by a collector's JSON exporter: a body plus severity text.
    if ("body" in obj or "Body" in obj) and ("severityText" in obj or "SeverityText" in obj):
        return "otlp"
    # Docker's json-file driver — exactly these three keys, nothing else.
    if "log" in obj and "stream" in obj and "time" in obj:
        return "docker"
    # Bunyan: numeric level plus its own format version.
    if "v" in obj and isinstance(obj.get("level"), int) and "msg" in obj:
        return "bunyan"
    if isinstance(obj.get("level"), int) and ("time" in obj or "msg" in obj):
        return "pino"
    return "generic"


# --- Scoring -----------------------------------------------------------------


def _sample(text: str, limit: int = DETECT_SAMPLE_LINES) -> list[str]:
    """Up to `limit` non-blank lines from the head of `text`."""
    out: list[str] = []
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        out.append(line)
        if len(out) >= limit:
            break
    return out


def _score_json(lines: list[str]) -> tuple[float, str | None, str | None]:
    hits = 0
    flavors: dict[str, int] = {}
    evidence = None
    for line in lines:
        stripped = line.lstrip()
        if not stripped.startswith("{"):
            continue
        try:
            obj = json.loads(stripped)
        except ValueError:
            continue
        if not isinstance(obj, dict):
            continue
        hits += 1
        flavor = _json_flavor(obj)
        flavors[flavor] = flavors.get(flavor, 0) + 1
        if evidence is None:
            evidence = line[:200]
    if not hits:
        return 0.0, None, None
    # A collector may emit one OTLP envelope holding thousands of records, so
    # a single well-formed line is enough when it is the whole file.
    best = max(flavors, key=lambda k: flavors[k])
    return hits / len(lines), best, evidence


def _score_regex(lines: list[str], pattern: re.Pattern[str]) -> tuple[float, str | None]:
    evidence = None
    hits = 0
    for line in lines:
        if pattern.match(line):
            hits += 1
            if evidence is None:
                evidence = line[:200]
    return hits / len(lines), evidence


def _score_postgres(lines: list[str]) -> tuple[float, str | None]:
    """Prefix *and* a message tag, so a plain ISO line with a `[1234]` in it
    doesn't win. Continuation lines (`STATEMENT:` bodies) drag the raw prefix
    ratio down, so the tag ratio is allowed to carry the score."""
    prefix, evidence = _score_regex(lines, _POSTGRES_RE)
    if not prefix:
        return 0.0, None
    tagged = sum(1 for line in lines if _POSTGRES_TAG_RE.search(line)) / len(lines)
    return prefix * 0.5 + min(tagged * 2.0, 1.0) * 0.5, evidence


def _score_logfmt(lines: list[str]) -> tuple[float, str | None]:
    evidence = None
    hits = 0
    for line in lines:
        pairs = _LOGFMT_PAIR_RE.findall(line)
        if len(pairs) >= 3:
            hits += 1
            if evidence is None:
                evidence = line[:200]
    return hits / len(lines), evidence


def detect(text: str, *, override: str | None = None) -> Detection:
    """Identify the format of `text`.

    `override` is the user's explicit choice and is taken at face value — if
    someone says a file is syslog, it is parsed as syslog and the confidence
    reads 1.0.
    """
    if override:
        if override not in FORMATS:
            raise ValueError(f"unknown format {override!r}; expected one of {', '.join(FORMATS)}")
        return Detection(format=override, confidence=1.0, flavor=None, evidence=None)

    lines = _sample(text)
    if not lines:
        return Detection(format="plain", confidence=0.0)

    json_score, flavor, json_evidence = _score_json(lines)
    cri_score, cri_evidence = _score_regex(lines, _CRI_RE)
    pg_score, pg_evidence = _score_postgres(lines)
    clf_score, clf_evidence = _score_regex(lines, _CLF_RE)
    s5424_score, s5424_evidence = _score_regex(lines, _SYSLOG5424_RE)
    syslog_score, syslog_evidence = _score_regex(lines, _SYSLOG3164_RE)
    logfmt_score, logfmt_evidence = _score_logfmt(lines)

    candidates: list[Detection] = [
        Detection("json", json_score, flavor, json_evidence),
        Detection("cri", cri_score, None, cri_evidence),
        Detection("postgres", pg_score, None, pg_evidence),
        Detection("clf", clf_score, None, clf_evidence),
        Detection("syslog5424", s5424_score, None, s5424_evidence),
        Detection("syslog", syslog_score, None, syslog_evidence),
        Detection("logfmt", logfmt_score, None, logfmt_evidence),
    ]

    best = max(candidates, key=lambda c: c.confidence)
    # Below this the signal is too thin to act on and plain-text parsing — which
    # tolerates anything — is the safer read.
    if best.confidence < 0.30:
        return Detection("plain", 1.0 - best.confidence, None, lines[0][:200])
    return best
