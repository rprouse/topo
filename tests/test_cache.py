from pipeline.cache import CacheEntry, stable_hash


def test_stable_hash_ignores_key_order():
    assert stable_hash({"a": 1, "b": [1, 2]}) == stable_hash({"b": [1, 2], "a": 1})
    assert stable_hash({"a": 1}) != stable_hash({"a": 2})


def test_request_change_changes_key(tmp_path):
    a = CacheEntry(tmp_path, "s", {"bbox": [0, 0, 1, 1]})
    b = CacheEntry(tmp_path, "s", {"bbox": [0, 0, 1, 2]})
    assert a.dir != b.dir


def test_commit_and_completeness(tmp_path):
    e = CacheEntry(tmp_path, "s", {"x": 1})
    assert not e.is_complete()
    st = e.staging()
    (st / "data.txt").write_text("hello")
    rec = e.commit(st, ["data.txt"], {"retrieved_at": "t", "urls": ["u"]})
    assert e.is_complete()
    assert rec["files"][0]["sha256"] == "2cf24dba5fb0a30e26e83b2ac5b9e29e1b161e5c1fa7425e73043362938b9824"
    assert e.verify_checksums() == []
    (e.dir / "data.txt").write_text("HELLO")
    assert e.verify_checksums() == ["data.txt"]
    (e.dir / "data.txt").write_text("hello!")
    assert not e.is_complete()  # size changed
