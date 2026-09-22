"""API key management scripts — no network, no secrets printed in asserts."""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


def _load(name: str, relative: str):
    path = ROOT / relative
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_generate_gateway_api_key_length_and_uniqueness():
    gen = _load("generate_gateway_api_key", "scripts/generate_gateway_api_key.py")
    a = gen.generate_key(32)
    b = gen.generate_key(32)
    assert len(a) >= 32
    assert a != b
    assert gen.main(["--bytes", "16"]) == 2


def test_render_updater_uses_per_key_paths_only():
    """Guard against regressing to collection PUT that wipes all env vars."""
    src = (ROOT / "scripts/set_render_gateway_api_key.py").read_text(encoding="utf-8")
    assert "/env-vars/{encoded_key}" in src or 'f"/services/{service_id}/env-vars/{encoded_key}"' in src
    # Collection replace must not appear as the write path.
    assert 'f"/services/{service_id}/env-vars"' not in src or "_put_env_var" in src
    assert 'PUT", f"/services/{service_id}/env-vars"' not in src
    assert '_req("PUT", f"/services/{service_id}/env-vars"' not in src

    mod = _load("set_render_gateway_api_key", "scripts/set_render_gateway_api_key.py")
    updates = mod.managed_env_updates("strong-test-key-not-a-placeholder")
    keys = [k for k, _ in updates]
    assert keys == ["API_KEY", "AUTH_MODE", "ALLOW_UNAUTHENTICATED"]
    assert updates[1][1] == "required"
    assert updates[2][1] == "false"


def test_render_updater_rejects_weak_gateway_key(monkeypatch):
    mod = _load("set_render_gateway_api_key", "scripts/set_render_gateway_api_key.py")
    monkeypatch.setenv("RENDER_API_KEY", "rnd_fake")
    monkeypatch.setenv("GATEWAY_API_KEY", "changeme")
    assert mod.main() == 2
