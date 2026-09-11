"""Stage 5 — strip secrets and personal data before anything leaves ingest.

Runs after parsing, not before, for two reasons: structure is intact by then,
so a `password=` field can be redacted by key rather than by guessing at a
blob; and redacting first would corrupt the very delimiters the parsers need.

The policy is deliberately conservative about what it will destroy. IP
addresses are off by default because they are a genuine correlation key for a
proxy↔backend join, and card numbers are Luhn-checked because the naive
thirteen-to-sixteen-digit pattern eats order IDs — which are the single most
useful entity key in a checkout flow.

Every rule is compiled into **one** alternation and applied in a single pass.
That matters more than it looks: this stage touches every line of every file,
and running a dozen separate `subn` calls over each of them made redaction
roughly three quarters of the phase's total runtime. One pass with a dispatch
on the matched group does the same work for a fraction of the cost.

Redaction is one-way. The counts come back in a report so the UI can show what
was removed before the user commits to sending anything anywhere.
"""

import re
from dataclasses import dataclass, field
from functools import lru_cache
from typing import Any, Callable, Iterable

from ingest.entry import Entry

#: Keys whose value is a secret regardless of what the value looks like.
#: `token` is included; `trace_id`, `request_id` and friends deliberately are
#: not, because they are join keys and redacting them would break correlation.
_SECRET_KEYS = (
    "password",
    "passwd",
    "pwd",
    "secret",
    "api[_-]?key",
    "apikey",
    "access[_-]?token",
    "refresh[_-]?token",
    "auth[_-]?token",
    "id[_-]?token",
    "session[_-]?secret",
    "client[_-]?secret",
    "private[_-]?key",
    "authorization",
    "token",
)

#: A replacement returns the text to substitute, or `None` to decline the match
#: and leave it untouched — which is how the Luhn check rejects an order ID
#: that merely looks card-shaped.
Replacer = Callable[[re.Match[str]], str | None]


@dataclass(frozen=True, slots=True)
class Rule:
    name: str
    #: Pattern source, merged into the combined alternation. Any inner group
    #: must be *named* and prefixed with the rule name — group numbers shift
    #: once patterns are concatenated, so backreferences cannot be numeric.
    pattern: str
    replace: Replacer
    #: Whether this rule needs case-insensitive matching. Applied as a scoped
    #: `(?i:…)` inline flag rather than on the combined pattern, because a
    #: global `IGNORECASE` defeats the literal-prefix optimisation that makes
    #: the case-sensitive rules (`AKIA`, `eyJ`, `xox`) cheap to reject.
    ignore_case: bool = False


def _const(text: str) -> Replacer:
    return lambda _match: text


def _luhn(digits: str) -> bool:
    total = 0
    for i, char in enumerate(reversed(digits)):
        n = ord(char) - 48
        if i % 2:
            n *= 2
            if n > 9:
                n -= 9
        total += n
    return total % 10 == 0


def _replace_card(match: re.Match[str]) -> str | None:
    """Redact only a Luhn-valid run — an order ID is not a card number."""
    digits = re.sub(r"[ -]", "", match.group(0))
    if 13 <= len(digits) <= 19 and _luhn(digits):
        return "[REDACTED:card]"
    return None


#: Ordered, and the order is load-bearing: the combined pattern tries
#: alternatives left to right at each position, so the multi-line key block has
#: to precede anything that could match inside it, and `secret_kv` has to
#: precede `bearer` so `Authorization: Bearer <token>` is redacted once as a
#: whole rather than twice in pieces.
RULES: tuple[Rule, ...] = (
    Rule(
        "private_key",
        r"-----BEGIN [A-Z ]*PRIVATE KEY-----[\s\S]*?-----END [A-Z ]*PRIVATE KEY-----",
        _const("[REDACTED:private_key]"),
    ),
    Rule(
        "jwt",
        r"\beyJ[A-Za-z0-9_-]{6,}\.[A-Za-z0-9_-]{6,}\.[A-Za-z0-9_-]{4,}\b",
        _const("[REDACTED:jwt]"),
    ),
    Rule(
        "aws_key",
        r"\b(?:AKIA|ASIA|AGPA|AIDA|AROA|ANPA)[0-9A-Z]{16}\b",
        _const("[REDACTED:aws_key]"),
    ),
    Rule("github_token", r"\bgh[pousr]_[A-Za-z0-9]{20,}\b", _const("[REDACTED:github_token]")),
    Rule("slack_token", r"\bxox[abprs]-[A-Za-z0-9-]{10,}\b", _const("[REDACTED:slack_token]")),
    Rule("stripe_key", r"\b[sr]k_(?:live|test)_[A-Za-z0-9]{10,}\b", _const("[REDACTED:stripe_key]")),
    Rule(
        # Credentials embedded in a connection string or URL. The scheme and
        # host survive — `postgres://…@db-primary:5432` still identifies the
        # service, which is exactly what correlation needs from it.
        "url_credentials",
        r"\b(?P<url_credentials_scheme>[a-zA-Z][\w+.-]*://)[^\s/:@]+:[^\s/@]+@",
        lambda m: f"{m.group('url_credentials_scheme')}[REDACTED]@",
    ),
    Rule(
        # Written to swallow an auth scheme, so a keyed header is one match.
        "secret_kv",
        rf'\b(?P<secret_kv_key>{"|".join(_SECRET_KEYS)})(?P<secret_kv_sep>\s*[=:]\s*)'
        r"(?:Bearer\s+|Basic\s+|Token\s+)?"
        r"""(?:"[^"]*"|'[^']*'|[^\s,;&)}\]]+)""",
        lambda m: f"{m.group('secret_kv_key')}{m.group('secret_kv_sep')}[REDACTED]",
        ignore_case=True,
    ),
    Rule(
        # A bare scheme with no key in front of it, in free text.
        "bearer",
        r"\b(?P<bearer_scheme>[Bb]earer|[Bb]asic)\s+(?!\[REDACTED)[A-Za-z0-9._~+/=-]{12,}",
        lambda m: f"{m.group('bearer_scheme')} [REDACTED]",
    ),
    Rule("email", r"\b[\w.%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b", _const("[REDACTED:email]")),
    #: Luhn-checked, so it declines anything that is not really a card.
    Rule("card", r"\b(?:\d[ -]?){12,18}\d\b", _replace_card),
    #: Off unless asked for — an IP is a correlation key more often than PII.
    Rule("ip", r"\b(?:\d{1,3}\.){3}\d{1,3}\b", _const("[REDACTED:ip]")),
)

_BY_NAME: dict[str, Rule] = {rule.name: rule for rule in RULES}

#: Rules that only apply when the policy opts into them.
_OPT_IN: frozenset[str] = frozenset({"ip"})

#: An attribute *name* that marks its value as a secret whatever the value
#: looks like. The dotted/prefixed spellings that structured loggers produce
#: (`http.request.headers.authorization`) are matched on their last segment.
_SECRET_KEY_RE = re.compile(rf'^(?:.*[._-])?(?:{"|".join(_SECRET_KEYS)})$', re.IGNORECASE)


@dataclass(slots=True)
class RedactionPolicy:
    enabled: bool = True
    #: Redact IPv4 addresses. Breaks IP-based correlation; off by default.
    redact_ips: bool = False
    #: Redact Luhn-valid card numbers.
    redact_cards: bool = True
    #: Rule names to skip, for a user who needs one class of value kept.
    disabled_rules: frozenset[str] = frozenset()

    def active_rules(self) -> frozenset[str]:
        names = {r.name for r in RULES if r.name not in _OPT_IN}
        if self.redact_ips:
            names.add("ip")
        if not self.redact_cards:
            names.discard("card")
        return frozenset(names - set(self.disabled_rules))


@dataclass(slots=True)
class RedactionReport:
    """What was removed, by rule. Rendered as the "before you send this" diff."""

    counts: dict[str, int] = field(default_factory=dict)
    #: Entries touched by at least one rule.
    entries_affected: int = 0
    #: Running total, kept as a field rather than summed on read. It is checked
    #: once per entry to decide whether anything changed, and summing the dict
    #: on every one of those checks was showing up in profiles.
    total: int = 0

    def _add(self, rule: str, n: int = 1) -> None:
        if n:
            self.counts[rule] = self.counts.get(rule, 0) + n
            self.total += n


@lru_cache(maxsize=32)
def _compiled(active: frozenset[str]) -> re.Pattern[str] | None:
    """One alternation over every active rule, cached per rule set.

    The cache matters because a policy is fixed for a whole run: without it,
    every entry would recompile a pattern of a couple of thousand characters.
    """
    parts = [
        f"(?P<{rule.name}>(?i:{rule.pattern}))"
        if rule.ignore_case
        else f"(?P<{rule.name}>{rule.pattern})"
        for rule in RULES
        if rule.name in active
    ]
    if not parts:
        return None
    return re.compile("|".join(parts))


def redact_text(
    text: str,
    policy: RedactionPolicy,
    report: RedactionReport,
    pattern: re.Pattern[str] | None = None,
) -> str:
    """Apply every active rule to `text` in a single pass.

    `pattern` is resolved once per run by the caller and threaded through —
    looking it up here meant recomputing the active rule set for every string
    of every entry.
    """
    if not text:
        return text
    if pattern is None:
        pattern = _compiled(policy.active_rules())
    if pattern is None:
        return text

    def dispatch(match: re.Match[str]) -> str:
        # `lastgroup` names the outermost group that matched, which is the rule
        # — inner groups are all prefixed with their rule's name and are only
        # ever consulted by that rule's replacer.
        name = match.lastgroup
        rule = _BY_NAME.get(name or "")
        if rule is None:
            return match.group(0)
        replacement = rule.replace(match)
        if replacement is None:
            return match.group(0)
        report._add(rule.name)
        return replacement

    return pattern.sub(dispatch, text)


@dataclass(slots=True)
class _Pass:
    """State for one entry's redaction.

    Carries the containment shortcut described on `redact_entry`, which is the
    difference between one regex scan per entry and one per field.
    """

    policy: RedactionPolicy
    report: RedactionReport
    pattern: re.Pattern[str]
    #: `raw` as it was *before* redaction, and whether scanning it matched
    #: nothing.
    raw: str = ""
    raw_clean: bool = False

    def covered(self, text: str) -> bool:
        """True when `text` was already scanned as part of a clean `raw`.

        `str.__contains__` is a C-level substring search and costs a small
        fraction of a regex scan, so this is a cheap way to prove a field needs
        no pass of its own. It is a proof, not a heuristic: if the text appears
        verbatim inside a string that matched no rule, it cannot match one
        either.
        """
        return self.raw_clean and text in self.raw


def _redact_value(value: Any, p: _Pass, key: str | None = None) -> Any:
    """Walk a parsed attribute value, redacting the strings inside it.

    `key` carries the attribute's own name, because a structured log leaks
    differently from a text one: `{"password": "hunter2"}` has no `=` for the
    text rules to find, and the only thing marking the value as a secret is
    the key it sits under. Text rules alone would pass it straight through.
    """
    if key is not None and _SECRET_KEY_RE.match(key) and "secret_kv" not in p.policy.disabled_rules:
        if value not in (None, "", [], {}):
            p.report._add("secret_kv")
            return "[REDACTED]"
        return value
    if isinstance(value, str):
        if p.covered(value):
            return value
        return redact_text(value, p.policy, p.report, p.pattern)
    if isinstance(value, dict):
        return {k: _redact_value(v, p, key=k) for k, v in value.items()}
    if isinstance(value, list):
        return [_redact_value(v, p) for v in value]
    return value


def redact_entry(
    entry: Entry,
    policy: RedactionPolicy,
    report: RedactionReport,
    pattern: re.Pattern[str] | None = None,
) -> bool:
    """Redact one entry in place. Returns whether anything changed.

    `trace_id`, `span_id` and `correlation_keys` are left alone by design.
    They are opaque identifiers, they are what every later stage joins on, and
    redacting them would leave the diagnosis with nothing to work from.

    `raw` is scanned first, and every other field that appears verbatim inside
    a *clean* `raw` is then skipped — which for a text log is all of them, so
    the common case costs one regex pass per entry instead of one per field.

    Note what this deliberately does not assume: that `raw` contains
    everything. It usually does, but the OTLP envelope parser truncates `raw`
    and merges resource attributes in from outside the log record, so a
    blanket "clean raw means clean entry" gate would silently leak those. The
    containment test is checked per field instead, which is provable rather
    than conventional — a parser added later cannot quietly invalidate it.
    """
    if pattern is None:
        pattern = _compiled(policy.active_rules())
    if pattern is None:
        return False

    # Counted separately and discarded: `raw` restates what the message and
    # attributes already hold, so counting both would report two leaked IPs
    # for a line that contained one — and a person reads this report to decide
    # whether it is safe to send.
    scratch = RedactionReport()
    original_raw = entry.raw
    entry.raw = redact_text(original_raw, policy, scratch, pattern)

    p = _Pass(
        policy=policy,
        report=report,
        pattern=pattern,
        raw=original_raw,
        raw_clean=scratch.total == 0,
    )

    before = report.total
    if not p.covered(entry.message):
        entry.message = redact_text(entry.message, policy, report, pattern)
    if entry.attributes:
        entry.attributes = {
            key: _redact_value(value, p, key=key) for key, value in entry.attributes.items()
        }

    changed = report.total > before or scratch.total > 0
    if changed:
        report.entries_affected += 1
    return changed


def redact_entries(
    entries: Iterable[Entry], policy: RedactionPolicy | None = None
) -> RedactionReport:
    report = RedactionReport()
    policy = policy or RedactionPolicy()
    if not policy.enabled:
        return report
    # Resolved once for the whole run, not per entry.
    pattern = _compiled(policy.active_rules())
    if pattern is None:
        return report
    for entry in entries:
        redact_entry(entry, policy, report, pattern)
    return report


__all__ = [
    "RULES",
    "RedactionPolicy",
    "RedactionReport",
    "Rule",
    "redact_entries",
    "redact_entry",
    "redact_text",
]
