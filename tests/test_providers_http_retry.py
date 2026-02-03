import pytest


def _provider_cfg(base_url_v1: str):
    from lattice.config import ProviderConfig

    return ProviderConfig(
        name="openai",
        base_url=base_url_v1,
        api_key="test",
        model="gpt-4o-mini",
    )


def test_call_with_fallback_retries_transient_http_status(monkeypatch, tmp_path):
    from lattice.providers import call_with_fallback
    from lattice.runlog import RunLogger
    from tests.http_stub import StubHTTPServer, openai_chat_completion

    monkeypatch.setattr("lattice.providers.random.random", lambda: 0.0)

    with StubHTTPServer() as srv:
        srv.enqueue_json({"error": {"message": "server_error"}}, status=500)
        srv.enqueue_json(openai_chat_completion("ok"), status=200)

        logger = RunLogger(str(tmp_path / "run"))
        res = call_with_fallback(
            providers={"openai": _provider_cfg(srv.base_url_v1)},
            order=["openai"],
            messages=[{"role": "user", "content": "hi"}],
            temperature=0.0,
            max_tokens=8,
            logger=logger,
            retries=3,
            http_timeout=1.0,
            connect_timeout=1.0,
            max_retry_delay=0,
        )

        assert res.text == "ok"
        assert res.attempts == 1
        assert len(srv.requests) == 2


def test_call_with_fallback_does_not_retry_non_transient_status(monkeypatch, tmp_path):
    from lattice.providers import call_with_fallback
    from lattice.errors import ProviderError
    from lattice.runlog import RunLogger
    from tests.http_stub import StubHTTPServer

    monkeypatch.setattr("lattice.providers.random.random", lambda: 0.0)

    with StubHTTPServer() as srv:
        srv.enqueue_json({"error": {"message": "unauthorized"}}, status=401)

        logger = RunLogger(str(tmp_path / "run"))
        with pytest.raises(ProviderError):
            call_with_fallback(
                providers={"openai": _provider_cfg(srv.base_url_v1)},
                order=["openai"],
                messages=[{"role": "user", "content": "hi"}],
                temperature=0.0,
                max_tokens=8,
                logger=logger,
                retries=5,
                http_timeout=1.0,
                connect_timeout=1.0,
                max_retry_delay=0,
            )

        assert len(srv.requests) == 1


def test_call_with_fallback_retries_on_malformed_json(monkeypatch, tmp_path):
    from lattice.providers import call_with_fallback
    from lattice.runlog import RunLogger
    from tests.http_stub import StubHTTPServer, openai_chat_completion

    monkeypatch.setattr("lattice.providers.random.random", lambda: 0.0)

    with StubHTTPServer() as srv:
        srv.enqueue(status=200, headers={"Content-Type": "application/json"}, body=b"not-json\n")
        srv.enqueue_json(openai_chat_completion("ok"), status=200)

        logger = RunLogger(str(tmp_path / "run"))
        res = call_with_fallback(
            providers={"openai": _provider_cfg(srv.base_url_v1)},
            order=["openai"],
            messages=[{"role": "user", "content": "hi"}],
            temperature=0.0,
            max_tokens=8,
            logger=logger,
            retries=2,
            http_timeout=1.0,
            connect_timeout=1.0,
            max_retry_delay=0,
        )

        assert res.text == "ok"
        assert len(srv.requests) == 2


def test_call_with_fallback_retries_on_read_timeout(monkeypatch, tmp_path):
    from lattice.providers import call_with_fallback
    from lattice.runlog import RunLogger
    from tests.http_stub import StubHTTPServer, openai_chat_completion

    monkeypatch.setattr("lattice.providers.random.random", lambda: 0.0)

    with StubHTTPServer() as srv:
        srv.enqueue_json(openai_chat_completion("slow"), status=200, delay_sec=0.25)
        srv.enqueue_json(openai_chat_completion("ok"), status=200)

        logger = RunLogger(str(tmp_path / "run"))
        res = call_with_fallback(
            providers={"openai": _provider_cfg(srv.base_url_v1)},
            order=["openai"],
            messages=[{"role": "user", "content": "hi"}],
            temperature=0.0,
            max_tokens=8,
            logger=logger,
            retries=1,
            http_timeout=0.10,
            connect_timeout=0.10,
            max_retry_delay=0,
        )

        assert res.text == "ok"
        assert len(srv.requests) == 2


def test_call_with_fallback_does_not_retry_on_http_error_string(monkeypatch, tmp_path):
    from lattice.providers import call_with_fallback
    from lattice.errors import ProviderError
    from lattice.runlog import RunLogger
    import requests

    monkeypatch.setattr("lattice.providers.random.random", lambda: 0.0)
    monkeypatch.setattr("lattice.providers.time.sleep", lambda *_: None)

    calls = {"n": 0}

    def _fail(self, *args, **kwargs):
        calls["n"] += 1
        raise requests.exceptions.HTTPError("404 Client Error: Not Found for url")

    monkeypatch.setattr("lattice.providers.OpenAICompatProvider.chat_completion", _fail)

    logger = RunLogger(str(tmp_path / "run"))
    with pytest.raises(ProviderError):
        call_with_fallback(
            providers={"openai": _provider_cfg("http://example.invalid/v1")},
            order=["openai"],
            messages=[{"role": "user", "content": "hi"}],
            temperature=0.0,
            max_tokens=8,
            logger=logger,
            retries=3,
            http_timeout=0.1,
            connect_timeout=0.1,
            max_retry_delay=0,
        )

    assert calls["n"] == 1
