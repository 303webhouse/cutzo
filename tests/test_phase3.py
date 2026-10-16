"""Phase 3: speech to text (Parakeet, run by whisper.cpp's parakeet-cli), cutting by words on real episodes."""

import hashlib
import io
import json
import os
import subprocess
import tarfile
import wave
from pathlib import Path

import numpy as np
import pytest

from cutzo import tools
from cutzo.core import audio, cutpoints
from cutzo.core.errors import CutzoError
from cutzo.tools import install as tool_install
from cutzo.tools import parakeet

RATE = audio.RATE
FFMPEG = tools.find("ffmpeg")
needs_ffmpeg = pytest.mark.skipif(not FFMPEG, reason="FFmpeg isn't installed here")
TEST_MODEL = Path(os.environ.get("CUTZO_TOOLS") or "/nonexistent") / "for-tests-ggml-parakeet-tdt.bin"

# What parakeet-cli -ps reports on its error stream, from a real run on Nick's PC: six seconds of
# Season Debrief Part 2 (Oct 15, 2026), then a second piece made up of other real token lines.
SAMPLE_REPORT = """ggml_cuda_init: found 1 CUDA devices (Total VRAM: 8191 MiB):
Loading Parakeet model from: F:\\Cutzo\\app\\tools\\parakeet-model-tdt-0.6b-v3\\ggml-parakeet-tdt-0.6b-v3-f16.bin

Processing file: C:\\work\\piece-0000.wav
read_audio_data: reading audio data from 'C:\\work\\piece-0000.wav' ...
parakeet_print_timings:    total time =   498.21 ms

Segments (1):
Segment 0: [0 -> 601] "And uh I also love Paris and London. But"
Tokens [12]:
  [ 0] id= 1976 frame=  7 dur_idx= 4 dur_val= 4 p=0.6718 plog=-0.3979 t0=  56 t1=  88 word_start=true "\u2581And"
  [ 1] id= 1282 frame= 15 dur_idx= 3 dur_val= 3 p=0.8776 plog=-0.1305 t0= 120 t1= 144 word_start=true "\u2581uh"
  [ 2] id=  380 frame= 20 dur_idx= 4 dur_val= 4 p=0.9993 plog=-0.0007 t0= 160 t1= 192 word_start=true "\u2581I"
  [ 3] id= 2556 frame= 24 dur_idx= 3 dur_val= 3 p=1.0000 plog=-0.0000 t0= 192 t1= 216 word_start=true "\u2581also"
  [ 4] id= 6714 frame= 27 dur_idx= 4 dur_val= 4 p=0.9963 plog=-0.0037 t0= 216 t1= 248 word_start=true "\u2581love"
  [ 5] id= 1694 frame= 31 dur_idx= 4 dur_val= 4 p=0.9983 plog=-0.0017 t0= 248 t1= 280 word_start=true "\u2581Par"
  [ 6] id=  290 frame= 35 dur_idx= 3 dur_val= 3 p=1.0000 plog=-0.0000 t0= 280 t1= 304 word_start=false "is"
  [ 7] id=  575 frame= 61 dur_idx= 3 dur_val= 3 p=0.9881 plog=-0.0119 t0= 488 t1= 512 word_start=true "\u2581and"
  [ 8] id= 5994 frame= 64 dur_idx= 3 dur_val= 3 p=0.9984 plog=-0.0016 t0= 512 t1= 536 word_start=true "\u2581Lond"
  [ 9] id=  289 frame= 67 dur_idx= 2 dur_val= 2 p=1.0000 plog=-0.0000 t0= 536 t1= 552 word_start=false "on"
  [10] id= 7883 frame= 69 dur_idx= 1 dur_val= 1 p=0.4427 plog=-0.8149 t0= 552 t1= 552 word_start=false "."
  [11] id= 3592 frame= 70 dur_idx= 3 dur_val= 3 p=0.7319 plog=-0.3120 t0= 560 t1= 584 word_start=true "\u2581But"

Processing file: C:\\work\\piece-0001.wav
Segments (1):
Segment 0: [0 -> 300] "it's"
Tokens [4]:
  [ 0] id=  869 frame= 13 dur_idx= 1 dur_val= 1 p=0.9945 plog=-0.0055 t0= 104 t1= 112 word_start=true "\u2581it"
  [ 1] id= 7931 frame= 14 dur_idx= 1 dur_val= 1 p=0.9999 plog=-0.0001 t0= 112 t1= 112 word_start=false "'"
  [ 2] id= 7870 frame= 15 dur_idx= 0 dur_val= 0 p=1.0000 plog=-0.0000 t0= 120 t1= 120 word_start=false "s"
  [ 3] id=    0 frame= 16 dur_idx= 0 dur_val= 0 p=0.5809 plog=-0.5432 t0= 128 t1= 128 word_start=false "<unk>"
"""


def test_words_from_a_parakeet_report():
    first, second = parakeet.parse(SAMPLE_REPORT.splitlines(), 2)
    assert len(first) == 12 and len(second) == 4
    words = parakeet.words_from_tokens(first)
    assert [w["w"] for w in words] == ["And", "uh", "I", "also", "love", "Paris", "and", "London.", "But"]
    uh, paris, london = words[1], words[5], words[7]
    assert (uh["s"], uh["e"], uh["p"]) == (1.2, 1.44, 0.878)
    assert (paris["s"], paris["e"]) == (2.48, 3.04)  # "Par" + "is"
    assert london["e"] == 5.52 and london["p"] == 0.998  # the full stop's doubt isn't the word's
    its = parakeet.words_from_tokens(second, offset=34.0)
    assert [w["w"] for w in its] == ["it's"] and (its[0]["s"], its[0]["e"]) == (35.04, 35.2)  # <unk> is skipped
    with pytest.raises(CutzoError):
        parakeet.parse(SAMPLE_REPORT.splitlines()[:20], 2)  # the second piece never came back


def test_long_recordings_go_in_overlapping_pieces_that_meet_in_pauses():
    duration = 125.0
    levels = np.full(int(duration / 0.01), -20.0)
    for t in (30.0, 61.0, 90.5):
        levels[int(t / 0.01):int((t + 0.4) / 0.01)] = -70.0
    pieces = parakeet.plan(levels, duration)
    assert pieces[0].start == 0 and pieces[0].own_from == 0
    assert pieces[-1].own_to == duration and pieces[-1].end == duration
    for p in pieces:
        assert p.end - p.start <= parakeet.PIECE + 1e-6
        assert p.start <= p.own_from < p.own_to <= p.end
        assert p.own_from - p.start in (0.0, parakeet.MARGIN) or p.start == 0
    for a, b in zip(pieces, pieces[1:]):
        assert a.own_to == b.own_from
    marks = [p.own_to for p in pieces[:-1]]
    for mark, pause in zip(marks, (30.0, 61.0, 90.5)):
        assert pause <= mark <= pause + 0.4, (mark, pause)
    assert parakeet.plan(levels[:3000], 30.0) == [parakeet.Piece(0.0, 30.0, 0.0, 30.0)]


def test_pieces_join_without_repeating_a_word():
    a = [{"w": "so", "s": 1.0, "e": 1.2}, {"w": "the", "s": 33.9, "e": 34.0}]
    b = [{"w": "The", "s": 34.05, "e": 34.2}, {"w": "Nuggets", "s": 34.3, "e": 34.8}]
    assert [w["w"] for w in parakeet._join([a, b])] == ["so", "the", "Nuggets"]
    c = [{"w": "the", "s": 34.5, "e": 34.6}]  # said twice ("the... the"), half a second apart
    assert [w["w"] for w in parakeet._join([a, c])] == ["so", "the", "the"]


def test_untar_keeps_the_inner_folder_and_refuses_escapes(tmp_path):
    def build(entries):
        buf = io.BytesIO()
        with tarfile.open(fileobj=buf, mode="w:gz") as tf:
            for name, data, kind in entries:
                info = tarfile.TarInfo(name)
                if kind == "link":
                    info.type, info.linkname = tarfile.SYMTYPE, data
                    tf.addfile(info)
                else:
                    info.size, info.mode = len(data), 0o755 if kind == "exe" else 0o644
                    tf.addfile(info, io.BytesIO(data))
        return buf.getvalue()

    good = tmp_path / "good.tar.gz"
    good.write_bytes(build([("pkg/whisper-cli", b"#!", "exe"), ("pkg/libw.so.1.9", b"lib", "file"),
                            ("pkg/libw.so.1", "libw.so.1.9", "link"), ("other/skip.txt", b"x", "file")]))
    dest = tmp_path / "out"
    dest.mkdir()
    tool_install._untar(good, "pkg", dest)
    assert (dest / "whisper-cli").read_bytes() == b"#!" and os.access(dest / "whisper-cli", os.X_OK)
    assert (dest / "libw.so.1").resolve() == (dest / "libw.so.1.9").resolve()
    assert not (dest / "other").exists()
    for bad_entries in ([("pkg/../../evil", b"no", "file")], [("pkg/escape", "../../../etc/passwd", "link")]):
        bad = tmp_path / "bad.tar.gz"
        bad.write_bytes(build(bad_entries))
        out = tmp_path / f"bad-{len(list(tmp_path.iterdir()))}"
        out.mkdir()
        with pytest.raises(RuntimeError):
            tool_install._untar(bad, "pkg", out)


def test_installing_a_model_file(monkeypatch, tmp_path):
    monkeypatch.setenv("CUTZO_TOOLS", str(tmp_path))
    payload = b"pretend speech model"
    digest = hashlib.sha256(payload).hexdigest()
    download = tools.Download(url="https://example.invalid/model.bin", sha256=digest, size=len(payload), kind="file")
    tool = tools.Tool(name="parakeet-model", label="Speech model", version="test", programs={},
                      files={"parakeet-model": "model.bin"}, downloads={"linux-x86_64": download})

    def fake_download(url, dest, size, report):
        dest.write_bytes(payload)
        return digest

    monkeypatch.setattr(tool_install, "_download", fake_download)
    monkeypatch.setattr(tool_install, "platform_key", lambda: "linux-x86_64")
    folder = tool_install.install(tool, report=lambda _: None)
    assert (folder / "model.bin").read_bytes() == payload
    assert json.loads((folder / tool_install.MARKER).read_text())["sha256"] == digest


def test_setup_removes_retired_tools_it_installed(monkeypatch, tmp_path):
    monkeypatch.setenv("CUTZO_TOOLS", str(tmp_path))
    old = tmp_path / "whisper-model-large-v3-turbo"
    old.mkdir()
    (old / "ggml-large-v3-turbo.bin").write_bytes(b"1.6 GB, once")
    (old / tool_install.MARKER).write_text(json.dumps({"tool": "whisper-model", "version": "large-v3-turbo"}))
    mine = tmp_path / "whisper-model-mine"  # not installed by Cutzo (no marker): left alone
    mine.mkdir()
    keep = tmp_path / "whisper-1.9.5"
    keep.mkdir()
    (keep / tool_install.MARKER).write_text(json.dumps({"tool": "whisper", "version": "1.9.5"}))
    assert tool_install.remove_retired() == ["whisper-model-large-v3-turbo"]
    assert not old.exists() and mine.exists() and keep.exists()


# ----------------------------------------------------------- cut points
def bursts_with_gaps(tmp_path):
    """Five 'words' (tones) with silent gaps between them; returns the file and the true times."""
    true, parts, t = [], [], 0.3
    parts.append(np.zeros(int(0.3 * RATE), dtype=np.float32))
    for length, gap in ((0.40, 0.30), (0.30, 0.25), (0.50, 0.35), (0.30, 0.30), (0.40, 0.4)):
        n = int(length * RATE)
        parts.append((0.3 * np.sin(2 * np.pi * 220 * np.arange(n) / RATE)).astype(np.float32))
        true.append((t, t + length))
        t += length
        parts.append(np.random.default_rng(1).normal(0, 0.0005, int(gap * RATE)).astype(np.float32))
        t += gap
    return audio.write_f32(tmp_path / "voice.f32", np.concatenate(parts)), true


def test_word_cuts_land_in_the_quiet(tmp_path):
    voice, true = bursts_with_gaps(tmp_path)
    # Whisper-ish times: up to a tenth of a second off in both directions.
    jitter = [(0.08, -0.05), (-0.07, 0.06), (0.05, 0.09), (-0.09, -0.04), (0.02, 0.0)]
    words = [{"w": f"w{k}", "s": a + da, "e": b + db} for k, ((a, b), (da, db)) in enumerate(zip(true, jitter))]
    duration = audio.Pcm(voice).duration
    a, b = cutpoints.word_cut(voice, words, 1, 3, duration)  # cut words 1 and 2
    assert true[0][1] < a < true[1][0], (a, true)  # in the gap before word 1
    assert true[2][1] < b < true[3][0], (b, true)  # in the gap after word 2
    assert cutpoints.words_in(words, true[1][0] - 0.02, true[2][1] + 0.04) == (1, 3)
    assert cutpoints.words_in(words, 100, 101) is None


def test_an_um_that_starts_a_run_of_talk_is_taken_out(tmp_path):
    """ "...pointless. [pause] uh state of the NBA": the "uh" runs straight into the next word, and
    speech to text put it a little early and too short (mostly in the pause before it). Cutting
    at the quietest moments near those times would take out only silence."""
    rng = np.random.default_rng(2)

    def tone(freq, seconds, level=0.3):
        n = int(seconds * RATE)
        return (level * np.sin(2 * np.pi * freq * np.arange(n) / RATE)).astype(np.float32)

    def hush(seconds):
        return rng.normal(0, 0.0005, int(seconds * RATE)).astype(np.float32)

    parts = [hush(0.3), tone(260, 0.3), hush(0.4), tone(180, 0.25), tone(180, 0.03, 0.017), tone(300, 0.4), hush(0.5)]
    voice = audio.write_f32(tmp_path / "voice.f32", np.concatenate(parts))
    uh_start, uh_end, state_start = 1.0, 1.25, 1.28  # where the sound really is
    words = [{"w": "pointless.", "s": 0.32, "e": 0.6}, {"w": "uh", "s": 0.88, "e": 1.06},
             {"w": "state", "s": 1.3, "e": 1.62}]
    a, b = cutpoints.word_cut(voice, words, 1, 2, audio.Pcm(voice).duration)
    assert 0.6 < a < uh_start, a  # in the pause before the "uh"
    assert uh_end - 0.01 <= b <= state_start + 0.01, b  # in the dip between "uh" and "state"


def test_a_cut_edge_in_a_pause_goes_to_the_middle_of_the_whole_pause():
    # 10 ms steps: a pause with a faint tick just over the quiet level (-43.2 vs -43.3), then a
    # breath (-33 dB) that really does split the pause. Real numbers from Season Debrief, 10:23.
    env = np.array([-55.0] * 15 + [-33.0] * 4 + [-58.0] * 18 + [-43.2] + [-62.0] * 8 + [-14.0] * 5)
    cost = env.copy()
    k = 40  # the quietest moment, just before the talk
    assert cutpoints._middle(cost, env, -43.3, k) == (19 + 45) // 2  # middle of 0.19-0.46 s, tick included


def test_words_are_judged_cut_by_their_sound_not_their_stretched_times():
    """Speech to text often stretches the last word before a pause into the pause, or puts a short
    word wholly inside it. Shortening that pause mustn't strike those words through."""
    from cutzo.core import build, transcript

    env = np.full(500, -70.0)  # 5 s outline, 10 ms steps: silence...
    env[100:160] = -20.0  # ...talk from 1.0 to 1.6 s ("a cheap guy.")
    env[300:350] = -22.0  # ...talk again from 3.0 to 3.5 s ("And actually")
    words = [{"w": "guy.", "s": 1.4, "e": 2.2},  # its end stretched 0.6 s into the pause
             {"w": "me", "s": 1.75, "e": 1.95},  # wholly in the pause, just after the talk
             {"w": "And", "s": 3.02, "e": 3.2},
             {"w": "um", "s": 4.5, "e": 4.7}]  # in silence with no talk near: left alone
    build.anchor_words(words, env, quiet=-42.0)
    guy, me, and_, um = words
    assert 1.4 <= guy["c"] < 1.6  # inside its sound
    assert me["c"] == 1.595  # the nearest sound: the end of the talk before it
    assert 3.02 <= and_["c"] <= 3.2
    assert "c" not in um and transcript.anchor(um) == 4.6
    dead_air = [(1.85, 2.75)]  # the pause after "guy." shortened, keeping a quarter second each side
    assert not transcript.is_word_cut(guy, dead_air) and not transcript.is_word_cut(me, dead_air)
    assert transcript.is_word_cut(guy, [(1.3, 1.7)])  # cutting the word itself still strikes it


# ------------------------------------------------- transcription in the build
@pytest.fixture()
def real_dig(tmp_path, monkeypatch):
    if not FFMPEG:
        pytest.skip("FFmpeg isn't installed here")
    from cutzo.core import Cutzo

    voice, true = bursts_with_gaps(tmp_path)
    rec = tmp_path / "Talk.mp3"
    subprocess.run([str(FFMPEG), "-hide_banner", "-loglevel", "error", "-y", "-f", "f32le", "-ar", str(RATE), "-ac", "1",
                    "-i", str(voice), "-b:a", "192k", str(rec)], check=True)
    app = Cutzo(tmp_path / "library", speed=0.0, drive=tmp_path / "drive")
    ep_id = app.create_episode(show="The Dig", title="Words")["episode"]["id"]
    app.jobs.wait(app.import_media(ep_id, [str(rec)])["job"]["id"], 60)
    app.set_voice(ep_id, 0)
    yield app, ep_id, true
    app.jobs.shutdown()


def fake_speech(true):
    def transcribe(wav16k, **kwargs):
        assert wav16k.exists()  # the 16 kHz copy was made for it
        progress = kwargs.get("progress")
        if progress:
            progress(0.5)
        words = [{"w": w, "s": round(a + 0.06, 3), "e": round(b - 0.05, 3), "p": 0.9}
                 for w, (a, b) in zip(["Um,", "so", "the", "Nuggets", "won."], true)]
        return {"words": words, "text": " ".join(w["w"] for w in words)}
    return transcribe


def test_build_transcribes_and_words_cut_cleanly(real_dig, monkeypatch):
    app, ep_id, true = real_dig
    from cutzo.core import service

    monkeypatch.setattr(service.toolbox, "available", lambda *names: True)
    monkeypatch.setattr(parakeet, "transcribe", fake_speech(true))
    job = app.jobs.wait(app.build_episode(ep_id)["job"]["id"], 120)
    assert job["status"] == "done", job.get("error")
    ep = app.get_episode(ep_id)
    assert {s["key"]: s["status"] for s in ep["stages"]}["transcribe"] == "done" and ep["has_transcript"]
    text = app.get_transcript(ep_id)["text"]
    assert "Um, so the Nuggets won." in text
    part = app.get_transcript(ep_id, start=1.0, end=2.0)
    assert part["words"] == 2 and part["text"].endswith("] so the")  # words that start in that second; no speaker label

    cut = app.cut_words(ep_id, "the Nuggets")["edits"][0]
    assert true[1][1] < cut["start"] < true[2][0] and true[3][1] < cut["end"] < true[4][0], (cut, true)
    app.undo(ep_id)
    # Selecting words in Review sends their times: the cut moves to the quiet the same way.
    sel = app.cut_section(ep_id, true[0][0] + 0.06 - 0.02, true[0][1] - 0.05 + 0.04, reason="words")["edit"]
    assert sel["start"] < true[0][0] and true[0][1] < sel["end"] < true[1][0], (sel, true)
    assert sel["text"] == "Um,"


def test_episodes_built_before_speech_to_text_can_be_transcribed(real_dig, monkeypatch):
    app, ep_id, true = real_dig
    from cutzo.core import service

    monkeypatch.setattr(service.toolbox, "available", lambda *names: False)
    assert app.jobs.wait(app.build_episode(ep_id)["job"]["id"], 120)["status"] == "done"
    ep = app.get_episode(ep_id)
    assert {s["key"]: s["status"] for s in ep["stages"]}["transcribe"] == "later" and not ep["has_transcript"]
    assert any("Update Cutzo" in n for n in ep["build_notes"])
    with pytest.raises(CutzoError):
        app.cut_words(ep_id, "Nuggets")

    monkeypatch.setattr(service.toolbox, "require", lambda name: Path(name))
    monkeypatch.setattr(service.toolbox, "require_file", lambda name: Path(name))
    monkeypatch.setattr(parakeet, "transcribe", fake_speech(true))
    job = app.jobs.wait(app.transcribe(ep_id)["job"]["id"], 60)
    assert job["status"] == "done", job.get("error")
    ep = app.get_episode(ep_id)
    assert ep["has_transcript"] and not any("Update Cutzo" in n for n in ep["build_notes"])
    assert {s["key"]: s["status"] for s in ep["stages"]}["transcribe"] == "done"
    assert app.cut_words(ep_id, "Nuggets")["edits"]


@pytest.mark.skipif(not (tools.find("parakeet-cli") and TEST_MODEL.exists()),
                    reason="parakeet-cli and its test model aren't installed here")
def test_real_parakeet_cli_runs(tmp_path, monkeypatch):
    # whisper.cpp's tiny Parakeet test model has random weights: what it "hears" is nonsense, but the
    # program, its options, the report it writes and the pieces of a long recording are the real ones.
    monkeypatch.setenv("CUTZO_PARAKEET_MODEL", str(TEST_MODEL))
    seconds = 100
    t = np.arange(seconds * parakeet.RATE) / parakeet.RATE
    talk = 0.2 * np.sin(2 * np.pi * 220 * t) * (np.sin(2 * np.pi * 0.3 * t) > 0)
    wav = tmp_path / "talk.wav"
    with wave.open(str(wav), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(parakeet.RATE)
        w.writeframes((talk * 32767).astype("<i2").tobytes())
    runs, real_run = [], parakeet.run

    def counting_run(exe, model, files, **kw):
        runs.append(len(files))
        return real_run(exe, model, files, **kw)

    monkeypatch.setattr(parakeet, "run", counting_run)
    seen = []
    result = parakeet.transcribe(wav, progress=seen.append, work_dir=tmp_path)
    assert runs == [len(parakeet.plan(parakeet.loudness_outline(wav)[0], seconds))] and runs[0] >= 3
    assert seen[-1] == 1.0 and seen == sorted(seen)
    starts = [w["s"] for w in result["words"]]
    assert starts == sorted(starts) and all(0 <= x <= seconds for x in starts)
    assert not list(tmp_path.glob("parakeet-*"))  # the pieces were tidied away
