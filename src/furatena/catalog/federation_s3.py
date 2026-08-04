"""Bounded-streaming SigV4 client for S3-compatible shard publication."""

from __future__ import annotations

import datetime as dt
import hashlib
import hmac
import http.client
import re
from contextlib import AbstractContextManager
from pathlib import Path
from types import TracebackType
from typing import BinaryIO
from urllib.parse import quote, unquote, urlsplit

from furatena.catalog.federation_publish import (
    ShardPublishAuthError,
    ShardPublishConflictError,
    ShardPublishPartialError,
    StoredObject,
)

_CHUNK_BYTES = 1024 * 1024
_BUCKET_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,254}$")
_REGION_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9-]{0,62}$")
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


class _ResponseReader(AbstractContextManager[BinaryIO]):
    def __init__(
        self, connection: http.client.HTTPConnection, response: http.client.HTTPResponse
    ) -> None:
        self.connection = connection
        self.response = response

    def __enter__(self) -> BinaryIO:
        return self.response

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        _ = (exc_type, exc, traceback)
        self.response.close()
        self.connection.close()


class S3ObjectBackend:
    """Hierarchical S3-compatible backend with create-only immutable writes."""

    def __init__(
        self,
        *,
        endpoint: str,
        bucket: str,
        prefix: str,
        region: str,
        access_key_id: str,
        secret_access_key: str,
        session_token: str | None = None,
        timeout: float = 60,
    ) -> None:
        _reject_control_characters(endpoint, label="S3 endpoint")
        try:
            parsed = urlsplit(endpoint.rstrip("/"))
            endpoint_port = parsed.port
        except ValueError as exc:
            raise ValueError("The S3 endpoint is not a valid HTTPS origin.") from exc
        if parsed.scheme != "https" or not parsed.hostname or parsed.query or parsed.fragment:
            raise ValueError("The S3 endpoint must be an absolute HTTPS origin without parameters.")
        if parsed.username or parsed.password:
            raise ValueError("The S3 endpoint must not contain embedded access credentials.")
        if endpoint_port is not None and not 1 <= endpoint_port <= 65535:
            raise ValueError("The S3 endpoint port must be between one and 65535.")
        _validated_relative_path(
            parsed.path.lstrip("/"), label="S3 endpoint path", allow_empty=True
        )
        if not _BUCKET_RE.fullmatch(bucket) or bucket in {".", ".."}:
            raise ValueError("The S3 bucket must be one safe path segment without separators.")
        if not access_key_id or not secret_access_key:
            raise ValueError("The S3 bucket and both access credentials are required.")
        _reject_control_characters(access_key_id, label="S3 access key identifier")
        _reject_control_characters(secret_access_key, label="S3 secret access key")
        if session_token is not None:
            _reject_control_characters(session_token, label="S3 session token")
        if not _REGION_RE.fullmatch(region):
            raise ValueError(
                "The configured S3 signing region contains unsupported or unsafe characters."
            )
        normalized_prefix = prefix.strip("/")
        _validated_relative_path(normalized_prefix, label="S3 object prefix", allow_empty=True)
        self._host = parsed.netloc
        self._endpoint_path = parsed.path.rstrip("/")
        self._bucket = bucket
        self._prefix = normalized_prefix
        self._region = region
        self._access_key_id = access_key_id
        self._secret_access_key = secret_access_key
        self._session_token = session_token
        self._timeout = timeout

    def inspect(self, key: str) -> StoredObject | None:
        connection, response = self._request(
            "HEAD", key, payload_sha=hashlib.sha256(b"").hexdigest()
        )
        try:
            if response.status == 404:
                return None
            self._raise_status(response, key)
            size = int(response.getheader("content-length") or 0)
            digest = response.getheader("x-amz-meta-sha256")
            return StoredObject(size=size, sha256=digest)
        finally:
            response.close()
            connection.close()

    def put(
        self,
        key: str,
        source: Path,
        *,
        media_type: str,
        sha256: str,
        create_only: bool,
    ) -> None:
        _validated_relative_path(key, label="S3 object key")
        _reject_control_characters(media_type, label="S3 object media type")
        if not _SHA256_RE.fullmatch(sha256):
            raise ValueError(
                "The S3 object digest must be exactly 64 lowercase hexadecimal characters."
            )
        size = source.stat().st_size
        headers = {
            "content-length": str(size),
            "content-type": media_type,
            "x-amz-meta-sha256": sha256,
        }
        if create_only:
            headers["if-none-match"] = "*"
        connection, response = self._request(
            "PUT", key, payload_sha=sha256, headers=headers, source=source
        )
        try:
            self._raise_status(response, key)
        finally:
            response.close()
            connection.close()

    def open(self, key: str) -> AbstractContextManager[BinaryIO]:
        connection, response = self._request(
            "GET", key, payload_sha=hashlib.sha256(b"").hexdigest()
        )
        if not 200 <= response.status < 300:
            try:
                self._raise_status(response, key)
            finally:
                response.close()
                connection.close()
        return _ResponseReader(connection, response)

    def _request(
        self,
        method: str,
        key: str,
        *,
        payload_sha: str,
        headers: dict[str, str] | None = None,
        source: Path | None = None,
    ) -> tuple[http.client.HTTPConnection, http.client.HTTPResponse]:
        _validated_relative_path(key, label="S3 object key")
        now = dt.datetime.now(dt.UTC)
        amz_date = now.strftime("%Y%m%dT%H%M%SZ")
        date_stamp = now.strftime("%Y%m%d")
        path = self._object_path(key)
        request_headers = {
            "host": self._host,
            "x-amz-content-sha256": payload_sha,
            "x-amz-date": amz_date,
            **(headers or {}),
        }
        if self._session_token:
            request_headers["x-amz-security-token"] = self._session_token
        canonical_headers = "".join(
            f"{name}:{' '.join(value.split())}\n" for name, value in sorted(request_headers.items())
        )
        signed_headers = ";".join(sorted(request_headers))
        canonical_request = "\n".join(
            (method, path, "", canonical_headers, signed_headers, payload_sha)
        )
        scope = f"{date_stamp}/{self._region}/s3/aws4_request"
        string_to_sign = "\n".join(
            (
                "AWS4-HMAC-SHA256",
                amz_date,
                scope,
                hashlib.sha256(canonical_request.encode()).hexdigest(),
            )
        )
        signature = hmac.new(
            self._signing_key(date_stamp), string_to_sign.encode(), hashlib.sha256
        ).hexdigest()
        request_headers["authorization"] = (
            f"AWS4-HMAC-SHA256 Credential={self._access_key_id}/{scope}, "
            f"SignedHeaders={signed_headers}, Signature={signature}"
        )
        connection = http.client.HTTPSConnection(self._host, timeout=self._timeout)
        try:
            connection.putrequest(method, path, skip_host=True, skip_accept_encoding=True)
            for name, value in request_headers.items():
                connection.putheader(name, value)
            connection.endheaders()
            if source is not None:
                with source.open("rb") as stream:
                    for chunk in iter(lambda: stream.read(_CHUNK_BYTES), b""):
                        connection.send(chunk)
            return connection, connection.getresponse()
        except OSError as exc:
            connection.close()
            raise ShardPublishPartialError(
                f"S3 request failed before verification for {key}: {exc}",
                operation="publish_shard_upload",
            ) from exc

    def _object_path(self, key: str) -> str:
        _validated_relative_path(key, label="S3 object key")
        parts = [self._endpoint_path, self._bucket, self._prefix, key.strip("/")]
        raw = "/".join(part.strip("/") for part in parts if part)
        return "/" + quote(raw, safe="/-_.~")

    def _signing_key(self, date_stamp: str) -> bytes:
        date_key = hmac.new(
            ("AWS4" + self._secret_access_key).encode(), date_stamp.encode(), hashlib.sha256
        ).digest()
        region_key = hmac.new(date_key, self._region.encode(), hashlib.sha256).digest()
        service_key = hmac.new(region_key, b"s3", hashlib.sha256).digest()
        return hmac.new(service_key, b"aws4_request", hashlib.sha256).digest()

    @staticmethod
    def _raise_status(response: http.client.HTTPResponse, key: str) -> None:
        if 200 <= response.status < 300:
            return
        if response.status in {401, 403}:
            raise ShardPublishAuthError(
                f"S3 authentication was rejected while accessing {key}",
                operation="publish_shard_auth",
            )
        if response.status in {409, 412}:
            raise ShardPublishConflictError(
                f"immutable S3 object already exists: {key}",
                operation="publish_shard_commit",
            )
        raise ShardPublishPartialError(
            f"S3 returned HTTP {response.status} while accessing {key}",
            operation="publish_shard_upload",
        )


def _reject_control_characters(value: str, *, label: str) -> None:
    if any(ord(character) < 32 or ord(character) == 127 for character in value):
        raise ValueError(f"The {label} contains forbidden control characters.")


def _validated_relative_path(value: str, *, label: str, allow_empty: bool = False) -> str:
    _reject_control_characters(value, label=label)
    if not value:
        if allow_empty:
            return value
        raise ValueError(f"The {label} must be a non-empty relative path.")
    if value.startswith(("/", "\\")) or "\\" in value:
        raise ValueError(f"The {label} must not contain absolute or backslash separators.")
    decoded = value
    for _ in range(3):
        unquoted = unquote(decoded)
        if unquoted == decoded:
            break
        decoded = unquoted
    segments = decoded.split("/")
    if any(segment in {"", ".", ".."} for segment in segments):
        raise ValueError(f"The {label} must not contain empty, dot, or traversal segments.")
    return value
