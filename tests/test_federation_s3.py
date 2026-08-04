"""Wire-level contracts for the bounded S3-compatible publisher backend."""

from __future__ import annotations

import datetime as dt
import hashlib
import hmac
import io
from pathlib import Path
from typing import Any

import pytest

from furatena.catalog import federation_s3
from furatena.catalog.federation_publish import (
    ShardPublishAuthError,
    ShardPublishConflictError,
    ShardPublishError,
    ShardPublishPartialError,
)
from furatena.catalog.federation_s3 import S3ObjectBackend


class FakeResponse:
    def __init__(
        self,
        status: int,
        *,
        headers: dict[str, str] | None = None,
        body: bytes = b"",
    ) -> None:
        self.status = status
        self.headers = {key.lower(): value for key, value in (headers or {}).items()}
        self.stream = io.BytesIO(body)
        self.read_sizes: list[int] = []
        self.closed = False

    def getheader(self, name: str) -> str | None:
        return self.headers.get(name.lower())

    def read(self, size: int = -1) -> bytes:
        self.read_sizes.append(size)
        return self.stream.read(size)

    def close(self) -> None:
        self.closed = True


class FakeConnection:
    queued: list[FakeResponse | BaseException] = []
    instances: list[FakeConnection] = []

    def __init__(self, host: str, *, timeout: float) -> None:
        self.host = host
        self.timeout = timeout
        self.method = ""
        self.path = ""
        self.request_options: dict[str, bool] = {}
        self.headers: dict[str, str] = {}
        self.sent: list[bytes] = []
        self.ended = False
        self.closed = False
        self.instances.append(self)

    def putrequest(self, method: str, path: str, **options: bool) -> None:
        self.method = method
        self.path = path
        self.request_options = options

    def putheader(self, name: str, value: str) -> None:
        self.headers[name.lower()] = value

    def endheaders(self) -> None:
        self.ended = True

    def send(self, value: bytes) -> None:
        self.sent.append(value)

    def getresponse(self) -> FakeResponse:
        result = self.queued.pop(0)
        if isinstance(result, BaseException):
            raise result
        return result

    def close(self) -> None:
        self.closed = True


class FrozenDateTime(dt.datetime):
    @classmethod
    def now(cls, tz: dt.tzinfo | None = None) -> FrozenDateTime:
        return cls(2026, 8, 3, 12, 34, 56, tzinfo=tz)


@pytest.fixture(autouse=True)
def fake_https(monkeypatch: pytest.MonkeyPatch) -> None:
    FakeConnection.queued = []
    FakeConnection.instances = []
    monkeypatch.setattr(federation_s3.http.client, "HTTPSConnection", FakeConnection)
    monkeypatch.setattr(federation_s3.dt, "datetime", FrozenDateTime)


def _backend(**overrides: Any) -> S3ObjectBackend:
    values: dict[str, Any] = {
        "endpoint": "https://objects.example.com:9443/root path",
        "bucket": "docs-bucket",
        "prefix": "team/public",
        "region": "us-east-1",
        "access_key_id": "ACCESS123",
        "secret_access_key": "secret-key-material",
        "session_token": "session-token-material",
        "timeout": 17,
    }
    values.update(overrides)
    return S3ObjectBackend(**values)


def _expected_authorization(headers: dict[str, str], *, method: str, path: str) -> str:
    unsigned = {key: value for key, value in headers.items() if key != "authorization"}
    canonical_headers = "".join(
        f"{name}:{' '.join(value.split())}\n" for name, value in sorted(unsigned.items())
    )
    signed_headers = ";".join(sorted(unsigned))
    payload_sha = unsigned["x-amz-content-sha256"]
    canonical_request = "\n".join(
        (method, path, "", canonical_headers, signed_headers, payload_sha)
    )
    scope = "20260803/us-east-1/s3/aws4_request"
    string_to_sign = "\n".join(
        (
            "AWS4-HMAC-SHA256",
            "20260803T123456Z",
            scope,
            hashlib.sha256(canonical_request.encode()).hexdigest(),
        )
    )
    date_key = hmac.new(b"AWS4secret-key-material", b"20260803", hashlib.sha256).digest()
    region_key = hmac.new(date_key, b"us-east-1", hashlib.sha256).digest()
    service_key = hmac.new(region_key, b"s3", hashlib.sha256).digest()
    signing_key = hmac.new(service_key, b"aws4_request", hashlib.sha256).digest()
    signature = hmac.new(signing_key, string_to_sign.encode(), hashlib.sha256).hexdigest()
    return (
        f"AWS4-HMAC-SHA256 Credential=ACCESS123/{scope}, "
        f"SignedHeaders={signed_headers}, Signature={signature}"
    )


def test_head_builds_exact_sigv4_path_and_returns_metadata() -> None:
    response = FakeResponse(
        200,
        headers={"Content-Length": "17", "X-Amz-Meta-Sha256": "a" * 64},
    )
    FakeConnection.queued.append(response)

    result = _backend().inspect("sha256/fingerprint/object name.json")

    connection = FakeConnection.instances[0]
    assert result is not None
    assert result.size == 17
    assert result.sha256 == "a" * 64
    assert connection.host == "objects.example.com:9443"
    assert connection.timeout == 17
    assert connection.method == "HEAD"
    assert connection.path == (
        "/root%20path/docs-bucket/team/public/sha256/fingerprint/object%20name.json"
    )
    assert connection.request_options == {"skip_host": True, "skip_accept_encoding": True}
    assert connection.headers["host"] == "objects.example.com:9443"
    assert connection.headers["x-amz-security-token"] == "session-token-material"
    assert "x-amz-security-token" in connection.headers["authorization"]
    assert connection.headers["authorization"] == _expected_authorization(
        connection.headers,
        method="HEAD",
        path=connection.path,
    )
    assert connection.ended is True
    assert response.closed is True
    assert connection.closed is True


def test_head_missing_returns_none_and_closes_transport() -> None:
    response = FakeResponse(404, body=b"private response body")
    FakeConnection.queued.append(response)

    assert _backend().inspect("sha256/missing/manifest.json") is None
    assert response.closed is True
    assert FakeConnection.instances[0].closed is True


def test_get_reader_preserves_bounded_reads_and_closes_context() -> None:
    response = FakeResponse(200, body=b"abcdefgh")
    FakeConnection.queued.append(response)

    with _backend().open("sha256/fingerprint/manifest.json") as stream:
        assert stream.read(3) == b"abc"
        assert response.closed is False

    assert response.read_sizes == [3]
    assert response.closed is True
    assert FakeConnection.instances[0].closed is True


def test_put_streams_chunks_and_signs_create_only_header(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "object.json"
    source.write_bytes(b"abcdefghij")
    digest = hashlib.sha256(source.read_bytes()).hexdigest()
    response = FakeResponse(201)
    FakeConnection.queued.append(response)
    monkeypatch.setattr(federation_s3, "_CHUNK_BYTES", 4)

    _backend().put(
        "sha256/fingerprint/objects/object.json",
        source,
        media_type="application/json",
        sha256=digest,
        create_only=True,
    )

    connection = FakeConnection.instances[0]
    assert connection.method == "PUT"
    assert connection.headers["content-length"] == "10"
    assert connection.headers["content-type"] == "application/json"
    assert connection.headers["x-amz-meta-sha256"] == digest
    assert connection.headers["if-none-match"] == "*"
    assert "if-none-match" in connection.headers["authorization"]
    assert connection.sent == [b"abcd", b"efgh", b"ij"]
    assert response.closed is True
    assert connection.closed is True


@pytest.mark.parametrize(
    ("status", "method", "error_type", "code"),
    (
        (403, "HEAD", ShardPublishAuthError, "fura.publish_shard.auth"),
        (412, "PUT", ShardPublishConflictError, "fura.publish_shard.conflict"),
        (500, "GET", ShardPublishPartialError, "fura.publish_shard.partial"),
    ),
)
def test_http_failures_map_without_response_or_credential_leakage(
    tmp_path: Path,
    status: int,
    method: str,
    error_type: type[ShardPublishError],
    code: str,
) -> None:
    response = FakeResponse(status, body=b"server-secret-body")
    FakeConnection.queued.append(response)
    backend = _backend(
        access_key_id="DO-NOT-LEAK-ACCESS",
        secret_access_key="DO-NOT-LEAK-SECRET",
        session_token="DO-NOT-LEAK-TOKEN",
    )

    with pytest.raises(error_type) as raised:
        if method == "HEAD":
            backend.inspect("sha256/fingerprint/manifest.json")
        elif method == "GET":
            backend.open("sha256/fingerprint/manifest.json")
        else:
            source = tmp_path / "manifest.json"
            source.write_bytes(b"{}")
            backend.put(
                "sha256/fingerprint/manifest.json",
                source,
                media_type="application/json",
                sha256=hashlib.sha256(b"{}").hexdigest(),
                create_only=True,
            )

    message = str(raised.value)
    assert raised.value.code == code
    assert "server-secret-body" not in message
    assert "DO-NOT-LEAK" not in message
    assert response.closed is True
    assert FakeConnection.instances[0].closed is True


def test_transport_failure_maps_partial_without_credential_leakage() -> None:
    FakeConnection.queued.append(OSError("socket unavailable"))
    backend = _backend(
        access_key_id="DO-NOT-LEAK-ACCESS",
        secret_access_key="DO-NOT-LEAK-SECRET",
        session_token="DO-NOT-LEAK-TOKEN",
    )

    with pytest.raises(ShardPublishPartialError) as raised:
        backend.inspect("sha256/fingerprint/manifest.json")

    assert "DO-NOT-LEAK" not in str(raised.value)
    assert FakeConnection.instances[0].closed is True


@pytest.mark.parametrize(
    ("field", "value"),
    (
        ("endpoint", "https://objects.example.com/base/../private"),
        ("endpoint", "https://objects.example.com/base/%2e%2e/private"),
        ("endpoint", "https://objects.example.com\r\nX-Evil: yes"),
        ("bucket", "../private"),
        ("bucket", "bucket\r\nX-Evil: yes"),
        ("prefix", "safe/../private"),
        ("prefix", "safe/%2e%2e/private"),
        ("prefix", "safe\r\nX-Evil: yes"),
    ),
)
def test_invalid_endpoint_bucket_and_prefix_fail_before_request(field: str, value: str) -> None:
    with pytest.raises(ValueError):
        _backend(**{field: value})
    assert FakeConnection.instances == []


@pytest.mark.parametrize(
    "key",
    (
        "../private",
        "safe/../private",
        "safe/%2e%2e/private",
        "/absolute/object",
        "safe\\private",
        "safe\r\nX-Evil: yes",
    ),
)
def test_invalid_keys_fail_before_request(key: str) -> None:
    with pytest.raises(ValueError):
        _backend().inspect(key)
    assert FakeConnection.instances == []


def test_put_rejects_header_injection_before_request(tmp_path: Path) -> None:
    source = tmp_path / "object.json"
    source.write_bytes(b"{}")
    with pytest.raises(ValueError):
        _backend().put(
            "sha256/object.json",
            source,
            media_type="application/json\r\nX-Evil: yes",
            sha256=hashlib.sha256(b"{}").hexdigest(),
            create_only=True,
        )
    assert FakeConnection.instances == []
