"""
proxy_forwarder.py
------------------
A loopback HTTP proxy that adds `Proxy-Authorization` to an upstream proxy,
so a browser that cannot authenticate a proxy can still use one.

Why this exists
---------------
Chrome's `--proxy-server` flag has nowhere to put a credential, and avito.ru
makes that fatal rather than inconvenient: the 2Captcha gateway LOGIN is what
selects the Russian region and pins the session, so an unauthenticated exit
is no exit at all.

Selenium's documented answer, WebDriver BiDi `network.add_auth_handler`, was
measured on 4.50.0 (2026-10-06) and DEADLOCKS: its handler sends a BiDi
command from inside the websocket's own event callback and waits for a reply
that the same thread would have to read — "Timed out waiting for response to
BiDi command 4", then a renderer timeout. So instead:

    Chrome --proxy-server=http://127.0.0.1:<port>   (no credential, loopback)
        -> this forwarder, which adds Proxy-Authorization
        -> the real gateway

The credential stays in this process's memory: never on a command line
(`ps` reads argv), never on disk. The listener binds 127.0.0.1 only, on a
port the OS picks.

Handles `CONNECT` (every https:// page) by tunnelling, and plain-http
requests by rewriting them to absolute-form with the header added.
"""

from __future__ import annotations

import base64
import select
import socket
import threading
from typing import Optional, Tuple
from urllib.parse import urlparse

_BUF = 65536


class ProxyForwarder:
    def __init__(self, upstream_url: str):
        parts = urlparse(upstream_url)
        if not (parts.hostname and parts.port):
            raise ValueError("upstream proxy needs host and port")
        self._upstream: Tuple[str, int] = (parts.hostname, parts.port)
        token = "%s:%s" % (parts.username or "", parts.password or "")
        self._auth = b"Proxy-Authorization: Basic " + base64.b64encode(token.encode()) + b"\r\n"
        self._server: Optional[socket.socket] = None
        self._thread: Optional[threading.Thread] = None
        self._stopping = threading.Event()
        self.port: Optional[int] = None

    @property
    def url(self) -> str:
        return "http://127.0.0.1:%d" % self.port

    def start(self) -> "ProxyForwarder":
        server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        server.bind(("127.0.0.1", 0))
        server.listen(64)
        server.settimeout(0.5)
        self._server = server
        self.port = server.getsockname()[1]
        self._thread = threading.Thread(target=self._accept_loop, daemon=True)
        self._thread.start()
        return self

    def stop(self) -> None:
        self._stopping.set()
        if self._server is not None:
            try:
                self._server.close()
            except OSError:
                pass

    def _accept_loop(self) -> None:
        while not self._stopping.is_set():
            try:
                client, _ = self._server.accept()
            except socket.timeout:
                continue
            except OSError:
                return
            threading.Thread(target=self._handle, args=(client,), daemon=True).start()

    def _handle(self, client: socket.socket) -> None:
        upstream = None
        try:
            client.settimeout(60)
            head = b""
            while b"\r\n\r\n" not in head:
                chunk = client.recv(_BUF)
                if not chunk:
                    return
                head += chunk
                if len(head) > 1 << 20:
                    return
            header, rest = head.split(b"\r\n\r\n", 1)
            lines = header.split(b"\r\n")
            # Drop any credential the client sent; ours is the only one.
            kept = [l for l in lines[1:] if not l.lower().startswith(b"proxy-authorization:")]
            upstream = socket.create_connection(self._upstream, timeout=60)
            request = lines[0] + b"\r\n" + b"".join(l + b"\r\n" for l in kept) + self._auth + b"\r\n"
            upstream.sendall(request + rest)
            self._pipe(client, upstream)
        except OSError:
            pass
        finally:
            for s in (client, upstream):
                if s is not None:
                    try:
                        s.close()
                    except OSError:
                        pass

    def _pipe(self, a: socket.socket, b: socket.socket) -> None:
        """Copy both directions until either side closes."""
        socks = [a, b]
        while not self._stopping.is_set():
            readable, _, broken = select.select(socks, [], socks, 60)
            if broken or not readable:
                return
            for s in readable:
                data = s.recv(_BUF)
                if not data:
                    return
                (b if s is a else a).sendall(data)
