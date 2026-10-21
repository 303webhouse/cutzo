"""Phase 2: real audio. FFmpeg must be installed (it is on Nick's PC and in the test setup);
the DeepFilterNet parts run only where deep-filter is available."""

import json
import shutil
import subprocess
import zipfile
from pathlib import Path

import numpy as np
import pytest

from cutzo import tools
from cutzo.core import audio, recipes, render
from cutzo.core.errors import CutzoError, NotFound
from cutzo.tools import install as tool_install

FFMPEG = tools.find("ffmpeg") is not None and tools.find("ffprobe") is not None
DEEPFILTER = tools.find("deep-filter") is not None
needs_ffmpeg = pytest.mark.skipif(not FFMPEG, reason="FFmpeg isn't installed here")
needs_deepfilter = pytest.mark.skipif(not DEEPFILTER, reason="deep-filter isn't installed here")
RATE = audio.RATE


def tone(seconds, freq=220.0, level=0.3, rate=RATE):
    t = np.arange(int(seconds * rate)) / rate
    return (level * np.sin(2 * np.pi * freq * t)).astype(np.float32)


def talk_with_gaps(gaps=(0.4, 1.6, 2.5, 0.3, 3.0), talk=2.0, seed=3):
    """Bursts of 'speech' (a wobbling tone) separated by quiet gaps of the given lengths."""
    rng = np.random.default_rng(seed)
    parts = []
    for gap in gaps:
        burst = tone(talk, 180 + 40 * rng.random(), 0.3) * (0.6 + 0.4 * np.sin(np.linspace(0, 20, int(talk * RATE))))
        parts += [burst.astype(np.float32), rng.normal(0, 0.0005, int(gap * RATE)).astype(np.float32)]
    parts.append(tone(talk, 200, 0.3))
    return np.concatenate(parts)


def write_mp3(path: Path, samples: np.ndarray):
    raw = path.with_suffix(".f32")
    samples.astype("<f4").tofile(raw)
    subprocess.run([str(tools.find("ffmpeg")), "-hide_banner", "-loglevel", "error", "-y", "-f", "f32le", "-ar",
                    str(RATE), "-ac", "1", "-i", str(raw), "-ac", "2", "-b:a", "192k", str(path)], check=True)
    raw.unlink()
    return path


# ------------------------------------------------------------------ tools
def test_modules_import_on_their_own():
    # Lab scripts and setup import cutzo.tools first; that must not trip over cutzo.core.
    import sys

    for stmt in ("from cutzo.tools import ffmpeg", "import cutzo.tools.install", "from cutzo.core import audio"):
        done = subprocess.run([sys.executable, "-c", stmt], capture_output=True, text=True)
        assert done.returncode == 0, done.stderr


def test_tool_registry_and_status():
    assert tools.platform_key().count("-") == 1
    names = {t["tool"] for t in tools.status()}
    assert names == {"ffmpeg", "deep-filter", "whisper", "parakeet-model"}
    assert not set(tools.RETIRED) & names
    for tool in tools.TOOLS.values():
        for d in tool.downloads.values():
            assert len(d.sha256) == 64 and d.size > 0
            assert d.url.startswith(("https://github.com/", "https://huggingface.co/ggml-org/parakeet-GGUF/resolve/3515645"))
            assert d.kind in ("zip", "tar.gz", "exe", "file")
    with pytest.raises(KeyError):
        tools.find("photoshop")


def test_speech_model_can_come_from_an_environment_variable(monkeypatch, tmp_path):
    monkeypatch.setenv("CUTZO_TOOLS", str(tmp_path / "tools"))
    monkeypatch.delenv("CUTZO_PARAKEET_MODEL", raising=False)
    assert tools.find_file("parakeet-model") is None
    with pytest.raises(CutzoError) as err:
        tools.require_file("parakeet-model")
    assert "Update Cutzo" in (err.value.hint or "")
    model = tmp_path / "model.bin"
    model.write_bytes(b"x")
    monkeypatch.setenv("CUTZO_PARAKEET_MODEL", str(model))
    assert tools.find_file("parakeet-model") == model and tools.available("parakeet-model")


def test_require_explains_how_to_install(monkeypatch, tmp_path):
    monkeypatch.setenv("CUTZO_TOOLS", str(tmp_path))
    monkeypatch.setenv("CUTZO_TOOLS_ONLY", "1")
    with pytest.raises(CutzoError) as missing:
        tools.require("deep-filter")
    assert "Update Cutzo" in missing.value.hint


def test_unzip_refuses_unsafe_paths(tmp_path):
    archive = tmp_path / "bad.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr("tool-1/bin/ok.txt", "fine")
        zf.writestr("tool-1/../../evil.txt", "nope")
    dest = tmp_path / "out"
    dest.mkdir()
    with pytest.raises(RuntimeError):
        tool_install._unzip(archive, "tool-1", dest)
    good = tmp_path / "good.zip"
    with zipfile.ZipFile(good, "w") as zf:
        zf.writestr("tool-1/bin/ffmpeg.exe", "binary")
        zf.writestr("other/readme.txt", "skip me")
    dest2 = tmp_path / "out2"
    dest2.mkdir()
    tool_install._unzip(good, "tool-1", dest2)
    assert (dest2 / "bin" / "ffmpeg.exe").read_text() == "binary"
    assert not (dest2 / "other").exists()


def test_install_checks_the_checksum(monkeypatch, tmp_path):
    monkeypatch.setenv("CUTZO_TOOLS", str(tmp_path))
    tool = tools.DEEPFILTER

    def fake_download(url, dest, size, report):
        dest.write_bytes(b"not the real program")
        return "0" * 64

    monkeypatch.setattr(tool_install, "_download", fake_download)
    monkeypatch.setattr(tools, "platform_key", lambda: "linux-x86_64")
    monkeypatch.setattr(tool_install, "platform_key", lambda: "linux-x86_64")
    with pytest.raises(RuntimeError) as bad:
        tool_install.install(tool, report=lambda _: None)
    assert "checksum" in str(bad.value)
    assert not (tmp_path / tool.folder).exists()


def test_old_versions_are_removed(monkeypatch, tmp_path):
    monkeypatch.setenv("CUTZO_TOOLS", str(tmp_path))
    old = tmp_path / "ffmpeg-8.0"
    old.mkdir()
    (old / tool_install.MARKER).write_text(json.dumps({"tool": "ffmpeg", "version": "8.0"}))
    mine = tmp_path / "ffmpeg-7.1"  # not installed by Cutzo (no marker): left alone
    mine.mkdir()
    assert tool_install.remove_old_versions(tools.FFMPEG) == ["ffmpeg-8.0"]
    assert mine.exists() and not old.exists()


# ------------------------------------------------------------------ audio
def test_envelope_silences_and_dead_air(tmp_path):
    pcm = audio.Pcm(audio.write_f32(tmp_path / "x.f32", talk_with_gaps()))
    env = audio.envelope(pcm)
    assert len(env) == pytest.approx(pcm.duration / audio.HOP, abs=2)
    threshold = audio.suggest_threshold(env)
    assert audio.noise_floor(env) < threshold < audio.speech_level(env)
    silences = audio.find_silences(env, threshold, min_len=1.0)
    lengths = sorted(round(b - a, 1) for a, b in silences)
    assert lengths == [1.6, 2.5, 3.0]
    cuts = audio.dead_air_cuts(silences, longer_than=1.0, keep=0.5, duration=pcm.duration)
    assert [round(b - a, 1) for a, b in cuts] == [round(x - 0.5, 1) for x in (1.6, 2.5, 3.0)]


def test_peaks_whole_and_window(tmp_path):
    x = np.zeros(RATE * 4, dtype=np.float32)
    x[RATE * 2: RATE * 2 + 10] = 0.8
    pcm = audio.Pcm(audio.write_f32(tmp_path / "x.f32", x))
    whole = audio.peaks(pcm, 40)
    assert len(whole["peaks"]) == 40 and max(whole["peaks"]) == pytest.approx(0.8, abs=1e-4)
    assert whole["peaks"].index(max(whole["peaks"])) == 20
    window = audio.peaks(pcm, 10, start=1.5, end=2.5)
    assert window["start"] == 1.5 and window["end"] == 2.5
    assert window["peaks"].index(max(window["peaks"])) == 5


def test_offset_by_sound_finds_the_shift(tmp_path):
    ref = talk_with_gaps()
    late = np.concatenate([np.zeros(int(1.25 * RATE), dtype=np.float32), ref])[: len(ref)]
    e1 = audio.envelope(audio.Pcm(audio.write_f32(tmp_path / "a.f32", ref)))
    e2 = audio.envelope(audio.Pcm(audio.write_f32(tmp_path / "b.f32", late)))
    shift, confidence = audio.offset_by_sound(e1, e2)
    # `late` has 1.25 s more before the same sounds: it started recording 1.25 s earlier.
    assert shift == pytest.approx(-1.25, abs=0.02) and confidence > 0.3


def test_read_wav_formats(tmp_path):
    from cutzo.core.voice import _write_wav

    x = tone(0.5, level=0.5)
    _write_wav(tmp_path / "f.wav", x, RATE)
    y, rate = audio.read_wav(tmp_path / "f.wav")
    assert rate == RATE and y.shape == (len(x), 1) and np.allclose(y[:, 0], x)
    import wave

    with wave.open(str(tmp_path / "i.wav"), "wb") as w:
        w.setnchannels(2)
        w.setsampwidth(2)
        w.setframerate(44100)
        w.writeframes((np.stack([x, -x], axis=1) * 32767).astype("<i2").tobytes())
    z, rate = audio.read_wav(tmp_path / "i.wav")
    assert rate == 44100 and z.shape == (len(x), 2) and np.allclose(z[:, 1], -x, atol=1e-3)


# ------------------------------------------------------------------ voice
@needs_ffmpeg
@needs_deepfilter
def test_noise_removal_in_pieces_keeps_length_and_timing(tmp_path):
    from cutzo.core import voice

    rng = np.random.default_rng(1)
    x = talk_with_gaps() + rng.normal(0, 0.01, len(talk_with_gaps())).astype(np.float32)
    src = audio.write_f32(tmp_path / "in.f32", x)
    out = voice.denoise_long(src, tmp_path / "out.f32", limit_db=24, work_dir=tmp_path, piece=3.0, workers=2)
    y = audio.Pcm(out).open()[:, 0]
    assert len(y) == len(x) and np.isfinite(y).all()
    assert audio.noise_floor(audio.envelope(audio.Pcm(out))) < audio.noise_floor(audio.envelope(audio.Pcm(src))) - 6


def test_voice_settings():
    from cutzo.core.voice import VoiceSettings

    s = VoiceSettings.from_dict({"strength": 50})
    assert s.limit_db == 24.0 and "Noise removal (strength 50 of 100)" in s.steps()
    assert VoiceSettings.from_dict({"strength": 100}).limit_db == 100.0
    off = VoiceSettings.from_dict({"strength": 0, "rumble_hz": 0, "deess": False, "compress": False, "level": False})
    assert off.steps() == ["Nothing (all cleanup switched off)"] and off.post_filters() is None


# ----------------------------------------------------------------- recipes
def _ep(duration=600.0, chapters=(), cold=(), outro=(), edits=()):
    return {"duration": duration, "edits": [dict(type="cut", start=a, end=b) for a, b in edits],
            "chapters": [{"time": t, "title": f"C{n}"} for n, t in enumerate(chapters)],
            "picks": {"cold_open": [{"start": a, "end": b} for a, b in cold], "outro": [{"start": a, "end": b} for a, b in outro]},
            "assets": {}}


def test_dig_layout_matches_season_debrief_part_2():
    from cutzo.core import shows

    show = shows.get_show("the-dig")
    # Nick's cold open was 18.65 s long, so the talk starts where his did (1:00.661).
    ep = _ep(chapters=(0, 200, 400), cold=((500, 503.27), (510, 515.97), (520, 529.41)), outro=((580, 590),))
    plan = recipes.plan_episode(ep, show, voice_path=Path("v.f32"), talk_lufs=recipes.TALK_REFERENCE_LUFS,
                                kit_file=lambda a: Path(a), length_of=lambda a: 1e9)
    assert plan.talk_start == pytest.approx(60.661, abs=0.05)
    labels = [p.label for p in plan.pieces]
    assert labels.count("Theme (break)") == 2 and labels.count("Stinger") == 2
    assert labels.count("Sound bite") == 7 and labels.count("Sign-off") == 1
    gap = recipes.THE_DIG["break"]["alone"] + recipes.THE_DIG["break"]["stinger_length"]
    assert [c["time"] for c in plan.chapters][0] == 0.0
    # Chapter 2 starts with its stinger, a beat before the talk: talk_start + 200 s + one break.
    assert plan.chapters[1]["time"] == pytest.approx(60.661 + 200 + gap - 1.1, abs=0.1)
    assert plan.total > plan.talk_end > plan.talk_start
    assert plan.duck_db == recipes.THE_DIG["duck_db"] > 0  # music dips under voices (Nick, Oct 14)


def test_dig_music_stays_on_its_cue_and_breaks_use_different_parts():
    from cutzo.core import shows

    show = shows.get_show("the-dig")

    def plan_for(cold):
        ep = _ep(chapters=(0, 200, 400, 600), cold=cold)
        return recipes.plan_episode(ep, show, voice_path=Path("v.f32"), talk_lufs=recipes.TALK_REFERENCE_LUFS,
                                    kit_file=lambda a: Path(a), length_of=lambda a: 1e9)

    def first(plan, label):
        return [p for p in plan.pieces if p.label == label][0]

    nick, short, long = plan_for(((500, 518.65),)), plan_for(((500, 510),)), plan_for(((500, 530),))
    # A shorter cold open leaves the theme, the bites and the talk where Nick put them (the band at ~21 s)...
    for label in ("Theme (intro)", "Sound bite"):
        assert first(short, label).at == pytest.approx(first(nick, label).at)
    assert short.talk_start == pytest.approx(nick.talk_start)
    # ...and a longer one moves all of it later by as much, so the band still comes in after it.
    for label in ("Theme (intro)", "Sound bite"):
        assert first(long, label).at == pytest.approx(first(nick, label).at + 11.35, abs=0.05)
    assert recipes.THE_DIG["intro"]["cold_open_room"] == pytest.approx(
        recipes.THE_DIG["intro"]["cold_open_end"] - recipes.THE_DIG["intro"]["cold_open_at"])
    # Three breaks, three different parts of the song.
    starts = [p.start for p in nick.pieces if p.label == "Theme (break)"]
    assert starts == recipes.THE_DIG["break"]["theme_starts"][:3] and len(set(starts)) == 3


def test_dig_extras_play_between_the_theme_and_the_stinger():
    from cutzo.core import shows

    show = shows.get_show("the-dig")
    ep = _ep(chapters=(0, 200, 400))
    ep["chapters"] = [{"id": f"c{n}", **c} for n, c in enumerate(ep["chapters"])]
    plain = recipes.plan_episode(ep, show, voice_path=Path("v.f32"), talk_lufs=recipes.TALK_REFERENCE_LUFS,
                                 kit_file=lambda a: Path(a), length_of=lambda a: 1e9)
    ep["inserts"] = [{"id": "i1", "file": "drop.wav", "chapter": "c1", "time": 200, "kind": "voice", "seconds": 5.0},
                     {"id": "i2", "file": "clip.mp4", "time": 300.0, "kind": "video", "seconds": 7.0}]
    plan = recipes.plan_episode(ep, show, voice_path=Path("v.f32"), talk_lufs=recipes.TALK_REFERENCE_LUFS,
                                kit_file=lambda a: Path(a), length_of=lambda a: 1e9)
    drop, clip = sorted((p for p in plan.pieces if p.label == "Extra"), key=lambda p: p.at)
    theme = [p for p in plan.pieces if p.label == "Theme (break)"][0]
    stinger = [p for p in plan.pieces if p.label == "Stinger"][0]
    assert drop.at == pytest.approx(theme.at + recipes.THE_DIG["break"]["lead_in"] + recipes.THE_DIG["break"]["alone"])
    assert stinger.at == pytest.approx(drop.until + recipes.EXTRA_BREATH)
    assert clip.length == 7.0 and plan.picture is None  # The Dig is audio only: a video extra plays its sound
    assert plan.total - plain.total == pytest.approx(5.0 + recipes.EXTRA_BREATH + 7.0)


def test_music_dips_while_someone_talks(tmp_path):
    music = audio.write_f32(tmp_path / "music.f32", np.full((RATE * 10, 2), 0.5, dtype=np.float32))
    silent = audio.write_f32(tmp_path / "voice.f32", np.zeros(RATE * 10, dtype=np.float32))
    pieces = [render.Piece(src=music, channels=2, start=0.0, end=10.0, at=0.0, kind="music"),
              render.Piece(src=silent, channels=1, start=0.0, end=2.0, at=3.0, kind="voice"),
              render.Piece(src=silent, channels=1, start=0.0, end=1.0, at=5.5, kind="voice")]  # joins: gap < 1 s
    y = audio.Pcm(render.mix(render.Plan(pieces=pieces, duck_db=6.0), tmp_path / "m.f32"), channels=2).open()
    level = lambda t: float(y[int(t * RATE), 0])  # noqa: E731
    assert level(1.0) == pytest.approx(0.5, abs=1e-4)  # before anyone talks
    assert level(2.8) == pytest.approx(0.5 * 10 ** (-3 / 20), abs=2e-3)  # halfway down the 0.4 s lead-in
    assert level(4.0) == pytest.approx(0.5 * 10 ** (-6 / 20), abs=1e-3)
    assert level(5.2) == pytest.approx(0.5 * 10 ** (-6 / 20), abs=1e-3)  # stays down across the short gap
    assert level(8.0) == pytest.approx(0.5, abs=1e-4)  # back up after the 1 s release
    flat = audio.Pcm(render.mix(render.Plan(pieces=pieces), tmp_path / "f.f32"), channels=2).open()
    assert float(flat[int(4.0 * RATE), 0]) == pytest.approx(0.5, abs=1e-4)  # no dips unless asked


def test_layout_without_kit_is_just_the_talk():
    from cutzo.core import shows

    show = shows.get_show("the-dig")
    ep = _ep(chapters=(0, 200), edits=((100, 160),))
    ep["assets"] = {"theme": "none", "montage": "none", "stinger": "none"}
    plan = recipes.plan_episode(ep, show, voice_path=Path("v.f32"), talk_lufs=-18, kit_file=lambda a: Path(a),
                                length_of=lambda a: 1e9)
    assert plan.talk_start == 0.0 and plan.total == pytest.approx(540.0, abs=0.1)
    assert {p.label for p in plan.pieces} == {"Conversation"}


def test_mix_places_pieces_with_gain_and_fades(tmp_path):
    src = audio.write_f32(tmp_path / "s.f32", np.full(RATE * 2, 0.5, dtype=np.float32))
    plan = render.Plan(pieces=[render.Piece(src=src, channels=1, start=0.0, end=1.0, at=0.5, gain_db=-6.0206),
                               render.Piece(src=src, channels=1, start=0.0, end=1.0, at=2.0, fade_in=0.5)])
    out = render.mix(plan, tmp_path / "m.f32")
    y = audio.Pcm(out, channels=2).open()
    assert y.shape[1] == 2
    assert y[int(0.25 * RATE), 0] == 0.0
    assert y[int(1.0 * RATE), 0] == pytest.approx(0.25, abs=1e-3)
    assert y[int(2.25 * RATE), 1] == pytest.approx(0.25, abs=0.01)  # halfway up the fade-in
    assert y[int(2.75 * RATE), 1] == pytest.approx(0.5, abs=1e-3)


@needs_ffmpeg
def test_encode_keeps_peaks_under_the_limiter_ceiling(tmp_path):
    from cutzo.core import presets

    spec = presets.get_preset("podcast-mp3")["spec"]
    rng = np.random.default_rng(5)
    noise = rng.normal(0, 0.25, (RATE * 8, 2)).astype(np.float32)  # loud, bright, peaky: the hard case
    mix = audio.write_f32(tmp_path / "mix.f32", noise)
    dst = tmp_path / "out.mp3"
    made = render.encode(mix, dst, spec, tags={"title": "t"}, chapters=[], total=8.0)
    assert made["ceiling_db"] == spec["true_peak_max"] - render.PEAK_MARGIN
    lower = render.encode(mix, dst, spec, tags={"title": "t"}, chapters=[], total=8.0, measured=made["before"],
                          ceiling_db=-6.0)
    assert lower["ceiling_db"] == -6.0
    assert render.check_file(dst, spec)["loudness"]["true_peak"] < -3.0


@needs_ffmpeg
def test_encode_lands_on_the_loudness_target_even_when_the_limiter_works_hard(tmp_path):
    """The limiter takes loudness away from peaky sound; the gain is corrected before encoding,
    so one encode is enough (Spooky Sode's first 14-minute AAC encode missed and had to be redone)."""
    from cutzo.core import presets

    spec = presets.get_preset("youtube-1080p")["spec"]
    rng = np.random.default_rng(3)
    t = np.arange(RATE * 12) / RATE
    x = 0.05 * np.sin(2 * np.pi * 150 * t) * (np.sin(2 * np.pi * 2 * t) > 0) + 0.02 * rng.normal(size=len(t))
    x[:: RATE // 10] += 0.95  # sharp clicks far above the loudness
    mix = audio.write_f32(tmp_path / "mix.f32", np.stack([x, x], 1).astype(np.float32))
    dst = tmp_path / "out.m4a"
    made = render.encode(mix, dst, spec, tags={"title": "t"}, chapters=[], total=12.0)
    plain = spec["loudness"] - made["before"]["integrated"]
    assert made["gain_db"] > plain + 2.0  # the limiter's loss was made up
    checked = render.check_file(dst, spec)
    assert abs(checked["loudness"]["integrated"] - spec["loudness"]) <= 0.2
    assert checked["loudness"]["true_peak"] <= spec["true_peak_max"]


# ------------------------------------------------------------- kit copies
@needs_ffmpeg
def test_kit_local_copy_refreshes_when_drive_changes(tmp_path):
    from cutzo.core import kit

    drive = tmp_path / "drive"
    folder = drive / "Denver Stiffs" / "The Dig" / "Sound Effects"
    folder.mkdir(parents=True)
    src = write_mp3(folder / "Mining Chops.mp3", tone(1.0))
    src.rename(folder / "Mining Chops.wav")
    copy = kit.local_copy("dig-sfx-mining-chops", kit_dir=tmp_path / "kit", drive=drive)
    assert copy.exists() and audio.Pcm(copy, channels=2).duration == pytest.approx(1.0, abs=0.06)
    first = copy.stat().st_mtime_ns
    assert kit.local_copy("dig-sfx-mining-chops", kit_dir=tmp_path / "kit", drive=drive).stat().st_mtime_ns == first
    shutil.rmtree(drive)  # Drive offline: last copy still works
    assert kit.local_copy("dig-sfx-mining-chops", kit_dir=tmp_path / "kit", drive=drive) == copy
    with pytest.raises(NotFound):
        kit.local_copy("dig-intro-doug-moe", kit_dir=tmp_path / "kit", drive=drive)


# --------------------------------------------------------- whole episode
@pytest.fixture()
def real_episode(tmp_path, monkeypatch):
    if not FFMPEG:
        pytest.skip("FFmpeg isn't installed here")
    from cutzo.core import Cutzo

    drive = tmp_path / "drive"
    dig = drive / "Denver Stiffs" / "The Dig"
    for sub, name, seconds in (("", "Out of Flux - DABOOMJIGGLE.mp3", 150), ("Intro", "Not meant for this game.wav", 20),
                               ("Intro", "Doug Moe Greatness.wav", 32), ("Intro", "Take that l on the way out.wav", 8),
                               ("Old Files", "The Dig Intro Audio.wav", 20), ("Sound Effects", "Mining Chops.wav", 2)):
        (dig / sub).mkdir(parents=True, exist_ok=True)
        made = write_mp3(dig / sub / (Path(name).stem + ".mp3"), tone(seconds, 330, 0.2))
        made.rename(dig / sub / name)
    recording = write_mp3(tmp_path / "Season Test.mp3", talk_with_gaps(gaps=(0.4, 1.6, 2.5, 0.3, 3.0) * 4))
    app = Cutzo(tmp_path / "library", speed=0.0, drive=drive)
    ep_id = app.create_episode(show="The Dig", title="Season Test")["episode"]["id"]
    app.jobs.wait(app.import_media(ep_id, [str(recording)])["job"]["id"], 60)
    if not DEEPFILTER:
        app.set_voice(ep_id, 0)
    yield app, ep_id
    app.jobs.shutdown()


def test_real_build_review_and_export(real_episode):
    app, ep_id = real_episode
    media = app.get_episode(ep_id)["media"][0]
    # 21 bursts of 2 s talk plus four rounds of gaps (7.8 s each) = 73.2 s
    assert media["probe"]["audio"]["codec"] == "mp3" and media["duration"] == pytest.approx(73.2, abs=0.2)
    job = app.jobs.wait(app.build_episode(ep_id)["job"]["id"], 300)
    assert job["status"] == "done", job.get("error")
    ep = app.get_episode(ep_id)
    stages = {s["key"]: s["status"] for s in ep["stages"]}
    assert stages["transcribe"] == "later" and stages["auto_edit"] == "done" and ep["stage"] == "review"
    dead = [e for e in ep["edits"] if e["reason"] == "dead air"]
    assert len(dead) == 12  # the 1.6, 2.5 and 3.0 s gaps, four times over
    files = {p.name for p in (app.library / "episodes" / ep_id).iterdir()}
    assert {"voice.f32", "original.f32", "preview.m4a", "original.m4a"} <= files and "work" not in files
    assert app.preview_audio(ep_id).name == "preview.m4a"
    assert app.preview_audio(ep_id, which="original").name == "original.m4a"
    zoomed = app.waveform(ep_id, 50, start=10, end=20)
    assert len(zoomed["peaks"]) == 50 and zoomed["start"] == 10
    with pytest.raises(CutzoError):
        app.cut_words(ep_id, "hello")  # no transcript until Phase 3

    # Bring one back (a 2.5 or 3.0 s gap): finding dead air again keeps it back.
    first = next(e for e in dead if e["end"] - e["start"] > 1.5)
    app.restore_cut(ep_id, first["id"])
    looser = app.set_dead_air(ep_id, longer_than=2.0, keep=0.5)
    assert looser["suggested_cuts"] == 7  # the 2.5 and 3.0 s gaps, minus the one brought back
    assert all(not (e["start"] < first["end"] and first["start"] < e["end"])
               for e in app.get_episode(ep_id)["edits"] if e["reason"] == "dead air")
    with pytest.raises(CutzoError):
        app.set_dead_air(ep_id, longer_than=1.0, keep=1.5)

    # Picks, chapters, undo
    app.add_chapter(ep_id, 0, "Opening")
    app.add_chapter(ep_id, 30, "Second half")
    cold = app.add_pick(ep_id, "cold open", 40, 44)
    app.add_pick(ep_id, "sign-off", 60, 64)
    with pytest.raises(CutzoError):
        app.add_pick(ep_id, "cold open", 0, 50)  # longer than 45 s in all
    app.undo(ep_id)
    assert not app.get_episode(ep_id)["picks"]["outro"]
    app.add_pick(ep_id, "ending", 60, 64)
    view = app.get_episode(ep_id)
    assert view["finished_duration"] > view["final_duration"]
    assert view["chapters"][0]["output_time"] == 0.0 and view["chapters"][1]["output_time"] > 60
    app.remove_pick(ep_id, cold["pick"]["id"])
    app.add_pick(ep_id, "cold open", 40, 44)

    with pytest.raises(CutzoError):
        app.export(ep_id, ["youtube-4k"])  # video is Phase 3
    job = app.jobs.wait(app.export(ep_id, ["podcast-mp3"])["job"]["id"], 300)
    assert job["status"] == "done", job.get("error")
    x = app.get_episode(ep_id)["exports"][-1]
    assert x["written"] and Path(x["file"]).exists() and x["check"]["passed"], x["check"]
    assert {r["rule"] for r in x["check"]["rules"]} == {"File type", "Channels", "Sample rate", "Bitrate", "Loudness", "Peaks"}
    probe = subprocess.run([str(tools.find("ffprobe")), "-v", "error", "-show_chapters", "-show_entries", "format_tags",
                            "-of", "json", x["file"]], capture_output=True, text=True).stdout
    info = json.loads(probe)
    assert [c["tags"]["title"] for c in info["chapters"]] == ["Opening", "Second half"]
    assert info["format"]["tags"]["album"] == "The Dig"
    assert x["layout"]["pieces"]["Stinger"] == 1 and x["layout"]["pieces"]["Cold open"] == 1
    assert not (app.library / "episodes" / ep_id / "mix.f32").exists()
    # Replacing a finished export needs a confirm
    from cutzo.core.errors import NeedsConfirmation

    with pytest.raises(NeedsConfirmation):
        app.export(ep_id, ["podcast-mp3"])


def write_wav(path: Path, samples: np.ndarray) -> Path:
    import wave

    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(RATE)
        w.writeframes((np.clip(samples, -1, 1) * 32767).astype("<i2").tobytes())
    return path


def bursts(seed: int, total: float) -> np.ndarray:
    """One person's track: talk bursts of random lengths with random pauses (no repeating pattern)."""
    rng = np.random.default_rng(seed)
    out = rng.normal(0, 0.0005, int(total * RATE)).astype(np.float32)
    t = 0.5
    while t < total - 3.5:
        length = float(rng.uniform(1.0, 3.0))
        n = int(length * RATE)
        a = int(t * RATE)
        out[a:a + n] += tone(length, 150 + 100 * rng.random(), 0.3)[:n] * (0.6 + 0.4 * np.sin(np.linspace(0, 15, n)))
        t += length + float(rng.uniform(1.5, 4.0))
    return out


@needs_ffmpeg
def test_streamyard_tracks_are_lined_up_by_sound(tmp_path):
    from cutzo.core import Cutzo

    alice, bob = bursts(11, 40.0), bursts(12, 38.0)
    true_a, true_b = 1.40, 0.30  # where each track really starts in the combined recording
    mix = np.zeros(int(42.0 * RATE), dtype=np.float32)
    for track, off in ((alice, true_a), (bob, true_b)):
        a = int(round(off * RATE))
        mix[a:a + len(track)] += track
    folder = tmp_path / "raw"
    folder.mkdir()
    write_wav(folder / "Test-Alice_Smith-audio-00h_00m_01s_250ms-StreamYard.wav", alice)  # the name is 0.15 s early
    write_wav(folder / "Test-Bob_Jones-audio-00h_00m_00s_300ms-StreamYard.wav", bob)
    write_mp3(folder / "Test StreamYard.mp3", mix)
    app = Cutzo(tmp_path / "library", speed=0.0)
    try:
        ep_id = app.create_episode(show="The Dig", title="Tracks")["episode"]["id"]
        app.jobs.wait(app.import_media(ep_id, [str(folder)])["job"]["id"], 60)
        app.set_voice(ep_id, 0)
        job = app.jobs.wait(app.build_episode(ep_id)["job"]["id"], 300)
        assert job["status"] == "done", job.get("error")
        ep = app.get_episode(ep_id)
        sync = {m["person"] or m["name"]: m["sync"] for m in ep["media"]}
        # Recording time starts with the earliest track (Bob's); Alice's slip in her file name is corrected.
        assert sync["Bob Jones"]["used"] and sync["Bob Jones"]["offset"] == pytest.approx(0.0, abs=0.02)
        assert sync["Alice Smith"]["offset"] == pytest.approx(true_a - true_b, abs=0.02)
        assert sync["Alice Smith"]["method"].startswith("Matched by sound")
        assert sync["Test StreamYard.mp3"]["used"] is False  # the combined recording only helps line things up
        assert ep["duration"] == pytest.approx(true_a - true_b + 40.0, abs=0.1)
    finally:
        app.jobs.shutdown()


def test_engine_routes_for_real_episodes(real_episode):
    from fastapi.testclient import TestClient

    from cutzo.engine import create_app

    app, ep_id = real_episode
    app.set_voice(ep_id, 0)  # noise removal is tested above; skip it here to save time
    assert app.jobs.wait(app.build_episode(ep_id)["job"]["id"], 300)["status"] == "done"
    app_headers = {"X-Cutzo-Source": "app"}
    with TestClient(create_app(app, extra_hosts=("testserver",))) as client:
        assert client.get(f"/api/episodes/{ep_id}/waveform?buckets=30&start=5&end=8").json()["end"] == 8
        audio_resp = client.get(f"/api/episodes/{ep_id}/audio?which=original", headers={"Range": "bytes=0-99"})
        assert audio_resp.status_code == 206 and audio_resp.headers["content-type"] == "audio/mp4"
        r = client.post(f"/api/episodes/{ep_id}/picks", json={"kind": "cold open", "start": "0:10", "end": "0:14"},
                        headers={"X-Cutzo-Source": "claude"})
        assert r.status_code == 200 and r.json()["pick"]["by"] == "claude"
        assert client.post(f"/api/episodes/{ep_id}/picks", json={"kind": "middle", "start": 1, "end": 4},
                           headers=app_headers).status_code == 400
        assert client.post(f"/api/episodes/{ep_id}/dead-air", json={"longer_than": 1.2}, headers=app_headers).status_code == 200
        assert client.post(f"/api/episodes/{ep_id}/voice", json={"strength": 40}, headers=app_headers).json()["needs_rebuild"]
        assert client.post("/api/reveal", json={"file": "C:/Windows/notepad.exe"}, headers=app_headers).status_code == 404
