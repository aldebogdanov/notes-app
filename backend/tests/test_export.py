import io
import zipfile


def _auth(client, username="user1", password="pw123456"):
    client.post("/api/auth/register", json={"username": username, "password": password})
    r = client.post(
        "/api/auth/login",
        data={"username": username, "password": password},
    )
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


def _note(client, h, title="My Note", content="body text"):
    r = client.post("/api/notes", headers=h, json={"title": title, "content": content})
    return r.json()["id"]


def _zip_names(response):
    archive = zipfile.ZipFile(io.BytesIO(response.content))
    return archive, archive.namelist()


def test_export_single_markdown(client):
    h = _auth(client)
    nid = _note(client, h, title="Grocery List!", content="- milk")

    r = client.get(f"/api/notes/{nid}/export", headers=h)
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/markdown")
    assert r.headers["content-disposition"] == 'attachment; filename="grocery-list.md"'
    assert r.text == "# Grocery List!\n\n- milk"


def test_export_single_requires_ownership(client):
    h_owner = _auth(client, "alice")
    h_other = _auth(client, "eve")
    nid = _note(client, h_owner)

    assert client.get(f"/api/notes/{nid}/export", headers=h_other).status_code == 404


def test_export_all_includes_archived(client):
    h = _auth(client)
    active = _note(client, h, title="Active")
    archived = _note(client, h, title="Old stuff")
    client.post(f"/api/notes/{archived}/archive", headers=h)

    r = client.get("/api/notes/export", headers=h)
    assert r.status_code == 200
    assert r.headers["content-type"] == "application/zip"
    archive, names = _zip_names(r)
    assert names == [f"{active}-active.md", f"{archived}-old-stuff.md"]
    assert archive.read(names[0]).decode().startswith("# Active")


def test_bulk_export_selected_only_and_foreign_skipped(client):
    h = _auth(client, "alice")
    h_other = _auth(client, "eve")
    mine1 = _note(client, h, title="One")
    mine2 = _note(client, h, title="Two")
    _note(client, h, title="Not selected")
    foreign = _note(client, h_other, title="Eve note")

    r = client.post("/api/notes/bulk-export", headers=h, json={"ids": [mine1, mine2, foreign]})
    assert r.status_code == 200
    _, names = _zip_names(r)
    assert names == [f"{mine1}-one.md", f"{mine2}-two.md"]  # foreign id silently skipped

    # nothing exportable -> 404
    assert (
        client.post("/api/notes/bulk-export", headers=h, json={"ids": [foreign]}).status_code == 404
    )
