"""Phase 3: speech to text (whisper.cpp), cutting by words on real episodes."""

import hashlib
import io
import json
import os
import subprocess
import tarfile
from pathlib import Path

import numpy as np
import pytest

from cutzo import tools
from cutzo.core import audio, cutpoints
from cutzo.core.errors import CutzoError
from cutzo.tools import install as tool_install
from cutzo.tools import whisper

RATE = audio.RATE
FFMPEG = tools.find("ffmpeg")
needs_ffmpeg = pytest.mark.skipif(not FFMPEG, reason="FFmpeg isn't installed here")
TEST_MODEL = Path(os.environ.get("CUTZO_TOOLS") or "/nonexistent") / "for-tests-ggml-tiny.en.bin"

# A slice of whisper.cpp's -ojf output for Season Debrief Part 2 (Oct 15, 2026).
SAMPLE_JSON = {"transcription": [{"offsets": {"from": 0, "to": 23860}, "text": " Um, yeah, for me, it's, like,", "tokens": [
    {"text": "[_BEG_]", "offsets": {"from": 0, "to": 0}, "p": 0.88},
    {"text": " Um", "offsets": {"from": 0, "to": 100}, "p": 0.46},
    {"text": ",", "offsets": {"from": 100, "to": 200}, "p": 0.97},
    {"text": " yeah", "offsets": {"from": 230, "to": 410}, "p": 0.99},
    {"text": ",", "offsets": {"from": 410, "to": 510}, "p": 0.96},
    {"text": " it", "offsets": {"from": 910, "to": 970}, "p": 0.99},
    {"text": "'s", "offsets": {"from": 970, "to": 1070}, "p": 0.99},
    {"text": " like", "offsets": {"from": 1170, "to": 1310}, "p": 0.99},
    {"text": " –", "offsets": {"from": 1590, "to": 1610}, "p": 0.32},
    {"text": " New", "offsets": {"from": 1950, "to": 2110}, "p": 0.99},
    {"text": " York", "offsets": {"from": 2110, "to": 2320}, "p": 0.99},
    {"text": "-", "offsets": {"from": 2320, "to": 2370}, "p": 0.55},
    {"text": " ram", "offsets": {"from": 23460, "to": 23460}, "p": 0.31},
    {"text": "ble", "offsets": {"from": 23530, "to": 23640}, "p": 0.99},
    {"text": ".", "offsets": {"from": 23640, "to": 23860}, "p": 0.97},
    {"text": "[_TT_1193]", "offsets": {"from": 23860, "to": 23860}, "p": 0.07},
]}]}


def test_words_from_whisper_json():
    words = whisper.words_from_json(SAMPLE_JSON)
    assert [w["w"] for w in words] == ["Um,", "yeah,", "it's", "like –", "New", "York-", "ramble."]
    um, _, its, like, _, york, ramble = words
    assert um["s"] == 0.0 and um["e"] == 0.2 and um["p"] == 0.46  # the comma's time belongs to the word
    assert its["s"] == 0.91 and its["e"] == 1.07
    assert like["e"] == 1.61 and like["p"] == 0.32  # the dash rides along, and its doubt with it
    assert york["e"] == 2.37
    assert ramble["s"] == 23.46 and ramble["e"] == 23.86 and ramble["p"] == 0.31
    assert whisper.words_from_json({"transcription": []}) == []


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
    tool = tools.Tool(name="whisper-model", label="Speech model", version="test", programs={},
                      files={"whisper-model": "model.bin"}, downloads={"linux-x86_64": download})

    def fake_download(url, dest, size, report):
        dest.write_bytes(payload)
        return digest

    monkeypatch.setattr(tool_install, "_download", fake_download)
    monkeypatch.setattr(tool_install, "platform_key", lambda: "linux-x86_64")
    folder = tool_install.install(tool, report=lambda _: None)
    assert (folder / "model.bin").read_bytes() == payload
    assert json.loads((folder / tool_install.MARKER).read_text())["sha256"] == digest


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


def fake_whisper(true):
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
    monkeypatch.setattr(whisper, "transcribe", fake_whisper(true))
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
    monkeypatch.setattr(whisper, "transcribe", fake_whisper(true))
    job = app.jobs.wait(app.transcribe(ep_id)["job"]["id"], 60)
    assert job["status"] == "done", job.get("error")
    ep = app.get_episode(ep_id)
    assert ep["has_transcript"] and not any("Update Cutzo" in n for n in ep["build_notes"])
    assert {s["key"]: s["status"] for s in ep["stages"]}["transcribe"] == "done"
    assert app.cut_words(ep_id, "Nuggets")["edits"]


@pytest.mark.skipif(not (tools.find("whisper-cli") and TEST_MODEL.exists()),
                    reason="whisper-cli and its test model aren't installed here")
def test_real_whisper_cli_runs(tmp_path, monkeypatch):
    # whisper.cpp's tiny test model has random weights: it hears nothing, but the program,
    # its options and the JSON it writes are the real ones.
    monkeypatch.setenv("CUTZO_WHISPER_MODEL", str(TEST_MODEL))
    wav = tmp_path / "tone.wav"
    subprocess.run([str(FFMPEG), "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi", "-i", "sine=frequency=300:duration=3",
                    "-ar", "16000", "-ac", "1", "-c:a", "pcm_s16le", str(wav)], check=True)
    seen = []
    result = whisper.transcribe(wav, progress=seen.append, work_dir=tmp_path)
    assert result["words"] == [] and seen[-1] == 1.0
