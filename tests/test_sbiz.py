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


# ---------------- combine 상세 (#7 스파이크 반영, fail-closed 게이트) ----------------

def _detail_response(sn, title, body="<p>본문 내용</p>"):
    return {"result": True, "data": {"default": {
        "pbancSn": sn, "pbancNm": title, "pbancDtlCn": body,
        "rcptPd": {"from": "2026-07-01", "to": "2026-07-31"}, "pbancSttsCd": "P"}}}


def _files_response(files=()):
    return {"result": True, "data": {"default": {"list": list(files)}}}


def _combine_rec(sn, title, biz_type="지방정부사업", gubun="P"):
    return {"source": "sbiz24_combine", "source_id": str(sn), "title": title,
            "canonical_url": f"https://www.sbiz24.kr/#/pbanc/{sn}",
            "raw": {"bizType": biz_type, "pbancKindCd": gubun},
            "content_hash": None, "attachments": [], "attachments_complete": False}


def _detail_args(tmp_path, sn, source="sbiz24_combine", merge=True):
    return argparse.Namespace(
        pbanc_sn=str(sn), source=source, output=str(tmp_path / "detail.json"),
        download_dir=None, delay=0.5,
        merge_into=str(tmp_path / "combine.jsonl") if merge else None)


def test_combine_detail_requires_merge_into(sbiz, tmp_path, no_network, capsys):
    """fail-closed: 목록 레코드 없이는 대출상품 네임스페이스 충돌을 판정할 수 없다."""
    rc = sbiz.cmd_detail(_detail_args(tmp_path, 799, merge=False))
    assert rc == 2
    assert "--merge-into" in capsys.readouterr().err


def test_combine_detail_refuses_loan_product(sbiz, tmp_path, no_network, capsys):
    """대출상품(bizType)은 /loanProduct 별도 네임스페이스 — pbanc API로 읽으면
    같은 숫자의 다른 공고를 읽는다. 네트워크 요청 없이 거부해야 한다."""
    _write_jsonl(tmp_path / "combine.jsonl",
                 [_combine_rec(413, "미소금융 재기자금", biz_type="대출상품")])
    rc = sbiz.cmd_detail(_detail_args(tmp_path, 413))
    assert rc == 2
    assert "대출상품" in capsys.readouterr().err


def test_combine_detail_missing_record_fail_closed(sbiz, tmp_path, no_network):
    _write_jsonl(tmp_path / "combine.jsonl", [_combine_rec(1, "다른 공고")])
    assert sbiz.cmd_detail(_detail_args(tmp_path, 799)) == 2


def test_combine_detail_title_mismatch_fail_closed(monkeypatch, sbiz, tmp_path,
                                                   no_network, capsys):
    """상세 응답 제목이 목록 제목과 다르면 네임스페이스 불일치 — 기록하지 않는다."""
    _write_jsonl(tmp_path / "combine.jsonl", [_combine_rec(413, "목록의 제목")])

    def fake_post(path, body, retries=3, delay=0.5):
        assert path == "/api/pbanc/413"
        return _detail_response(413, "전혀 다른 공고 제목")

    monkeypatch.setattr(sbiz, "post", fake_post)
    rc = sbiz.cmd_detail(_detail_args(tmp_path, 413))
    assert rc == 2
    assert "불일치" in capsys.readouterr().err
    # 목록 레코드는 오염되지 않아야 한다
    r = json.loads((tmp_path / "combine.jsonl").read_text().splitlines()[0])
    assert r["content_hash"] is None


def test_combine_detail_success_merges_as_combine_source(monkeypatch, sbiz, tmp_path,
                                                         no_network):
    """비PBLN·비대출 combine 레코드는 pbanc 상세 API로 읽고 combine 레코드에 병합한다.
    제목 비교는 HTML 엔티티·공백 정규화 후 수행한다."""
    title = "2026년 소상공인 지원(추가) 공고"
    _write_jsonl(tmp_path / "combine.jsonl", [_combine_rec(799, title)])

    def fake_post(path, body, retries=3, delay=0.5):
        if path == "/api/pbanc/799":
            return _detail_response(799, "2026년 소상공인 지원&#40;추가&#41;  공고")
        assert path == "/api/cmmn/file"
        return _files_response()

    monkeypatch.setattr(sbiz, "post", fake_post)
    rc = sbiz.cmd_detail(_detail_args(tmp_path, 799))
    assert rc == 0
    r = json.loads((tmp_path / "combine.jsonl").read_text().splitlines()[0])
    assert r["source"] == "sbiz24_combine"
    assert r["content_hash"] == sbiz.content_hash_of("본문 내용", [])
    assert r["hash_version"] == 2
    assert r["attachments_complete"] is True  # 첨부 없음
    detail = json.loads((tmp_path / "detail.json").read_text())
    assert detail["source_id"] == "799"


def test_pbln_detail_still_redirects_to_bizinfo(sbiz, tmp_path, no_network, capsys):
    rc = sbiz.cmd_detail(_detail_args(tmp_path, "PBLN_000000000124620"))
    assert rc == 2
    assert "bizinfo" in capsys.readouterr().err


def test_combine_to_record_keeps_pbanc_gubun(sbiz):
    rec = sbiz.to_record({"pbancSn": 799, "pbancNm": "t", "departNm": "제주",
                          "aplyPd": "2026-07-13 ~ 2026-07-24",
                          "bizType": "지방정부사업", "pbancGubun": "D",
                          "aplyPsbltySe": "신청가능"}, "combine")
    assert rec["raw"]["pbancGubun"] == "D"
    assert rec["raw"]["bizType"] == "지방정부사업"


# ---------------- --max-pages smoke 계약 (#10) ----------------

def test_list_max_pages_caps_requests_and_skips_coverage(monkeypatch, sbiz, pages,
                                                         tmp_path, no_network, capsys):
    calls = []

    def fake_post(path, body, retries=3, delay=0.5):
        calls.append(body["startRow"])
        return pages.get(body["startRow"],
                         {"result": True, "data": {"default": {"total": 3, "list": []}}})

    monkeypatch.setattr(sbiz, "post", fake_post)
    args = argparse.Namespace(target="pbanc", output=str(tmp_path / "out.jsonl"),
                              page_size=2, delay=0.5, max_pages=1)
    rc = sbiz.cmd_list(args)
    assert rc == 0  # smoke: 첫 페이지만 — coverage 미달을 실패로 치지 않는다
    assert calls == [0]  # 두 번째 페이지 요청 없음
