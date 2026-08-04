"""Hosted preview grants, local sessions, and cross-surface denial contracts."""

from __future__ import annotations

import asyncio
import base64
import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from pathlib import Path

import pytest
from chirp.testing import TestClient
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
)

from furatena.catalog.docs_app import DocsApp
from furatena.catalog.preview_auth_contracts import (
    PreviewAuthAlgorithm,
    PreviewAuthRegistration,
    PreviewGrantClaims,
    PreviewJwk,
    PreviewJwks,
    PreviewSignedGrant,
    canonical_preview_auth_json,
)
from furatena.catalog.preview_grant_runtime import (
    PreviewGrantBroker,
    PreviewGrantRuntime,
    PreviewGrantRuntimeConfig,
    PreviewGrantRuntimeError,
    PreviewGrantVerifier,
    append_authorization_request,
)
from furatena.catalog.preview_security import _browser_failure
from furatena.catalog.runtime import ServeConfig, ServeMode
from tests.preview_auth_support import grant_claims, registration, revocation
from tests.support import copy_app_theme, write_minimal_docs_yaml, write_mounts_yaml

REPO = Path(__file__).resolve().parents[1]
NOW = 1_893_456_090
CODE = "c" * 43


def _encode(value: object) -> str:
    return base64.urlsafe_b64encode(canonical_preview_auth_json(value)).rstrip(b"=").decode()


def _jwk(private_key: Ed25519PrivateKey, key_id: str) -> PreviewJwk:
    raw = private_key.public_key().public_bytes(
        serialization.Encoding.Raw,
        serialization.PublicFormat.Raw,
    )
    return PreviewJwk(
        key_id,
        PreviewAuthAlgorithm.EDDSA,
        "OKP",
        "Ed25519",
        base64.urlsafe_b64encode(raw).rstrip(b"=").decode(),
    )


def _jwks(*keys: PreviewJwk) -> PreviewJwks:
    return PreviewJwks(
        issuer=registration().issuer,
        keys=keys,
        generated_at="2030-01-01T00:00:00Z",
        stale_after="2030-01-01T00:10:00Z",
    )


def _signed(private_key: Ed25519PrivateKey, claims: PreviewGrantClaims) -> PreviewSignedGrant:
    header = {
        "alg": "EdDSA",
        "kid": claims.key_id,
        "typ": "FURA-PREVIEW-GRANT+jwt",
    }
    signing_input = f"{_encode(header)}.{_encode(claims.to_dict())}"
    signature = base64.urlsafe_b64encode(private_key.sign(signing_input.encode())).rstrip(b"=")
    return PreviewSignedGrant(f"{signing_input}.{signature.decode()}")


class _Broker(PreviewGrantBroker):
    def __init__(self, private_key: Ed25519PrivateKey, document: PreviewJwks) -> None:
        self.private_key = private_key
        self.document = document
        self.authorization_requests = []
        self.exchanges = []
        self.fetches = 0
        self.authorization_error: Exception | None = None
        self.exchange_error: Exception | None = None
        self.fetch_error: Exception | None = None
        self.fetch_gate: threading.Event | None = None
        self._lock = threading.Lock()

    def authorization_url(self, request):
        with self._lock:
            self.authorization_requests.append(request)
        if self.authorization_error is not None:
            raise self.authorization_error
        return append_authorization_request(registration().authorization_endpoint, request)

    def exchange(self, exchange):
        with self._lock:
            self.exchanges.append(exchange)
            exchange_number = len(self.exchanges)
        if self.exchange_error is not None:
            raise self.exchange_error
        claims = replace(
            grant_claims(),
            nonce=exchange.nonce,
            jti=(
                grant_claims().jti if exchange_number == 1 else f"grant_jti_{exchange_number:032d}"
            ),
            issued_at=NOW,
            not_before=NOW,
            expires_at=NOW + 300,
        )
        return _signed(self.private_key, claims)

    def fetch_jwks(self):
        with self._lock:
            self.fetches += 1
        if self.fetch_gate is not None:
            self.fetch_gate.wait(timeout=2)
        if self.fetch_error is not None:
            raise self.fetch_error
        return self.document


def _runtime(
    *,
    private_key: Ed25519PrivateKey | None = None,
    document: PreviewJwks | None = None,
    initial_jwks: PreviewJwks | None = None,
    registration_record: PreviewAuthRegistration | None = None,
    clock=lambda: NOW,
) -> tuple[PreviewGrantRuntime, _Broker, Ed25519PrivateKey]:
    key = private_key or Ed25519PrivateKey.generate()
    key_set = document or _jwks(_jwk(key, grant_claims().key_id))
    broker = _Broker(key, key_set)
    runtime = PreviewGrantRuntime(
        PreviewGrantRuntimeConfig(
            registration_record or registration(),
            "furatena.preview",
            b"session-secret-with-at-least-32-bytes",
        ),
        broker,
        initial_jwks=key_set if initial_jwks is None else initial_jwks,
        clock=clock,
    )
    return runtime, broker, key


def _session(runtime: PreviewGrantRuntime, broker: _Broker) -> str:
    login = runtime.start_browser_login("/")
    established = runtime.complete_browser_login(code=CODE, state=login.state)
    assert broker.exchanges[-1].nonce == broker.authorization_requests[-1].nonce
    return established.token


@pytest.mark.parametrize(
    ("change", "code"),
    [
        ({"issuer": "https://other.example/preview"}, "invalid_issuer"),
        ({"audience": "furatena.other"}, "invalid_audience"),
        ({"repository_id": "repository-other"}, "invalid_binding"),
        ({"pull_request_number": 519}, "invalid_binding"),
        ({"head_sha": "b" * 40}, "invalid_binding"),
        ({"origin": "https://other-preview.example"}, "invalid_binding"),
        (
            {"not_before": NOW + 31, "expires_at": NOW + 300},
            "grant_not_active",
        ),
        (
            {"issued_at": NOW - 60, "not_before": NOW - 60, "expires_at": NOW},
            "expired_grant",
        ),
        ({"scopes": ("preview:metadata",)}, "insufficient_scope"),
    ],
)
def test_signed_grants_enforce_exact_runtime_identity_and_time(
    change: dict[str, object],
    code: str,
) -> None:
    runtime, _, key = _runtime()
    claims = replace(grant_claims(), **change)

    with pytest.raises(PreviewGrantRuntimeError) as failure:
        runtime.authenticate_bearer(_signed(key, claims).compact)

    assert failure.value.code == code


def test_verifier_rejects_non_exact_raw_signature_length() -> None:
    key = Ed25519PrivateKey.generate()
    public = _jwk(key, grant_claims().key_id)

    with pytest.raises(PreviewGrantRuntimeError, match="invalid length"):
        PreviewGrantVerifier._verify_signature(public, b"input", b"s" * 63)


def test_browser_exchange_is_one_time_and_sessions_are_broker_independent() -> None:
    runtime, broker, _ = _runtime()
    login = runtime.start_browser_login("/guide")
    established = runtime.complete_browser_login(code=CODE, state=login.state)

    broker.exchange_error = OSError("broker offline")
    broker.fetch_error = OSError("broker offline")
    assert runtime.authenticate_session(established.token) is not None
    assert runtime.authenticate_session(established.token).source == "session"

    with pytest.raises(PreviewGrantRuntimeError) as replay:
        runtime.complete_browser_login(code=CODE, state=login.state)
    assert replay.value.code == "invalid_state"

    second = runtime.start_browser_login("/")
    with pytest.raises(PreviewGrantRuntimeError) as outage:
        runtime.complete_browser_login(code="d" * 43, state=second.state)
    assert outage.value.code == "broker_unavailable"
    assert outage.value.retryable is True


def test_failed_authorization_redirects_do_not_exhaust_pending_capacity() -> None:
    key = Ed25519PrivateKey.generate()
    document = _jwks(_jwk(key, grant_claims().key_id))
    broker = _Broker(key, document)
    broker.authorization_error = OSError("broker offline")
    runtime = PreviewGrantRuntime(
        PreviewGrantRuntimeConfig(
            registration(),
            "furatena.preview",
            b"session-secret-with-at-least-32-bytes",
            max_pending_logins=1,
        ),
        broker,
        initial_jwks=document,
        clock=lambda: NOW,
    )

    for _ in range(2):
        with pytest.raises(PreviewGrantRuntimeError) as failure:
            runtime.start_browser_login("/")
        assert failure.value.code == "broker_unavailable"


def test_distinct_live_credentials_establish_independent_sessions() -> None:
    runtime, broker, _ = _runtime()
    first = runtime.start_browser_login("/first")
    first_session = runtime.complete_browser_login(code=CODE, state=first.state)
    second = runtime.start_browser_login("/second")
    second_session = runtime.complete_browser_login(code="d" * 43, state=second.state)

    assert runtime.authenticate_session(first_session.token) is not None
    assert runtime.authenticate_session(second_session.token) is not None
    assert len(broker.exchanges) == 2


def test_expired_revocation_entries_are_reclaimed_before_capacity_check() -> None:
    now = [NOW]
    key = Ed25519PrivateKey.generate()
    document = _jwks(_jwk(key, grant_claims().key_id))
    broker = _Broker(key, document)
    runtime = PreviewGrantRuntime(
        PreviewGrantRuntimeConfig(
            registration(),
            "furatena.preview",
            b"session-secret-with-at-least-32-bytes",
            max_revocations=1,
        ),
        broker,
        initial_jwks=document,
        clock=lambda: now[0],
    )
    runtime.apply_revocation(revocation())
    now[0] += (
        registration().lifetimes.session_seconds + registration().lifetimes.clock_skew_seconds + 1
    )

    runtime.apply_revocation(
        replace(
            revocation(),
            jti="replacement_grant_jti_1234567890",
            sequence=2,
        )
    )


def test_revocation_invalidates_bearer_and_established_session() -> None:
    runtime, broker, key = _runtime()
    token = _session(runtime, broker)
    grant = _signed(key, grant_claims()).compact
    assert runtime.authenticate_bearer(grant) is not None
    assert runtime.authenticate_session(token) is not None

    runtime.apply_revocation(revocation())

    with pytest.raises(PreviewGrantRuntimeError) as revoked:
        runtime.authenticate_bearer(grant)
    assert revoked.value.code == "revoked_grant"
    assert runtime.authenticate_session(token) is None


def test_stale_jwks_blocks_new_grants_while_existing_session_stays_local() -> None:
    now = [NOW]
    runtime, broker, key = _runtime(clock=lambda: now[0])
    token = _session(runtime, broker)
    now[0] = 1_893_456_600
    broker.fetch_error = OSError("broker offline")
    claims = replace(
        grant_claims(),
        issued_at=now[0],
        not_before=now[0],
        expires_at=now[0] + 300,
    )

    assert runtime.authenticate_session(token) is not None
    with pytest.raises(PreviewGrantRuntimeError) as stale:
        runtime.authenticate_bearer(_signed(key, claims).compact)
    assert stale.value.code == "jwks_unavailable"
    assert stale.value.retryable is True
    assert runtime.authenticate_session(token) is not None


def test_key_rotation_refreshes_once_and_normal_reads_remain_local() -> None:
    old_key = Ed25519PrivateKey.generate()
    new_key = Ed25519PrivateKey.generate()
    initial = _jwks(_jwk(old_key, "preview-key-old"))
    current = _jwks(
        _jwk(old_key, "preview-key-old"),
        _jwk(new_key, grant_claims().key_id),
    )
    runtime, broker, _ = _runtime(
        private_key=new_key,
        document=current,
        initial_jwks=initial,
    )
    grant = _signed(new_key, grant_claims()).compact

    assert runtime.authenticate_bearer(grant).source == "bearer"
    assert runtime.authenticate_bearer(grant).source == "bearer"
    assert broker.fetches == 1


def test_repeated_unknown_key_is_bounded_by_refresh_cooldown() -> None:
    known_key = Ed25519PrivateKey.generate()
    unknown_key = Ed25519PrivateKey.generate()
    current = _jwks(_jwk(known_key, "preview-key-old"))
    runtime, broker, _ = _runtime(
        private_key=unknown_key,
        document=current,
        initial_jwks=current,
    )
    grant = _signed(unknown_key, grant_claims()).compact

    for _ in range(2):
        with pytest.raises(PreviewGrantRuntimeError) as failure:
            runtime.authenticate_bearer(grant)
        assert failure.value.code == "unknown_key"
    assert broker.fetches == 1


def test_unknown_key_refresh_is_single_flight_under_free_threaded_reads() -> None:
    old_key = Ed25519PrivateKey.generate()
    new_key = Ed25519PrivateKey.generate()
    initial = _jwks(_jwk(old_key, "preview-key-old"))
    current = _jwks(_jwk(new_key, grant_claims().key_id))
    runtime, broker, _ = _runtime(
        private_key=new_key,
        document=current,
        initial_jwks=initial,
    )
    grant = _signed(new_key, grant_claims()).compact
    gate = threading.Event()
    broker.fetch_gate = gate

    with ThreadPoolExecutor(max_workers=8) as pool:
        futures = [pool.submit(runtime.authenticate_bearer, grant) for _ in range(8)]
        gate.set()
        principals = [future.result(timeout=3) for future in futures]

    assert {principal.subject for principal in principals} == {grant_claims().subject}
    assert broker.fetches == 1


def test_concurrent_callback_replay_has_exactly_one_winner() -> None:
    runtime, broker, _ = _runtime()
    login = runtime.start_browser_login("/")

    with ThreadPoolExecutor(max_workers=8) as pool:
        futures = [
            pool.submit(runtime.complete_browser_login, code=CODE, state=login.state)
            for _ in range(8)
        ]
    outcomes = []
    for future in futures:
        try:
            outcomes.append(future.result())
        except PreviewGrantRuntimeError as error:
            outcomes.append(error.code)

    assert len([item for item in outcomes if not isinstance(item, str)]) == 1
    assert outcomes.count("invalid_state") == 7
    assert len(broker.exchanges) == 1


def _hosted_docs(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    environment_overrides: dict[str, str] | None = None,
) -> tuple[DocsApp, PreviewGrantRuntime, _Broker]:
    environment = {
        "FURA_PR_PREVIEW": "1",
        "FURA_PREVIEW_PR_NUMBER": "518",
        "FURA_PREVIEW_SHA": "a" * 40,
        "FURA_BUILD_GIT_SHA": "a" * 40,
        "FURA_PREVIEW_REVIEW_URL": "https://github.com/example/furatena/pull/518",
        "FURA_PREVIEW_ORIGIN": "https://pr-518.preview.example",
    }
    environment.update(environment_overrides or {})
    for name, value in environment.items():
        monkeypatch.setenv(name, value)
    monkeypatch.delenv("FURA_PREVIEW_AUTH_TOKEN", raising=False)

    app_root = tmp_path / "app"
    content_root = tmp_path / "content"
    app_root.mkdir()
    content_root.mkdir()
    copy_app_theme(app_root, REPO / "app")
    write_minimal_docs_yaml(app_root / "docs.yaml")
    write_mounts_yaml(app_root / "mounts.yaml", content_root)
    (content_root / "_index.md").write_text("# Hosted preview\n", encoding="utf-8")
    runtime, broker, _ = _runtime()
    docs = DocsApp.from_paths(
        app_root / "docs.yaml",
        repo_root=tmp_path,
        autodoc=False,
        serve=ServeConfig(ServeMode.PREVIEW, None, False, False),
        preview_grant_runtime=runtime,
    )
    return docs, runtime, broker


@pytest.mark.parametrize(
    "environment_overrides",
    [
        {"FURA_PREVIEW_PR_NUMBER": "519"},
        {"FURA_PREVIEW_SHA": "b" * 40, "FURA_BUILD_GIT_SHA": "b" * 40},
        {"FURA_PREVIEW_ORIGIN": "https://other-preview.example"},
    ],
)
def test_docs_app_rejects_runtime_identity_drift(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    environment_overrides: dict[str, str],
) -> None:
    with pytest.raises(PreviewGrantRuntimeError) as failure:
        _hosted_docs(
            tmp_path,
            monkeypatch,
            environment_overrides=environment_overrides,
        )
    assert failure.value.code == "invalid_configuration"


def test_hosted_browser_session_cookie_and_surface_policy(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    docs, runtime, broker = _hosted_docs(tmp_path, monkeypatch)
    client = TestClient(docs.create_app())

    async def verify() -> None:
        bearer = _signed(broker.private_key, grant_claims()).compact
        machine = await client.get(
            "/catalog.json",
            headers={"Authorization": f"Bearer {bearer}"},
        )
        assert machine.status == 200

        search = await client.get("/search", headers={"Accept": "text/html"})
        assert search.status == 302

        login = await client.get("/", headers={"Accept": "text/html"})
        assert login.status == 302
        assert login.header("Location").startswith(registration().authorization_endpoint)
        state = broker.authorization_requests[-1].state
        callback = await client.get(f"/_fura/preview-auth/callback?code={CODE}&state={state}")
        assert callback.status == 303
        cookie = callback.header("Set-Cookie") or ""
        assert cookie.startswith("__Host-furatena-preview=")
        assert "; Path=/;" in cookie
        assert "; Secure;" in cookie
        assert "; HttpOnly;" in cookie
        assert "; SameSite=Lax" in cookie
        assert "Domain=" not in cookie
        token = cookie.split(";", 1)[0]

        before = runtime.metrics()
        for path in ("/", "/catalog.json", "/llms.txt", "/docs-theme/branding/favicon.svg"):
            response = await client.get(path, headers={"Cookie": token})
            assert response.status == 200
            assert response.header("Cache-Control") == "private, no-store"
            assert response.header("X-Robots-Tag") == ("noindex, nofollow, noarchive, nosnippet")
            assert {item.strip() for item in response.header("Vary").split(",")} >= {
                "Authorization",
                "Cookie",
            }
        after = runtime.metrics()
        assert after.broker_exchanges == before.broker_exchanges
        assert after.jwks_refreshes == before.jwks_refreshes

        for path in (
            "/guide.md",
            "/search.json",
            "/catalog.json",
            "/graph/query.json",
            "/llms.txt",
            "/metadata.json",
            "/dcp/resources",
            "/mcp/resources",
            "/cli.json",
            "/docs-theme/branding/favicon.svg",
        ):
            denied = await client.get(path)
            assert denied.status == 401
            assert denied.header("WWW-Authenticate").startswith("Bearer ")
            assert denied.content_type.startswith("application/problem+json")
            assert {item.strip() for item in denied.header("Vary").split(",")} >= {
                "Authorization",
                "Cookie",
            }

        for path in ("/healthz", "/readyz"):
            probe = await client.get(path)
            assert probe.status in {200, 503}
            assert probe.header("X-Robots-Tag") == ("noindex, nofollow, noarchive, nosnippet")

    asyncio.run(verify())


def test_broker_error_text_is_sanitized_before_browser_response(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    docs, _, broker = _hosted_docs(tmp_path, monkeypatch)
    broker.exchange_error = PreviewGrantRuntimeError(
        "<script>alert('unsafe')</script>",
        code="broker_unavailable",
        status=503,
        retryable=True,
    )
    client = TestClient(docs.create_app())

    async def verify() -> None:
        await client.get("/", headers={"Accept": "text/html"})
        state = broker.authorization_requests[-1].state
        response = await client.get(f"/_fura/preview-auth/callback?code={CODE}&state={state}")
        assert response.status == 503
        assert "<script>" not in response.text
        assert "&lt;script&gt;" not in response.text
        assert "broker is unavailable" in response.text

    asyncio.run(verify())


def test_browser_failure_html_escapes_error_boundary() -> None:
    response = _browser_failure(
        PreviewGrantRuntimeError(
            "<script>alert('unsafe')</script>",
            code="unsafe",
        )
    )

    assert "<script>" not in response.body
    assert "&lt;script&gt;" in response.body
