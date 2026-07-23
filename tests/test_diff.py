"""diff_surveys — 산식 전환 1회 CHANGED 수렴, GONE 분리, 증분 억제, fail-closed."""
import json

import pytest


def write_jsonl(path, records):
    with open(path, "w", encoding="utf-8") as fh:
        for r in records:
            fh.write(json.dumps(r, ensure_ascii=False) + "\n")


def rec(source="bizinfo", sid="PBLN_1", title="공고", status="접수중",
        content_hash=None, hash_version=None, **kw):
    r = {"source": source, "source_id": sid, "title": title, "status": status,
         "apply_start": "2026-07-01", "apply_end": "2027-08-01", "agency": "기관"}
    if content_hash is not None:
        r["content_hash"] = content_hash
    if hash_version is not None:
        r["hash_version"] = hash_version
    r.update(kw)
    return r


def run_diff(diff, monkeypatch, old_dir, new_dir, out, extra=None):
    argv = ["diff_surveys.py", str(old_dir), str(new_dir), "--out", str(out)]
    argv += extra or []
    monkeypatch.setattr("sys.argv", argv)
    try:
        diff.main()
        return 0
    except SystemExit as e:
        return e.code


def read_kinds(path):
    return [json.loads(l)["kind"] for l in path.read_text().splitlines()]


@pytest.fixture
def dirs(tmp_path):
    old, new = tmp_path / "old", tmp_path / "new"
    old.mkdir(), new.mkdir()
    return old, new


def test_hash_version_change_is_one_time_changed(diff, monkeypatch, dirs, tmp_path,
                                                 capsys):
    """v1↔v2 산식 전환은 1회 CHANGED — 재실행(양쪽 v2)에서 UNCHANGED로 수렴."""
    old, new = dirs
    write_jsonl(old / "bizinfo.jsonl", [rec(content_hash="aaa", hash_version=1)])
    write_jsonl(new / "bizinfo.jsonl", [rec(content_hash="bbb", hash_version=2)])
    out = tmp_path / "new_items.jsonl"
    assert run_diff(diff, monkeypatch, old, new, out) == 0
    rows = [json.loads(l) for l in out.read_text().splitlines()]
    assert len(rows) == 1 and rows[0]["kind"] == "CHANGED"
    assert "산식 전환" in rows[0]["changed_fields"][0]

    # 2차 실행: 직전 조사(new)가 old가 되고 다음 조사도 v2 동일 해시 → UNCHANGED
    old2, new2 = tmp_path / "old2", tmp_path / "new2"
    old2.mkdir(), new2.mkdir()
    write_jsonl(old2 / "bizinfo.jsonl", [rec(content_hash="bbb", hash_version=2)])
    write_jsonl(new2 / "bizinfo.jsonl", [rec(content_hash="bbb", hash_version=2)])
    out2 = tmp_path / "new_items2.jsonl"
    assert run_diff(diff, monkeypatch, old2, new2, out2) == 0
    assert out2.read_text() == ""
    assert '"UNCHANGED": 1' in capsys.readouterr().err


def test_classify_v2_v3_transition(diff):
    """v2↔v3(첨부 포함 산식) 전환도 동일하게 1회 CHANGED로 흡수된다."""
    old = rec(content_hash="aaa", hash_version=2)
    new = rec(content_hash="ccc", hash_version=3)
    r = diff.classify(old, new)
    assert r["kind"] == "CHANGED" and "산식 전환" in r["changed_fields"][0]
    # 같은 버전·같은 해시면 UNCHANGED
    assert diff.classify(rec(content_hash="c", hash_version=3),
                         rec(content_hash="c", hash_version=3))["kind"] == "UNCHANGED"


def test_gone_goes_to_separate_file(diff, monkeypatch, dirs, tmp_path):
    old, new = dirs
    write_jsonl(old / "bizinfo.jsonl", [rec(sid="PBLN_1"), rec(sid="PBLN_2")])
    write_jsonl(new / "bizinfo.jsonl", [rec(sid="PBLN_1")])
    out = tmp_path / "new_items.jsonl"
    assert run_diff(diff, monkeypatch, old, new, out) == 0
    assert out.read_text() == ""  # GONE은 검토 대상 파일에 섞이지 않는다
    gone = tmp_path / "gone_new_items.jsonl"
    rows = [json.loads(l) for l in gone.read_text().splitlines()]
    assert len(rows) == 1 and rows[0]["kind"] == "GONE"
    assert rows[0]["record"]["source_id"] == "PBLN_2"


def test_incremental_sources_suppress_gone(diff, monkeypatch, dirs, tmp_path):
    old, new = dirs
    write_jsonl(old / "fanfandaero.jsonl",
                [rec(source="fanfandaero", sid="ntc-1"),
                 rec(source="fanfandaero", sid="ntc-2")])
    write_jsonl(new / "fanfandaero.jsonl", [rec(source="fanfandaero", sid="ntc-1")])
    out = tmp_path / "new_items.jsonl"
    assert run_diff(diff, monkeypatch, old, new, out,
                    extra=["--incremental-sources", "fanfan"]) == 0  # 별칭 수용
    gone = tmp_path / "gone_new_items.jsonl"
    assert gone.read_text() == ""


def test_empty_new_dir_exits_1(diff, monkeypatch, dirs, tmp_path):
    old, new = dirs
    write_jsonl(old / "bizinfo.jsonl", [rec()])
    assert run_diff(diff, monkeypatch, old, new, tmp_path / "out.jsonl") == 1


PROFILE_OK = """---
entity_type: 개인사업자
industry_text: 카페
province: 서울
---
본문
"""

PROFILE_NO_AXES = """---
memo: 판정 필드 없음
---
본문
"""


def test_profile_without_axes_invalidates_carryover(diff, monkeypatch, dirs, tmp_path,
                                                    capsys):
    """판정 필드(axes)가 전무한 프로필은 파싱 실패와 동일 — 전체 NEW (fail-closed)."""
    old, new = dirs
    write_jsonl(old / "bizinfo.jsonl", [rec(content_hash="x", hash_version=2)])
    write_jsonl(new / "bizinfo.jsonl", [rec(content_hash="x", hash_version=2)])
    p_old, p_new = tmp_path / "p_old.md", tmp_path / "p_new.md"
    p_old.write_text(PROFILE_NO_AXES)
    p_new.write_text(PROFILE_NO_AXES)
    out = tmp_path / "out.jsonl"
    assert run_diff(diff, monkeypatch, old, new, out,
                    extra=["--old-profile", str(p_old),
                           "--new-profile", str(p_new)]) == 0
    assert read_kinds(out) == ["NEW"]
    assert "승계 무효" in capsys.readouterr().err


def test_same_profile_with_axes_keeps_carryover(diff, monkeypatch, dirs, tmp_path):
    old, new = dirs
    write_jsonl(old / "bizinfo.jsonl", [rec(content_hash="x", hash_version=2)])
    write_jsonl(new / "bizinfo.jsonl", [rec(content_hash="x", hash_version=2)])
    p_old, p_new = tmp_path / "p_old.md", tmp_path / "p_new.md"
    p_old.write_text(PROFILE_OK)
    p_new.write_text(PROFILE_OK)
    out = tmp_path / "out.jsonl"
    assert run_diff(diff, monkeypatch, old, new, out,
                    extra=["--old-profile", str(p_old),
                           "--new-profile", str(p_new)]) == 0
    assert out.read_text() == ""  # UNCHANGED — 승계 유지
