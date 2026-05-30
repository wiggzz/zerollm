"""Unit tests for cluster API handler wiring."""

from __future__ import annotations

import json

from control_plane.backends.aws import handlers


def test_cluster_scale_up_invokes_orchestrator_without_compute(monkeypatch, state):
    triggered_up = []

    monkeypatch.setattr(handlers, "_get_state_store", lambda: state)
    monkeypatch.setattr(handlers, "_make_trigger_scale_up", lambda: triggered_up.append)
    monkeypatch.setattr(handlers, "_make_trigger_scale_down", lambda: lambda *_: None)
    monkeypatch.setattr(
        handlers,
        "_get_compute_backend",
        lambda: (_ for _ in ()).throw(AssertionError("cluster handler must not build compute")),
    )

    response = handlers.cluster_handler(
        {
            "requestContext": {"http": {"method": "POST"}},
            "rawPath": "/api/cluster/scale",
            "body": json.dumps({"model": "Qwen/Qwen3-32B", "action": "up"}),
        },
        None,
    )

    assert response["statusCode"] == 200
    assert triggered_up == ["Qwen/Qwen3-32B"]


def test_cluster_scale_down_invokes_orchestrator_without_compute(monkeypatch, state):
    triggered_down = []

    monkeypatch.setattr(handlers, "_get_state_store", lambda: state)
    monkeypatch.setattr(handlers, "_make_trigger_scale_up", lambda: lambda *_: None)
    monkeypatch.setattr(handlers, "_make_trigger_scale_down", lambda: triggered_down.append)
    monkeypatch.setattr(
        handlers,
        "_get_compute_backend",
        lambda: (_ for _ in ()).throw(AssertionError("cluster handler must not build compute")),
    )

    response = handlers.cluster_handler(
        {
            "requestContext": {"http": {"method": "POST"}},
            "rawPath": "/api/cluster/scale",
            "body": json.dumps({"model": "Qwen/Qwen3-32B", "action": "down"}),
        },
        None,
    )

    assert response["statusCode"] == 200
    assert triggered_down == ["Qwen/Qwen3-32B"]
