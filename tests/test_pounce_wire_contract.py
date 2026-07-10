"""Wire-level regression proof for the Pounce production boundary."""

from __future__ import annotations

import json
import socket
import threading
import time
from dataclasses import dataclass
from http.client import HTTPConnection
from itertools import pairwise
from typing import Any

import pytest
from pounce import ServerConfig
from pounce.server import Server
from pounce.testing import TestServer


async def _unreachable_app(scope, receive, send) -> None:
    """Fail if Pounce does not intercept its configured readiness endpoint."""
    if scope["type"] == "lifespan":
        while True:
            message = await receive()
            if message["type"] == "lifespan.startup":
                await send({"type": "lifespan.startup.complete"})
            elif message["type"] == "lifespan.shutdown":
                await send({"type": "lifespan.shutdown.complete"})
                return
    if scope["type"].startswith("pounce.worker."):
        return
    raise AssertionError("Pounce built-in readiness did not intercept the request")


@dataclass(frozen=True, slots=True)
class _WireResponse:
    status: int
    headers: dict[str, str]
    body: bytes


def _request(server: TestServer, method: str, path: str) -> _WireResponse:
    with socket.create_connection((server.host, server.port), timeout=5.0) as connection:
        connection.sendall(
            (
                f"{method} {path} HTTP/1.1\r\n"
                f"Host: {server.host}:{server.port}\r\n"
                "Accept: application/json\r\n"
                "Connection: close\r\n\r\n"
            ).encode("ascii")
        )
        chunks: list[bytes] = []
        while chunk := connection.recv(65536):
            chunks.append(chunk)
    head, body = b"".join(chunks).split(b"\r\n\r\n", 1)
    lines = head.decode("latin-1").split("\r\n")
    status = int(lines[0].split(" ", 2)[1])
    headers = {
        name.strip().lower(): value.strip()
        for name, value in (line.split(":", 1) for line in lines[1:])
    }
    return _WireResponse(status, headers, body)


def test_builtin_readiness_head_has_get_metadata_and_zero_body_octets() -> None:
    with TestServer(
        _unreachable_app,
        health_check_path="/readyz",
        compression=False,
    ) as server:
        get = _request(server, "GET", "/readyz")
        head = _request(server, "HEAD", "/readyz")

    assert get.status == head.status == 200
    assert json.loads(get.body)["status"] == "ok"
    assert head.body == b""
    for name in ("content-type", "content-length", "cache-control"):
        assert head.headers[name] == get.headers[name]
    assert int(head.headers["content-length"]) == len(get.body)


_SLOW_STARTED = threading.Event()


async def _runtime_probe_app(scope: dict[str, Any], receive: Any, send: Any) -> None:
    if scope["type"] == "lifespan":
        while True:
            message = await receive()
            if message["type"] == "lifespan.startup":
                await send({"type": "lifespan.startup.complete"})
            elif message["type"] == "lifespan.shutdown":
                await send({"type": "lifespan.shutdown.complete"})
                return
    if scope["type"].startswith("pounce.worker."):
        return
    if scope["type"] != "http":
        return
    await receive()
    if scope["path"] == "/slow":
        import asyncio

        _SLOW_STARTED.set()
        await asyncio.sleep(0.75)
    worker = scope.get("extensions", {}).get("pounce.worker", {})
    body = json.dumps(
        {"status": "ok", "generation": worker.get("generation", 0)},
        separators=(",", ":"),
    ).encode()
    await send(
        {
            "type": "http.response.start",
            "status": 200,
            "headers": [
                (b"content-type", b"application/json"),
                (b"content-length", str(len(body)).encode("ascii")),
            ],
        }
    )
    await send({"type": "http.response.body", "body": body})


class _ThreadWorkerServer:
    def __init__(self) -> None:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
            probe.bind(("127.0.0.1", 0))
            self.host, self.port = probe.getsockname()
        config = ServerConfig(
            host=self.host,
            port=self.port,
            workers=2,
            worker_mode="sync",
            health_check_path="/readyz",
            access_log=False,
            compression=False,
            reload_timeout=3.0,
            shutdown_timeout=2.0,
        )
        self.server = Server(config, _runtime_probe_app)
        self.thread = threading.Thread(target=self.server.run, daemon=True)

    def __enter__(self) -> _ThreadWorkerServer:
        self.thread.start()
        deadline = time.monotonic() + 5.0
        while time.monotonic() < deadline and self.thread.is_alive():
            try:
                status, _ = self.get("/readyz")
                if status == 200:
                    return self
            except (ConnectionError, OSError):
                pass
            time.sleep(0.01)
        self.server.shutdown()
        self.thread.join(timeout=5.0)
        raise RuntimeError("Pounce thread-worker server did not become ready")

    def __exit__(self, *exc: object) -> None:
        self.server.shutdown()
        self.thread.join(timeout=5.0)
        assert not self.thread.is_alive()

    @property
    def address(self) -> tuple[str, int]:
        return self.host, self.port

    def get(self, path: str) -> tuple[int, bytes]:
        connection = HTTPConnection(*self.address, timeout=3.0)
        try:
            connection.request("GET", path, headers={"connection": "close"})
            response = connection.getresponse()
            return response.status, response.read()
        finally:
            connection.close()


def test_thread_worker_reload_hands_off_listener_without_failed_requests() -> None:
    observations: list[tuple[int, int, int]] = []
    failures: list[str] = []
    stop = threading.Event()

    with _ThreadWorkerServer() as runtime:
        status, body = runtime.get("/probe")
        initial_generation = int(json.loads(body)["generation"])
        assert status == 200

        def sample() -> None:
            while not stop.is_set():
                try:
                    sampled_status, sampled_body = runtime.get("/probe")
                    generation = int(json.loads(sampled_body)["generation"])
                    observations.append((time.time_ns(), sampled_status, generation))
                except Exception as exc:
                    failures.append(f"{type(exc).__name__}: {exc}")
                stop.wait(0.01)

        sampler = threading.Thread(target=sample, daemon=True)
        sampler.start()
        time.sleep(0.1)
        supervisor = runtime.server._supervisor
        assert supervisor is not None
        supervisor.graceful_reload()
        deadline = time.monotonic() + 3.0
        while time.monotonic() < deadline:
            if any(generation > initial_generation for _, _, generation in observations):
                break
            time.sleep(0.02)
        stop.set()
        sampler.join(timeout=2.0)

    assert not failures
    assert len(observations) >= 5
    assert all(status == 200 for _, status, _ in observations)
    assert all(
        earlier < later
        for (earlier, _, _), (later, _, _) in pairwise(observations)
    )
    assert any(generation > initial_generation for _, _, generation in observations)


def test_thread_worker_shutdown_returns_bounded_draining_503() -> None:
    _SLOW_STARTED.clear()
    slow_result: list[tuple[int, bytes]] = []
    observed: list[tuple[int, int, bytes]] = []

    runtime = _ThreadWorkerServer()
    runtime.__enter__()
    try:
        slow = threading.Thread(target=lambda: slow_result.append(runtime.get("/slow")), daemon=True)
        slow.start()
        assert _SLOW_STARTED.wait(timeout=2.0)
        runtime.server.shutdown()
        deadline = time.monotonic() + 2.0
        while time.monotonic() < deadline:
            try:
                status, body = runtime.get("/readyz")
                observed.append((time.time_ns(), status, body))
                if status == 503:
                    break
            except (ConnectionError, OSError):
                pass
            time.sleep(0.005)
        slow.join(timeout=2.0)
    finally:
        runtime.thread.join(timeout=5.0)

    assert not runtime.thread.is_alive()
    assert slow_result and slow_result[0][0] == 200
    draining = [(timestamp, body) for timestamp, status, body in observed if status == 503]
    assert draining
    if b"draining" not in draining[0][1]:
        pytest.xfail("Pounce 0.9.0 listener drain body is tracked by lbliii/pounce#308")
