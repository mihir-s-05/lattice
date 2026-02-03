from __future__ import annotations

import json
import threading
import time
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Dict, List, Optional, Tuple


@dataclass
class QueuedResponse:
    status: int
    headers: Dict[str, str]
    body: bytes
    delay_sec: float = 0.0


class StubHTTPServer:
    """
    Tiny in-process HTTP server for black-box tests against requests semantics.

    - Records request method/path/body (JSON when possible).
    - Returns queued responses in FIFO order.
    """

    def __init__(self, host: str = "127.0.0.1", port: int = 0) -> None:
        self._host = host
        self._port = port
        self._httpd: Optional[ThreadingHTTPServer] = None
        self._thread: Optional[threading.Thread] = None

    def __enter__(self) -> "StubHTTPServer":
        parent = self

        class Handler(BaseHTTPRequestHandler):
            server_version = "LatticeTestHTTP/1.0"

            def log_message(self, fmt: str, *args: Any) -> None:
                return

            def do_POST(self) -> None:  # noqa: N802
                length = int(self.headers.get("Content-Length") or "0")
                raw = self.rfile.read(length) if length > 0 else b""
                parsed: Any = None
                try:
                    parsed = json.loads(raw.decode("utf-8")) if raw else None
                except Exception:
                    parsed = None

                self.server.requests.append(  # type: ignore[attr-defined]
                    {
                        "method": "POST",
                        "path": self.path,
                        "headers": dict(self.headers),
                        "raw": raw,
                        "json": parsed,
                    }
                )

                resp = self.server.pop_response()  # type: ignore[attr-defined]
                if resp.delay_sec:
                    time.sleep(resp.delay_sec)

                try:
                    self.send_response(resp.status)
                    for k, v in resp.headers.items():
                        self.send_header(k, v)
                    self.send_header("Content-Length", str(len(resp.body)))
                    self.end_headers()
                    if resp.body:
                        self.wfile.write(resp.body)
                except (BrokenPipeError, ConnectionResetError, OSError):
                    return

        httpd = ThreadingHTTPServer((self._host, self._port), Handler)
        httpd.responses = []  # type: ignore[attr-defined]
        httpd.requests = []  # type: ignore[attr-defined]
        httpd._lock = threading.Lock()  # type: ignore[attr-defined]

        def pop_response() -> QueuedResponse:
            with httpd._lock:  # type: ignore[attr-defined]
                if not httpd.responses:  # type: ignore[attr-defined]
                    last = None
                    try:
                        last = httpd.requests[-1] if httpd.requests else None  # type: ignore[attr-defined]
                    except Exception:
                        last = None
                    payload = {
                        "error": "no queued response",
                        "requests_seen": len(getattr(httpd, "requests", []) or []),
                        "last_request": {
                            "method": (last or {}).get("method"),
                            "path": (last or {}).get("path"),
                        }
                        if isinstance(last, dict)
                        else None,
                        "note": "Integration tests enqueue a fixed call sequence; if orchestration changes, update the queue.",
                    }
                    return QueuedResponse(
                        status=500,
                        headers={"Content-Type": "application/json"},
                        body=(json.dumps(payload, ensure_ascii=False) + "\n").encode("utf-8"),
                    )
                return httpd.responses.pop(0)  # type: ignore[attr-defined]

        httpd.pop_response = pop_response  # type: ignore[attr-defined]
        self._httpd = httpd

        th = threading.Thread(target=httpd.serve_forever, daemon=True)
        th.start()
        self._thread = th
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        if self._httpd is not None:
            try:
                self._httpd.shutdown()
            finally:
                try:
                    self._httpd.server_close()
                finally:
                    self._httpd = None
        self._thread = None

    @property
    def port(self) -> int:
        assert self._httpd is not None
        return int(self._httpd.server_address[1])

    @property
    def base_url(self) -> str:
        return f"http://{self._host}:{self.port}"

    @property
    def base_url_v1(self) -> str:
        return f"{self.base_url}/v1"

    @property
    def requests(self) -> List[Dict[str, Any]]:
        assert self._httpd is not None
        return list(self._httpd.requests)  # type: ignore[attr-defined]

    def enqueue(
        self,
        *,
        status: int = 200,
        headers: Optional[Dict[str, str]] = None,
        body: bytes = b"",
        delay_sec: float = 0.0,
    ) -> None:
        assert self._httpd is not None
        hdrs = dict(headers or {})
        if "Content-Type" not in hdrs:
            hdrs["Content-Type"] = "application/json; charset=utf-8"
        with self._httpd._lock:  # type: ignore[attr-defined]
            self._httpd.responses.append(  # type: ignore[attr-defined]
                QueuedResponse(status=int(status), headers=hdrs, body=body, delay_sec=float(delay_sec))
            )

    def enqueue_json(
        self,
        obj: Any,
        *,
        status: int = 200,
        headers: Optional[Dict[str, str]] = None,
        delay_sec: float = 0.0,
    ) -> None:
        self.enqueue(
            status=status,
            headers=headers,
            body=(json.dumps(obj, ensure_ascii=False) + "\n").encode("utf-8"),
            delay_sec=delay_sec,
        )


def openai_chat_completion(content: str, *, tool_calls: Optional[List[Dict[str, Any]]] = None) -> Dict[str, Any]:
    msg: Dict[str, Any] = {"role": "assistant", "content": content}
    if tool_calls:
        msg["tool_calls"] = tool_calls
    return {"id": "cmpl-test", "choices": [{"index": 0, "message": msg}]}
