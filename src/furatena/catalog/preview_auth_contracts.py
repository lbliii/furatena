"""Provider-neutral preview authorization wire contracts.

This module defines messages only.  Broker storage, signing, HTTP routing, and
authorization policy belong to the preview-auth runtime work.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import ipaddress
import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from types import MappingProxyType
from typing import Any, cast
from urllib.parse import SplitResult, urlsplit, urlunsplit

import idna

PREVIEW_AUTH_SCHEMA_VERSION = 1
PREVIEW_AUTH_CODE_TTL_SECONDS = 120
PREVIEW_AUTH_GRANT_TTL_SECONDS = 300
PREVIEW_AUTH_SESSION_TTL_SECONDS = 3600
PREVIEW_AUTH_JWKS_CACHE_SECONDS = 300
PREVIEW_AUTH_CLOCK_SKEW_SECONDS = 30
PREVIEW_AUTH_ROLLOVER_OVERLAP_SECONDS = 600
PREVIEW_AUTH_MAX_CODE_TTL_SECONDS = 300
PREVIEW_AUTH_MAX_GRANT_TTL_SECONDS = 600
PREVIEW_AUTH_MAX_URL_LENGTH = 2048
PREVIEW_AUTH_MAX_REDIRECT_PATHS = 32
PREVIEW_AUTH_MAX_AUDIENCES = 16
PREVIEW_AUTH_MAX_SCOPES = 32
PREVIEW_AUTH_MAX_JWKS_KEYS = 32
PREVIEW_AUTH_MIN_HMAC_KEY_BYTES = 32

_SHA_RE = re.compile(r"^[0-9a-f]{40}(?:[0-9a-f]{24})?$")
_OPAQUE_RE = re.compile(r"^[A-Za-z0-9_-]{32,256}$")
_ONE_TIME_RE = re.compile(r"^[A-Za-z0-9_-]{43,256}$")
_PKCE_VERIFIER_RE = re.compile(r"^[A-Za-z0-9._~-]{43,128}$")
_PKCE_CHALLENGE_RE = re.compile(r"^[A-Za-z0-9_-]{42}[AEIMQUYcgkosw048]$")
_USER_CODE_RE = re.compile(r"^[A-HJ-NP-Z2-9]{4}-[A-HJ-NP-Z2-9]{4}$")
_JTI_RE = re.compile(r"^[A-Za-z0-9_-]{16,128}$")
_KID_RE = re.compile(r"^[A-Za-z0-9._~-]{1,128}$")
_AUDIENCE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/-]{0,255}$")
_SCOPE_RE = re.compile(r"^[a-z][a-z0-9:_-]{0,63}$")
_B64URL_RE = re.compile(r"^[A-Za-z0-9_-]+$")


class PreviewAuthRecordType(StrEnum):
    REGISTRATION = "furatena.preview-auth.registration"
    AUTHORIZATION_REQUEST = "furatena.preview-auth.authorization-request"
    ONE_TIME_CODE = "furatena.preview-auth.one-time-code"
    GRANT_EXCHANGE = "furatena.preview-auth.grant-exchange"
    SIGNED_GRANT = "furatena.preview-auth.signed-grant"
    JWKS = "furatena.preview-auth.jwks"
    REVOCATION = "furatena.preview-auth.revocation"
    ERROR = "furatena.preview-auth.error"


class PreviewAuthClientKind(StrEnum):
    BROWSER = "browser"
    DEVICE = "device"


class PreviewAuthCodeKind(StrEnum):
    AUTHORIZATION_CODE = "authorization_code"
    DEVICE_CODE = "device_code"


class PreviewAuthConsumptionKind(StrEnum):
    CODE = "code"
    USER_CODE = "user_code"
    NONCE = "nonce"
    JTI = "jti"


class PreviewAuthAlgorithm(StrEnum):
    EDDSA = "EdDSA"
    ES256 = "ES256"


class PreviewAuthRevocationReason(StrEnum):
    EXPLICIT = "explicit"
    HEAD_CHANGED = "head_changed"
    PREVIEW_CLOSED = "preview_closed"
    REPOSITORY_ACCESS_CHANGED = "repository_access_changed"
    KEY_COMPROMISE = "key_compromise"


class PreviewAuthErrorCode(StrEnum):
    INVALID_REQUEST = "invalid_request"
    INVALID_ORIGIN = "invalid_origin"
    INVALID_BINDING = "invalid_binding"
    ACCESS_DENIED = "access_denied"
    EXPIRED_CODE = "expired_code"
    REPLAYED_CODE = "replayed_code"
    INVALID_GRANT = "invalid_grant"
    UNKNOWN_KEY = "unknown_key"
    STALE_KEY_SET = "stale_key_set"
    REVOKED_GRANT = "revoked_grant"
    AUTHORIZATION_PENDING = "authorization_pending"
    SLOW_DOWN = "slow_down"
    UNSUPPORTED_VERSION = "unsupported_version"
    SERVER_ERROR = "server_error"


_ERROR_COPY: Mapping[PreviewAuthErrorCode, tuple[str, str, bool]] = MappingProxyType(
    {
        PreviewAuthErrorCode.INVALID_REQUEST: (
            "The authorization request is invalid.",
            "Restart authorization with a valid v1 request.",
            False,
        ),
        PreviewAuthErrorCode.INVALID_ORIGIN: (
            "The authorization origin is invalid.",
            "Restart authorization from the registered preview origin.",
            False,
        ),
        PreviewAuthErrorCode.INVALID_BINDING: (
            "The authorization request does not match this preview.",
            "Restart authorization from the preview URL.",
            False,
        ),
        PreviewAuthErrorCode.ACCESS_DENIED: (
            "Access to this preview was denied.",
            "Request access from a repository administrator.",
            False,
        ),
        PreviewAuthErrorCode.EXPIRED_CODE: (
            "The one-time authorization code expired.",
            "Restart authorization to obtain a new code.",
            False,
        ),
        PreviewAuthErrorCode.REPLAYED_CODE: (
            "The one-time authorization code was already used.",
            "Restart authorization; do not retry the consumed code.",
            False,
        ),
        PreviewAuthErrorCode.INVALID_GRANT: (
            "The preview grant is invalid.",
            "Restart authorization to obtain a new grant.",
            False,
        ),
        PreviewAuthErrorCode.UNKNOWN_KEY: (
            "The preview grant signing key is unknown.",
            "Refresh the current key set once, then restart authorization.",
            True,
        ),
        PreviewAuthErrorCode.STALE_KEY_SET: (
            "The preview authorization key set is stale.",
            "Restore fresh key discovery before accepting grants.",
            True,
        ),
        PreviewAuthErrorCode.REVOKED_GRANT: (
            "The preview grant was revoked.",
            "Restart authorization if preview access is still allowed.",
            False,
        ),
        PreviewAuthErrorCode.AUTHORIZATION_PENDING: (
            "Preview authorization is still pending.",
            "Continue polling after retry_after_seconds.",
            True,
        ),
        PreviewAuthErrorCode.SLOW_DOWN: (
            "Preview authorization polling is too frequent.",
            "Wait for retry_after_seconds before polling again.",
            True,
        ),
        PreviewAuthErrorCode.UNSUPPORTED_VERSION: (
            "The preview authorization protocol version is unsupported.",
            "Retry using one of supported_versions; do not reinterpret this message as v1.",
            False,
        ),
        PreviewAuthErrorCode.SERVER_ERROR: (
            "Preview authorization is temporarily unavailable.",
            "Retry the authorization request later.",
            True,
        ),
    }
)


@dataclass(frozen=True, slots=True)
class PreviewAuthLifetimes:
    code_seconds: int = PREVIEW_AUTH_CODE_TTL_SECONDS
    grant_seconds: int = PREVIEW_AUTH_GRANT_TTL_SECONDS
    session_seconds: int = PREVIEW_AUTH_SESSION_TTL_SECONDS
    jwks_cache_seconds: int = PREVIEW_AUTH_JWKS_CACHE_SECONDS
    clock_skew_seconds: int = PREVIEW_AUTH_CLOCK_SKEW_SECONDS
    rollover_overlap_seconds: int = PREVIEW_AUTH_ROLLOVER_OVERLAP_SECONDS

    def __post_init__(self) -> None:
        limits = {
            "code_seconds": (30, 300),
            "grant_seconds": (60, 600),
            "session_seconds": (300, 43_200),
            "jwks_cache_seconds": (30, 900),
            "clock_skew_seconds": (0, 60),
            "rollover_overlap_seconds": (600, 86_400),
        }
        for name, (minimum, maximum) in limits.items():
            value = getattr(self, name)
            if not isinstance(value, int) or isinstance(value, bool):
                raise TypeError(
                    f"The preview-auth {name} field must be an integer; correct the producer."
                )
            if not minimum <= value <= maximum:
                raise ValueError(
                    f"The preview-auth {name} field must be in [{minimum}, {maximum}]; correct the producer."
                )
        if self.rollover_overlap_seconds < self.grant_seconds + self.clock_skew_seconds:
            raise ValueError(
                "The preview-auth rollover overlap must cover grant lifetime plus clock skew; increase it."
            )

    def to_dict(self) -> dict[str, int]:
        return {
            "code_seconds": self.code_seconds,
            "grant_seconds": self.grant_seconds,
            "session_seconds": self.session_seconds,
            "jwks_cache_seconds": self.jwks_cache_seconds,
            "clock_skew_seconds": self.clock_skew_seconds,
            "rollover_overlap_seconds": self.rollover_overlap_seconds,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> PreviewAuthLifetimes:
        _exact_keys(
            value,
            {
                "code_seconds",
                "grant_seconds",
                "session_seconds",
                "jwks_cache_seconds",
                "clock_skew_seconds",
                "rollover_overlap_seconds",
            },
            "lifetimes",
        )
        return cls(**{name: _integer(value[name], name) for name in value})


@dataclass(frozen=True, slots=True)
class PreviewAuthBinding:
    repository_id: str
    pull_request_number: int
    head_sha: str
    origin: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "repository_id", _text(self.repository_id, "repository_id", 256))
        number = _integer(self.pull_request_number, "pull_request_number")
        if number < 1:
            raise ValueError(
                "The preview-auth pull_request_number must be positive; provide the reviewed pull request number."
            )
        object.__setattr__(self, "pull_request_number", number)
        sha = str(self.head_sha)
        if not _SHA_RE.fullmatch(sha):
            raise ValueError(
                "The preview-auth head_sha must be a lowercase 40- or 64-hex SHA; provide the reviewed commit."
            )
        object.__setattr__(self, "head_sha", sha)
        object.__setattr__(self, "origin", canonical_preview_origin(self.origin))

    def to_dict(self) -> dict[str, object]:
        return {
            "repository_id": self.repository_id,
            "pull_request_number": self.pull_request_number,
            "head_sha": self.head_sha,
            "origin": self.origin,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> PreviewAuthBinding:
        _exact_keys(
            value, {"repository_id", "pull_request_number", "head_sha", "origin"}, "binding"
        )
        return cls(
            repository_id=_string(value["repository_id"], "repository_id"),
            pull_request_number=_integer(value["pull_request_number"], "pull_request_number"),
            head_sha=_string(value["head_sha"], "head_sha"),
            origin=_string(value["origin"], "origin"),
        )


@dataclass(frozen=True, slots=True)
class PreviewAuthRegistration:
    registration_id: str
    issuer: str
    binding: PreviewAuthBinding
    authorization_endpoint: str
    grant_endpoint: str
    jwks_uri: str
    revocation_endpoint: str
    redirect_paths: tuple[str, ...]
    audiences: tuple[str, ...]
    created_at: str
    expires_at: str
    lifetimes: PreviewAuthLifetimes = PreviewAuthLifetimes()
    supported_versions: tuple[int, ...] = (PREVIEW_AUTH_SCHEMA_VERSION,)
    deprecated_versions: tuple[int, ...] = ()
    schema_version: int = PREVIEW_AUTH_SCHEMA_VERSION
    record_type: PreviewAuthRecordType = PreviewAuthRecordType.REGISTRATION

    def __post_init__(self) -> None:
        _header(self.schema_version, self.record_type, PreviewAuthRecordType.REGISTRATION)
        object.__setattr__(
            self, "registration_id", _opaque(self.registration_id, "registration_id")
        )
        object.__setattr__(self, "issuer", canonical_issuer(self.issuer))
        issuer_origin = _url_origin(self.issuer, "issuer")
        for name in ("authorization_endpoint", "grant_endpoint", "jwks_uri", "revocation_endpoint"):
            endpoint = _endpoint_url(getattr(self, name), name)
            if _url_origin(endpoint, name) != issuer_origin:
                raise ValueError(
                    f"The preview-auth {name} must use the issuer origin; correct broker discovery."
                )
            object.__setattr__(self, name, endpoint)
        if len(self.redirect_paths) > PREVIEW_AUTH_MAX_REDIRECT_PATHS:
            raise ValueError(
                f"The preview-auth redirect_paths list exceeds {PREVIEW_AUTH_MAX_REDIRECT_PATHS} entries; reduce registration scope."
            )
        validated_paths = tuple(_redirect_path(item) for item in self.redirect_paths)
        if len(set(validated_paths)) != len(validated_paths):
            raise ValueError(
                "The preview-auth redirect_paths list contains duplicates; emit each callback once."
            )
        paths = tuple(sorted(validated_paths))
        if not paths:
            raise ValueError(
                "The preview-auth redirect_paths list must not be empty; register an exact callback path."
            )
        object.__setattr__(self, "redirect_paths", paths)
        object.__setattr__(self, "audiences", _audiences(self.audiences))
        object.__setattr__(self, "created_at", _timestamp(self.created_at, "created_at"))
        object.__setattr__(self, "expires_at", _timestamp(self.expires_at, "expires_at"))
        _ordered_times(self.created_at, self.expires_at, "registration")
        version_values = tuple(
            _integer(item, "supported_versions") for item in self.supported_versions
        )
        deprecated_values = tuple(
            _integer(item, "deprecated_versions") for item in self.deprecated_versions
        )
        if len(set(version_values)) != len(version_values) or len(set(deprecated_values)) != len(
            deprecated_values
        ):
            raise ValueError(
                "The preview-auth registration version lists contain duplicates; emit each version once."
            )
        versions = tuple(sorted(version_values))
        deprecated = tuple(sorted(deprecated_values))
        if (
            PREVIEW_AUTH_SCHEMA_VERSION not in versions
            or not versions
            or len(versions) > 16
            or len(deprecated) > 16
            or min(versions) < 1
            or max(versions) > 65_535
            or set(deprecated) - set(versions)
        ):
            raise ValueError(
                "The preview-auth v1 registration has inconsistent version negotiation fields; advertise supported positive versions."
            )
        object.__setattr__(self, "supported_versions", versions)
        object.__setattr__(self, "deprecated_versions", deprecated)

    def to_dict(self) -> dict[str, object]:
        return _message(
            self,
            {
                "registration_id": self.registration_id,
                "issuer": self.issuer,
                "binding": self.binding.to_dict(),
                "authorization_endpoint": self.authorization_endpoint,
                "grant_endpoint": self.grant_endpoint,
                "jwks_uri": self.jwks_uri,
                "revocation_endpoint": self.revocation_endpoint,
                "redirect_paths": list(self.redirect_paths),
                "audiences": list(self.audiences),
                "created_at": self.created_at,
                "expires_at": self.expires_at,
                "lifetimes": self.lifetimes.to_dict(),
                "supported_versions": list(self.supported_versions),
                "deprecated_versions": list(self.deprecated_versions),
            },
        )

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> PreviewAuthRegistration:
        fields = {
            "schema_version",
            "record_type",
            "registration_id",
            "issuer",
            "binding",
            "authorization_endpoint",
            "grant_endpoint",
            "jwks_uri",
            "revocation_endpoint",
            "redirect_paths",
            "audiences",
            "created_at",
            "expires_at",
            "lifetimes",
            "supported_versions",
            "deprecated_versions",
        }
        _message_keys(value, fields, PreviewAuthRecordType.REGISTRATION)
        return cls(
            registration_id=_string(value["registration_id"], "registration_id"),
            issuer=_string(value["issuer"], "issuer"),
            binding=PreviewAuthBinding.from_dict(_mapping(value["binding"], "binding")),
            authorization_endpoint=_string(
                value["authorization_endpoint"], "authorization_endpoint"
            ),
            grant_endpoint=_string(value["grant_endpoint"], "grant_endpoint"),
            jwks_uri=_string(value["jwks_uri"], "jwks_uri"),
            revocation_endpoint=_string(value["revocation_endpoint"], "revocation_endpoint"),
            redirect_paths=_strings(value["redirect_paths"], "redirect_paths"),
            audiences=_strings(value["audiences"], "audiences"),
            created_at=_string(value["created_at"], "created_at"),
            expires_at=_string(value["expires_at"], "expires_at"),
            lifetimes=PreviewAuthLifetimes.from_dict(_mapping(value["lifetimes"], "lifetimes")),
            supported_versions=_integers(value["supported_versions"], "supported_versions"),
            deprecated_versions=_integers(value["deprecated_versions"], "deprecated_versions"),
        )


@dataclass(frozen=True, slots=True)
class PreviewAuthorizationRequest:
    request_id: str
    client_kind: PreviewAuthClientKind
    binding: PreviewAuthBinding
    audience: str
    nonce: str
    requested_at: str
    expires_at: str
    redirect_uri: str | None = None
    state: str | None = None
    code_challenge: str | None = None
    code_challenge_method: str | None = None
    schema_version: int = PREVIEW_AUTH_SCHEMA_VERSION
    record_type: PreviewAuthRecordType = PreviewAuthRecordType.AUTHORIZATION_REQUEST

    def __post_init__(self) -> None:
        _header(self.schema_version, self.record_type, PreviewAuthRecordType.AUTHORIZATION_REQUEST)
        object.__setattr__(self, "request_id", _opaque(self.request_id, "request_id"))
        object.__setattr__(self, "client_kind", PreviewAuthClientKind(self.client_kind))
        object.__setattr__(self, "audience", _audience(self.audience))
        object.__setattr__(self, "nonce", _one_time(self.nonce, "nonce"))
        object.__setattr__(self, "requested_at", _timestamp(self.requested_at, "requested_at"))
        object.__setattr__(self, "expires_at", _timestamp(self.expires_at, "expires_at"))
        _bounded_window(self.requested_at, self.expires_at, 300, "authorization request")
        if self.client_kind is PreviewAuthClientKind.BROWSER:
            uri = _redirect_uri(self.redirect_uri, self.binding.origin)
            object.__setattr__(self, "redirect_uri", uri)
            object.__setattr__(self, "state", _one_time(self.state, "state"))
            challenge = _string(self.code_challenge, "code_challenge")
            if not _PKCE_CHALLENGE_RE.fullmatch(challenge):
                raise ValueError(
                    "The preview-auth code_challenge must be a 43-character base64url SHA-256 value; regenerate PKCE."
                )
            object.__setattr__(self, "code_challenge", challenge)
            if self.code_challenge_method != "S256":
                raise ValueError(
                    "The preview-auth browser request requires PKCE S256; replace the challenge method."
                )
        elif any((self.redirect_uri, self.state, self.code_challenge, self.code_challenge_method)):
            raise ValueError(
                "The preview-auth device request forbids redirect, state, and PKCE fields; remove them."
            )

    def to_dict(self) -> dict[str, object]:
        return _message(
            self,
            {
                "request_id": self.request_id,
                "client_kind": self.client_kind.value,
                "binding": self.binding.to_dict(),
                "audience": self.audience,
                "nonce": self.nonce,
                "requested_at": self.requested_at,
                "expires_at": self.expires_at,
                "redirect_uri": self.redirect_uri,
                "state": self.state,
                "code_challenge": self.code_challenge,
                "code_challenge_method": self.code_challenge_method,
            },
        )

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> PreviewAuthorizationRequest:
        fields = {
            "schema_version",
            "record_type",
            "request_id",
            "client_kind",
            "binding",
            "audience",
            "nonce",
            "requested_at",
            "expires_at",
            "redirect_uri",
            "state",
            "code_challenge",
            "code_challenge_method",
        }
        _message_keys(value, fields, PreviewAuthRecordType.AUTHORIZATION_REQUEST)
        return cls(
            request_id=_string(value["request_id"], "request_id"),
            client_kind=PreviewAuthClientKind(_string(value["client_kind"], "client_kind")),
            binding=PreviewAuthBinding.from_dict(_mapping(value["binding"], "binding")),
            audience=_string(value["audience"], "audience"),
            nonce=_string(value["nonce"], "nonce"),
            requested_at=_string(value["requested_at"], "requested_at"),
            expires_at=_string(value["expires_at"], "expires_at"),
            redirect_uri=_optional_string(value["redirect_uri"], "redirect_uri"),
            state=_optional_string(value["state"], "state"),
            code_challenge=_optional_string(value["code_challenge"], "code_challenge"),
            code_challenge_method=_optional_string(
                value["code_challenge_method"], "code_challenge_method"
            ),
        )


@dataclass(frozen=True, slots=True)
class PreviewOneTimeCode:
    code: str
    code_kind: PreviewAuthCodeKind
    binding: PreviewAuthBinding
    audience: str
    subject: str
    nonce: str
    issued_at: str
    expires_at: str
    redirect_uri: str | None = None
    user_code: str | None = None
    verification_uri: str | None = None
    poll_interval_seconds: int | None = None
    schema_version: int = PREVIEW_AUTH_SCHEMA_VERSION
    record_type: PreviewAuthRecordType = PreviewAuthRecordType.ONE_TIME_CODE

    def __post_init__(self) -> None:
        _header(self.schema_version, self.record_type, PreviewAuthRecordType.ONE_TIME_CODE)
        object.__setattr__(self, "code", _one_time(self.code, "code"))
        object.__setattr__(self, "code_kind", PreviewAuthCodeKind(self.code_kind))
        object.__setattr__(self, "audience", _audience(self.audience))
        object.__setattr__(self, "subject", _text(self.subject, "subject", 256))
        object.__setattr__(self, "nonce", _one_time(self.nonce, "nonce"))
        object.__setattr__(self, "issued_at", _timestamp(self.issued_at, "issued_at"))
        object.__setattr__(self, "expires_at", _timestamp(self.expires_at, "expires_at"))
        _bounded_window(
            self.issued_at, self.expires_at, PREVIEW_AUTH_MAX_CODE_TTL_SECONDS, "one-time code"
        )
        if self.code_kind is PreviewAuthCodeKind.AUTHORIZATION_CODE:
            object.__setattr__(
                self, "redirect_uri", _redirect_uri(self.redirect_uri, self.binding.origin)
            )
            if any(
                value is not None
                for value in (self.user_code, self.verification_uri, self.poll_interval_seconds)
            ):
                raise ValueError(
                    "The preview-auth authorization code forbids device fields; remove all device values."
                )
        else:
            if self.redirect_uri is not None:
                raise ValueError(
                    "The preview-auth device code forbids redirect_uri; remove the browser callback."
                )
            user_code = _string(self.user_code, "user_code")
            if not _USER_CODE_RE.fullmatch(user_code):
                raise ValueError(
                    "The preview-auth user_code must use the unambiguous XXXX-XXXX alphabet; regenerate it."
                )
            object.__setattr__(self, "user_code", user_code)
            object.__setattr__(
                self,
                "verification_uri",
                _endpoint_url(
                    _string(self.verification_uri, "verification_uri"), "verification_uri"
                ),
            )
            interval = _integer(self.poll_interval_seconds, "poll_interval_seconds")
            if not 1 <= interval <= 30:
                raise ValueError(
                    "The preview-auth poll_interval_seconds must be in [1, 30]; correct the broker response."
                )
            object.__setattr__(self, "poll_interval_seconds", interval)

    def to_dict(self) -> dict[str, object]:
        return _message(
            self,
            {
                "code": self.code,
                "code_kind": self.code_kind.value,
                "binding": self.binding.to_dict(),
                "audience": self.audience,
                "subject": self.subject,
                "nonce": self.nonce,
                "issued_at": self.issued_at,
                "expires_at": self.expires_at,
                "redirect_uri": self.redirect_uri,
                "user_code": self.user_code,
                "verification_uri": self.verification_uri,
                "poll_interval_seconds": self.poll_interval_seconds,
            },
        )

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> PreviewOneTimeCode:
        fields = {
            "schema_version",
            "record_type",
            "code",
            "code_kind",
            "binding",
            "audience",
            "subject",
            "nonce",
            "issued_at",
            "expires_at",
            "redirect_uri",
            "user_code",
            "verification_uri",
            "poll_interval_seconds",
        }
        _message_keys(value, fields, PreviewAuthRecordType.ONE_TIME_CODE)
        return cls(
            code=_string(value["code"], "code"),
            code_kind=PreviewAuthCodeKind(_string(value["code_kind"], "code_kind")),
            binding=PreviewAuthBinding.from_dict(_mapping(value["binding"], "binding")),
            audience=_string(value["audience"], "audience"),
            subject=_string(value["subject"], "subject"),
            nonce=_string(value["nonce"], "nonce"),
            issued_at=_string(value["issued_at"], "issued_at"),
            expires_at=_string(value["expires_at"], "expires_at"),
            redirect_uri=_optional_string(value["redirect_uri"], "redirect_uri"),
            user_code=_optional_string(value["user_code"], "user_code"),
            verification_uri=_optional_string(value["verification_uri"], "verification_uri"),
            poll_interval_seconds=(
                None
                if value["poll_interval_seconds"] is None
                else _integer(value["poll_interval_seconds"], "poll_interval_seconds")
            ),
        )


@dataclass(frozen=True, slots=True)
class PreviewGrantExchange:
    exchange_id: str
    client_kind: PreviewAuthClientKind
    code: str
    binding: PreviewAuthBinding
    audience: str
    nonce: str
    exchanged_at: str
    redirect_uri: str | None = None
    code_verifier: str | None = None
    schema_version: int = PREVIEW_AUTH_SCHEMA_VERSION
    record_type: PreviewAuthRecordType = PreviewAuthRecordType.GRANT_EXCHANGE

    def __post_init__(self) -> None:
        _header(self.schema_version, self.record_type, PreviewAuthRecordType.GRANT_EXCHANGE)
        object.__setattr__(self, "exchange_id", _opaque(self.exchange_id, "exchange_id"))
        object.__setattr__(self, "client_kind", PreviewAuthClientKind(self.client_kind))
        object.__setattr__(self, "code", _one_time(self.code, "code"))
        object.__setattr__(self, "audience", _audience(self.audience))
        object.__setattr__(self, "nonce", _one_time(self.nonce, "nonce"))
        object.__setattr__(self, "exchanged_at", _timestamp(self.exchanged_at, "exchanged_at"))
        if self.client_kind is PreviewAuthClientKind.BROWSER:
            object.__setattr__(
                self, "redirect_uri", _redirect_uri(self.redirect_uri, self.binding.origin)
            )
            verifier = _string(self.code_verifier, "code_verifier")
            if not _PKCE_VERIFIER_RE.fullmatch(verifier):
                raise ValueError(
                    "The preview-auth code_verifier must satisfy RFC 7636 length and alphabet; restart authorization."
                )
            object.__setattr__(self, "code_verifier", verifier)
        elif self.redirect_uri is not None or self.code_verifier is not None:
            raise ValueError(
                "The preview-auth device exchange forbids redirect_uri and code_verifier; remove them."
            )

    def to_dict(self) -> dict[str, object]:
        return _message(
            self,
            {
                "exchange_id": self.exchange_id,
                "client_kind": self.client_kind.value,
                "code": self.code,
                "binding": self.binding.to_dict(),
                "audience": self.audience,
                "nonce": self.nonce,
                "exchanged_at": self.exchanged_at,
                "redirect_uri": self.redirect_uri,
                "code_verifier": self.code_verifier,
            },
        )

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> PreviewGrantExchange:
        fields = {
            "schema_version",
            "record_type",
            "exchange_id",
            "client_kind",
            "code",
            "binding",
            "audience",
            "nonce",
            "exchanged_at",
            "redirect_uri",
            "code_verifier",
        }
        _message_keys(value, fields, PreviewAuthRecordType.GRANT_EXCHANGE)
        return cls(
            exchange_id=_string(value["exchange_id"], "exchange_id"),
            client_kind=PreviewAuthClientKind(_string(value["client_kind"], "client_kind")),
            code=_string(value["code"], "code"),
            binding=PreviewAuthBinding.from_dict(_mapping(value["binding"], "binding")),
            audience=_string(value["audience"], "audience"),
            nonce=_string(value["nonce"], "nonce"),
            exchanged_at=_string(value["exchanged_at"], "exchanged_at"),
            redirect_uri=_optional_string(value["redirect_uri"], "redirect_uri"),
            code_verifier=_optional_string(value["code_verifier"], "code_verifier"),
        )


@dataclass(frozen=True, slots=True)
class PreviewGrantClaims:
    issuer: str
    audience: str
    subject: str
    repository_id: str
    pull_request_number: int
    head_sha: str
    origin: str
    nonce: str
    key_id: str
    jti: str
    issued_at: int
    not_before: int
    expires_at: int
    scopes: tuple[str, ...]

    def __post_init__(self) -> None:
        object.__setattr__(self, "issuer", canonical_issuer(self.issuer))
        object.__setattr__(self, "audience", _audience(self.audience))
        object.__setattr__(self, "subject", _text(self.subject, "subject", 256))
        binding = PreviewAuthBinding(
            self.repository_id, self.pull_request_number, self.head_sha, self.origin
        )
        object.__setattr__(self, "repository_id", binding.repository_id)
        object.__setattr__(self, "head_sha", binding.head_sha)
        object.__setattr__(self, "origin", binding.origin)
        object.__setattr__(self, "nonce", _one_time(self.nonce, "nonce"))
        object.__setattr__(self, "key_id", _key_id(self.key_id))
        if not _JTI_RE.fullmatch(self.jti):
            raise ValueError(
                "The preview-auth jti must contain 16-128 base64url characters; regenerate the grant ID."
            )
        for field_name in ("issued_at", "not_before", "expires_at"):
            object.__setattr__(self, field_name, _integer(getattr(self, field_name), field_name))
        if min(self.issued_at, self.not_before, self.expires_at) < 0:
            raise ValueError(
                "The preview-auth grant times must be nonnegative NumericDate values; correct the signer clock."
            )
        if self.not_before < self.issued_at - PREVIEW_AUTH_CLOCK_SKEW_SECONDS:
            raise ValueError(
                "The preview-auth not_before time exceeds allowed clock skew; correct the signer clock."
            )
        if self.expires_at <= max(self.issued_at, self.not_before):
            raise ValueError(
                "The preview-auth grant expiry must follow issue and not-before times; correct the lifetime."
            )
        if self.expires_at - self.issued_at > PREVIEW_AUTH_MAX_GRANT_TTL_SECONDS:
            raise ValueError(
                "The preview-auth grant exceeds the v1 maximum lifetime; shorten the signed grant."
            )
        if len(self.scopes) > PREVIEW_AUTH_MAX_SCOPES:
            raise ValueError(
                f"The preview-auth scopes list exceeds {PREVIEW_AUTH_MAX_SCOPES} entries; reduce the grant scope."
            )
        validated_scopes = tuple(_scope(item) for item in self.scopes)
        if len(set(validated_scopes)) != len(validated_scopes):
            raise ValueError(
                "The preview-auth scopes list contains duplicates; emit each granted scope once."
            )
        scopes = tuple(sorted(validated_scopes))
        if not scopes:
            raise ValueError(
                "The preview-auth scopes list must not be empty; request an allowed read scope."
            )
        object.__setattr__(self, "scopes", scopes)

    def to_dict(self) -> dict[str, object]:
        return {
            "ver": PREVIEW_AUTH_SCHEMA_VERSION,
            "iss": self.issuer,
            "aud": self.audience,
            "sub": self.subject,
            "repository_id": self.repository_id,
            "pull_request_number": self.pull_request_number,
            "head_sha": self.head_sha,
            "origin": self.origin,
            "nonce": self.nonce,
            "kid": self.key_id,
            "jti": self.jti,
            "iat": self.issued_at,
            "nbf": self.not_before,
            "exp": self.expires_at,
            "scope": list(self.scopes),
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> PreviewGrantClaims:
        fields = {
            "ver",
            "iss",
            "aud",
            "sub",
            "repository_id",
            "pull_request_number",
            "head_sha",
            "origin",
            "nonce",
            "kid",
            "jti",
            "iat",
            "nbf",
            "exp",
            "scope",
        }
        _exact_keys(value, fields, "grant claims")
        if _integer(value["ver"], "ver") != PREVIEW_AUTH_SCHEMA_VERSION:
            raise ValueError(
                "The preview-auth grant claim version is unsupported; retry with supported_versions=[1]."
            )
        return cls(
            issuer=_string(value["iss"], "iss"),
            audience=_string(value["aud"], "aud"),
            subject=_string(value["sub"], "sub"),
            repository_id=_string(value["repository_id"], "repository_id"),
            pull_request_number=_integer(value["pull_request_number"], "pull_request_number"),
            head_sha=_string(value["head_sha"], "head_sha"),
            origin=_string(value["origin"], "origin"),
            nonce=_string(value["nonce"], "nonce"),
            key_id=_string(value["kid"], "kid"),
            jti=_string(value["jti"], "jti"),
            issued_at=_integer(value["iat"], "iat"),
            not_before=_integer(value["nbf"], "nbf"),
            expires_at=_integer(value["exp"], "exp"),
            scopes=_strings(value["scope"], "scope"),
        )


@dataclass(frozen=True, slots=True)
class PreviewSignedGrant:
    compact: str
    schema_version: int = PREVIEW_AUTH_SCHEMA_VERSION
    record_type: PreviewAuthRecordType = PreviewAuthRecordType.SIGNED_GRANT

    def __post_init__(self) -> None:
        _header(self.schema_version, self.record_type, PreviewAuthRecordType.SIGNED_GRANT)
        if len(self.compact) > 8192:
            raise ValueError(
                "The preview-auth compact grant exceeds 8192 characters; reject the oversized credential."
            )
        header_segment, payload_segment, signature = _jws_segments(self.compact)
        header = _json_segment(header_segment, "protected header")
        _exact_keys(header, {"alg", "kid", "typ"}, "protected header")
        algorithm = PreviewAuthAlgorithm(_string(header["alg"], "alg"))
        key_id = _key_id(_string(header["kid"], "kid"))
        if header["typ"] != "FURA-PREVIEW-GRANT+jwt":
            raise ValueError(
                "The preview-auth signed grant has an unsupported typ; request a v1 grant."
            )
        if algorithm not in frozenset(PreviewAuthAlgorithm):
            raise ValueError(
                "The preview-auth signed grant algorithm is not allowed; use an advertised v1 algorithm."
            )
        claims = PreviewGrantClaims.from_dict(_json_segment(payload_segment, "grant claims"))
        if claims.key_id != key_id:
            raise ValueError(
                "The preview-auth signed grant kid does not match its claims; reject the credential."
            )
        if _encode_b64url(canonical_preview_auth_json(claims)) != payload_segment:
            raise ValueError(
                "The preview-auth grant claims are not in canonical typed form; reject the credential before verification."
            )
        _canonical_b64url(signature, "signature", decoded_bytes=64)

    @property
    def claims(self) -> PreviewGrantClaims:
        return PreviewGrantClaims.from_dict(
            _json_segment(self.compact.split(".")[1], "grant claims")
        )

    @property
    def key_id(self) -> str:
        return _string(_json_segment(self.compact.split(".")[0], "protected header")["kid"], "kid")

    def to_dict(self) -> dict[str, object]:
        return _message(self, {"compact": self.compact})

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> PreviewSignedGrant:
        fields = {"schema_version", "record_type", "compact"}
        _message_keys(value, fields, PreviewAuthRecordType.SIGNED_GRANT)
        return cls(compact=_string(value["compact"], "compact"))


@dataclass(frozen=True, slots=True)
class PreviewJwk:
    key_id: str
    algorithm: PreviewAuthAlgorithm
    key_type: str
    curve: str
    x: str
    y: str | None = None
    use: str = "sig"
    key_ops: tuple[str, ...] = ("verify",)

    def __post_init__(self) -> None:
        object.__setattr__(self, "key_id", _key_id(self.key_id))
        object.__setattr__(self, "algorithm", PreviewAuthAlgorithm(self.algorithm))
        if self.use != "sig":
            raise ValueError(
                "The preview-auth JWKS key must declare signing use; replace the public key record."
            )
        if self.key_ops != ("verify",):
            raise ValueError(
                "The preview-auth public JWKS key must allow only verification; remove other operations."
            )
        _b64_coordinate(self.x, "x")
        if self.algorithm is PreviewAuthAlgorithm.EDDSA:
            if self.key_type != "OKP" or self.curve != "Ed25519" or self.y is not None:
                raise ValueError(
                    "The preview-auth EdDSA key must be an OKP Ed25519 public key; replace it."
                )
        elif self.key_type != "EC" or self.curve != "P-256" or self.y is None:
            raise ValueError(
                "The preview-auth ES256 key must be an EC P-256 public key with x and y; replace it."
            )
        if self.y is not None:
            _b64_coordinate(self.y, "y")

    def to_dict(self) -> dict[str, object]:
        result: dict[str, object] = {
            "kid": self.key_id,
            "alg": self.algorithm.value,
            "use": self.use,
            "key_ops": list(self.key_ops),
            "kty": self.key_type,
            "crv": self.curve,
            "x": self.x,
        }
        if self.y is not None:
            result["y"] = self.y
        return result

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> PreviewJwk:
        allowed = {"kid", "alg", "use", "key_ops", "kty", "crv", "x", "y"}
        if set(value) - allowed:
            _exact_keys(value, allowed, "JWK")
        required = {"kid", "alg", "use", "key_ops", "kty", "crv", "x"}
        missing = required - set(value)
        if missing:
            raise ValueError(
                f"The preview-auth JWK is missing required fields: {', '.join(sorted(missing))}; replace the record."
            )
        return cls(
            key_id=_string(value["kid"], "kid"),
            algorithm=PreviewAuthAlgorithm(_string(value["alg"], "alg")),
            use=_string(value["use"], "use"),
            key_ops=_strings(value["key_ops"], "key_ops"),
            key_type=_string(value["kty"], "kty"),
            curve=_string(value["crv"], "crv"),
            x=_string(value["x"], "x"),
            y=_optional_string(value.get("y"), "y"),
        )


@dataclass(frozen=True, slots=True)
class PreviewJwks:
    issuer: str
    keys: tuple[PreviewJwk, ...]
    generated_at: str
    stale_after: str
    cache_seconds: int = PREVIEW_AUTH_JWKS_CACHE_SECONDS
    schema_version: int = PREVIEW_AUTH_SCHEMA_VERSION
    record_type: PreviewAuthRecordType = PreviewAuthRecordType.JWKS

    def __post_init__(self) -> None:
        _header(self.schema_version, self.record_type, PreviewAuthRecordType.JWKS)
        object.__setattr__(self, "issuer", canonical_issuer(self.issuer))
        if len(self.keys) > PREVIEW_AUTH_MAX_JWKS_KEYS:
            raise ValueError(
                f"The preview-auth JWKS exceeds {PREVIEW_AUTH_MAX_JWKS_KEYS} keys; publish a bounded rollover set."
            )
        keys = tuple(sorted(self.keys, key=lambda item: item.key_id))
        if not keys or len({key.key_id for key in keys}) != len(keys):
            raise ValueError(
                "The preview-auth JWKS requires at least one uniquely identified key; correct discovery output."
            )
        object.__setattr__(self, "keys", keys)
        object.__setattr__(self, "generated_at", _timestamp(self.generated_at, "generated_at"))
        object.__setattr__(self, "stale_after", _timestamp(self.stale_after, "stale_after"))
        _bounded_window(self.generated_at, self.stale_after, 900, "JWKS freshness")
        object.__setattr__(self, "cache_seconds", _integer(self.cache_seconds, "cache_seconds"))
        if not 30 <= self.cache_seconds <= 900:
            raise ValueError(
                "The preview-auth JWKS cache_seconds must be in [30, 900]; correct discovery output."
            )

    def to_dict(self) -> dict[str, object]:
        return _message(
            self,
            {
                "issuer": self.issuer,
                "keys": [key.to_dict() for key in self.keys],
                "generated_at": self.generated_at,
                "stale_after": self.stale_after,
                "cache_seconds": self.cache_seconds,
            },
        )

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> PreviewJwks:
        fields = {
            "schema_version",
            "record_type",
            "issuer",
            "keys",
            "generated_at",
            "stale_after",
            "cache_seconds",
        }
        _message_keys(value, fields, PreviewAuthRecordType.JWKS)
        return cls(
            issuer=_string(value["issuer"], "issuer"),
            keys=tuple(
                PreviewJwk.from_dict(_mapping(item, "key"))
                for item in _sequence(value["keys"], "keys")
            ),
            generated_at=_string(value["generated_at"], "generated_at"),
            stale_after=_string(value["stale_after"], "stale_after"),
            cache_seconds=_integer(value["cache_seconds"], "cache_seconds"),
        )


@dataclass(frozen=True, slots=True)
class PreviewRevocationSignal:
    issuer: str
    audience: str
    binding: PreviewAuthBinding
    subject: str
    jti: str
    key_id: str
    revoked_at: str
    reason: PreviewAuthRevocationReason
    sequence: int
    schema_version: int = PREVIEW_AUTH_SCHEMA_VERSION
    record_type: PreviewAuthRecordType = PreviewAuthRecordType.REVOCATION

    def __post_init__(self) -> None:
        _header(self.schema_version, self.record_type, PreviewAuthRecordType.REVOCATION)
        object.__setattr__(self, "issuer", canonical_issuer(self.issuer))
        object.__setattr__(self, "audience", _audience(self.audience))
        object.__setattr__(self, "subject", _text(self.subject, "subject", 256))
        if not _JTI_RE.fullmatch(self.jti):
            raise ValueError(
                "The preview-auth revocation jti is malformed; reject the revocation signal safely."
            )
        object.__setattr__(self, "key_id", _key_id(self.key_id))
        object.__setattr__(self, "revoked_at", _timestamp(self.revoked_at, "revoked_at"))
        object.__setattr__(self, "reason", PreviewAuthRevocationReason(self.reason))
        object.__setattr__(self, "sequence", _integer(self.sequence, "sequence"))
        if self.sequence < 1:
            raise ValueError(
                "The preview-auth revocation sequence must be positive; request a current signal."
            )

    def to_dict(self) -> dict[str, object]:
        return _message(
            self,
            {
                "issuer": self.issuer,
                "audience": self.audience,
                "binding": self.binding.to_dict(),
                "subject": self.subject,
                "jti": self.jti,
                "key_id": self.key_id,
                "revoked_at": self.revoked_at,
                "reason": self.reason.value,
                "sequence": self.sequence,
            },
        )

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> PreviewRevocationSignal:
        fields = {
            "schema_version",
            "record_type",
            "issuer",
            "audience",
            "binding",
            "subject",
            "jti",
            "key_id",
            "revoked_at",
            "reason",
            "sequence",
        }
        _message_keys(value, fields, PreviewAuthRecordType.REVOCATION)
        return cls(
            issuer=_string(value["issuer"], "issuer"),
            audience=_string(value["audience"], "audience"),
            binding=PreviewAuthBinding.from_dict(_mapping(value["binding"], "binding")),
            subject=_string(value["subject"], "subject"),
            jti=_string(value["jti"], "jti"),
            key_id=_string(value["key_id"], "key_id"),
            revoked_at=_string(value["revoked_at"], "revoked_at"),
            reason=PreviewAuthRevocationReason(_string(value["reason"], "reason")),
            sequence=_integer(value["sequence"], "sequence"),
        )


@dataclass(frozen=True, slots=True)
class PreviewAuthError:
    error: PreviewAuthErrorCode
    safe_message: str
    remediation: str
    retryable: bool
    request_id: str | None
    redirect_allowed: bool
    state: str | None = None
    retry_after_seconds: int | None = None
    supported_versions: tuple[int, ...] = (PREVIEW_AUTH_SCHEMA_VERSION,)
    deprecated_versions: tuple[int, ...] = ()
    schema_version: int = PREVIEW_AUTH_SCHEMA_VERSION
    record_type: PreviewAuthRecordType = PreviewAuthRecordType.ERROR

    def __post_init__(self) -> None:
        _header(self.schema_version, self.record_type, PreviewAuthRecordType.ERROR)
        object.__setattr__(self, "error", PreviewAuthErrorCode(self.error))
        object.__setattr__(self, "retryable", _boolean(self.retryable, "retryable"))
        object.__setattr__(
            self, "redirect_allowed", _boolean(self.redirect_allowed, "redirect_allowed")
        )
        object.__setattr__(self, "safe_message", _text(self.safe_message, "safe_message", 512))
        object.__setattr__(self, "remediation", _text(self.remediation, "remediation", 512))
        expected_message, expected_remediation, expected_retryable = _ERROR_COPY[self.error]
        if (self.safe_message, self.remediation, self.retryable) != (
            expected_message,
            expected_remediation,
            expected_retryable,
        ):
            raise ValueError(
                "The preview-auth error must use fixed safe copy and retry semantics; replace arbitrary text."
            )
        if self.request_id is not None:
            object.__setattr__(self, "request_id", _opaque(self.request_id, "request_id"))
        redirectable_errors = {
            PreviewAuthErrorCode.INVALID_REQUEST,
            PreviewAuthErrorCode.ACCESS_DENIED,
        }
        if self.redirect_allowed and self.error not in redirectable_errors:
            raise ValueError(
                f"The preview-auth {self.error.value} error must not redirect; return it directly."
            )
        if self.redirect_allowed:
            if self.request_id is None:
                raise ValueError(
                    "A redirectable preview-auth error requires its validated request_id correlation."
                )
            object.__setattr__(self, "state", _one_time(self.state, "state"))
        elif self.state is not None:
            raise ValueError(
                "A non-redirecting preview-auth error must omit state; do not expose browser correlation."
            )
        device_poll_error = self.error in {
            PreviewAuthErrorCode.AUTHORIZATION_PENDING,
            PreviewAuthErrorCode.SLOW_DOWN,
        }
        if device_poll_error:
            if self.request_id is None:
                raise ValueError(
                    "A preview-auth device polling error requires its validated request_id correlation."
                )
            if self.retry_after_seconds is None:
                raise ValueError(
                    "A preview-auth device polling error requires retry_after_seconds in [1, 60]."
                )
            retry_after = _integer(self.retry_after_seconds, "retry_after_seconds")
            if not 1 <= retry_after <= 60:
                raise ValueError(
                    "A preview-auth device polling error requires retry_after_seconds in [1, 60]."
                )
            object.__setattr__(self, "retry_after_seconds", retry_after)
        elif self.retry_after_seconds is not None:
            raise ValueError("Only preview-auth device polling errors may set retry_after_seconds.")
        version_values = tuple(
            _integer(item, "supported_versions") for item in self.supported_versions
        )
        deprecated_values = tuple(
            _integer(item, "deprecated_versions") for item in self.deprecated_versions
        )
        if len(set(version_values)) != len(version_values) or len(set(deprecated_values)) != len(
            deprecated_values
        ):
            raise ValueError(
                "The preview-auth error version lists contain duplicates; emit each version once."
            )
        versions = tuple(sorted(version_values))
        deprecated = tuple(sorted(deprecated_values))
        if (
            PREVIEW_AUTH_SCHEMA_VERSION not in versions
            or not versions
            or len(versions) > 16
            or len(deprecated) > 16
            or min(versions) < 1
            or max(versions) > 65_535
            or set(deprecated) - set(versions)
        ):
            raise ValueError(
                "The preview-auth version diagnostics are inconsistent; advertise only supported deprecations."
            )
        object.__setattr__(self, "supported_versions", versions)
        object.__setattr__(self, "deprecated_versions", deprecated)

    def to_dict(self) -> dict[str, object]:
        return _message(
            self,
            {
                "error": self.error.value,
                "safe_message": self.safe_message,
                "remediation": self.remediation,
                "retryable": self.retryable,
                "request_id": self.request_id,
                "redirect_allowed": self.redirect_allowed,
                "state": self.state,
                "retry_after_seconds": self.retry_after_seconds,
                "supported_versions": list(self.supported_versions),
                "deprecated_versions": list(self.deprecated_versions),
            },
        )

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> PreviewAuthError:
        fields = {
            "schema_version",
            "record_type",
            "error",
            "safe_message",
            "remediation",
            "retryable",
            "request_id",
            "redirect_allowed",
            "state",
            "retry_after_seconds",
            "supported_versions",
            "deprecated_versions",
        }
        _message_keys(value, fields, PreviewAuthRecordType.ERROR)
        return cls(
            error=PreviewAuthErrorCode(_string(value["error"], "error")),
            safe_message=_string(value["safe_message"], "safe_message"),
            remediation=_string(value["remediation"], "remediation"),
            retryable=_boolean(value["retryable"], "retryable"),
            request_id=_optional_string(value["request_id"], "request_id"),
            redirect_allowed=_boolean(value["redirect_allowed"], "redirect_allowed"),
            state=_optional_string(value["state"], "state"),
            retry_after_seconds=(
                None
                if value["retry_after_seconds"] is None
                else _integer(value["retry_after_seconds"], "retry_after_seconds")
            ),
            supported_versions=_integers(value["supported_versions"], "supported_versions"),
            deprecated_versions=_integers(value["deprecated_versions"], "deprecated_versions"),
        )


PREVIEW_AUTH_SENSITIVITY: Mapping[str, str] = MappingProxyType(
    {
        "registration.registration_id": "confidential",
        "registration.binding": "confidential",
        "authorization.request_id": "confidential",
        "authorization.binding": "confidential",
        "authorization.audience": "confidential",
        "authorization.nonce": "confidential",
        "authorization.redirect_uri": "confidential",
        "authorization.state": "secret",
        "authorization.code_challenge": "confidential",
        "code.code": "secret",
        "code.user_code": "secret",
        "code.verification_uri": "confidential",
        "code.poll_interval_seconds": "public",
        "code.subject": "pseudonymous",
        "code.nonce": "confidential",
        "exchange.exchange_id": "confidential",
        "exchange.code": "secret",
        "exchange.nonce": "confidential",
        "exchange.code_verifier": "secret",
        "grant.compact": "secret",
        "claims.sub": "pseudonymous",
        "claims.nonce": "confidential",
        "claims.jti": "confidential",
        "jwks.keys": "public",
        "revocation.subject": "pseudonymous",
        "revocation.jti": "confidential",
        "error.request_id": "confidential",
        "error.state": "secret",
        "error.retry_after_seconds": "public",
    }
)

_SENSITIVE_KEYS = frozenset(
    {
        "code",
        "user_code",
        "code_verifier",
        "verifier",
        "compact",
        "token",
        "grant",
        "signature",
        "state",
    }
)
_HASHED_KEYS = frozenset(
    {
        "subject",
        "sub",
        "nonce",
        "jti",
        "request_id",
        "exchange_id",
        "registration_id",
        "repository_id",
        "pull_request_number",
        "head_sha",
        "origin",
        "redirect_uri",
        "code_challenge",
        "verification_uri",
        "issuer",
        "authorization_endpoint",
        "grant_endpoint",
        "jwks_uri",
        "revocation_endpoint",
        "binding",
        "audience",
        "audiences",
        "redirect_paths",
        "key_id",
        "kid",
        "created_at",
        "expires_at",
        "requested_at",
        "issued_at",
        "exchanged_at",
        "generated_at",
        "stale_after",
        "revoked_at",
        "scope",
        "scopes",
        "reason",
        "sequence",
    }
)
_PUBLIC_KEYS = frozenset(
    {
        "schema_version",
        "record_type",
        "client_kind",
        "code_kind",
        "poll_interval_seconds",
        "lifetimes",
        "code_seconds",
        "grant_seconds",
        "session_seconds",
        "jwks_cache_seconds",
        "clock_skew_seconds",
        "rollover_overlap_seconds",
        "supported_versions",
        "deprecated_versions",
        "keys",
        "alg",
        "use",
        "key_ops",
        "kty",
        "crv",
        "x",
        "y",
        "cache_seconds",
        "error",
        "safe_message",
        "remediation",
        "retryable",
        "redirect_allowed",
        "retry_after_seconds",
    }
)


def canonical_preview_origin(value: str) -> str:
    """Return the unique HTTPS origin spelling used in every binding."""

    parsed = _split_https(value, "origin")
    if parsed.path not in {"", "/"} or parsed.query or parsed.fragment:
        raise ValueError(
            "The preview-auth origin must not contain a path, query, or fragment; provide an exact origin."
        )
    return urlunsplit(("https", _canonical_netloc(parsed), "", "", ""))


def canonical_issuer(value: str) -> str:
    parsed = _split_https(value, "issuer")
    if parsed.query or parsed.fragment:
        raise ValueError(
            "The preview-auth issuer must not contain a query or fragment; correct broker discovery."
        )
    path = parsed.path.rstrip("/")
    return urlunsplit(("https", _canonical_netloc(parsed), path, "", ""))


def pkce_s256_challenge(verifier: str) -> str:
    if not _PKCE_VERIFIER_RE.fullmatch(verifier):
        raise ValueError(
            "The preview-auth code_verifier must satisfy RFC 7636 length and alphabet; restart authorization."
        )
    return (
        base64.urlsafe_b64encode(hashlib.sha256(verifier.encode("ascii")).digest())
        .rstrip(b"=")
        .decode()
    )


def verify_pkce_s256(verifier: str, expected_challenge: str) -> bool:
    return hmac.compare_digest(pkce_s256_challenge(verifier), expected_challenge)


def preview_auth_consumption_digest(
    kind: PreviewAuthConsumptionKind,
    value: str,
    *,
    issuer: str,
    binding: PreviewAuthBinding,
    secret_key: bytes,
) -> str:
    """Return a keyed, domain- and binding-separated key for one-time storage."""

    if not isinstance(secret_key, bytes) or len(secret_key) < PREVIEW_AUTH_MIN_HMAC_KEY_BYTES:
        raise ValueError(
            f"The preview-auth consumption HMAC key must contain at least {PREVIEW_AUTH_MIN_HMAC_KEY_BYTES} bytes; configure a broker secret."
        )

    purpose = PreviewAuthConsumptionKind(kind)
    if purpose in {PreviewAuthConsumptionKind.CODE, PreviewAuthConsumptionKind.NONCE}:
        secret = _one_time(value, purpose.value)
    elif purpose is PreviewAuthConsumptionKind.USER_CODE:
        secret = _string(value, purpose.value)
        if not _USER_CODE_RE.fullmatch(secret):
            raise ValueError(
                "The preview-auth user_code is malformed for consumption storage; reject the supplied code."
            )
    elif not _JTI_RE.fullmatch(value):
        raise ValueError(
            "The preview-auth jti is malformed for consumption storage; reject the supplied identifier."
        )
    else:
        secret = value
    payload = {
        "domain": "furatena.preview-auth.consumption.v1",
        "kind": purpose.value,
        "issuer": canonical_issuer(issuer),
        "binding": binding.to_dict(),
        "value": secret,
    }
    digest = hmac.new(secret_key, canonical_preview_auth_json(payload), hashlib.sha256).hexdigest()
    return f"hmac-sha256:{digest}"


def canonical_preview_auth_json(value: object) -> bytes:
    serializer = getattr(value, "to_dict", None)
    plain = serializer() if callable(serializer) else value
    return json.dumps(
        plain, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode()


def redact_preview_auth(value: Mapping[str, object]) -> Mapping[str, object]:
    """Return a deeply immutable, log-safe view of a wire message."""

    def walk(item: object) -> object:
        if isinstance(item, Mapping):
            result: dict[str, object] = {}
            for key, child in item.items():
                if key in _SENSITIVE_KEYS:
                    result[str(key)] = "[REDACTED]"
                elif key in _HASHED_KEYS:
                    encoded = canonical_preview_auth_json(
                        {
                            "domain": "furatena.preview-auth.redaction.v1",
                            "field": str(key),
                            "value": child,
                        }
                    )
                    result[str(key)] = f"sha256:{hashlib.sha256(encoded).hexdigest()[:16]}"
                elif key in _PUBLIC_KEYS:
                    result[str(key)] = walk(child)
                else:
                    result[str(key)] = "[OMITTED]"
            return MappingProxyType(result)
        if isinstance(item, Sequence) and not isinstance(item, (str, bytes, bytearray)):
            return tuple(walk(child) for child in item)
        return item

    return cast("Mapping[str, object]", walk(value))


def unsupported_version_error(version: int, request_id: str | None = None) -> PreviewAuthError:
    del version
    safe_message, remediation, retryable = _ERROR_COPY[PreviewAuthErrorCode.UNSUPPORTED_VERSION]
    return PreviewAuthError(
        error=PreviewAuthErrorCode.UNSUPPORTED_VERSION,
        safe_message=safe_message,
        remediation=remediation,
        retryable=retryable,
        request_id=request_id,
        redirect_allowed=False,
    )


def _message(instance: Any, body: dict[str, object]) -> dict[str, object]:
    return {
        "schema_version": instance.schema_version,
        "record_type": instance.record_type.value,
        **body,
    }


def _header(version: int, actual: PreviewAuthRecordType, expected: PreviewAuthRecordType) -> None:
    version = _integer(version, "schema_version")
    if version != PREVIEW_AUTH_SCHEMA_VERSION:
        raise ValueError(
            f"unsupported preview-auth schema_version: {version!r}; supported_versions=[1]"
        )
    if PreviewAuthRecordType(actual) is not expected:
        raise ValueError(
            f"The preview-auth record_type must be {expected.value!r}; select the matching v1 reader."
        )


def _message_keys(
    value: Mapping[str, Any], fields: set[str], expected: PreviewAuthRecordType
) -> None:
    _exact_keys(value, fields, expected.value)
    _header(
        _integer(value["schema_version"], "schema_version"),
        PreviewAuthRecordType(_string(value["record_type"], "record_type")),
        expected,
    )


def _exact_keys(value: Mapping[str, Any], fields: set[str], context: str) -> None:
    missing, extra = fields - set(value), set(value) - fields
    if missing or extra:
        detail = []
        if missing:
            detail.append(f"missing {', '.join(sorted(missing))}")
        if extra:
            detail.append(f"unknown {', '.join(sorted(extra))}")
        raise ValueError(
            f"The preview-auth {context} has incompatible fields: {'; '.join(detail)}; use the exact v1 schema."
        )


def _split_https(value: str, field: str) -> SplitResult:
    text = _text(value, field, PREVIEW_AUTH_MAX_URL_LENGTH)
    parsed = urlsplit(text)
    if (
        parsed.scheme.lower() != "https"
        or not parsed.hostname
        or parsed.username
        or parsed.password
    ):
        raise ValueError(
            f"The preview-auth {field} must be an HTTPS URL without credentials; correct the producer."
        )
    try:
        _ = parsed.port
    except ValueError as exc:
        raise ValueError(
            f"The preview-auth {field} has an invalid port; correct the HTTPS URL."
        ) from exc
    return parsed


def _canonical_netloc(parsed: SplitResult) -> str:
    assert parsed.hostname is not None
    host = parsed.hostname
    if "%" in host or "\\" in host:
        raise ValueError(
            "The preview-auth origin hostname must not contain escaping or a zone identifier; provide a canonical host."
        )
    host = host[:-1] if host.endswith(".") else host
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        try:
            host = idna.encode(
                host,
                uts46=True,
                std3_rules=True,
                transitional=False,
            ).decode("ascii")
        except idna.IDNAError as exc:
            raise ValueError(
                "The preview-auth origin contains an invalid IDNA hostname; correct the registered host."
            ) from exc
        labels = host.split(".")
        if len(host) > 253 or any(
            not label
            or len(label) > 63
            or not re.fullmatch(r"[a-z0-9](?:[a-z0-9-]*[a-z0-9])?", label)
            for label in labels
        ):
            raise ValueError(
                "The preview-auth origin contains an invalid DNS hostname; provide canonical IDNA labels."
            ) from None
        numeric_label = re.compile(r"(?:[0-9]+|0x[0-9a-f]+)")
        if all(numeric_label.fullmatch(label) for label in labels):
            raise ValueError(
                "The preview-auth origin contains an ambiguous numeric hostname; use a canonical IP literal."
            ) from None
    else:
        host = f"[{address.compressed}]" if address.version == 6 else address.compressed
    if not host:
        raise ValueError(
            "The preview-auth origin hostname must not be empty; provide the registered host."
        )
    return host if parsed.port in {None, 443} else f"{host}:{parsed.port}"


def _https_url(value: str, field: str) -> str:
    parsed = _split_https(value, field)
    if parsed.fragment:
        raise ValueError(
            f"The preview-auth {field} must not contain a fragment; remove it from the URL."
        )
    return urlunsplit(("https", _canonical_netloc(parsed), parsed.path or "/", parsed.query, ""))


def _endpoint_url(value: str, field: str) -> str:
    result = _https_url(value, field)
    if urlsplit(result).query:
        raise ValueError(
            f"The preview-auth {field} must not contain a query; remove it from broker discovery."
        )
    return result


def _url_origin(value: str, field: str) -> str:
    parsed = _split_https(value, field)
    return urlunsplit(("https", _canonical_netloc(parsed), "", "", ""))


def _redirect_uri(value: str | None, origin: str) -> str:
    uri = _https_url(_string(value, "redirect_uri"), "redirect_uri")
    if urlsplit(uri).query:
        raise ValueError(
            "The preview-auth redirect_uri must not contain a query; reserve callback parameters for code, state, and error."
        )
    uri_origin = _url_origin(uri, "redirect_uri")
    if uri_origin != origin:
        raise ValueError(
            "The preview-auth redirect_uri must use the bound preview origin; restart from that preview."
        )
    return uri


def _redirect_path(value: str) -> str:
    path = _text(value, "redirect_path", 512)
    if not path.startswith("/") or path.startswith("//") or "?" in path or "#" in path:
        raise ValueError(
            "The preview-auth redirect path must be absolute without query or fragment; correct registration."
        )
    return path


def _timestamp(value: str, field: str) -> str:
    text = _text(value, field, 64)
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(
            f"The preview-auth {field} must be an RFC 3339 timestamp; correct the producer clock."
        ) from exc
    if parsed.tzinfo is None:
        raise ValueError(
            f"The preview-auth {field} must include a timezone; emit an explicit UTC offset."
        )
    canonical = parsed.astimezone(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")
    if text != canonical:
        raise ValueError(
            f"The preview-auth {field} must use canonical UTC seconds ending in Z; normalize the producer clock."
        )
    return canonical


def _ordered_times(start: str, end: str, context: str) -> None:
    if datetime.fromisoformat(end.replace("Z", "+00:00")) <= datetime.fromisoformat(
        start.replace("Z", "+00:00")
    ):
        raise ValueError(
            f"The preview-auth {context} expiry must follow issuance; correct the message lifetime."
        )


def _bounded_window(start: str, end: str, maximum: int, context: str) -> None:
    _ordered_times(start, end, context)
    delta = datetime.fromisoformat(end.replace("Z", "+00:00")) - datetime.fromisoformat(
        start.replace("Z", "+00:00")
    )
    if delta.total_seconds() > maximum:
        raise ValueError(
            f"The preview-auth {context} exceeds {maximum} seconds; shorten the message lifetime."
        )


def _text(value: object, field: str, maximum: int) -> str:
    text = _string(value, field)
    if (
        not text
        or text != text.strip()
        or len(text) > maximum
        or any(not char.isprintable() for char in text)
    ):
        raise ValueError(
            f"The preview-auth {field} must contain 1-{maximum} trimmed printable characters; correct the producer."
        )
    return text


def _string(value: object, field: str) -> str:
    if not isinstance(value, str):
        raise TypeError(f"The preview-auth {field} must be a string; correct the message producer.")
    return value


def _optional_string(value: object, field: str) -> str | None:
    return None if value is None else _string(value, field)


def _integer(value: object, field: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool):
        raise TypeError(
            f"The preview-auth {field} must be an integer; correct the message producer."
        )
    return value


def _boolean(value: object, field: str) -> bool:
    if not isinstance(value, bool):
        raise TypeError(
            f"The preview-auth {field} must be a boolean; correct the message producer."
        )
    return value


def _mapping(value: object, field: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise TypeError(
            f"The preview-auth {field} must be an object; correct the message producer."
        )
    return cast("Mapping[str, Any]", value)


def _sequence(value: object, field: str) -> Sequence[object]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)):
        raise TypeError(f"The preview-auth {field} must be an array; correct the message producer.")
    return value


def _strings(value: object, field: str) -> tuple[str, ...]:
    return tuple(_string(item, field) for item in _sequence(value, field))


def _integers(value: object, field: str) -> tuple[int, ...]:
    return tuple(_integer(item, field) for item in _sequence(value, field))


def _opaque(value: object, field: str) -> str:
    text = _string(value, field)
    if not _OPAQUE_RE.fullmatch(text):
        raise ValueError(
            f"The preview-auth {field} must contain 32-256 base64url characters; regenerate the identifier."
        )
    return text


def _one_time(value: object, field: str) -> str:
    text = _string(value, field)
    if not _ONE_TIME_RE.fullmatch(text):
        raise ValueError(
            f"The preview-auth {field} must contain 43-256 base64url characters; regenerate the secret."
        )
    return text


def _audience(value: str) -> str:
    if not _AUDIENCE_RE.fullmatch(value):
        raise ValueError(
            "The preview-auth audience is malformed; select an audience advertised during registration."
        )
    return value


def _audiences(values: Sequence[str]) -> tuple[str, ...]:
    if len(values) > PREVIEW_AUTH_MAX_AUDIENCES:
        raise ValueError(
            f"The preview-auth audiences list exceeds {PREVIEW_AUTH_MAX_AUDIENCES} entries; reduce registration scope."
        )
    validated = tuple(_audience(item) for item in values)
    if len(set(validated)) != len(validated):
        raise ValueError(
            "The preview-auth audiences list contains duplicates; emit each audience once."
        )
    result = tuple(sorted(validated))
    if not result:
        raise ValueError(
            "The preview-auth audiences list must not be empty; advertise an allowed audience."
        )
    return result


def _scope(value: str) -> str:
    if not _SCOPE_RE.fullmatch(value):
        raise ValueError(
            "The preview-auth scope is malformed; request a lowercase advertised scope value."
        )
    return value


def _key_id(value: str) -> str:
    if not _KID_RE.fullmatch(value):
        raise ValueError(
            "The preview-auth key ID is malformed; select an advertised verification key."
        )
    return value


def _b64_coordinate(value: str, field: str) -> None:
    _canonical_b64url(value, f"JWK {field} coordinate", decoded_bytes=32)


def _jws_segments(value: str) -> tuple[str, str, str]:
    parts = value.split(".")
    if len(parts) != 3 or any(not part for part in parts):
        raise ValueError(
            "The preview-auth signed grant must use compact JWS serialization; reject the credential."
        )
    return parts[0], parts[1], parts[2]


def _json_segment(value: str, context: str) -> Mapping[str, Any]:
    if not _B64URL_RE.fullmatch(value):
        raise ValueError(
            f"The preview-auth {context} is not valid base64url; reject the signed grant."
        )
    try:
        raw = base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))
        decoded = json.loads(raw)
    except (ValueError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError(
            f"The preview-auth {context} is not valid JSON; reject the signed grant."
        ) from exc
    if _encode_b64url(raw) != value:
        raise ValueError(
            f"The preview-auth {context} is not canonical base64url; reject the signed grant."
        )
    mapping = _mapping(decoded, context)
    if canonical_preview_auth_json(mapping) != raw:
        raise ValueError(
            f"The preview-auth {context} must use canonical JSON bytes; reject the signed grant."
        )
    return mapping


def _encode_b64url(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def _canonical_b64url(value: str, field: str, *, decoded_bytes: int) -> bytes:
    if not _B64URL_RE.fullmatch(value):
        raise ValueError(f"The preview-auth {field} is malformed; reject the credential.")
    try:
        raw = base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))
    except ValueError as exc:
        raise ValueError(f"The preview-auth {field} is malformed; reject the credential.") from exc
    if len(raw) != decoded_bytes or _encode_b64url(raw) != value:
        raise ValueError(
            f"The preview-auth {field} must be canonical base64url for {decoded_bytes} bytes; reject the credential."
        )
    return raw
