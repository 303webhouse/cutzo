"""Phase 3, step 3: The New Heretics' video (layout, spectrum, title graphic, the finished file)."""

from __future__ import annotations

import copy
import json
import subprocess
from pathlib import Path

import numpy as np
import pytest

from cutzo import tools
from cutzo.tools import ffmpeg
from cutzo.core import captions, kit, presets, recipes, render, shows, spectrum, titles, video
from cutzo.core.errors import CutzoError

RATE = 48000
FFMPEG = tools.find("ffmpeg")
needs_ffmpeg = pytest.mark.skipif(not (FFMPEG and tools.find("ffprobe")), reason="FFmpeg isn't installed here")


def _ep(duration=7742.0, chapters=(), cold=(), outro=(), assets=None):
    return {"duration": duration, "edits": [],
            "chapters": [dict(c) for c in chapters],
            "picks": {"cold_open": [{"start": a, "end": b} for a, b in cold],
                      "outro": [{"start": a, "end": b} for a, b in outro]},
            "assets": assets or {}}


SPOOKY = _ep(chapters=[{"id": "c1", "time": 12.2, "title": "The New Heretics"},
                       {"id": "c2", "time": 2462.0, "title": "Second segment", "segment": True},
                       {"id": "c3", "time": 4875.0, "title": "Third segment", "segment": True},
                       {"id": "c4", "time": 6949.0, "title": "A new topic"}],
             cold=[(4844.306, 4846.642), (4850.045, 4855.484), (4856.452, 4866.295)],
             outro=[(7677.303, 7742.435)])


# The one-channel talk level at which every cue plays at exactly Nick's gain: the reference, less the
# 3 dB the talk gains by playing in both stereo channels.
REF_MONO = recipes.NEW_HERETICS["talk_reference_lufs"] - render.MONO_IN_STEREO_DB


def _plan(ep, talk_lufs=REF_MONO):
    show = shows.get_show("the-new-heretics")
    return recipes.plan_episode(ep, show, voice_path=Path("voice.f32"), talk_lufs=talk_lufs,
                                kit_file=lambda a: Path(a), length_of=lambda a: kit.seconds(a) or 1e9)


def test_new_heretics_layout_follows_spooky_sode():
    plan = _plan(SPOOKY)
    by = lambda label: [p for p in plan.pieces if p.label == label]  # noqa: E731
    teaser = by("Teaser")
    teaser_end = teaser[-1].until
    assert teaser[0].at == 1.5 and teaser[0].gain_db == pytest.approx(1.8)
    # The montage's music (1.888 s into it) starts as the teaser ends; the talk 83.7 s into the montage.
    montage = by("Opening montage")[0]
    assert montage.at + 1.888 == pytest.approx(teaser_end, abs=1e-6)
    assert plan.talk_start == pytest.approx(montage.at + 83.684)
    layers = {layer.label: [] for layer in plan.picture.layers}
    for layer in plan.picture.layers:
        layers[layer.label].append(layer)
    assert layers["Opening still"][0].at == 0 and layers["Opening still"][0].until == pytest.approx(montage.at)
    assert layers["Background"][0].at == pytest.approx(montage.at + 93.36)
    # Under the talk: the montage's frame at 87.354 s (Nick's frame hold), with the fire loop screened over
    # it at 2.03x speed (4275 s of loop per 2104.5 s), so a 2-hour episode runs it four times, as in his project.
    held = layers["Held title card"][0]
    assert held.kind == "still" and held.at == pytest.approx(montage.until) and held.start == 87.354
    loop = layers["Background"]
    assert len(loop) == 4 and all(x.blend == "screen" for x in loop)
    assert loop[0].speed == pytest.approx(2.0314, abs=1e-4) and loop[0].length == pytest.approx(2104.5, abs=0.1)
    assert loop[1].fade_in == 1.0 and loop[0].fade_out == 1.0  # one run dissolves into the next
    assert all(x.blend == "screen" for x in layers["Break visuals"] + layers["Ending visuals"])
    # Under the ending visuals and Full Flames: the montage's opening frame, held (no footage, no show name).
    backdrop = layers["Ending backdrop"][0]
    assert backdrop.kind == "still" and backdrop.blend == "normal" and backdrop.start == 0.434
    assert backdrop.at == pytest.approx(plan.talk_end - 0.834) and backdrop.until == pytest.approx(plan.total)
    order = [layer.label for layer in plan.picture.layers]
    assert order.index("Ending backdrop") < max(i for i, label in enumerate(order) if label == "Ending visuals")
    assert layers["Episode title"][0].at == pytest.approx(montage.at + 91.191)
    # Breaks: the cue fades in under the last seconds of talk, plays to its end, a page flip, then talk.
    music = by("Break music")
    assert [str(m.src) for m in music] == ["tnh-cue-239-short3", "tnh-cue-239-short2"]
    assert music[0].fade_in == pytest.approx(28.83) and music[0].curve == "premiere"
    flips = by("Page flip")
    assert len(flips) == 3  # two breaks and one new topic
    chapters = {c["title"]: c["time"] for c in plan.chapters}
    assert chapters["The New Heretics"] == 0.0
    assert chapters["Second segment"] == pytest.approx(flips[0].at)
    assert flips[0].at == pytest.approx(music[0].until - 1.268)
    # Ending: music fades in over the last minute of talk; Full Flames, then the sign-off.
    ending = by("Ending music")[0]
    assert ending.at == pytest.approx(plan.talk_end - 60.527) and ending.gain_db == pytest.approx(-17.2)
    assert by("Sign-off")[0].at == pytest.approx(plan.talk_end + 18.452)
    flames = [x for x in layers["Ending visuals"] if x.src == "tnh-full-flames"][0]
    assert flames.at == pytest.approx(plan.talk_end + 15.115) and flames.until == pytest.approx(plan.total)
    assert plan.total == pytest.approx(ending.until)
    assert plan.picture.fade_to_black == pytest.approx(31.966)
    # The spectrum shows during the teaser and the talk, not in the breaks.
    windows = plan.picture.spectrum
    assert windows[0][1] == pytest.approx(teaser_end) and len(windows) == 4
    assert all(not (a < music[0].until - 2 < b) for a, b, _, _ in windows)


def test_levels_follow_the_talk_and_unmeasured_cues_match_by_loudness():
    quiet = _plan(SPOOKY, talk_lufs=REF_MONO - 4.0)
    cue = [p for p in quiet.pieces if p.label == "Break music"][0]
    assert cue.gain_db == pytest.approx(-9.4 - 4.0)  # 4 dB quieter talk, 4 dB quieter music
    # The talk is measured on one channel; in the stereo mix it is 3 dB louder, and the cues follow that.
    assert render.MONO_IN_STEREO_DB == pytest.approx(3.01, abs=0.01)
    show = shows.get_show("the-new-heretics")
    ep = copy.deepcopy(SPOOKY)
    ep["assets"] = {"break_music": "tnh-cue-god-of-love", "transition": "none", "spectrum": "none"}
    plan = recipes.plan_episode(ep, show, voice_path=Path("v.f32"), talk_lufs=REF_MONO, kit_file=lambda a: Path(a),
                                length_of=lambda a: kit.seconds(a) or 1e9, loudness_of=lambda a: -12.0)
    music = [p for p in plan.pieces if p.label == "Break music"]
    assert all(m.gain_db == pytest.approx(-16.0 - 3.2 + 12.0) for m in music)
    assert not [p for p in plan.pieces if p.label == "Page flip"] and plan.picture.spectrum == []


def test_a_chapter_named_like_a_voiced_title_plays_it_instead_of_the_page_flip():
    ep = copy.deepcopy(SPOOKY)
    ep["chapters"][3]["title"] = "Heretical Haiku"
    plan = _plan(ep)
    voiced = [p for p in plan.pieces if p.label == "Voiced title"]
    assert len(voiced) == 1 and str(voiced[0].src) == "tnh-seg-heretical-haiku"
    assert voiced[0].length == pytest.approx(7.602)
    assert len([p for p in plan.pieces if p.label == "Page flip"]) == 2
    chapter = next(c for c in plan.chapters if c["title"] == "Heretical Haiku")
    assert chapter["time"] == pytest.approx(voiced[0].at, abs=0.001)


def test_without_montage_or_teaser_the_talk_starts_at_once():
    show = shows.get_show("the-new-heretics")
    ep = _ep(duration=600.0, chapters=[{"id": "c1", "time": 0, "title": "Start"}],
             assets={"montage": "none", "ending_visuals": "none"})
    plan = recipes.plan_episode(ep, show, voice_path=Path("v.f32"), talk_lufs=-16.5, kit_file=lambda a: Path(a),
                                length_of=lambda a: kit.seconds(a) or 1e9)
    assert plan.talk_start == 0.5
    assert plan.total == pytest.approx(plan.talk_end + 40.0)  # no sign-off: a short ending
    ending = [p for p in plan.pieces if p.label == "Ending music"][0]
    assert ending.fade_out == 8.0 and ending.until == pytest.approx(plan.total)
    assert {layer.label for layer in plan.picture.layers} == {"Background", "Episode title", "Ending visuals"}


# ---------------------------------------------------------------- spectrum
def test_spectrum_lines_rise_where_the_sound_is():
    sp = spectrum.Spectrum()
    t = np.arange(int(0.2 * RATE)) / RATE
    tone = (0.05 * np.sin(2 * np.pi * 1000.0 * t)).astype(np.float32)
    h = sp.heights(tone[None, :sp._n])[0]
    band = int(round((1000.0 - 20.0) / (2280.0 / 399)))
    assert int(np.argmax(h)) in (band - 1, band, band + 1)
    assert h.max() == pytest.approx(480 * 0.05 ** 0.7, rel=0.15)  # between FFT bins it reads a little low
    planes = sp.planes(h)
    H, W = sp.box.height, sp.box.width
    fill, rim = planes[:H], planes[H:]
    col = int(round(sp.style.x0 + (sp.style.x1 - sp.style.x0) * band / 399)) - sp.box.x
    mid = int(round(sp.style.y - sp.box.y))
    assert fill[mid - 20, col] > 150 and fill[mid - 20, 20] == 0  # a tall line there, nothing at the far left
    assert rim[mid, 20] > 150  # the bright line along the path
    silent = sp.planes(np.zeros(400, dtype=np.float32))
    assert silent[:H][mid - 6].max() == 0 and silent[H:][mid].max() > 150
    blank = list(sp.frames(lambda a, b: np.zeros(b - a, np.float32), 5, lambda _t: 0.0))
    assert len(blank) == 5 and all(not any(f) for f in blank) and len(blank[0]) == 2 * H * W
    colours = sp.fill_colours()
    assert colours.shape == (H, W, 3)
    left, middle = colours[mid, 10], colours[mid, W // 2]
    assert middle[1] > left[1]  # yellow in the middle, orange at the ends


def test_spectrum_visibility_windows():
    visible = spectrum.windows_visibility([(10.0, 20.0, 2.0, 4.0)])
    assert visible(9.9) == 0 and visible(11.0) == pytest.approx(0.5) and visible(15) == 1
    assert visible(18.0) == pytest.approx(0.5) and visible(20.0) == 0


# ------------------------------------------------------------ title graphic
def test_title_graphic_lines_and_placement(tmp_path):
    assert titles.lines_for({"title": "Halloween Special: JW Ghost Stories"}) == \
        {"label": "Halloween Special:", "title": "JW Ghost Stories", "guests": ""}
    assert titles.lines_for({"title": "Plain"})["title"] == "Plain"
    box = titles.render({"label": "Halloween Special:", "title": "JW Ghost Stories", "guests": "ft. Two exJWs"},
                        tmp_path / "t.png")
    x, y, w, h = box
    assert abs(x - 958) <= 6 and abs(y - 472) <= 6 and abs((x + w) - 1618) <= 10 and abs((y + h) - 636) <= 8
    assert w % 2 == 0 and h % 2 == 0
    wide = titles.render({"title": "A very long title that would run right off the edge of the frame"}, tmp_path / "w.png")
    assert wide[0] + wide[2] <= 1880
    assert titles.render({}, tmp_path / "n.png") is None
    small = titles.render({"title": "Hi"}, tmp_path / "s.png", width=320, height=180)
    assert small[0] < 170 and small[1] < 100


def _render_graph(g: video.Graph, out: Path, total: float, w: int, h: int, feed=None) -> np.ndarray:
    args = [arg for group in g.inputs for arg in group]
    ffmpeg.run([*args, "-filter_complex", g.finish(0.0), "-map", "[vout]", "-t", f"{total}", "-f", "rawvideo",
                "-pix_fmt", "rgb24", str(out)], duration=total, feed=feed)
    return np.fromfile(out, np.uint8).reshape(-1, h, w, 3).astype(int)


def _encode_rgb(frames: np.ndarray, dst: Path) -> Path:
    """RGB frames -> a kit-like video (BT.709, TV range, 29.97 fps), as good as lossless."""
    n, h, w, _ = frames.shape
    subprocess.run([str(FFMPEG), "-hide_banner", "-loglevel", "error", "-y", "-f", "rawvideo", "-pix_fmt", "rgb24",
                    "-s", f"{w}x{h}", "-r", "30000/1001", "-i", "-", "-vf",
                    "scale=out_color_matrix=bt709:out_range=tv,format=yuv444p", "-c:v", "libx264", "-preset",
                    "ultrafast", "-qp", "0", "-colorspace", "bt709", str(dst)],
                   input=np.ascontiguousarray(frames, np.uint8).tobytes(), check=True)
    return dst


@needs_ffmpeg
def test_title_and_spectrum_in_the_picture(tmp_path):
    """The title graphic fades in and out on time in its cream colour; the spectrum's colours show
    where its lines are (everything drawn in RGB, then BT.709 video colours)."""
    w, h, total = 320, 180, 1.0
    sp = spectrum.Spectrum(frame_height=h)
    fill, rim = tmp_path / "fill.png", tmp_path / "rim.png"
    video._save_rgb(sp.fill_colours(), fill)
    video._save_rgb(sp.rim_colours(), rim)
    title = tmp_path / "title.png"
    x, y, tw, th = titles.render({"label": "Halloween Special:", "title": "Test"}, title, width=w, height=h)
    bw, bh = sp.box.width, sp.box.height
    frames = int(total * video.FPS_F) + 2
    band = np.zeros((2 * bh, bw), np.uint8)
    band[bh // 3: bh // 2] = 200  # a band of fill
    band[bh + bh // 2: bh + bh // 2 + 2] = 255  # and a rim line
    g = video.Graph(total, w, h)
    g.image(title, x, y, at=0.2, length=0.6, fi=0.2, fo=0.2)
    g.spectrum(sp.box, fill, rim)
    out = _render_graph(g, tmp_path / "out.rgb", total, w, h, feed=lambda pipe: pipe.write(band.tobytes() * frames))
    assert out.shape[0] >= 29
    lit = out[:, sp.box.y + bh // 3: sp.box.y + bh // 2, sp.box.x: sp.box.x + bw]
    assert lit.mean() > 40 and (lit[..., 0].mean() > lit[..., 1].mean() > lit[..., 2].mean())  # orange to yellow
    bright = [int(f[y: y + th, x: x + tw].max()) for f in out]
    assert bright[5] == 0 and bright[15] > 200 and bright[25] == 0  # in at 0.2 s, out by 0.8 s
    assert 0 < bright[8] < bright[10] < bright[12] and bright[19] > bright[21] > bright[23] > 0  # fading
    region = out[15, y: y + th, x: x + tw].reshape(-1, 3)
    cream = region[np.argsort(region.sum(axis=1))[-20:]].mean(axis=0)  # the brightest pixels of the letters
    # cream (236, 220, 198); thin letters in a small frame lose a little colour to 4:2:0 video
    assert np.abs(cream - np.array([236, 220, 198])).max() <= 16 and cream[0] > cream[2], cream


@needs_ffmpeg
def test_screen_layers_lighten_and_play_at_their_speed(tmp_path):
    """A screen layer lightens what's below (255 - (255 - a)(255 - b) / 255), black changes nothing,
    its fades go to black, and a layer at 2x speed shows its video twice as fast."""
    w, h = 64, 36
    grey = _encode_rgb(np.full((20, h, w, 3), 100, np.uint8), tmp_path / "grey.mp4")
    ramp = np.zeros((200, h, w, 3), np.uint8)
    for n in range(200):
        ramp[n] = min(n, 180)  # brightness = frame number
    ramp_video = _encode_rgb(ramp, tmp_path / "ramp.mp4")
    g = video.Graph(2.0, w, h)
    g.still(grey, 0.0, 0.0, 2.0, 0.0, 0.0)
    g.video(ramp_video, 0.0, 0.0, 2.0, 0.0, 0.5, blend="screen", speed=2.0)
    out = _render_graph(g, tmp_path / "out.rgb", 2.0, w, h)

    def screen(a: float, b: float) -> float:
        return 255 - (255 - a) * (255 - b) / 255

    assert out[0, 10, 10, 0] == pytest.approx(100, abs=4)  # black on top: the grey as it was
    assert out[30, 10, 10, 1] == pytest.approx(screen(100, 60), abs=5)  # frame 30 shows the ramp's frame 60
    assert out[44, 10, 10, 2] == pytest.approx(screen(100, 88), abs=5)
    assert out[-1, 10, 10, 0] < out[44, 10, 10, 0]  # fading out to black at the end


def test_set_video_title_and_segments(cutzo):
    ep = cutzo.create_episode(show="The New Heretics", title="Spooky: Ghosts")["episode"]
    assert cutzo.get_episode(ep["id"])["video_title"] == {"label": "Spooky:", "title": "Ghosts", "guests": ""}
    res = cutzo.set_video_title(ep["id"], guests="ft. Two exJWs", source="claude")
    assert "FT." not in res["summary"] and res["video_title"]["guests"] == "ft. Two exJWs"
    cutzo.undo(ep["id"])
    assert cutzo.get_episode(ep["id"])["video_title"]["guests"] == ""
    with pytest.raises(CutzoError):
        cutzo.set_video_title(ep["id"], label="", title="", guests="")


def test_video_presets():
    assert presets.find_preset("youtube")["id"] == "youtube-1080p"
    spec = presets.get_preset("youtube-1080p")["spec"]
    assert spec["video"]["width"] == 1920 and spec["loudness"] == -14.0
    assert presets.get_preset("youtube-4k").get("spec") is None  # 1080p only for now
    args = video.encoder_args(spec["video"])
    assert "-g" in args or any(a.startswith("keyint=15") for a in args)
    assert args[args.index("-colorspace") + 1] == "bt709"
    # quality-based with a ceiling (YouTube asks for a variable bitrate), 2 B-frames
    assert args[args.index("-maxrate") + 1] == "12000k" and args[args.index("-bf") + 1] == "2"
    assert ("-crf" in args) != ("-cq" in args)
    assert video.BT709 in video.Graph(1.0, 320, 180).finish(0.5)  # the labels go on the frames (FFmpeg 9)


# --------------------------------------------------------- the whole export
def _make_video(path: Path, seconds: float, colour: str, sound: bool = False) -> None:
    args = [str(FFMPEG), "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi", "-i",
            f"testsrc2=s=320x180:r=24:d={seconds}"]
    if sound:
        args += ["-f", "lavfi", "-i", f"sine=frequency=440:duration={seconds}", "-c:a", "aac", "-b:a", "96k"]
    args += ["-vf", f"drawbox=c={colour}@0.6:t=fill", "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
             str(path)]
    subprocess.run(args, check=True)


def _make_sound(path: Path, seconds: float, freq: float) -> None:
    subprocess.run([str(FFMPEG), "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi", "-i",
                    f"sine=frequency={freq}:duration={seconds}", "-ac", "2", "-ar", "48000", str(path)], check=True)


@pytest.fixture()
def heretics(tmp_path, monkeypatch):
    if not (FFMPEG and tools.find("ffprobe")):
        pytest.skip("FFmpeg isn't installed here")
    from cutzo.core import Cutzo

    drive = tmp_path / "drive"
    tnh = drive / "The New Heretics"
    videos = {"tnh-montage": 6.0, "tnh-bg-loop-1hr": 20.0, "tnh-music-loop": 8.0, "tnh-full-flames": 8.0}
    for asset_id, seconds in videos.items():
        asset = kit.get_asset(asset_id)
        folder = tnh.parent.joinpath(*asset["folder"].split("/"))
        folder.mkdir(parents=True, exist_ok=True)
        _make_video(folder / asset["name"], seconds, {"tnh-montage": "blue", "tnh-bg-loop-1hr": "red",
                                                       "tnh-music-loop": "green", "tnh-full-flames": "orange"}[asset_id],
                    sound=asset_id == "tnh-montage")
        monkeypatch.setitem(kit._BY_ID[asset_id], "seconds", seconds)
    for asset_id, seconds, freq in (("tnh-cue-239-short3", 8.0, 330), ("tnh-cue-239-short2", 6.0, 392),
                                    ("tnh-cue-239-full", 12.0, 262), ("tnh-page-flip", 1.0, 880)):
        asset = kit.get_asset(asset_id)
        folder = tnh.parent.joinpath(*asset["folder"].split("/"))
        folder.mkdir(parents=True, exist_ok=True)
        _make_sound(folder / asset["name"], seconds, freq)
        monkeypatch.setitem(kit._BY_ID[asset_id], "seconds", seconds)
    # Spooky Sode's timings, shrunk so the test video is short
    r = copy.deepcopy(recipes.NEW_HERETICS)
    r["montage"].update(music_at=0.5, talk_at=3.0, title_at=3.2, background_at=4.0)
    r["cues"]["tnh-cue-239-short3"] = (-10.6, 3.0)
    r["cues"]["tnh-cue-239-short2"] = (-9.0, 2.0)
    r["ending"].update(lead_in=4.0, visuals_until=1.5, flames_at=1.0, signoff_at=1.5, fade_to_black=2.0,
                       visuals_fade_short=0.5)
    monkeypatch.setattr(recipes, "NEW_HERETICS", r)
    monkeypatch.setitem(recipes.RECIPES, "the-new-heretics", r)
    small = copy.deepcopy(presets._BY_ID["youtube-1080p"])
    small["spec"]["video"].update(width=320, height=180, max_kbps=3000, min_kbps=50)
    monkeypatch.setitem(presets._BY_ID, "youtube-1080p", small)
    monkeypatch.setattr(kit, "VIDEO_SIZE", (320, 180))
    monkeypatch.setattr(video, "_nvenc", False)
    rate = RATE
    rng = np.random.default_rng(5)
    parts = []
    for k in range(10):  # 10 x (2 s of "talk" + 0.5 s pause) = 25 s
        t = np.arange(2 * rate) / rate
        f0 = 120 + 20 * k
        talk = sum(np.sin(2 * np.pi * f0 * n * t) / n for n in range(1, 8)) * 0.1
        talk *= 0.6 + 0.4 * np.sin(2 * np.pi * 3 * t)
        parts += [talk + 0.002 * rng.standard_normal(len(t)), np.zeros(rate // 2)]
    raw = tmp_path / "talk.f32"
    np.concatenate(parts).astype("<f4").tofile(raw)
    rec = tmp_path / "Talk.wav"
    subprocess.run([str(FFMPEG), "-hide_banner", "-loglevel", "error", "-y", "-f", "f32le", "-ar", str(rate), "-ac", "1",
                    "-i", str(raw), "-c:a", "pcm_s16le", str(rec)], check=True)
    app = Cutzo(tmp_path / "library", speed=0.0, drive=drive)
    ep_id = app.create_episode(show="The New Heretics", title="Halloween Special: Test")["episode"]["id"]
    app.jobs.wait(app.import_media(ep_id, [str(rec)])["job"]["id"], 60)
    app.set_voice(ep_id, 0)
    yield app, ep_id
    app.jobs.shutdown()


def test_video_export_end_to_end(heretics):
    app, ep_id = heretics
    job = app.jobs.wait(app.build_episode(ep_id)["job"]["id"], 300)
    assert job["status"] == "done", job.get("error")
    for e in app.get_episode(ep_id)["edits"]:  # keep all of the made-up talk, so the captions are easy to check
        app.restore_cut(ep_id, e["id"])
    app.add_chapter(ep_id, 0, "Start")
    app.add_chapter(ep_id, 7.5, "A topic")
    seg = app.add_chapter(ep_id, 15.0, "Next segment", segment=True)
    assert seg["chapter"]["segment"] is True
    app.add_pick(ep_id, "cold open", 10.0, 12.0)
    app.add_pick(ep_id, "sign-off", 20.0, 22.0)
    app.set_video_title(ep_id, guests="ft. Nobody")
    # One word per burst of "talk" (2.5 s apart), so the captions can be checked against the layout.
    words = [{"w": f"Word{k}.", "s": 2.5 * k + 0.2, "e": 2.5 * k + 0.6, "sp": None} for k in range(10)]
    (app.library / "episodes" / ep_id / "transcript.json").write_text(json.dumps({"speakers": [], "words": words}))
    with pytest.raises(CutzoError):
        app.export(ep_id, ["youtube-4k"])
    job = app.jobs.wait(app.export(ep_id, ["youtube-1080p", "podcast-mp3"])["job"]["id"], 600)
    assert job["status"] == "done", job.get("error")
    exports = {x["target"]: x for x in app.get_episode(ep_id)["exports"]}
    v = exports["youtube-1080p"]
    failed = [r for r in v["check"]["rules"] if not r["pass"]]
    assert v["check"]["passed"], failed
    assert {"Video", "Frame rate", "Colour", "Keyframes", "Loudness", "Length"} <= {r["rule"] for r in v["check"]["rules"]}
    assert exports["podcast-mp3"]["check"]["passed"]
    assert v["layout"]["picture"]["layers"]["Episode title"] == 2
    assert v["seconds"] == pytest.approx(exports["podcast-mp3"]["seconds"], abs=0.05)
    path = Path(v["file"])
    info = json.loads(subprocess.run([str(tools.find("ffprobe")), "-v", "error", "-show_streams", "-of", "json", str(path)],
                                     capture_output=True, text=True).stdout)
    kinds = {s["codec_type"]: s for s in info["streams"]}
    assert kinds["video"]["width"] == 320 and kinds["audio"]["codec_name"] == "aac"
    # A frame in the talk shows the title graphic (cream) over the red background loop.
    plan_start = v["layout"]["talk_starts"]
    frame = subprocess.run([str(FFMPEG), "-v", "error", "-ss", f"{plan_start + 1.5:.2f}", "-i", str(path), "-frames:v", "1",
                            "-f", "rawvideo", "-pix_fmt", "rgb24", "-"], capture_output=True, check=True).stdout
    img = np.frombuffer(frame, np.uint8).reshape(180, 320, 3).astype(int)
    title = img[78:108, 160:270]
    assert ((title[..., 0] > 200) & (title[..., 1] > 190) & (title[..., 2] > 160)).sum() > 30
    kit_dir = app.library / "kit" / "video"
    assert (kit_dir / "tnh-bg-loop-1hr-1080p.mp4").exists()  # kept for next time
    assert not (app.library / "episodes" / ep_id / "work-export").exists()
    # Captions and the chapter list sit next to the exports, timed to the finished file.
    extras = app.get_episode(ep_id)["extras"]
    cues = captions.read_srt(Path(extras["captions_file"]).read_text(encoding="utf-8"))
    teaser_at = recipes.NEW_HERETICS["teaser"]["at"]
    assert cues[0][2] == "Word4." and cues[0][0] == pytest.approx(teaser_at + 0.2, abs=0.01)  # the teaser (10-12 s)
    talk = [c for c in cues if c[2] == "Word0."]
    assert talk and talk[0][0] == pytest.approx(plan_start + 0.2, abs=0.01), cues
    assert sum(1 for c in cues if c[2] == "Word8.") == 2  # in the talk, and again in the sign-off (20-22 s)
    chapters = Path(extras["chapters_file"]).read_text(encoding="utf-8").splitlines()
    assert chapters[0] == "0:00 Start" and len(chapters) == 3
    assert "Captions" in job["result"]["summary"]
