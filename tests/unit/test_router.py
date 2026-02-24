"""Unit tests for router core logic (Phase 2)."""

from __future__ import annotations

from control_plane.core import router


def test_list_models_returns_openai_compatible_payload(state):
    result = router.list_models(state)

    assert result["object"] == "list"
    assert len(result["data"]) == 1
    assert result["data"][0]["id"] == "Qwen/Qwen3-32B"
    assert result["data"][0]["object"] == "model"


def test_handle_inference_returns_503_and_triggers_scale_up_when_cold(state):
    triggered = []

    def trigger(model_name: str):
        triggered.append(model_name)

    result = router.handle_inference(
        model="Qwen/Qwen3-32B",
        body={"model": "Qwen/Qwen3-32B", "messages": [{"role": "user", "content": "hi"}]},
        path="/v1/chat/completions",
        state=state,
        trigger_scale_up=trigger,
    )

    assert result["status_code"] == 503
    assert result["headers"]["Retry-After"] == "10"
    assert triggered == ["Qwen/Qwen3-32B"]


def test_handle_inference_returns_400_when_model_missing(state):
    result = router.handle_inference(
        model="",
        body={"messages": [{"role": "user", "content": "hi"}]},
        state=state,
        trigger_scale_up=lambda *_: None,
    )

    assert result["status_code"] == 400
    assert "model is required" in result["body"]["error"]["message"]


def test_handle_inference_proxies_to_ready_instance_and_updates_last_request(state, monkeypatch):
    state.put_instance(
        {
            "instance_id": "model#Qwen/Qwen3-32B",
            "provider_instance_id": "i-ready",
            "model": "Qwen/Qwen3-32B",
            "status": "ready",
            "ip": "10.0.0.1",
            "instance_type": "g5.xlarge",
            "launched_at": 100,
            "last_request_at": 100,
        }
    )

    monkeypatch.setattr(router.time, "time", lambda: 500)

    captured = {}

    def fake_proxy(ip: str, port: int, path: str, body: dict, **kwargs):
        captured.update({"ip": ip, "port": port, "path": path, "body": body})
        return 200, {"id": "chatcmpl-1"}, {}

    monkeypatch.setattr(router, "proxy_request", fake_proxy)

    result = router.handle_inference(
        model="Qwen/Qwen3-32B",
        body={"model": "Qwen/Qwen3-32B", "messages": []},
        path="/v1/chat/completions",
        state=state,
        trigger_scale_up=lambda *_: None,
    )

    assert result == {"status_code": 200, "body": {"id": "chatcmpl-1"}}
    assert captured["ip"] == "10.0.0.1"
    assert captured["port"] == router.VLLM_PORT
    assert captured["path"] == "/v1/chat/completions"
    assert state.get_instance("model#Qwen/Qwen3-32B")["last_request_at"] == 500


def test_handle_inference_returns_502_when_proxy_errors(state, monkeypatch):
    state.put_instance(
        {
            "instance_id": "model#Qwen/Qwen3-32B",
            "provider_instance_id": "i-ready",
            "model": "Qwen/Qwen3-32B",
            "status": "ready",
            "ip": "10.0.0.1",
            "instance_type": "g5.xlarge",
            "launched_at": 100,
            "last_request_at": 100,
        }
    )

    def fail_proxy(*args, **kwargs):
        raise router.requests.RequestException("boom")

    monkeypatch.setattr(router, "proxy_request", fail_proxy)

    result = router.handle_inference(
        model="Qwen/Qwen3-32B",
        body={"model": "Qwen/Qwen3-32B", "messages": []},
        state=state,
        trigger_scale_up=lambda *_: None,
    )

    assert result["status_code"] == 502
    assert result["body"]["error"]["type"] == "bad_gateway"


def test_handle_inference_proxies_sse_payload_and_headers(state, monkeypatch):
    state.put_instance(
        {
            "instance_id": "model#Qwen/Qwen3-32B",
            "provider_instance_id": "i-ready",
            "model": "Qwen/Qwen3-32B",
            "status": "ready",
            "ip": "10.0.0.1",
            "instance_type": "g5.xlarge",
            "launched_at": 100,
            "last_request_at": 100,
        }
    )

    monkeypatch.setattr(
        router,
        "proxy_request",
        lambda *args, **kwargs: (
            200,
            "data: {\"id\":\"chunk-1\"}\n\ndata: [DONE]\n\n",
            {"Content-Type": "text/event-stream; charset=utf-8"},
        ),
    )

    result = router.handle_inference(
        model="Qwen/Qwen3-32B",
        body={"model": "Qwen/Qwen3-32B", "messages": [], "stream": True},
        state=state,
        trigger_scale_up=lambda *_: None,
    )

    assert result["status_code"] == 200
    assert result["headers"]["Content-Type"] == "text/event-stream; charset=utf-8"
    assert result["body"].startswith("data: ")


def test_proxy_request_returns_buffered_sse_body(monkeypatch):
    class FakeResponse:
        status_code = 200
        headers = {"Content-Type": "text/event-stream; charset=utf-8"}
        text = "data: hello\n\n"

        def json(self):
            raise AssertionError("json() should not be called for SSE responses")

    monkeypatch.setattr(router.requests, "post", lambda *args, **kwargs: FakeResponse())

    status, payload, headers = router.proxy_request(
        ip="10.0.0.1",
        port=router.VLLM_PORT,
        path="/v1/chat/completions",
        body={"model": "Qwen/Qwen3-32B", "stream": True},
    )

    assert status == 200
    assert payload == "data: hello\n\n"
    assert headers == {"Content-Type": "text/event-stream; charset=utf-8"}
