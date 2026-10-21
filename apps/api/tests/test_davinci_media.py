import xml.etree.ElementTree as ET
from fractions import Fraction
from pathlib import Path
from urllib.parse import unquote, urlsplit

import pytest
from PIL import Image

from davinci_agent.fcpxml import Clip, Music, build_fcpxml
from davinci_agent.media_probe import UnsupportedMediaError, probe

FIXTURES = Path(__file__).parent / "fixtures" / "davinci"


# Expected values are ffprobe's own readings of these files.
@pytest.mark.parametrize(
    ("name", "seconds", "video", "audio", "size"),
    [
        ("avatar.mp4", 1.2, True, True, (72, 128)),
        ("insert.mov", 0.8, True, False, (128, 72)),
        ("music.m4a", 1.5, False, True, (None, None)),
        ("music.wav", 0.75, False, True, (None, None)),
        ("music_cbr.mp3", 2.3, False, True, (None, None)),
        ("music_vbr.mp3", 1.9, False, True, (None, None)),
    ],
)
def test_timed_media_matches_ffprobe(name, seconds, video, audio, size) -> None:
    info = probe(FIXTURES / name)
    assert float(info.duration) == pytest.approx(seconds, abs=0.001)
    assert (info.has_video, info.has_audio) == (video, audio)
    assert (info.width, info.height) == size


@pytest.mark.parametrize(("name", "size"), [("still.png", (90, 60)), ("still.jpg", (60, 90))])
def test_stills_have_size_and_no_duration(name, size) -> None:
    info = probe(FIXTURES / name)
    assert info.duration is None
    assert (info.width, info.height) == size


@pytest.mark.parametrize("lossless", [True, False])
def test_webp_stills(tmp_path: Path, lossless: bool) -> None:
    path = tmp_path / "still.webp"
    Image.new("RGB", (37, 53), "red").save(path, "WEBP", lossless=lossless)
    info = probe(path)
    assert (info.width, info.height) == (37, 53)


def test_unknown_file_is_refused(tmp_path: Path) -> None:
    path = tmp_path / "notes.txt"
    path.write_text("not media")
    with pytest.raises(UnsupportedMediaError):
        probe(path)


def _timeline(tmp_path: Path, **overrides) -> ET.Element:
    media = tmp_path / "Монтаж тест"
    media.mkdir()
    for name in ("avatar.mp4", "still.png", "insert.mov", "music_vbr.mp3"):
        (media / name).write_bytes((FIXTURES / name).read_bytes())
    kwargs = {
        "project_name": "Cindra тест",
        "width": 1080,
        "height": 1920,
        "fps": 25,
        "clips": [
            Clip(media / "avatar.mp4", probe(media / "avatar.mp4"), "Аватар"),
            Clip(media / "still.png", probe(media / "still.png"), "B-roll", Fraction(2)),
            Clip(media / "insert.mov", probe(media / "insert.mov"), "Вставка"),
        ],
        "music": Music(media / "music_vbr.mp3", probe(media / "music_vbr.mp3"), -12),
    }
    kwargs.update(overrides)
    text = build_fcpxml(**kwargs)
    assert text.startswith('<?xml version="1.0" encoding="UTF-8"?>\n<!DOCTYPE fcpxml>\n')
    return ET.fromstring(text.split("\n", 2)[2])


def test_sequence_is_the_requested_frame(tmp_path: Path) -> None:
    root = _timeline(tmp_path)
    sequence = root.find(".//sequence")
    seq_format = root.find(f".//format[@id='{sequence.get('format')}']")
    assert (seq_format.get("width"), seq_format.get("height")) == ("1080", "1920")
    assert seq_format.get("frameDuration") == "1/25s"
    # 1.2s -> 30 frames, still 2s -> 50, 0.8s -> 20
    assert sequence.get("duration") == "4s"


def test_clips_are_back_to_back_and_never_longer_than_their_file(tmp_path: Path) -> None:
    spine = _timeline(tmp_path).find(".//spine")
    items = list(spine)
    assert [(i.tag, i.get("name")) for i in items] == [
        ("asset-clip", "Аватар"),
        ("video", "B-roll"),
        ("asset-clip", "Вставка"),
    ]
    assert [i.get("offset") for i in items] == ["0s", "30/25s", "80/25s"]
    assert [i.get("duration") for i in items] == ["30/25s", "2s", "20/25s"]


def test_video_length_rounds_down_to_a_whole_frame(tmp_path: Path) -> None:
    """1.21s at 25fps is 30.25 frames: rounding up to 31 would put an
    offline frame past the end of the file in Resolve."""
    media = tmp_path / "m"
    media.mkdir()
    path = media / "avatar.mp4"
    path.write_bytes((FIXTURES / "avatar.mp4").read_bytes())
    info = probe(path)
    longer = type(info)(Fraction(121, 100), info.has_video, info.has_audio, info.width, info.height)
    root = _timeline(tmp_path, clips=[Clip(path, longer, "A")], music=None)
    assert root.find(".//spine/asset-clip").get("duration") == "30/25s"


def test_each_source_keeps_its_own_size_and_rate(tmp_path: Path) -> None:
    root = _timeline(tmp_path)
    insert = root.find(".//asset[@name='Вставка']")
    fmt = root.find(f".//format[@id='{insert.get('format')}']")
    assert (fmt.get("width"), fmt.get("height")) == ("128", "72")
    # Rate left for Resolve to read from the file, not asserted here.
    assert fmt.get("frameDuration") is None
    assert insert.get("hasAudio") is None  # silent clip claims no audio


def test_music_runs_under_the_whole_cut_at_the_requested_level(tmp_path: Path) -> None:
    root = _timeline(tmp_path)
    music = root.find(".//spine/asset-clip/asset-clip[@lane='-1']")
    assert music.get("name") == "Музыка"
    # The track (1.9s) is shorter than the cut (4s): it stops at its own end.
    assert music.get("duration") == "47/25s"
    assert music.find("adjust-volume").get("amount") == "-12dB"


def test_music_longer_than_the_cut_is_trimmed_to_it(tmp_path: Path) -> None:
    media = tmp_path / "x"
    media.mkdir()
    (media / "m.mp3").write_bytes((FIXTURES / "music_cbr.mp3").read_bytes())
    (media / "a.mp4").write_bytes((FIXTURES / "avatar.mp4").read_bytes())
    root = _timeline(
        tmp_path,
        clips=[Clip(media / "a.mp4", probe(media / "a.mp4"), "A")],
        music=Music(media / "m.mp3", probe(media / "m.mp3")),
    )
    assert root.find(".//asset-clip[@lane='-1']").get("duration") == "30/25s"


def test_paths_with_cyrillic_and_spaces_round_trip(tmp_path: Path) -> None:
    root = _timeline(tmp_path)
    src = root.find(".//asset[@name='Аватар']").get("src")
    assert src.startswith("file:///")
    assert " " not in src
    assert unquote(urlsplit(src).path).endswith("/Монтаж тест/avatar.mp4")


def test_empty_montage_is_rejected(tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        build_fcpxml(project_name="x", width=1080, height=1920, fps=25, clips=[])
