"""The ingest routes.

Authentication is stubbed out with a dependency override — these tests are
about the ingest contract, and standing up a database to obtain a session
cookie would only make them slower and flakier without testing anything the
auth tests do not already cover.
"""

import json
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from api.deps import get_current_user
from db.models import User
from main import app

PLAIN = """2026-08-04T14:58:11.004Z INFO  [api.gateway] request complete path=/checkout status=200
2026-08-04T14:58:21.113Z ERROR [db.pool] connection acquire timeout after 5000ms
    at Pool.acquire (/app/node_modules/pg-pool/index.js:212)
"""

NGINX = (
    '10.0.0.4 - - [04/Aug/2026:14:58:11 +0000] "GET /checkout HTTP/1.1" 200 4213 '
    '"-" "curl/8.4.0" request_id=r-8831\n'
)

POSTGRES = "2026-08-04 14:58:21.113 UTC [1234] app@shop ERROR:  statement timeout\n"


@pytest.fixture
def client():
    app.dependency_overrides[get_current_user] = lambda: User(
        id=uuid4(), email="dev@example.com", plan="free"
    )
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()


# --- formats -----------------------------------------------------------------


def test_formats_endpoint_lists_parsers_and_roles(client) -> None:
    body = client.get("/api/ingest/formats").json()
    assert "postgres" in body["formats"]
    assert "db" in body["roles"]


# --- preview -----------------------------------------------------------------


def test_preview_detects_each_file_and_merges_the_timeline(client) -> None:
    response = client.post(
        "/api/ingest/preview",
        json={
            "files": [
                {"name": "nginx.log", "content": NGINX, "service": "edge", "role": "proxy"},
                {"name": "api.log", "content": PLAIN, "service": "api", "role": "backend"},
                {"name": "pg.log", "content": POSTGRES, "service": "shop-db", "role": "db"},
            ]
        },
    )
    assert response.status_code == 200
    body = response.json()

    assert [f["format"] for f in body["files"]] == ["clf", "plain", "postgres"]
    assert body["entry_count"] == 4
    assert body["first_timestamp"] < body["last_timestamp"]
    assert body["skew_suspected"] is False
    # No trace IDs anywhere, but two files carry explicit join keys.
    assert body["trace_coverage"] == 0.0
    assert body["correlation_coverage"] > 0.0


def test_preview_returns_a_bounded_sample(client) -> None:
    many = "\n".join(f"2026-08-04T14:58:{i % 60:02d}Z INFO [api] line {i}" for i in range(200))
    body = client.post(
        "/api/ingest/preview",
        json={"files": [{"name": "a.log", "content": many}], "sample_limit": 5},
    ).json()
    assert body["entry_count"] == 200
    assert len(body["sample"]) == 5


def test_preview_reports_redactions(client) -> None:
    body = client.post(
        "/api/ingest/preview",
        json={
            "files": [
                {
                    "name": "a.log",
                    "content": "2026-08-04T14:58:11Z INFO [api] connect password=hunter2\n",
                }
            ]
        },
    ).json()
    assert body["redaction"]["entries_affected"] == 1
    assert "hunter2" not in json.dumps(body["sample"])


def test_preview_honours_a_format_override(client) -> None:
    body = client.post(
        "/api/ingest/preview",
        json={"files": [{"name": "a.log", "content": NGINX, "format": "plain"}]},
    ).json()
    assert body["files"][0]["format"] == "plain"
    assert body["files"][0]["format_overridden"] is True
    assert body["files"][0]["format_confidence"] == 1.0


def test_preview_reports_suspected_skew(client) -> None:
    shifted = PLAIN.replace("T14:58", "T19:58")
    body = client.post(
        "/api/ingest/preview",
        json={
            "files": [
                {"name": "a.log", "content": NGINX, "service": "edge"},
                {"name": "b.log", "content": shifted, "service": "api"},
            ]
        },
    ).json()
    assert body["skew_suspected"] is True
    assert any(f["skew_warning"] for f in body["files"])


def test_preview_rejects_an_unknown_role(client) -> None:
    response = client.post(
        "/api/ingest/preview",
        json={"files": [{"name": "a.log", "content": PLAIN, "role": "mainframe"}]},
    )
    assert response.status_code == 422


def test_preview_rejects_an_unknown_format(client) -> None:
    response = client.post(
        "/api/ingest/preview",
        json={"files": [{"name": "a.log", "content": PLAIN, "format": "parquet"}]},
    )
    assert response.status_code == 422


def test_preview_rejects_an_empty_file_list(client) -> None:
    assert client.post("/api/ingest/preview", json={"files": []}).status_code == 422


def test_preview_reports_no_parsable_lines(client) -> None:
    response = client.post(
        "/api/ingest/preview", json={"files": [{"name": "a.log", "content": "   \n\n"}]}
    )
    assert response.status_code == 422
    assert "No log lines" in response.json()["detail"]


def test_preview_requires_authentication() -> None:
    app.dependency_overrides.clear()
    with TestClient(app) as anon:
        response = anon.post(
            "/api/ingest/preview", json={"files": [{"name": "a.log", "content": PLAIN}]}
        )
    assert response.status_code == 401


# --- upload ------------------------------------------------------------------


def test_upload_accepts_multiple_files_with_metadata(client) -> None:
    response = client.post(
        "/api/ingest/upload",
        files=[
            ("files", ("nginx.log", NGINX, "text/plain")),
            ("files", ("pg.log", POSTGRES, "text/plain")),
        ],
        data={
            "meta": json.dumps(
                [
                    {"name": "nginx.log", "service": "edge", "role": "proxy"},
                    {"name": "pg.log", "service": "shop-db", "role": "db"},
                ]
            )
        },
    )
    assert response.status_code == 200
    body = response.json()
    assert [f["service"] for f in body["files"]] == ["edge", "shop-db"]
    assert [f["role"] for f in body["files"]] == ["proxy", "db"]
    assert [f["format"] for f in body["files"]] == ["clf", "postgres"]


def test_upload_survives_a_malformed_metadata_sidecar(client) -> None:
    """Bad metadata costs the labels, not the upload."""
    response = client.post(
        "/api/ingest/upload",
        files=[("files", ("nginx.log", NGINX, "text/plain"))],
        data={"meta": "{not json"},
    )
    assert response.status_code == 200
    assert response.json()["files"][0]["service"] == "nginx"  # filename stem


def test_upload_defaults_to_redacting_when_the_policy_is_malformed(client) -> None:
    """An unparseable policy must never be read as "redact nothing"."""
    response = client.post(
        "/api/ingest/upload",
        files=[("files", ("a.log", "2026-08-04T14:58:11Z INFO x password=hunter2\n", "text/plain"))],
        data={"redaction": "{not json"},
    )
    assert response.status_code == 200
    assert response.json()["redaction"]["entries_affected"] == 1


def test_upload_accepts_a_redaction_policy(client) -> None:
    response = client.post(
        "/api/ingest/upload",
        files=[("files", ("a.log", "2026-08-04T14:58:11Z INFO x from 10.0.0.4\n", "text/plain"))],
        data={"redaction": json.dumps({"enabled": True, "redact_ips": True})},
    )
    body = response.json()
    assert body["redaction"]["counts"].get("ip") == 1


def test_upload_decodes_invalid_utf8_rather_than_rejecting_it(client) -> None:
    response = client.post(
        "/api/ingest/upload",
        files=[("files", ("a.log", b"2026-08-04T14:58:11Z INFO \xff\xfe boom\n", "text/plain"))],
    )
    assert response.status_code == 200
    assert response.json()["entry_count"] == 1


def test_upload_derives_the_service_from_the_filename(client) -> None:
    response = client.post(
        "/api/ingest/upload", files=[("files", ("checkout.log", NGINX, "text/plain"))]
    )
    assert response.json()["files"][0]["service"] == "checkout"


# --- the analyze path still works on the new phase ---------------------------


def test_estimate_runs_through_the_new_ingest_phase(client, monkeypatch) -> None:
    """`/api/analyses/estimate` was switched off the old parser."""

    async def fake_count(text: str):
        return len(text) // 4, False

    monkeypatch.setattr("api.analyses.tokens.count_tokens", fake_count)

    response = client.post("/api/analyses/estimate", json={"logs": PLAIN})
    assert response.status_code == 200
    body = response.json()
    # Three lines, two entries — the stack frame folded into its error.
    assert body["log_line_count"] == 2
