def _auth(client, username="user1", password="pw123456"):
    client.post("/api/auth/register", json={"username": username, "password": password})
    r = client.post(
        "/api/auth/login",
        data={"username": username, "password": password},
    )
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


def _note(client, h, **overrides):
    payload = {"title": "Shared thoughts", "content": "secret **markdown**", **overrides}
    return client.post("/api/notes", headers=h, json=payload).json()


def test_share_idempotent_revoke_and_rotation(client):
    h = _auth(client)
    nid = _note(client, h)["id"]

    token1 = client.post(f"/api/notes/{nid}/share", headers=h).json()["share_token"]
    assert token1
    token_again = client.post(f"/api/notes/{nid}/share", headers=h).json()["share_token"]
    assert token_again == token1  # repeated POST keeps the token

    assert (
        client.request("DELETE", f"/api/notes/{nid}/share", headers=h).json()["share_token"] is None
    )

    token2 = client.post(f"/api/notes/{nid}/share", headers=h).json()["share_token"]
    assert token2 != token1  # re-share rotates; the leaked old link stays dead
    assert client.get(f"/api/public/notes/{token1}").status_code == 404


def test_public_view_minimal_payload_and_live_content(client):
    h = _auth(client)
    nid = _note(client, h, note_date="2026-06-11", tags=["work"])["id"]
    token = client.post(f"/api/notes/{nid}/share", headers=h).json()["share_token"]

    r = client.get(f"/api/public/notes/{token}")
    assert r.status_code == 200
    assert r.headers["cache-control"] == "no-store"
    body = r.json()
    assert set(body) == {"title", "content", "tags", "note_date", "updated_at"}
    assert "user_id" not in r.text and "notification_status" not in r.text

    # live content: edits show up without re-sharing
    client.put(
        f"/api/notes/{nid}",
        headers=h,
        json={"title": "Edited", "content": "new body", "tags": []},
    )
    assert client.get(f"/api/public/notes/{token}").json()["title"] == "Edited"


def test_public_view_404s(client):
    h = _auth(client)
    nid = _note(client, h)["id"]
    token = client.post(f"/api/notes/{nid}/share", headers=h).json()["share_token"]

    assert client.get("/api/public/notes/not-a-token").status_code == 404

    client.post(f"/api/notes/{nid}/archive", headers=h)
    assert client.get(f"/api/public/notes/{token}").status_code == 404  # archived

    client.post(f"/api/notes/{nid}/unarchive", headers=h)
    client.request("DELETE", f"/api/notes/{nid}/share", headers=h)
    assert client.get(f"/api/public/notes/{token}").status_code == 404  # revoked


def test_share_requires_ownership(client):
    h_owner = _auth(client, "alice")
    h_other = _auth(client, "eve")
    nid = _note(client, h_owner)["id"]

    assert client.post(f"/api/notes/{nid}/share", headers=h_other).status_code == 404
    assert client.request("DELETE", f"/api/notes/{nid}/share", headers=h_other).status_code == 404
