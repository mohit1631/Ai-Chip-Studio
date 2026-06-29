"""
tests/test_workshop.py
------------------------
Covers the Workshop / Community sharing feature added in the
workshop_feature_update patch: publish, unpublish, browse, view (+1 view),
and import/fork (+1 fork, file copy, new owner gets their own project).

Projects are created directly via the ORM rather than through the real
ZIP-upload endpoint -- that keeps these tests focused on workshop.py's own
logic instead of also depending on staging/ZIP-slip behavior, which has
its own test file once it exists. A real file is still written through
the storage layer for the import/fork tests, since copying files via
storage.list_keys()/get_bytes()/put_bytes() is exactly the behavior being
tested there.
"""
from __future__ import annotations

import pytest


def _make_project(db_session, user_id: int, *, name: str = "adder", is_public: bool = False):
    from app.models import Project, ProjectMember, ProjectRole

    project = Project(
        name=name,
        storage_path=f"projects/proj_{name}/source",
        top_module="adder_top",
        is_public=is_public,
    )
    db_session.add(project)
    db_session.flush()
    db_session.add(ProjectMember(project_id=project.id, user_id=user_id, role=ProjectRole.owner))
    db_session.commit()
    db_session.refresh(project)
    return project


@pytest.fixture()
def make_project(db_engine):
    """Returns a helper that creates a Project + owner ProjectMember row
    directly against the test DB, bypassing the ZIP-upload endpoint."""
    from sqlalchemy.orm import sessionmaker

    SessionLocal = sessionmaker(bind=db_engine)

    def _do(user_id: int, **kwargs):
        session = SessionLocal()
        try:
            return _make_project(session, user_id, **kwargs)
        finally:
            session.close()

    return _do


def _get_user_id(client, headers) -> int:
    return client.get("/auth/me", headers=headers).json()["id"]


def test_publish_makes_project_visible_in_workshop(client, auth_headers, make_project):
    user_id = _get_user_id(client, auth_headers)
    project = make_project(user_id)

    resp = client.post(f"/projects/{project.id}/publish", json={}, headers=auth_headers)
    assert resp.status_code == 200, resp.text
    assert resp.json()["is_public"] is True

    browse = client.get("/workshop", headers=auth_headers)
    assert browse.status_code == 200
    ids = [p["id"] for p in browse.json()]
    assert project.id in ids


def test_publish_sets_pdk_when_provided(client, auth_headers, make_project):
    user_id = _get_user_id(client, auth_headers)
    project = make_project(user_id)

    resp = client.post(
        f"/projects/{project.id}/publish", json={"pdk": "SKY130"}, headers=auth_headers
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["pdk"] == "SKY130"


def test_publish_requires_owner_role(client, make_project):
    """A non-owner (or non-member) must not be able to publish someone
    else's project -- require_project_role(owner) should 404 it rather
    than leak whether the project exists."""
    resp = client.post(
        "/auth/register", json={"email": "owner@example.com", "password": "owner-pass-1"}
    )
    owner_id = resp.json()["id"]
    project = make_project(owner_id)

    # Register and log in as a *different* user who has no membership at all.
    client.post("/auth/register", json={"email": "intruder@example.com", "password": "x-pass-1"})
    intruder_login = client.post(
        "/auth/login", data={"username": "intruder@example.com", "password": "x-pass-1"}
    )
    intruder_headers = {"Authorization": f"Bearer {intruder_login.json()['access_token']}"}

    resp = client.post(f"/projects/{project.id}/publish", json={}, headers=intruder_headers)
    assert resp.status_code == 404


def test_unpublish_removes_project_from_workshop(client, auth_headers, make_project):
    user_id = _get_user_id(client, auth_headers)
    project = make_project(user_id, is_public=True)

    resp = client.post(f"/projects/{project.id}/unpublish", headers=auth_headers)
    assert resp.status_code == 200, resp.text
    assert resp.json()["is_public"] is False

    browse = client.get("/workshop", headers=auth_headers)
    ids = [p["id"] for p in browse.json()]
    assert project.id not in ids


def test_browse_workshop_requires_login(client):
    resp = client.get("/workshop")
    assert resp.status_code == 401


def test_browse_workshop_excludes_private_projects(client, auth_headers, make_project):
    user_id = _get_user_id(client, auth_headers)
    make_project(user_id, name="private-one", is_public=False)
    public_project = make_project(user_id, name="public-one", is_public=True)

    resp = client.get("/workshop", headers=auth_headers)
    names = [p["name"] for p in resp.json()]
    assert "public-one" in names
    assert "private-one" not in names
    assert resp.json()[names.index("public-one")]["id"] == public_project.id


def test_view_workshop_project_increments_views(client, auth_headers, make_project):
    user_id = _get_user_id(client, auth_headers)
    project = make_project(user_id, is_public=True)

    first = client.get(f"/workshop/{project.id}", headers=auth_headers)
    assert first.status_code == 200
    assert first.json()["views"] == 1

    second = client.get(f"/workshop/{project.id}", headers=auth_headers)
    assert second.json()["views"] == 2


def test_view_private_project_returns_404(client, auth_headers, make_project):
    user_id = _get_user_id(client, auth_headers)
    project = make_project(user_id, is_public=False)

    resp = client.get(f"/workshop/{project.id}", headers=auth_headers)
    assert resp.status_code == 404


def test_view_nonexistent_project_returns_404(client, auth_headers):
    resp = client.get("/workshop/999999", headers=auth_headers)
    assert resp.status_code == 404


def test_import_forks_project_with_copied_files(client, auth_headers, make_project, tmp_path, monkeypatch):
    import app.services.storage as storage_module

    # get_storage() is @lru_cache'd and storage.py reads settings.storage_root
    # at call time from the *already-imported* settings object -- reloading
    # the module wouldn't help, since app.routers.workshop already holds a
    # reference to the original get_storage. Patch settings in place instead,
    # and clear the cache so the next get_storage() call picks it up.
    monkeypatch.setattr(storage_module.settings, "storage_root", str(tmp_path))
    storage_module.get_storage.cache_clear()

    owner_id = _get_user_id(client, auth_headers)
    project = make_project(owner_id, is_public=True)

    storage = storage_module.get_storage()
    storage.put_bytes(f"{project.storage_path}/adder.v", b"module adder_top(); endmodule")

    # Log in as a second user who will fork the design.
    client.post("/auth/register", json={"email": "forker@example.com", "password": "fork-pass-1"})
    login = client.post(
        "/auth/login", data={"username": "forker@example.com", "password": "fork-pass-1"}
    )
    forker_headers = {"Authorization": f"Bearer {login.json()['access_token']}"}

    resp = client.post(f"/workshop/{project.id}/import", headers=forker_headers)
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["name"] == f"{project.name} (forked)"
    assert "new_project_id" in body

    # The forked copy must have its own independent file, copied to a new
    # storage prefix -- the import endpoint mints a fresh random UUID for
    # this, unrelated to new_project_id, so search broadly rather than
    # assuming a path shape.
    all_keys_after_fork = storage.list_keys("projects")
    adder_copies = [k for k in all_keys_after_fork if k.endswith("adder.v")]
    assert len(adder_copies) == 2, adder_copies  # original + forked copy
    assert any(k != f"{project.storage_path}/adder.v" for k in adder_copies)

    view_after_fork = client.get(f"/workshop/{project.id}", headers=forker_headers)
    assert view_after_fork.json()["forks"] == 1

    storage_module.get_storage.cache_clear()


def test_import_nonexistent_project_returns_404(client, auth_headers):
    resp = client.post("/workshop/999999/import", headers=auth_headers)
    assert resp.status_code == 404


def test_import_private_project_returns_404(client, auth_headers, make_project):
    user_id = _get_user_id(client, auth_headers)
    project = make_project(user_id, is_public=False)

    resp = client.post(f"/workshop/{project.id}/import", headers=auth_headers)
    assert resp.status_code == 404
