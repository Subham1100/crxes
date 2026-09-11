"""Stage 5 — redaction.

Two failure modes matter and they pull against each other: leaking a secret,
and destroying a correlation key. The tests below pin both sides.
"""

from ingest.entry import Entry
from ingest.redact import RedactionPolicy, RedactionReport, redact_entries, redact_text


def scrub(text: str, policy: RedactionPolicy | None = None) -> tuple[str, RedactionReport]:
    report = RedactionReport()
    return redact_text(text, policy or RedactionPolicy(), report), report


def entry(message: str, **kwargs) -> Entry:
    return Entry(
        file_id="f0",
        line_no=1,
        service="svc",
        role="backend",
        timestamp=None,
        time_source="none",
        level="info",
        message=message,
        raw=message,
        **kwargs,
    )


# --- what must be removed ----------------------------------------------------


def test_jwt_is_redacted() -> None:
    out, report = scrub("auth ok token eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxIn0.abcd1234 done")
    assert "eyJhbGciOiJIUzI1NiJ9" not in out
    assert report.counts["jwt"] == 1


def test_keyed_auth_header_is_redacted_once() -> None:
    out, _ = scrub("Authorization: Bearer sk-abcdefghijklmnop")
    assert out == "Authorization: [REDACTED]"


def test_bare_bearer_token_keeps_its_scheme() -> None:
    out, _ = scrub("retrying with Bearer sk-abcdefghijklmnop now")
    assert "Bearer [REDACTED]" in out
    assert "abcdefghijklmnop" not in out


def test_secret_named_attribute_is_redacted_by_its_key() -> None:
    """A JSON log leaks through the key, not the value.

    `{"password": "hunter2"}` has no `=` for the text rules to match on, so
    without key-based redaction it would pass through untouched.
    """
    e = entry("ok")
    e.attributes = {"password": "hunter2", "http.headers.authorization": "Bearer abc"}
    redact_entries([e])
    assert e.attributes["password"] == "[REDACTED]"
    assert e.attributes["http.headers.authorization"] == "[REDACTED]"


def test_a_key_merely_containing_a_secret_word_is_not_redacted() -> None:
    e = entry("ok")
    e.attributes = {"password_reset_requested": True, "tokens_used": 412}
    redact_entries([e])
    assert e.attributes == {"password_reset_requested": True, "tokens_used": 412}


def test_aws_access_key_is_redacted() -> None:
    out, _ = scrub("key AKIAIOSFODNN7EXAMPLE used")
    assert "AKIAIOSFODNN7EXAMPLE" not in out


def test_password_in_a_key_value_pair_is_redacted() -> None:
    out, _ = scrub("connecting user=app password=hunter2 db=shop")
    assert "hunter2" not in out
    assert "user=app" in out
    assert "db=shop" in out


def test_url_credentials_are_redacted_but_the_host_survives() -> None:
    out, _ = scrub("dsn postgres://app:s3cret@db-primary:5432/shop")
    assert "s3cret" not in out
    # The host is a service identity that correlation needs.
    assert "db-primary:5432" in out


def test_private_key_block_is_redacted_whole() -> None:
    text = "-----BEGIN RSA PRIVATE KEY-----\nMIIEow\nabc\n-----END RSA PRIVATE KEY-----"
    out, report = scrub(text)
    assert "MIIEow" not in out
    assert report.counts["private_key"] == 1


def test_email_is_redacted() -> None:
    out, _ = scrub("login failed for ops@example.com")
    assert "ops@example.com" not in out


def test_github_and_stripe_tokens_are_redacted() -> None:
    out, _ = scrub("ghp_abcdefghijklmnopqrstuvwxyz0123456789 sk_live_abcdefghij123")
    assert "ghp_abcdefghijklmnopqrstuvwxyz0123456789" not in out
    assert "sk_live_abcdefghij123" not in out


# --- what must survive -------------------------------------------------------


def test_order_ids_survive_the_card_rule() -> None:
    """A 13-digit order number is not Luhn-valid and must not be eaten.

    This is the rule that matters most: order IDs are the strongest entity key
    a checkout flow has, and a naive card pattern would redact every one.
    """
    out, report = scrub("order=8831000000123 reserved")
    assert "8831000000123" in out
    assert "card" not in report.counts


def test_a_real_card_number_is_redacted() -> None:
    out, report = scrub("charged 4242424242424242 ok")  # Luhn-valid
    assert "4242424242424242" not in out
    assert report.counts["card"] == 1


def test_ips_survive_by_default() -> None:
    out, _ = scrub("upstream 10.0.0.4 timed out")
    assert "10.0.0.4" in out


def test_ips_are_redacted_when_asked_for() -> None:
    out, _ = scrub("upstream 10.0.0.4 timed out", RedactionPolicy(redact_ips=True))
    assert "10.0.0.4" not in out


def test_trace_and_correlation_keys_are_never_touched() -> None:
    e = entry(
        "request failed",
        trace_id="4bf92f3577b34da6a3ce929d0e0e4736",
        span_id="00f067aa0ba902b7",
        correlation_keys={"request_id": "ops@example.com-99"},
    )
    redact_entries([e])
    assert e.trace_id == "4bf92f3577b34da6a3ce929d0e0e4736"
    assert e.span_id == "00f067aa0ba902b7"
    # Even an email-shaped join key survives: it is what later stages join on.
    assert e.correlation_keys["request_id"] == "ops@example.com-99"


def test_trace_id_is_not_mistaken_for_a_secret_in_the_message() -> None:
    out, _ = scrub("trace_id=4bf92f3577b34da6a3ce929d0e0e4736 request_id=r-99")
    assert "4bf92f3577b34da6a3ce929d0e0e4736" in out
    assert "r-99" in out


# --- entry traversal ---------------------------------------------------------


def test_attributes_are_redacted_including_nested_values() -> None:
    e = entry("ok")
    e.attributes = {"db.dsn": "postgres://app:s3cret@db:5432", "nested": {"password": "hunter2"}}
    redact_entries([e])
    assert "s3cret" not in e.attributes["db.dsn"]
    assert "hunter2" not in e.attributes["nested"]["password"]


def test_non_string_attributes_are_left_alone() -> None:
    e = entry("ok")
    e.attributes = {"wait_queue": 44, "ok": True, "ratio": 1.5, "missing": None}
    redact_entries([e])
    assert e.attributes == {"wait_queue": 44, "ok": True, "ratio": 1.5, "missing": None}


def test_raw_is_redacted_alongside_the_message() -> None:
    e = entry("password=hunter2")
    redact_entries([e])
    assert "hunter2" not in e.raw
    assert "hunter2" not in e.message


def test_report_counts_affected_entries() -> None:
    entries = [entry("password=hunter2"), entry("nothing secret here")]
    report = redact_entries(entries)
    assert report.entries_affected == 1
    assert report.total >= 1


def test_disabled_policy_changes_nothing() -> None:
    e = entry("password=hunter2")
    report = redact_entries([e], RedactionPolicy(enabled=False))
    assert e.message == "password=hunter2"
    assert report.total == 0


def test_a_rule_can_be_disabled_individually() -> None:
    out, _ = scrub(
        "login failed for ops@example.com",
        RedactionPolicy(disabled_rules=frozenset({"email"})),
    )
    assert "ops@example.com" in out
