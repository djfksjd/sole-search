"""sbiz_crawl 목록 계약 + merge_detail hash_version 스탬프 (네트워크 없음)."""
import argparse
import json

import pytest


@pytest.fixture
def pages(fixtures_dir):
    return {
        0: json.loads((fixtures_dir / "sbiz_pbanc_page1.json").read_text()),
        2: json.loads((fixtures_dir / "sbiz_pbanc_page2.json").read_text()),
    }


def _patch_post(monkeypatch, sbiz, pages):
    def fake_post(path, body, retries=3, delay=0.5):
        assert path == "/api/pbanc/sbiz24PbancList"
        start = body["startRow"]
        if start in pages:
            return pages[start]
        return {"result": True, "data": {"default": {"total": 3, "list": []}}}
    monkeypatch.setattr(sbiz, "post", fake_post)


def test_list_parses_records(monkeypatch, sbiz, pages, tmp_path, no_network):
    _patch_post(monkeypatch, sbiz, pages)
    out = tmp_path / "sbiz24.jsonl"
    with open(out, "w", encoding="utf-8") as fh:
        total, collected, fetched = sbiz.crawl_list("pbanc", fh, page_size=2, delay=0.5)
    assert (total, collected, fetched) == (3, 2, 3)
    recs = [json.loads(l) for l in out.read_text().splitlines()]
    r = recs[0]
    assert r["source"] == "sbiz24"
    assert r["source_id"] == "101"
    assert r["title"] == "소상공인 시설개선 지원"
    assert r["canonical_url"] == "https://www.sbiz24.kr/#/pbanc/101"
    assert r["agency"] == "소상공인시장진흥공단"
    assert (r["apply_start"], r["apply_end"]) == ("2026-07-01", "2027-08-01")
    assert r["status"] == "접수중"
    assert r["tags"] == ["시설개선", "보조금"]
    assert r["attachments"] == [] and r["attachments_complete"] is False
    assert recs[1]["status"] == "마감"


def test_list_duplicates_offsetting_missing_is_partial(monkeypatch, sbiz, pages,
                                                       tmp_path, no_network, capsys):
    """fetched==total이어도 중복이 누락을 상쇄하면(고유<총건수) partial(exit 2)."""
    _patch_post(monkeypatch, sbiz, pages)
    args = argparse.Namespace(target="pbanc", output=str(tmp_path / "out.jsonl"),
                              page_size=2, delay=0.5)
    rc = sbiz.cmd_list(args)
    assert rc == 2
    err = capsys.readouterr().err
    assert "TOTAL 3 COLLECTED 2 DUPLICATES 1" in err
    assert "누락 가능" in err


def test_list_zero_collected_is_failed(monkeypatch, sbiz, tmp_path, no_network):
    _patch_post(monkeypatch, sbiz, {})  # 모든 페이지 빈 목록 (total=3)
    args = argparse.Namespace(target="pbanc", output=str(tmp_path / "out.jsonl"),
                              page_size=2, delay=0.5)
    assert sbiz.cmd_list(args) == 2


def _write_jsonl(path, records):
    with open(path, "w", encoding="utf-8") as fh:
        for r in records:
            fh.write(json.dumps(r, ensure_ascii=False) + "\n")


def test_merge_detail_stamps_hash_version_2(sbiz, tmp_path):
    p = tmp_path / "sbiz24.jsonl"
    _write_jsonl(p, [{"source": "sbiz24", "source_id": "101", "title": "t",
                      "content_hash": None, "attachments": [],
                      "attachments_complete": False}])
    assert sbiz.merge_detail(str(p), "101", "deadbeef", [{"file_id": "f"}], True)
    r = json.loads(p.read_text().splitlines()[0])
    assert r["content_hash"] == "deadbeef"
    assert r["hash_version"] == 2
    assert r["attachments_complete"] is True


def test_merge_detail_none_hash_does_not_stamp_version(sbiz, tmp_path):
    p = tmp_path / "sbiz24.jsonl"
    _write_jsonl(p, [{"source": "sbiz24", "source_id": "101", "title": "t"}])
    assert sbiz.merge_detail(str(p), "101", None, [], False)
    r = json.loads(p.read_text().splitlines()[0])
    assert r["content_hash"] is None
    assert "hash_version" not in r


def test_merge_detail_none_hash_removes_stale_version(sbiz, tmp_path):
    """기존 hash_version 있는 레코드에 None 병합 → 낡은 version도 제거해야 한다.
    {content_hash: null, hash_version: 2} 오염 금지."""
    p = tmp_path / "sbiz24.jsonl"
    _write_jsonl(p, [{"source": "sbiz24", "source_id": "101", "title": "t",
                      "content_hash": "deadbeef", "hash_version": 2,
                      "attachments": [], "attachments_complete": True}])
    assert sbiz.merge_detail(str(p), "101", None, [], False)
    r = json.loads(p.read_text().splitlines()[0])
    assert r["content_hash"] is None
    assert "hash_version" not in r
