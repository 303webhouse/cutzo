"""The local web engine: routes, error messages and the this-computer-only rules."""

APP = {"X-Cutzo-Source": "app"}
CLAUDE = {"X-Cutzo-Source": "claude"}


def test_health_and_screens(client):
    assert client.get("/api/health").json()["app"] == "cutzo"
    page = client.get("/")
    assert page.status_code == 200 and "Cutzo" in page.text
    js = client.get("/static/js/app.js")
    assert js.status_code == 200 and "javascript" in js.headers["content-type"]
    assert client.get("/static/fonts/big-shoulders-display-latin-800-normal.woff2").status_code == 200


def test_only_this_computer(client, cutzo):
    from fastapi.testclient import TestClient

    from cutzo.engine import create_app

    assert client.get("/api/health", headers={"Host": "evil.example.com"}).status_code == 403
    with TestClient(create_app(cutzo)) as plain:  # "testserver" is only allowed in tests
        assert plain.get("/api/health").status_code == 403
        assert plain.get("/api/health", headers={"Host": "127.0.0.1:8765"}).status_code == 200
    page = client.get("/")
    assert page.headers["x-frame-options"] == "DENY"
    assert client.post("/api/episodes/spooky/undo").status_code == 403  # no X-Cutzo-Source
    cross = client.post("/api/episodes/spooky/undo", headers={**APP, "Origin": "https://evil.example.com"})
    assert cross.status_code == 403


def test_edit_round_trip(client):
    eps = client.get("/api/episodes").json()
    assert len(eps) == 3
    r = client.post("/api/episodes/spooky/cuts", json={"start": "0:31", "end": "0:32.5"}, headers=CLAUDE)
    assert r.status_code == 200, r.text
    words = client.get("/api/episodes/spooky/transcript?format=words").json()
    assert any(w["cut"] for p in words["paragraphs"] for w in p["words"])
    wave = client.get("/api/episodes/spooky/waveform?buckets=300").json()
    assert len(wave["peaks"]) == 300
    audio = client.get("/api/episodes/spooky/audio")
    assert audio.status_code == 200 and audio.content[:4] == b"RIFF"
    log = client.get("/api/activity?reads=false&limit=1").json()["entries"][0]
    assert log["who"] == "Claude"


def test_errors_are_plain_words(client):
    missing = client.get("/api/episodes/nothing-like-this")
    assert missing.status_code == 404 and "No episode matches" in missing.json()["error"]
    bad = client.post("/api/episodes/spooky/cuts", json={"start": "0:20", "end": "0:10"}, headers=APP)
    assert bad.status_code == 400 and "must be after" in bad.json()["error"]
    shape = client.post("/api/episodes/spooky/cuts", json={"start": "0:20"}, headers=APP)
    assert shape.status_code == 422 and "end" in shape.json()["error"]
    big = client.post("/api/episodes/spooky/cuts", json={"start": 0, "end": 40}, headers=APP)
    assert big.status_code == 409
    failed = client.get("/api/activity?reads=false&limit=1").json()["entries"][0]
    assert failed["ok"] is False and failed["summary"].startswith("Paused")


def test_jobs_and_state(client, cutzo):
    r = client.post("/api/episodes/episode%201/build", json={}, headers=APP)
    assert r.status_code == 200, r.text
    job = cutzo.jobs.wait(r.json()["job"]["id"])
    assert job["status"] == "done"
    status = client.get(f"/api/jobs/{job['id']}").json()
    assert status["summary"].startswith("Built")
    state = client.get("/api/state").json()
    assert state["activity_last_id"] >= 3 and state["active_jobs"] == []


def test_scan_folder(client, tmp_path):
    folder = tmp_path / "Dig 14"
    folder.mkdir()
    (folder / "Season Preview.mp3").write_bytes(b"ID3")
    res = client.get("/api/scan", params={"folder": str(folder)}).json()
    assert res["show_guess"] == "the-dig"
    assert res["files"][0]["source"] == "mix"


def test_engine_claims_its_port(free_port):
    import socket

    import pytest

    from cutzo.engine import claim_port

    first = claim_port("127.0.0.1", free_port)
    try:
        with pytest.raises(OSError):
            claim_port("127.0.0.1", free_port)
    finally:
        first.close()
    with socket.socket() as s:
        s.bind(("127.0.0.1", free_port))  # free again once released


def test_broken_settings_file_is_reported(tmp_path, monkeypatch):
    import pytest

    from cutzo import config

    bad = tmp_path / "settings.json"
    bad.write_text('{"library_folder": "D:\\Podcasts\\"', encoding="utf-8")
    monkeypatch.setattr(config, "SETTINGS_FILE", bad)
    monkeypatch.delenv("CUTZO_HOME", raising=False)
    monkeypatch.delenv("CUTZO_PORT", raising=False)
    with pytest.raises(config.SettingsError) as err:
        config.load_settings()
    assert "double backslashes" in str(err.value)
    bad.write_text('{"library_folder": "D:\\\\Podcasts", "port": 80}', encoding="utf-8")
    with pytest.raises(config.SettingsError):
        config.load_settings()
