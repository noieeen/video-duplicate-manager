def test_make_video(tmp_path, make_video):
    p = make_video(tmp_path / "a" / "x.ts", source="black", duration=5)
    import os
    assert os.path.getsize(p) > 0
