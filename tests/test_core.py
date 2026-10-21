"""The core library: sample data, editing, undo, jobs, export and the safety rules."""

import json

import pytest

from cutzo.core import CutzoError, NeedsConfirmation, NotAllowed, NotFound

SPOOKY = "the-new-heretics-spooky-sode"
DIG = "the-dig-season-debrief-part-2"
EP1 = "the-new-heretics-episode-1"


def test_samples_are_set_up(cutzo):
    eps = {e["id"]: e for e in cutzo.list_episodes()}
    assert set(eps) == {SPOOKY, DIG, EP1}
    assert eps[SPOOKY]["stage"] == "review" and eps[SPOOKY]["cut_count"] > 0
    assert eps[EP1]["duration"] is None
    assert (cutzo.library / "episodes" / SPOOKY / "preview.wav").exists()


def test_find_episodes_loosely(cutzo):
    assert cutzo.resolve_episode("spooky") == SPOOKY
    assert cutzo.resolve_episode("Debrief Part 2") == DIG
    with pytest.raises(CutzoError):
        cutzo.resolve_episode("the new heretics")  # two matches
    with pytest.raises(NotFound):
        cutzo.resolve_episode("no such show")


def test_cut_section_and_undo(cutzo):
    before = cutzo.get_episode(SPOOKY)["final_duration"]
    res = cutzo.cut_section(SPOOKY, "0:31", "0:33", source="claude")
    assert "Cut 0:31.0" in res["summary"]
    after = cutzo.get_episode(SPOOKY)["final_duration"]
    assert after == pytest.approx(before - 2, abs=0.01)
    cutzo.undo(SPOOKY)
    assert cutzo.get_episode(SPOOKY)["final_duration"] == pytest.approx(before)
    with pytest.raises(CutzoError):
        cutzo.undo(SPOOKY)  # nothing left that a person did


def test_cut_section_rules(cutzo):
    with pytest.raises(CutzoError):
        cutzo.cut_section(SPOOKY, "0:20", "0:10")
    with pytest.raises(CutzoError):
        cutzo.cut_section(SPOOKY, "9:00", "9:10")
    with pytest.raises(NeedsConfirmation):
        cutzo.cut_section(SPOOKY, "0:00", "0:30")  # most of the episode
    assert cutzo.cut_section(SPOOKY, "0:00", "0:30", confirm=True)["edit"]["length"] == 30


def test_cut_words(cutzo):
    with pytest.raises(CutzoError) as many:
        cutzo.cut_words(DIG, "impossible")
    assert "3 times" in many.value.message
    one = cutzo.cut_words(DIG, "impossible", occurrence=2)
    assert len(one["edits"]) == 1
    rest = cutzo.cut_words(DIG, "impossible", occurrence="all")
    assert len(rest["edits"]) == 2
    with pytest.raises(CutzoError):
        cutzo.cut_words(DIG, "impossible")  # all cut now
    with pytest.raises(NotFound):
        cutzo.cut_words(DIG, "words nobody said")
    text = cutzo.get_transcript(DIG)["text"]
    assert "impossible" not in text.lower().replace("impossible trades", "")
    assert "[cut: impossible" in cutzo.get_transcript(DIG, include_cut=True)["text"].lower()


def test_restore_cut(cutzo):
    edit = cutzo.cut_words(SPOOKY, "They never do")["edits"][0]
    res = cutzo.restore_cut(SPOOKY, edit["id"])
    assert "Brought back" in res["summary"]
    assert edit["id"] not in [e["id"] for e in cutzo.get_episode(SPOOKY)["edits"]]
    cutzo.undo(SPOOKY)  # undoing the restore puts the cut back
    assert edit["id"] in [e["id"] for e in cutzo.get_episode(SPOOKY)["edits"]]


def test_chapters(cutzo):
    res = cutzo.add_chapter(SPOOKY, "0:30", "Define Your Terms")
    assert res["voiced_title"] == "Define Your Terms.mp3"
    renamed = cutzo.add_chapter(SPOOKY, "0:30.6", "Why Should We Care")
    assert renamed["summary"].startswith("Renamed")
    view = cutzo.get_episode(SPOOKY)
    assert [c["title"] for c in view["chapters"]].count("Why Should We Care") == 1
    assert view["youtube_chapters"].splitlines()[0].startswith("0:00 ")
    cutzo.undo(SPOOKY)
    assert "Define Your Terms" in [c["title"] for c in cutzo.get_episode(SPOOKY)["chapters"]]


def test_swap_asset(cutzo):
    res = cutzo.swap_asset(SPOOKY, "background", "full flames")
    assert res["slot"]["asset"]["name"] == "Full Flames (Final).mp4"
    assert res["slot"]["overridden"] is True
    with pytest.raises(CutzoError):
        cutzo.swap_asset(SPOOKY, "background", "Jok Banner")
    with pytest.raises(CutzoError):
        cutzo.swap_asset(SPOOKY, "laser show", "none")
    cutzo.undo(SPOOKY)
    slot = next(s for s in cutzo.get_episode(SPOOKY)["slots"] if s["slot"] == "background")
    assert slot["overridden"] is False


def test_camera_needs_video(cutzo):
    with pytest.raises(CutzoError):
        cutzo.set_camera(SPOOKY, "0:01", "0:05", "Everyone")


def test_build_then_camera(cutzo):
    with pytest.raises(CutzoError):
        cutzo.cut_section(EP1, 1, 2)  # not built yet
    job = cutzo.build_episode(EP1, source="claude")["job"]
    done = cutzo.jobs.wait(job["id"])
    assert done["status"] == "done", done
    view = cutzo.get_episode(EP1)
    assert view["cameras"] == ["Nick Baren", "Guest One", "Guest Two", "Everyone"]
    assert any(e["type"] == "camera" for e in view["edits"])
    assert view["media"][0]["sync"]["offset"] == pytest.approx(0.571, abs=0.02)
    res = cutzo.set_camera(EP1, "0:01", "0:04", "guest two")
    assert "Guest Two" in res["summary"]


def test_export_and_confirm(cutzo):
    res = cutzo.export(SPOOKY, ["youtube", "mp3"], source="claude")
    job = cutzo.jobs.wait(res["job"]["id"])
    assert job["status"] == "done", job
    view = cutzo.get_episode(SPOOKY)
    assert {x["target"] for x in view["exports"]} == {"youtube-1080p", "podcast-mp3"}  # "youtube" is 1080p now
    assert all(not x["written"] for x in view["exports"])
    for x in view["exports"]:
        assert cutzo.guard.is_writable(x["file"])
    with pytest.raises(NeedsConfirmation):
        cutzo.export(SPOOKY, ["youtube-1080p"])
    again = cutzo.export(SPOOKY, ["youtube-1080p"], confirm=True)
    assert cutzo.jobs.wait(again["job"]["id"])["status"] == "done"
    assert cutzo.check_export(episode=SPOOKY, target="youtube")["passed"] is True
    with pytest.raises(CutzoError):
        cutzo.export(SPOOKY, ["betamax"])


def test_safety_rules(cutzo, tmp_path):
    with pytest.raises(NotAllowed):
        cutzo.guard.writable(tmp_path / "outside.txt")
    with pytest.raises(NotAllowed):
        cutzo.guard.writable(cutzo.library / "app" / "cutzo" / "engine.py")
    assert cutzo.guard.is_writable(cutzo.library / "exports" / "The Dig" / "x.mp3")
    assert cutzo.guard.is_original(tmp_path / "raw.wav")


def test_import_reads_but_never_changes_originals(cutzo, tmp_path):
    raw = tmp_path / "Recordings" / "TNH 12"
    raw.mkdir(parents=True)
    track = raw / "TNH_12-Nick_Baren-webcam-00h_00m_01s_250ms-StreamYard.wav"
    track.write_bytes(b"RIFF....fake")
    (raw / "notes.txt").write_text("not media")
    before = track.read_bytes(), track.stat().st_mtime
    res = cutzo.create_episode(folder=str(raw), source="claude")
    assert res["episode"]["show"] == "the-new-heretics"  # guessed from "TNH" in the path
    job = cutzo.jobs.wait(res["import"]["job"]["id"])
    assert job["status"] == "done"
    view = cutzo.get_episode(res["episode"]["id"])
    assert view["media"][0]["person"] == "Nick Baren"
    assert view["media"][0]["offset"] == pytest.approx(1.25)
    assert (track.read_bytes(), track.stat().st_mtime) == before
    with pytest.raises(NotFound):
        cutzo.import_media(res["episode"]["id"], [str(raw / "missing.wav")])


def test_new_episode_builds_with_generic_sample(cutzo):
    ep = cutzo.create_episode(show="dig", title="Trade Deadline Special")["episode"]
    with pytest.raises(CutzoError):
        cutzo.build_episode(ep["id"])  # no recordings yet
    assert ep["show"] == "the-dig"


def test_activity_records_who(cutzo):
    cutzo.list_episodes(source="claude")
    cutzo.cut_section(SPOOKY, 1, 2, source="claude")
    log = cutzo.activity_log(limit=5)["entries"]
    assert log[0]["who"] == "Claude" and log[0]["action"] == "cut_section"
    assert any(e["action"] == "read" for e in cutzo.activity_log(limit=10)["entries"])
    assert all(e["action"] != "read" for e in cutzo.activity_log(limit=10, include_reads=False)["entries"])
    cutzo.list_episodes(source="you")  # the app's own refreshes are not logged
    assert cutzo.activity_log(limit=1)["entries"][0]["action"] == "cut_section"
    lines = (cutzo.library / "activity.jsonl").read_text(encoding="utf-8").splitlines()
    assert json.loads(lines[-1])["source"] == "claude"


def test_reset_samples_keeps_own_episodes(cutzo):
    mine = cutzo.create_episode(show="The Dig", title="My Episode")["episode"]["id"]
    cutzo.cut_section(SPOOKY, 1, 2)
    cutzo.reset_samples()
    ids = {e["id"] for e in cutzo.list_episodes()}
    assert mine in ids and SPOOKY in ids
    assert all(e["by"] == "auto" for e in cutzo.get_episode(SPOOKY)["edits"])
    assert any((cutzo.library / "trash").iterdir())


def test_remove_episode_goes_to_trash(cutzo):
    res = cutzo.remove_episode(DIG)
    assert DIG not in {e["id"] for e in cutzo.list_episodes()}
    assert (cutzo.library / "trash").exists() and res["moved_to"]


def test_self_test(cutzo):
    res = cutzo.self_test()
    names = {c["check"]: c for c in res["checks"]}
    assert names["Library folder"]["ok"] is True
    assert names["Safety rule"]["ok"] is True


def test_one_long_job_per_episode(library):
    from cutzo.core import Cutzo

    slow = Cutzo(library, speed=0.05)
    try:
        job = slow.build_episode(EP1)["job"]
        with pytest.raises(CutzoError) as busy:
            slow.import_media(EP1, [str(library)])
        assert "still building" in busy.value.message
        slow.jobs.wait(job["id"])
    finally:
        slow.jobs.shutdown()


def test_same_title_exports_get_their_own_file(cutzo):
    twin = cutzo.create_episode(show="heretics", title="Spooky Sode (JW Ghost Stories)")["episode"]["id"]
    first = cutzo.export(SPOOKY, ["mp3"])
    cutzo.jobs.wait(first["job"]["id"])
    # The twin isn't built, so give it the (pretend) sample build first.
    ep = cutzo._load(twin)
    ep["media"] = cutzo._load(SPOOKY)["media"]
    ep["sample"] = True
    cutzo._save(ep)
    cutzo.jobs.wait(cutzo.build_episode(twin)["job"]["id"])
    second = cutzo.export(twin, ["mp3"])
    assert second["targets"][0]["file"] != first["targets"][0]["file"]
    assert twin in second["targets"][0]["file"]


def test_long_cut_confirmation_has_plain_message(cutzo):
    with pytest.raises(NeedsConfirmation) as big:
        cutzo.cut_section(SPOOKY, "0:00", "0:30")
    assert "confirm" not in big.value.message and "confirm=true" in big.value.hint


def test_teaser_brief_and_saving(cutzo):
    from cutzo.core import teaser

    brief = cutzo.teaser_brief(DIG, source="claude")
    assert brief["part"] == 1 and brief["parts"] == 1 and brief["sample"]
    assert brief["past_teasers"] and all(x["title"] and x["text"] for x in brief["past_teasers"])
    assert "Miroslav" in " ".join(brief["style"]) and "| ft." in brief["title_format"]
    assert brief["transcript"].startswith("[0:") and "next" not in brief
    with pytest.raises(CutzoError):
        cutzo.teaser_brief(DIG, part=2)
    before = brief["transcript"].lower().count("impossible")
    cutzo.cut_words(DIG, "impossible", occurrence=1)
    assert cutzo.teaser_brief(DIG)["transcript"].lower().count("impossible") == before - 1  # cut words left out
    with pytest.raises(CutzoError):
        cutzo.teaser_brief(EP1)  # not built: no transcript
    with pytest.raises(CutzoError):
        cutzo.set_teaser(DIG, "")  # nothing to remove yet

    res = cutzo.set_teaser(DIG, "Miroslav, Jeremy, and Nick talk trades.\r\n\r\n\r\nGo Nuggets!  ",
                           title="Impossible Trades | ft. Miroslav Ćuk and Jeremy Poley", source="claude")
    assert res["summary"] == "Wrote the episode teaser (8 words)"
    saved = cutzo.get_episode(DIG)["teaser"]
    assert saved["text"] == "Miroslav, Jeremy, and Nick talk trades.\n\nGo Nuggets!" and saved["by"] == "claude"
    assert "file" not in saved  # sample episode: no file
    cutzo.set_teaser(DIG, "Shorter.", source="you")  # title kept
    assert cutzo.get_episode(DIG)["teaser"]["title"].startswith("Impossible Trades")
    assert cutzo.teaser_brief(DIG)["current_teaser"]["text"] == "Shorter."
    cutzo.undo(DIG)
    assert cutzo.get_episode(DIG)["teaser"]["text"].startswith("Miroslav")
    with pytest.raises(CutzoError):
        cutzo.set_teaser(DIG, "x" * (teaser.MAX_TEASER_CHARS + 1))
    assert cutzo.set_teaser(DIG, "")["summary"] == "Removed the episode teaser"
    assert cutzo.get_episode(DIG)["teaser"] is None
    log = cutzo.activity_log(limit=20, include_reads=True)
    assert any(e["action"] == "set_teaser" for e in log["entries"])


def test_teaser_parts_split_evenly():
    from cutzo.core import teaser

    rows = [(f"line {k}", 1000) for k in range(13)]
    parts = teaser.split(rows, 6000)
    assert len(parts) == 3 and sum(len(p) for p in parts) == 13
    assert max(len(p) for p in parts) - min(len(p) for p in parts) <= 1
    assert teaser.split([("only", 10)], 6000) == [["only"]]
    tr = {"speakers": [{"id": "S1", "name": "Jeremy"}],
          "words": [{"w": w, "s": k, "e": k + 0.5, "sp": "S1"} for k, w in enumerate(["Um,", "the", "uh", "Nuggets."])]}
    assert teaser.lines(tr, []) == [("[0:00] Jeremy: the Nuggets.", 2)]


def test_dig_cold_open_notes_when_it_runs_past_the_band(cutzo):
    assert "note" not in cutzo.add_pick(DIG, "cold open", 1, 18)
    long = cutzo.add_pick(DIG, "cold open", 20, 25)
    assert "18.6 s" in long["note"]
