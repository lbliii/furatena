"""Local verification and commit-bound sessions for hosted preview grants."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import re
import secrets
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from threading import Condition, RLock
from types import MappingProxyType
from typing import Protocol
from urllib.parse import SplitResult, urlencode, urlsplit, urlunsplit

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ec import (
    ECDSA,
    SECP256R1,
    EllipticCurvePublicNumbers,
)
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from cryptography.hazmat.primitives.asymmetric.utils import encode_dss_signature
from cryptography.hazmat.primitives.hashes import SHA256

from furatena.catalog.preview_auth_contracts import (
    PREVIEW_AUTH_SCHEMA_VERSION,
    PreviewAuthAlgorithm,
    PreviewAuthBinding,
    PreviewAuthClientKind,
    PreviewAuthConsumptionKind,
    PreviewAuthorizationRequest,
    PreviewAuthRegistration,
    PreviewGrantClaims,
    PreviewGrantExchange,
    PreviewJwk,
    PreviewJwks,
    PreviewRevocationSignal,
    PreviewSignedGrant,
    canonical_issuer,
    canonical_preview_auth_json,
    canonical_preview_origin,
    pkce_s256_challenge,
    preview_auth_consumption_digest,
)

PREVIEW_SESSION_COOKIE = "__Host-furatena-preview"
PREVIEW_CALLBACK_PATH = "/_fura/preview-auth/callback"
PREVIEW_REQUIRED_SCOPE = "preview:read"
_MAX_PENDING_LOGINS = 1024
_MAX_SESSIONS = 4096
_MAX_REPLAY_ENTRIES = 8192
_MAX_REVOCATIONS = 4096
_REFRESH_WAIT_SECONDS = 10.0
_REFRESH_COOLDOWN_SECONDS = 30.0
_OPAQUE_CALLBACK_RE = re.compile(r"^[A-Za-z0-9_-]{43,256}$")


class PreviewGrantRuntimeError(ValueError):
    """A safe, stable hosted-preview authorization failure."""

    __slots__ = ("code", "retryable", "status")

    def __init__(
        self,
        message: str,
        *,
        code: str,
        status: int = 401,
        retryable: bool = False,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.status = status
        self.retryable = retryable


class PreviewGrantBroker(Protocol):
    """Bounded broker operations; implementations own HTTP details and timeouts."""

    def authorization_url(self, request: PreviewAuthorizationRequest) -> str: ...

    def exchange(self, exchange: PreviewGrantExchange) -> PreviewSignedGrant: ...

    def fetch_jwks(self) -> PreviewJwks: ...


@dataclass(frozen=True, slots=True)
class PreviewGrantRuntimeConfig:
    registration: PreviewAuthRegistration
    audience: str
    session_secret: bytes = field(repr=False)
    cookie_name: str = PREVIEW_SESSION_COOKIE
    required_scope: str = PREVIEW_REQUIRED_SCOPE
    max_pending_logins: int = _MAX_PENDING_LOGINS
    max_sessions: int = _MAX_SESSIONS
    max_replay_entries: int = _MAX_REPLAY_ENTRIES
    max_revocations: int = _MAX_REVOCATIONS

    def __post_init__(self) -> None:
        if self.audience not in self.registration.audiences:
            raise PreviewGrantRuntimeError(
                "The hosted preview audience is not advertised by its registration.",
                code="invalid_configuration",
                status=500,
            )
        if not isinstance(self.session_secret, bytes) or len(self.session_secret) < 32:
            raise PreviewGrantRuntimeError(
                "The hosted preview session secret must contain at least 32 bytes.",
                code="invalid_configuration",
                status=500,
            )
        if self.cookie_name != PREVIEW_SESSION_COOKIE:
            raise PreviewGrantRuntimeError(
                "The hosted preview cookie must use the v1 __Host-furatena-preview name.",
                code="invalid_configuration",
                status=500,
            )
        if PREVIEW_CALLBACK_PATH not in self.registration.redirect_paths:
            raise PreviewGrantRuntimeError(
                "The hosted preview registration does not allow its runtime callback path.",
                code="invalid_configuration",
                status=500,
            )
        for name in (
            "max_pending_logins",
            "max_sessions",
            "max_replay_entries",
            "max_revocations",
        ):
            value = getattr(self, name)
            if not isinstance(value, int) or isinstance(value, bool) or not 1 <= value <= 65_536:
                raise PreviewGrantRuntimeError(
                    f"The hosted preview {name} limit must be in [1, 65536].",
                    code="invalid_configuration",
                    status=500,
                )

    @property
    def binding(self) -> PreviewAuthBinding:
        return self.registration.binding


@dataclass(frozen=True, slots=True)
class PreviewGrantRuntimeMetrics:
    authorization_redirects: int
    broker_exchanges: int
    jwks_refreshes: int
    session_reads: int
    bearer_reads: int
    denials: int


@dataclass(frozen=True, slots=True)
class PreviewPrincipal:
    subject: str
    jti: str
    expires_at: int
    source: str


@dataclass(frozen=True, slots=True)
class PreviewLoginStart:
    location: str
    state: str = field(repr=False)


@dataclass(frozen=True, slots=True)
class PreviewSessionEstablished:
    token: str = field(repr=False)
    max_age_seconds: int
    return_path: str


@dataclass(frozen=True, slots=True)
class _PendingLogin:
    request_id: str
    state: str = field(repr=False)
    nonce: str = field(repr=False)
    verifier: str = field(repr=False)
    redirect_uri: str
    return_path: str
    expires_at: int


@dataclass(frozen=True, slots=True)
class _SessionRecord:
    subject: str
    jti: str
    key_id: str
    expires_at: int
    binding: PreviewAuthBinding


def _unix_timestamp(value: str) -> int:
    return int(datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp())


def _opaque_secret() -> str:
    return base64.urlsafe_b64encode(secrets.token_bytes(32)).rstrip(b"=").decode("ascii")


def _session_digest(secret: bytes, token: str) -> str:
    return hmac.new(
        secret,
        b"furatena.preview-auth.session.v1\0" + token.encode("ascii"),
        hashlib.sha256,
    ).hexdigest()


def _state_digest(secret: bytes, state: str) -> str:
    if _OPAQUE_CALLBACK_RE.fullmatch(state) is None:
        raise PreviewGrantRuntimeError(
            "The preview sign-in state is malformed; restart authorization.",
            code="invalid_state",
        )
    return hmac.new(
        secret,
        b"furatena.preview-auth.state.v1\0" + state.encode("ascii"),
        hashlib.sha256,
    ).hexdigest()


def _canonical_return_path(path: str) -> str:
    if not path.startswith("/") or path.startswith("//") or "\\" in path:
        return "/"
    return path if path != PREVIEW_CALLBACK_PATH else "/"


def _validate_authorization_location(value: str, endpoint: str) -> str:
    if len(value) > 8192:
        raise PreviewGrantRuntimeError(
            "The preview broker authorization location is too large.",
            code="invalid_broker_response",
            status=502,
        )
    parsed = urlsplit(value)
    expected = urlsplit(endpoint)
    if (
        parsed.scheme != "https"
        or parsed.username is not None
        or parsed.password is not None
        or parsed.fragment
        or (parsed.scheme, parsed.netloc, parsed.path)
        != (expected.scheme, expected.netloc, expected.path)
    ):
        raise PreviewGrantRuntimeError(
            "The preview broker returned an invalid authorization location.",
            code="invalid_broker_response",
            status=502,
        )
    return urlunsplit(SplitResult("https", parsed.netloc, parsed.path, parsed.query, ""))


class PreviewJwksCache:
    """One-process immutable JWKS snapshots with RLock-owned single-flight refresh."""

    __slots__ = (
        "_condition",
        "_document",
        "_fetch",
        "_generation",
        "_issuer",
        "_last_refresh_at",
        "_last_refresh_error",
        "_lock",
        "_refresh_count",
        "_refreshing",
        "_wall_clock",
    )

    def __init__(
        self,
        issuer: str,
        fetch: Callable[[], PreviewJwks],
        *,
        initial: PreviewJwks | None = None,
        wall_clock: Callable[[], float] = time.time,
    ) -> None:
        self._issuer = canonical_issuer(issuer)
        self._fetch = fetch
        self._wall_clock = wall_clock
        self._lock = RLock()
        self._condition = Condition(self._lock)
        self._refreshing = False
        self._refresh_count = 0
        self._generation = 0
        self._last_refresh_at: float | None = None
        self._last_refresh_error: str | None = None
        self._document = self._validated(initial) if initial is not None else None

    @property
    def refresh_count(self) -> int:
        with self._lock:
            return self._refresh_count

    def _validated(self, document: PreviewJwks) -> PreviewJwks:
        if document.issuer != self._issuer:
            raise PreviewGrantRuntimeError(
                "The preview JWKS issuer does not match the registered broker.",
                code="invalid_jwks",
                status=503,
                retryable=True,
            )
        return document

    def _fresh(self, document: PreviewJwks | None, now: float) -> bool:
        return document is not None and now < _unix_timestamp(document.stale_after)

    @staticmethod
    def _find(document: PreviewJwks | None, key_id: str) -> PreviewJwk | None:
        if document is None:
            return None
        return next((key for key in document.keys if key.key_id == key_id), None)

    def resolve(self, key_id: str) -> PreviewJwk:
        now = self._wall_clock()
        with self._condition:
            current = self._document
            key = self._find(current, key_id)
            if key is not None and self._fresh(current, now):
                return key
            if self._refreshing:
                generation = self._generation
                self._condition.wait_for(
                    lambda: not self._refreshing, timeout=_REFRESH_WAIT_SECONDS
                )
                current = self._document
                key = self._find(current, key_id)
                if key is not None and self._fresh(current, self._wall_clock()):
                    return key
                if self._refreshing:
                    raise PreviewGrantRuntimeError(
                        "Preview signing-key refresh timed out; retry authorization again later.",
                        code="jwks_unavailable",
                        status=503,
                        retryable=True,
                    )
                if self._generation != generation:
                    if self._last_refresh_error is not None:
                        raise PreviewGrantRuntimeError(
                            "Preview signing keys are unavailable; retry authorization later.",
                            code="jwks_unavailable",
                            status=503,
                            retryable=True,
                        )
                    if self._fresh(current, self._wall_clock()):
                        raise PreviewGrantRuntimeError(
                            "The preview grant signing key is unknown; restart authorization.",
                            code="unknown_key",
                        )
                    raise PreviewGrantRuntimeError(
                        "The preview signing-key set is stale; retry authorization later.",
                        code="stale_key_set",
                        status=503,
                        retryable=True,
                    )
            if (
                self._last_refresh_at is not None
                and now - self._last_refresh_at < _REFRESH_COOLDOWN_SECONDS
            ):
                if self._last_refresh_error is not None or not self._fresh(current, now):
                    raise PreviewGrantRuntimeError(
                        "Preview signing keys are unavailable; retry authorization later.",
                        code="jwks_unavailable",
                        status=503,
                        retryable=True,
                    )
                raise PreviewGrantRuntimeError(
                    "The preview grant signing key is unknown; restart authorization.",
                    code="unknown_key",
                )
            self._refreshing = True

        try:
            refreshed = self._validated(self._fetch())
        except Exception as error:
            with self._condition:
                self._refreshing = False
                self._generation += 1
                self._last_refresh_at = self._wall_clock()
                self._last_refresh_error = type(error).__name__
                self._condition.notify_all()
                current = self._document
                key = self._find(current, key_id)
                if key is not None and self._fresh(current, self._wall_clock()):
                    return key
            raise PreviewGrantRuntimeError(
                "Preview signing keys are unavailable; retry authorization later.",
                code="jwks_unavailable",
                status=503,
                retryable=True,
            ) from error

        with self._condition:
            self._document = refreshed
            self._refresh_count += 1
            self._generation += 1
            self._last_refresh_at = self._wall_clock()
            self._last_refresh_error = None
            self._refreshing = False
            self._condition.notify_all()
            key = self._find(refreshed, key_id)
            if key is None:
                raise PreviewGrantRuntimeError(
                    "The preview grant signing key is unknown; restart authorization.",
                    code="unknown_key",
                )
            if not self._fresh(refreshed, self._wall_clock()):
                raise PreviewGrantRuntimeError(
                    "The preview signing-key set is stale; retry authorization later.",
                    code="stale_key_set",
                    status=503,
                    retryable=True,
                )
            return key


class _ReplayStore:
    __slots__ = ("_binding", "_entries", "_issuer", "_limit", "_lock", "_secret")

    def __init__(self, config: PreviewGrantRuntimeConfig) -> None:
        self._binding = config.binding
        self._issuer = config.registration.issuer
        self._secret = config.session_secret
        self._limit = config.max_replay_entries
        self._entries: dict[str, int] = {}
        self._lock = RLock()

    def consume(
        self, kind: PreviewAuthConsumptionKind, value: str, *, expires_at: int, now: int
    ) -> None:
        digest = preview_auth_consumption_digest(
            kind,
            value,
            issuer=self._issuer,
            binding=self._binding,
            secret_key=self._secret,
        )
        with self._lock:
            self._entries = {
                existing: expiry for existing, expiry in self._entries.items() if expiry > now
            }
            if digest in self._entries:
                raise PreviewGrantRuntimeError(
                    "The preview authorization credential was already consumed; restart authorization.",
                    code="replayed_credential",
                )
            if len(self._entries) >= self._limit:
                raise PreviewGrantRuntimeError(
                    "Preview authorization replay protection is at capacity; retry later.",
                    code="replay_cache_full",
                    status=503,
                    retryable=True,
                )
            self._entries[digest] = expires_at


class _PendingStore:
    __slots__ = ("_entries", "_limit", "_lock", "_secret")

    def __init__(self, config: PreviewGrantRuntimeConfig) -> None:
        self._secret = config.session_secret
        self._limit = config.max_pending_logins
        self._entries: dict[str, _PendingLogin] = {}
        self._lock = RLock()

    def add(self, pending: _PendingLogin, *, now: int) -> None:
        digest = _state_digest(self._secret, pending.state)
        with self._lock:
            self._entries = {
                key: value for key, value in self._entries.items() if value.expires_at > now
            }
            if len(self._entries) >= self._limit:
                raise PreviewGrantRuntimeError(
                    "Too many preview sign-ins are pending; retry later.",
                    code="pending_login_capacity",
                    status=503,
                    retryable=True,
                )
            self._entries[digest] = pending

    def consume(self, state: str, *, now: int) -> _PendingLogin:
        digest = _state_digest(self._secret, state)
        with self._lock:
            pending = self._entries.pop(digest, None)
        if (
            pending is None
            or pending.expires_at <= now
            or not hmac.compare_digest(pending.state, state)
        ):
            raise PreviewGrantRuntimeError(
                "The preview sign-in state is invalid or expired; restart authorization.",
                code="invalid_state",
            )
        return pending

    def discard(self, state: str) -> None:
        digest = _state_digest(self._secret, state)
        with self._lock:
            self._entries.pop(digest, None)


class _RevocationStore:
    __slots__ = (
        "_binding",
        "_entries",
        "_issuer",
        "_latest_digest",
        "_latest_sequence",
        "_limit",
        "_lock",
        "_overflowed",
        "_retention_seconds",
        "_secret",
    )

    def __init__(self, config: PreviewGrantRuntimeConfig) -> None:
        self._binding = config.binding
        self._issuer = config.registration.issuer
        self._secret = config.session_secret
        self._limit = config.max_revocations
        self._retention_seconds = (
            config.registration.lifetimes.session_seconds
            + config.registration.lifetimes.clock_skew_seconds
        )
        self._entries: dict[str, tuple[int, str, str]] = {}
        self._latest_sequence = 0
        self._latest_digest = ""
        self._overflowed = False
        self._lock = RLock()

    def apply(self, signal: PreviewRevocationSignal, *, now: int) -> None:
        if signal.issuer != self._issuer or signal.binding != self._binding:
            raise PreviewGrantRuntimeError(
                "The preview revocation does not match this runtime binding.",
                code="invalid_revocation",
            )
        digest = preview_auth_consumption_digest(
            PreviewAuthConsumptionKind.JTI,
            signal.jti,
            issuer=self._issuer,
            binding=self._binding,
            secret_key=self._secret,
        )
        expiry = now + self._retention_seconds
        signal_digest = hashlib.sha256(canonical_preview_auth_json(signal)).hexdigest()
        with self._lock:
            self._entries = {key: value for key, value in self._entries.items() if value[0] > now}
            if signal.sequence < self._latest_sequence:
                raise PreviewGrantRuntimeError(
                    "The preview revocation sequence is stale; request the current stream.",
                    code="stale_revocation",
                )
            if signal.sequence == self._latest_sequence:
                if hmac.compare_digest(signal_digest, self._latest_digest):
                    return
                raise PreviewGrantRuntimeError(
                    "The preview revocation sequence conflicts with prior state.",
                    code="conflicting_revocation",
                )
            if len(self._entries) >= self._limit:
                self._overflowed = True
                raise PreviewGrantRuntimeError(
                    "Preview revocation state is at capacity and has failed closed.",
                    code="revocation_capacity",
                    status=503,
                )
            self._entries[digest] = (expiry, signal.subject, signal.key_id)
            self._latest_sequence = signal.sequence
            self._latest_digest = signal_digest

    def revoked(self, jti: str, subject: str, key_id: str, *, now: int) -> bool:
        digest = preview_auth_consumption_digest(
            PreviewAuthConsumptionKind.JTI,
            jti,
            issuer=self._issuer,
            binding=self._binding,
            secret_key=self._secret,
        )
        with self._lock:
            if self._overflowed:
                return True
            entry = self._entries.get(digest)
            if entry is not None and entry[0] <= now:
                self._entries.pop(digest, None)
                return False
            return (
                entry is not None
                and hmac.compare_digest(entry[1], subject)
                and hmac.compare_digest(entry[2], key_id)
            )


class _SessionStore:
    __slots__ = ("_binding", "_entries", "_limit", "_lock", "_secret")

    def __init__(self, config: PreviewGrantRuntimeConfig) -> None:
        self._binding = config.binding
        self._secret = config.session_secret
        self._limit = config.max_sessions
        self._entries: dict[str, _SessionRecord] = {}
        self._lock = RLock()

    def establish(self, claims: PreviewGrantClaims, *, expires_at: int, now: int) -> str:
        token = _opaque_secret()
        digest = _session_digest(self._secret, token)
        record = _SessionRecord(
            claims.subject,
            claims.jti,
            claims.key_id,
            expires_at,
            self._binding,
        )
        with self._lock:
            self._entries = {
                key: value for key, value in self._entries.items() if value.expires_at > now
            }
            if len(self._entries) >= self._limit:
                raise PreviewGrantRuntimeError(
                    "Preview session capacity is exhausted; retry authorization again later.",
                    code="session_capacity",
                    status=503,
                    retryable=True,
                )
            self._entries[digest] = record
        return token

    def authenticate(self, token: str, *, now: int) -> _SessionRecord | None:
        if _OPAQUE_CALLBACK_RE.fullmatch(token) is None:
            return None
        try:
            digest = _session_digest(self._secret, token)
        except UnicodeEncodeError:
            return None
        with self._lock:
            record = self._entries.get(digest)
            if record is None:
                return None
            if record.expires_at <= now or record.binding != self._binding:
                self._entries.pop(digest, None)
                return None
            return record


def _decode_b64url(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


class PreviewGrantVerifier:
    """Verify a structural v1 grant against local keys and exact runtime identity."""

    __slots__ = ("_cache", "_clock", "_config", "_revocations")

    def __init__(
        self,
        config: PreviewGrantRuntimeConfig,
        cache: PreviewJwksCache,
        revocations: _RevocationStore,
        *,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self._config = config
        self._cache = cache
        self._revocations = revocations
        self._clock = clock

    def verify(self, compact: str) -> PreviewGrantClaims:
        try:
            grant = PreviewSignedGrant(compact)
            header_segment, payload_segment, signature_segment = grant.compact.split(".")
            header = json.loads(_decode_b64url(header_segment))
            algorithm = PreviewAuthAlgorithm(header["alg"])
            key = self._cache.resolve(grant.key_id)
            if key.algorithm is not algorithm:
                raise PreviewGrantRuntimeError(
                    "The preview grant algorithm does not match its signing key.",
                    code="algorithm_mismatch",
                )
            self._verify_signature(
                key,
                f"{header_segment}.{payload_segment}".encode("ascii"),
                _decode_b64url(signature_segment),
            )
            claims = grant.claims
        except PreviewGrantRuntimeError:
            raise
        except (InvalidSignature, KeyError, TypeError, ValueError, json.JSONDecodeError) as error:
            raise PreviewGrantRuntimeError(
                "The preview grant is malformed or has an invalid signature.",
                code="invalid_grant",
            ) from error
        self._validate_claims(claims)
        return claims

    @staticmethod
    def _verify_signature(key: PreviewJwk, signing_input: bytes, signature: bytes) -> None:
        if len(signature) != 64:
            raise PreviewGrantRuntimeError(
                "The preview grant signature has an invalid length.",
                code="invalid_grant",
            )
        x = _decode_b64url(key.x)
        if key.algorithm is PreviewAuthAlgorithm.EDDSA:
            Ed25519PublicKey.from_public_bytes(x).verify(signature, signing_input)
            return
        if key.y is None:
            raise PreviewGrantRuntimeError(
                "The preview ES256 verification key is incomplete.",
                code="invalid_jwks",
                status=503,
            )
        public_key = EllipticCurvePublicNumbers(
            int.from_bytes(x),
            int.from_bytes(_decode_b64url(key.y)),
            SECP256R1(),
        ).public_key()
        r = int.from_bytes(signature[:32])
        s = int.from_bytes(signature[32:])
        public_key.verify(encode_dss_signature(r, s), signing_input, ECDSA(SHA256()))

    def _validate_claims(self, claims: PreviewGrantClaims) -> None:
        config = self._config
        expected = config.binding
        now = int(self._clock())
        skew = config.registration.lifetimes.clock_skew_seconds
        registration_created = _unix_timestamp(config.registration.created_at)
        registration_expires = _unix_timestamp(config.registration.expires_at)
        if now >= registration_expires:
            raise PreviewGrantRuntimeError(
                "The preview authorization registration has expired.",
                code="expired_registration",
            )
        if claims.issuer != config.registration.issuer:
            raise PreviewGrantRuntimeError(
                "The preview grant issuer does not match the registered authorization broker.",
                code="invalid_issuer",
            )
        if claims.audience != config.audience:
            raise PreviewGrantRuntimeError(
                "The preview grant audience does not match this protected documentation runtime.",
                code="invalid_audience",
            )
        if (
            claims.repository_id != expected.repository_id
            or claims.pull_request_number != expected.pull_request_number
            or claims.head_sha != expected.head_sha
            or claims.origin != expected.origin
        ):
            raise PreviewGrantRuntimeError(
                "The preview grant does not match this repository, pull request, revision, and origin.",
                code="invalid_binding",
            )
        if claims.issued_at > now + skew or claims.not_before > now + skew:
            raise PreviewGrantRuntimeError(
                "The preview grant is not active for the current runtime clock.",
                code="grant_not_active",
            )
        if claims.issued_at < registration_created - skew:
            raise PreviewGrantRuntimeError(
                "The preview grant predates this authorization registration.",
                code="invalid_grant_time",
            )
        if (
            claims.expires_at - claims.issued_at > config.registration.lifetimes.grant_seconds
            or claims.expires_at > registration_expires
        ):
            raise PreviewGrantRuntimeError(
                "The preview grant exceeds its registered authorization window.",
                code="invalid_grant_time",
            )
        if now >= claims.expires_at:
            raise PreviewGrantRuntimeError(
                "The preview grant has expired and must be replaced before retrying.",
                code="expired_grant",
            )
        if (
            PREVIEW_REQUIRED_SCOPE not in claims.scopes
            or config.required_scope not in claims.scopes
        ):
            raise PreviewGrantRuntimeError(
                "The preview grant does not permit protected documentation reads.",
                code="insufficient_scope",
                status=403,
            )
        if self._revocations.revoked(
            claims.jti,
            claims.subject,
            claims.key_id,
            now=now,
        ):
            raise PreviewGrantRuntimeError(
                "The preview grant was revoked and can no longer authorize requests.",
                code="revoked_grant",
            )


class PreviewGrantRuntime:
    """Own hosted authorization state for one exact preview process."""

    __slots__ = (
        "_bearer_reads",
        "_broker",
        "_broker_exchanges",
        "_clock",
        "_config",
        "_denials",
        "_jwks",
        "_lock",
        "_pending",
        "_redirects",
        "_replay",
        "_revocations",
        "_session_reads",
        "_sessions",
        "_verifier",
    )

    def __init__(
        self,
        config: PreviewGrantRuntimeConfig,
        broker: PreviewGrantBroker,
        *,
        initial_jwks: PreviewJwks | None = None,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self._config = config
        self._broker = broker
        self._clock = clock
        self._lock = RLock()
        self._redirects = 0
        self._broker_exchanges = 0
        self._session_reads = 0
        self._bearer_reads = 0
        self._denials = 0
        self._pending = _PendingStore(config)
        self._replay = _ReplayStore(config)
        self._revocations = _RevocationStore(config)
        self._sessions = _SessionStore(config)
        self._jwks = PreviewJwksCache(
            config.registration.issuer,
            broker.fetch_jwks,
            initial=initial_jwks,
            wall_clock=clock,
        )
        self._verifier = PreviewGrantVerifier(
            config,
            self._jwks,
            self._revocations,
            clock=clock,
        )

    @property
    def config(self) -> PreviewGrantRuntimeConfig:
        return self._config

    def assert_environment(self, *, pull_request_number: int, head_sha: str, origin: str) -> None:
        binding = self._config.binding
        if (
            binding.pull_request_number != pull_request_number
            or binding.head_sha != head_sha
            or binding.origin != canonical_preview_origin(origin)
        ):
            raise PreviewGrantRuntimeError(
                "The hosted grant registration does not match the running preview identity.",
                code="invalid_configuration",
                status=500,
            )

    def start_browser_login(self, return_path: str) -> PreviewLoginStart:
        now = int(self._clock())
        ttl = min(self._config.registration.lifetimes.code_seconds, 300)
        verifier = _opaque_secret()
        state = _opaque_secret()
        pending = _PendingLogin(
            request_id=_opaque_secret(),
            state=state,
            nonce=_opaque_secret(),
            verifier=verifier,
            redirect_uri=f"{self._config.binding.origin}{PREVIEW_CALLBACK_PATH}",
            return_path=_canonical_return_path(return_path),
            expires_at=now + ttl,
        )
        request = PreviewAuthorizationRequest(
            request_id=pending.request_id,
            client_kind=PreviewAuthClientKind.BROWSER,
            binding=self._config.binding,
            audience=self._config.audience,
            nonce=pending.nonce,
            requested_at=datetime.fromtimestamp(now, UTC)
            .isoformat(timespec="seconds")
            .replace("+00:00", "Z"),
            expires_at=datetime.fromtimestamp(now + ttl, UTC)
            .isoformat(timespec="seconds")
            .replace("+00:00", "Z"),
            redirect_uri=pending.redirect_uri,
            state=state,
            code_challenge=pkce_s256_challenge(verifier),
            code_challenge_method="S256",
        )
        self._pending.add(pending, now=now)
        try:
            authorization_location = self._broker.authorization_url(request)
        except Exception as error:
            self._pending.discard(state)
            raise PreviewGrantRuntimeError(
                "The preview authorization broker is unavailable; retry sign-in later.",
                code="broker_unavailable",
                status=503,
                retryable=True,
            ) from error
        try:
            location = _validate_authorization_location(
                authorization_location,
                self._config.registration.authorization_endpoint,
            )
        except PreviewGrantRuntimeError:
            self._pending.discard(state)
            raise
        with self._lock:
            self._redirects += 1
        return PreviewLoginStart(location, state)

    def complete_browser_login(self, *, code: str, state: str) -> PreviewSessionEstablished:
        now = int(self._clock())
        pending = self._pending.consume(state, now=now)
        self._replay.consume(
            PreviewAuthConsumptionKind.CODE,
            code,
            expires_at=pending.expires_at,
            now=now,
        )
        exchange = PreviewGrantExchange(
            exchange_id=_opaque_secret(),
            client_kind=PreviewAuthClientKind.BROWSER,
            code=code,
            binding=self._config.binding,
            audience=self._config.audience,
            nonce=pending.nonce,
            exchanged_at=datetime.fromtimestamp(now, UTC)
            .isoformat(timespec="seconds")
            .replace("+00:00", "Z"),
            redirect_uri=pending.redirect_uri,
            code_verifier=pending.verifier,
        )
        try:
            with self._lock:
                self._broker_exchanges += 1
            grant = self._broker.exchange(exchange)
        except Exception as error:
            raise PreviewGrantRuntimeError(
                "The preview authorization broker is unavailable; restart sign-in later.",
                code="broker_unavailable",
                status=503,
                retryable=True,
            ) from error
        claims = self._verifier.verify(grant.compact)
        if not hmac.compare_digest(claims.nonce, pending.nonce):
            raise PreviewGrantRuntimeError(
                "The preview grant does not match the browser sign-in nonce.",
                code="invalid_nonce",
            )
        self._replay.consume(
            PreviewAuthConsumptionKind.NONCE,
            claims.nonce,
            expires_at=claims.expires_at,
            now=now,
        )
        self._replay.consume(
            PreviewAuthConsumptionKind.JTI,
            claims.jti,
            expires_at=claims.expires_at,
            now=now,
        )
        registration_expiry = _unix_timestamp(self._config.registration.expires_at)
        expires_at = min(
            registration_expiry,
            now + self._config.registration.lifetimes.session_seconds,
        )
        if expires_at <= now:
            raise PreviewGrantRuntimeError(
                "The preview registration expired before a session could be established.",
                code="expired_registration",
            )
        token = self._sessions.establish(claims, expires_at=expires_at, now=now)
        return PreviewSessionEstablished(token, expires_at - now, pending.return_path)

    def authenticate_session(self, token: str) -> PreviewPrincipal | None:
        now = int(self._clock())
        record = self._sessions.authenticate(token, now=now)
        if record is None or self._revocations.revoked(
            record.jti,
            record.subject,
            record.key_id,
            now=now,
        ):
            return None
        with self._lock:
            self._session_reads += 1
        return PreviewPrincipal(record.subject, record.jti, record.expires_at, "session")

    def authenticate_bearer(self, compact: str) -> PreviewPrincipal:
        claims = self._verifier.verify(compact)
        with self._lock:
            self._bearer_reads += 1
        return PreviewPrincipal(claims.subject, claims.jti, claims.expires_at, "bearer")

    def apply_revocation(self, signal: PreviewRevocationSignal) -> None:
        if signal.audience != self._config.audience:
            raise PreviewGrantRuntimeError(
                "The preview revocation audience does not match this runtime.",
                code="invalid_revocation",
            )
        self._revocations.apply(signal, now=int(self._clock()))

    def record_denial(self) -> None:
        with self._lock:
            self._denials += 1

    def metrics(self) -> PreviewGrantRuntimeMetrics:
        with self._lock:
            return PreviewGrantRuntimeMetrics(
                authorization_redirects=self._redirects,
                broker_exchanges=self._broker_exchanges,
                jwks_refreshes=self._jwks.refresh_count,
                session_reads=self._session_reads,
                bearer_reads=self._bearer_reads,
                denials=self._denials,
            )

    def state_snapshot(self) -> MappingProxyType[str, int]:
        metrics = self.metrics()
        return MappingProxyType(
            {
                "authorization_redirects": metrics.authorization_redirects,
                "broker_exchanges": metrics.broker_exchanges,
                "jwks_refreshes": metrics.jwks_refreshes,
                "session_reads": metrics.session_reads,
                "bearer_reads": metrics.bearer_reads,
                "denials": metrics.denials,
                "schema_version": PREVIEW_AUTH_SCHEMA_VERSION,
            }
        )


def append_authorization_request(endpoint: str, request: PreviewAuthorizationRequest) -> str:
    """Reference transport helper for brokers using one canonical request parameter."""

    encoded = base64.urlsafe_b64encode(canonical_preview_auth_json(request)).rstrip(b"=").decode()
    separator = "&" if urlsplit(endpoint).query else "?"
    return f"{endpoint}{separator}{urlencode({'request': encoded})}"
