"""Persisted gateway settings and dynamic model selection (FR-004, FR-066, D4).

The property under test is the one an operator actually cares about: what they
set in the drawer is still set after a restart. A restart is simulated by
clearing the settings cache and re-running the boot-time overlay, which is
exactly what `lifespan` does — so a change that breaks the real startup path
breaks these tests too.

Nothing here reaches the network. The gateway is allowed to be absent; the
tests assert the settings survive regardless, because a persisted URL that only
persists when the gateway happens to be up would be useless.
"""

from __future__ import annotations

import pytest

from pricing.config import get_settings


@pytest.fixture()
def runtime(app_db):
    """Runtime config bound to the temporary database from `app_db`."""
    from pricing.core import runtime_config

    return runtime_config


def _restart(settings) -> None:
    """Re-run what `lifespan` does at boot, against the same database."""
    from pricing.core import runtime_config

    for field in ("llm_gateway_url", "llm_gateway_api_key", "model_analyst",
                  "model_embedding"):
        setattr(settings, field, "")
    settings.allow_insecure_tls = False
    runtime_config.apply_persisted(settings)


# --- Encryption at rest ----------------------------------------------------


def test_the_api_key_round_trips_through_encryption(app_db):
    from pricing.core import secrets

    blob = secrets.encrypt("sk-live-0123456789")
    assert blob != "sk-live-0123456789"
    assert "sk-live" not in blob
    assert secrets.decrypt(blob) == "sk-live-0123456789"


def test_a_tampered_ciphertext_yields_nothing_rather_than_garbage(app_db):
    """Authentication failure must degrade, not raise — a corrupted row cannot
    be allowed to stop the platform from booting."""
    from pricing.core import secrets

    blob = secrets.encrypt("sk-live-0123456789")
    assert secrets.decrypt(blob[:-6] + "AAAAAA") == ""
    assert secrets.decrypt("not base64 at all !!") == ""
    assert secrets.decrypt("") == ""


def test_the_stored_key_never_appears_in_the_database(runtime, app_db):
    """The whole point of encrypting it: a copied app.db carries no credential."""
    runtime.persist_api_key("sk-should-never-be-readable")

    raw = get_settings().app_db_path.read_bytes()
    assert b"sk-should-never-be-readable" not in raw


def test_the_key_file_lives_outside_the_database(runtime, app_db):
    runtime.persist_api_key("sk-abc")
    settings = get_settings()

    key_file = settings.data_dir / ".secret.key"
    assert key_file.is_file()
    assert key_file != settings.app_db_path


def test_a_fingerprint_identifies_a_key_without_disclosing_it():
    from pricing.core import secrets

    key = "sk-live-0123456789"
    fingerprint = secrets.fingerprint(key)
    assert fingerprint and fingerprint not in key and key not in fingerprint
    assert secrets.fingerprint(key) == fingerprint          # stable
    assert secrets.fingerprint("sk-other") != fingerprint   # discriminating
    assert secrets.fingerprint("") == ""


# --- Persistence across a restart ------------------------------------------


def test_gateway_url_and_key_survive_a_restart(runtime, app_db):
    settings = get_settings()

    runtime.persist("llm_gateway_url", "http://gateway.internal:4000")
    runtime.persist_api_key("sk-persisted")

    _restart(settings)

    assert settings.llm_gateway_url == "http://gateway.internal:4000"
    assert settings.llm_gateway_api_key == "sk-persisted"


def test_model_assignments_survive_a_restart(runtime, app_db):
    settings = get_settings()

    runtime.persist("model_analyst", "vendor/some-analyst-model")
    _restart(settings)

    assert settings.model_analyst == "vendor/some-analyst-model"


def test_an_empty_embedding_model_is_a_choice_not_an_omission(runtime, app_db):
    """Empty means "no gateway embeddings, use the local strategies" — an
    operator must be able to select it, not merely inherit it from .env."""
    settings = get_settings()

    runtime.persist("model_embedding", "vendor/embed-large")
    _restart(settings)
    assert settings.model_embedding == "vendor/embed-large"

    runtime.persist("model_embedding", "")
    _restart(settings)
    assert settings.model_embedding == ""
    assert settings.gateway_embeddings_enabled is False


def test_clearing_the_key_removes_it_permanently(runtime, app_db):
    settings = get_settings()

    runtime.persist_api_key("sk-temporary")
    runtime.persist_api_key("")

    _restart(settings)
    assert settings.llm_gateway_api_key == ""
    assert runtime.stored_api_key() == ""


def test_only_declared_fields_can_be_persisted_from_the_ui(runtime, app_db):
    """The drawer's blast radius is bounded by design; everything else is .env."""
    with pytest.raises(KeyError):
        runtime.persist("data_dir", "/etc")


# --- Cache invalidation ----------------------------------------------------


def test_changing_the_gateway_drops_caches_computed_against_the_old_one(
    runtime, app_db, monkeypatch
):
    """Without this the save appears to work and nothing changes until a
    restart — which is precisely what these settings exist to avoid."""
    from pricing.llm import grounded, registry

    registry.probe_gateway.cache_clear()
    monkeypatch.setattr(registry, "_fetch_models", lambda *a, **k: ["m"])

    registry.probe_gateway()
    grounded._model_cache["stale"] = object()
    assert registry.probe_gateway.cache_info().currsize == 1

    runtime.invalidate_llm_caches()

    assert registry.probe_gateway.cache_info().currsize == 0
    assert not grounded._model_cache


# --- HTTP surface -----------------------------------------------------------


@pytest.fixture()
def client(app_db):
    from fastapi.testclient import TestClient

    from pricing.main import app

    with TestClient(app) as c:
        yield c


def test_the_config_endpoint_never_returns_the_key(client, runtime):
    from pricing.core import secrets

    runtime.persist_api_key("sk-must-not-leak")

    body = client.get("/api/config").json()
    assert "sk-must-not-leak" not in client.get("/api/config").text
    assert body["api_key_set"] is True
    assert body["api_key_persisted"] is True
    assert body["api_key_fingerprint"] == secrets.fingerprint("sk-must-not-leak")


def test_the_config_endpoint_reports_every_assignable_role(client):
    roles = client.get("/api/config").json()["model_roles"]

    # Embeddings included: it is chosen the same way and needs the same
    # dropdown, even though it is not a chat agent.
    assert set(roles) == {"router", "narrator", "analyst", "strategist", "embeddings"}


def test_saving_a_model_assignment_applies_it_immediately(client):
    response = client.put(
        "/api/config/models", json={"analyst": "vendor/new-analyst"}
    )
    assert response.status_code == 200
    assert response.json()["changed"] == {"analyst": "vendor/new-analyst"}

    from pricing.llm import registry

    assert registry.resolve("analyst") == "vendor/new-analyst"


def test_changing_the_embedding_model_warns_about_existing_collections(client):
    """A Chroma collection is fixed to the vectors first written into it, so
    this is a known re-ingest rather than a retrieval-quality mystery."""
    warnings = client.put(
        "/api/config/models", json={"embeddings": "vendor/embed-v2"}
    ).json()["warnings"]

    assert any("re-ingest" in w for w in warnings)


def test_an_unknown_role_is_rejected_by_name(client):
    response = client.post("/api/config/models/test", json={"role": "wizard"})
    assert response.status_code == 422
    assert "wizard" in response.text


def test_an_unreachable_gateway_reports_why_rather_than_an_empty_list(client):
    """An operator staring at an empty dropdown cannot tell a 404 from a
    certificate failure, and the two call for opposite fixes."""
    client.put("/api/config/gateway", json={"gateway_url": "http://127.0.0.1:1"})

    body = client.post("/api/config/gateway/test", json={}).json()
    assert body["reachable"] is False
    assert body["error"]
    assert body["detail"]


def test_a_gateway_save_persists_and_survives_a_rebuilt_app(client):
    client.put(
        "/api/config/gateway",
        json={"gateway_url": "http://saved.example:4000", "api_key": "sk-saved"},
    )

    _restart(get_settings())

    body = client.get("/api/config").json()
    assert body["gateway_url"] == "http://saved.example:4000"
    assert body["api_key_set"] is True
    assert "llm_gateway_url" in body["persisted_overrides"]
