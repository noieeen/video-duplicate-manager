import os

import pytest

from engine import db
from engine.library import library_files, library_json


def _add(conn, path, size, *, root=None, status="ok", width=1280, height=720, codec="h264",
         bit_rate=2_000_000, duration=60.0):
    conn.execute(
        "INSERT INTO files(path, root, size, mtime, width, height, codec, bit_rate, duration, status) "
        "VALUES (?,?,?,?,?,?,?,?,?,?)",
        (path, root or os.path.dirname(path), size, 0.0, width, height, codec, bit_rate, duration, status),
    )
    conn.commit()


def _conn(tmp_path):
    return db.connect(str(tmp_path / "lib" / "library.db"))


def _groups(result):
    return {g["key"]: g for g in result["groups"]}


def test_potential_buckets_and_order(tmp_path):
    conn = _conn(tmp_path)
    _add(conn, "/v/above.mp4", 100, width=2560, height=1440, codec="h264", bit_rate=8_000_000)
    _add(conn, "/v/old_high.mp4", 200, width=1280, height=720, codec="h264", bit_rate=6_000_000)
    _add(conn, "/v/old_low.mp4", 300, width=1280, height=720, codec="mpeg4", bit_rate=2_000_000)
    _add(conn, "/v/no_bitrate.mp4", 50, width=1280, height=720, codec="h264", bit_rate=None)
    _add(conn, "/v/hevc.mp4", 400, width=1920, height=1080, codec="HEVC", bit_rate=9_000_000)
    _add(conn, "/v/av1.mp4", 10, width=640, height=480, codec="av1", bit_rate=1_000_000)
    _add(conn, "/v/vp9.mp4", 20, width=640, height=480, codec="vp9", bit_rate=1_000_000)
    _add(conn, "/v/broken.mp4", 500, status="unreadable", width=None, height=None, codec=None,
         bit_rate=None)

    result = library_json(conn, None, "potential")

    assert [g["key"] for g in result["groups"]] == [
        "above_1080p", "old_high", "old_low", "efficient", "unreadable",
    ]
    groups = _groups(result)
    assert groups["above_1080p"] == {"key": "above_1080p", "label": "Above 1080p", "files": 1, "bytes": 100}
    assert groups["old_high"]["bytes"] == 200
    assert groups["old_low"]["files"] == 2 and groups["old_low"]["bytes"] == 350  # missing bit rate = low
    assert groups["efficient"]["files"] == 3 and groups["efficient"]["bytes"] == 430
    assert groups["unreadable"] == {"key": "unreadable", "label": "Unreadable", "files": 1, "bytes": 500}
    assert result["total_files"] == 8
    assert result["total_bytes"] == 1580
    conn.close()


def test_potential_omits_empty_buckets(tmp_path):
    conn = _conn(tmp_path)
    _add(conn, "/v/a.mp4", 100, width=1280, height=720, codec="h264", bit_rate=1_000_000)

    result = library_json(conn, None, "potential")

    assert [g["key"] for g in result["groups"]] == ["old_low"]
    conn.close()


def test_resolution_buckets_portrait_and_unknown(tmp_path):
    conn = _conn(tmp_path)
    _add(conn, "/v/sd.mp4", 1, width=640, height=480)
    _add(conn, "/v/720.mp4", 2, width=1280, height=720)
    _add(conn, "/v/1080.mp4", 4, width=1920, height=1080)
    _add(conn, "/v/portrait.mp4", 8, width=1080, height=1920)  # short side 1080 -> 1080p
    _add(conn, "/v/1440.mp4", 16, width=2560, height=1440)
    _add(conn, "/v/4k.mp4", 32, width=3840, height=2160)
    _add(conn, "/v/nodims.mp4", 64, width=None, height=None)
    _add(conn, "/v/broken.mp4", 128, status="unreadable", width=None, height=None)

    result = library_json(conn, None, "resolution")

    assert [g["key"] for g in result["groups"]] == [
        "sd", "720p", "1080p", "1440p", "2160p", "unknown", "unreadable",
    ]
    groups = _groups(result)
    assert groups["1080p"]["files"] == 2 and groups["1080p"]["bytes"] == 12
    assert groups["2160p"]["label"] == "4K and above"
    assert groups["unknown"]["bytes"] == 64
    assert groups["unreadable"]["bytes"] == 128
    conn.close()


def test_codec_top_five_fold_into_other(tmp_path):
    conn = _conn(tmp_path)
    sizes = {"h264": 700, "hevc": 600, "mpeg4": 500, "vp9": 400, "mpeg2video": 300,
             "wmv3": 200, "vc1": 100}
    for codec, size in sizes.items():
        _add(conn, f"/v/{codec}.mp4", size, codec=codec)
    _add(conn, "/v/broken.mp4", 50, status="unreadable", codec="h264")

    result = library_json(conn, None, "codec")

    assert [g["key"] for g in result["groups"]] == [
        "h264", "hevc", "mpeg4", "vp9", "mpeg2video", "other", "unreadable",
    ]
    groups = _groups(result)
    assert groups["h264"]["label"] == "H264"
    assert groups["h264"]["bytes"] == 700  # unreadable file is not counted as h264
    assert groups["other"] == {"key": "other", "label": "Other", "files": 2, "bytes": 300}
    assert groups["unreadable"]["bytes"] == 50
    assert result["total_files"] == 8
    conn.close()


def test_codec_other_files_listed_largest_first(tmp_path):
    conn = _conn(tmp_path)
    sizes = {"h264": 700, "hevc": 600, "mpeg4": 500, "vp9": 400, "mpeg2video": 300,
             "wmv3": 200, "vc1": 100}
    for codec, size in sizes.items():
        _add(conn, f"/v/{codec}.mp4", size, codec=codec)

    result = library_files(conn, None, "codec", "other")

    assert result["total"] == 2
    assert [f["codec"] for f in result["files"]] == ["wmv3", "vc1"]
    assert result["files"][0]["size"] == 200
    conn.close()


def test_folder_grouping_keys_are_roots(tmp_path):
    conn = _conn(tmp_path)
    _add(conn, "/a/x.mp4", 100, root="/a")
    _add(conn, "/a/y.mp4", 100, root="/a")
    _add(conn, "/b/z.mp4", 150, root="/b")
    _add(conn, "/b/broken.mp4", 80, root="/b", status="unreadable")

    result = library_json(conn, None, "folder")

    assert [(g["key"], g["label"], g["files"], g["bytes"]) for g in result["groups"]] == [
        ("/b", "/b", 2, 230),
        ("/a", "/a", 2, 200),
    ]
    conn.close()


def test_folder_top_five_fold_into_other_last(tmp_path):
    conn = _conn(tmp_path)
    for i, size in enumerate([600, 500, 400, 300, 200, 100]):
        _add(conn, f"/f{i}/clip.mp4", size, root=f"/f{i}")

    result = library_json(conn, None, "folder")

    assert [g["key"] for g in result["groups"]] == ["/f0", "/f1", "/f2", "/f3", "/f4", "other"]
    assert _groups(result)["other"]["bytes"] == 100
    conn.close()


def test_roots_filter_keeps_only_files_inside(tmp_path):
    vids = os.path.realpath(str(tmp_path / "vids"))
    conn = _conn(tmp_path)
    _add(conn, os.path.join(vids, "a.mp4"), 100)
    _add(conn, os.path.join(vids, "sub", "b.mp4"), 200)
    _add(conn, os.path.join(os.path.realpath(str(tmp_path)), "outside.mp4"), 400)

    result = library_json(conn, [str(tmp_path / "vids")], "potential")

    assert result["total_files"] == 2
    assert result["total_bytes"] == 300
    conn.close()


def test_roots_filter_does_not_match_sibling_with_common_prefix(tmp_path):
    vids = os.path.realpath(str(tmp_path / "vids"))
    sibling = os.path.realpath(str(tmp_path / "vids2"))
    conn = _conn(tmp_path)
    _add(conn, os.path.join(vids, "a.mp4"), 100)
    _add(conn, os.path.join(sibling, "b.mp4"), 200)

    result = library_json(conn, [str(tmp_path / "vids")], "folder")

    assert result["total_files"] == 1
    assert result["total_bytes"] == 100
    conn.close()


def test_roots_filter_accepts_trailing_separator_on_root(tmp_path):
    vids = os.path.realpath(str(tmp_path / "vids"))
    conn = _conn(tmp_path)
    _add(conn, os.path.join(vids, "a.mp4"), 100)

    assert library_json(conn, [vids + os.sep], "potential")["total_files"] == 1
    conn.close()


@pytest.mark.skipif(os.name != "nt", reason="case-insensitive root matching is a Windows rule")
def test_roots_filter_is_case_insensitive_on_windows(tmp_path):
    vids = os.path.realpath(str(tmp_path / "vids"))
    conn = _conn(tmp_path)
    _add(conn, os.path.join(vids, "a.mp4"), 100)

    result = library_json(conn, [str(tmp_path / "vids").upper()], "potential")

    assert result["total_files"] == 1
    conn.close()


def test_rows_not_ok_or_unreadable_are_excluded(tmp_path):
    conn = _conn(tmp_path)
    for status in ("new", "moved", "missing", "trashed"):
        _add(conn, f"/v/{status}.mp4", 1000, status=status)
    _add(conn, "/v/ok.mp4", 10)

    assert library_json(conn, None, "potential")["total_files"] == 1
    conn.close()


def test_files_sorted_largest_first_with_paging(tmp_path):
    conn = _conn(tmp_path)
    _add(conn, "/v/b.mp4", 100, width=2560, height=1440)
    _add(conn, "/v/a.mp4", 100, width=2560, height=1440)
    _add(conn, "/v/big.mp4", 500, width=2560, height=1440)
    _add(conn, "/v/small.mp4", 10, width=2560, height=1440)
    _add(conn, "/v/other.mp4", 999, width=640, height=480)

    full = library_files(conn, None, "potential", "above_1080p")
    assert full["group_by"] == "potential" and full["key"] == "above_1080p"
    assert full["total"] == 4
    assert [f["path"] for f in full["files"]] == ["/v/big.mp4", "/v/a.mp4", "/v/b.mp4", "/v/small.mp4"]
    assert set(full["files"][0]) == {"file_id", "path", "size", "width", "height", "codec",
                                     "bit_rate", "duration", "status"}

    page = library_files(conn, None, "potential", "above_1080p", limit=2, offset=1)
    assert page["total"] == 4
    assert [f["path"] for f in page["files"]] == ["/v/a.mp4", "/v/b.mp4"]
    conn.close()


def test_unknown_key_gives_zero_total_and_no_files(tmp_path):
    conn = _conn(tmp_path)
    _add(conn, "/v/a.mp4", 10)

    result = library_files(conn, None, "potential", "no_such_group")

    assert result == {"group_by": "potential", "key": "no_such_group", "total": 0, "files": []}
    conn.close()


def test_bad_group_by_raises_value_error(tmp_path):
    conn = _conn(tmp_path)
    with pytest.raises(ValueError):
        library_json(conn, None, "size")
    with pytest.raises(ValueError):
        library_files(conn, None, "size", "x")
    conn.close()
