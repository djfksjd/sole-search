"""리뷰 블로커 1·3·4 회귀: MANUAL 전파·merge 미발견·총건수 대조 — fetch 모킹 CLI 테스트."""
import json
import pathlib
import sys

import pytest

SCRIPTS = pathlib.Path(__file__).parent.parent / "skills/sole-search/scripts"
sys.path.insert(0, str(SCRIPTS))
import sbiz_crawl
import sources_crawl

FIX = pathlib.Path(__file__).parent / "fixtures"


class Args:
    def __init__(self, **kw):
        self.__dict__.update(kw)


def test_sbiz_list_manual_escalation_propagates(monkeypatch, tmp_path, capsys):
    def blocked(path, body, **kw):
        raise sbiz_crawl.ManualEscalation("HTTP 403")
    monkeypatch.setattr(sbiz_crawl, "post", blocked)
    args = Args(target="pbanc", output=str(tmp_path / "o.jsonl"), page_size=100, delay=0.5)
    with pytest.raises(sbiz_crawl.ManualEscalation):
        sbiz_crawl.cmd_list(args)  # main()이 잡아 exit 3으로 변환


def test_bizinfo_midpage_manual_returns_3(monkeypatch, tmp_path):
    first = (FIX / "bizinfo_page.html").read_text(encoding="utf-8", errors="replace")

    def fake_fetch(url, retries=3):
        if "cpage=1&" in url or url.endswith("cpage=1"):
            return first
        raise sources_crawl.ManualEscalation("HTTP 403")
    monkeypatch.setattr(sources_crawl, "fetch", fake_fetch)
    args = Args(output=str(tmp_path / "b.jsonl"), delay=0.5)
    assert sources_crawl.cmd_list(args) == 3


def test_bizinfo_first_page_manual_returns_3(monkeypatch, tmp_path):
    def fake_fetch(url, retries=3):
        raise sources_crawl.ManualEscalation("차단")
    monkeypatch.setattr(sources_crawl, "fetch", fake_fetch)
    args = Args(output=str(tmp_path / "b.jsonl"), delay=0.5)
    assert sources_crawl.cmd_list(args) == 3


def test_bizinfo_total_count_parsed_from_fixture():
    first = (FIX / "bizinfo_page.html").read_text(encoding="utf-8", errors="replace")
    assert sources_crawl.parse_total_count(first) == 1435


def test_bizinfo_collected_vs_expected_mismatch_is_partial(monkeypatch, tmp_path):
    # 총건수 1435인데 1페이지 15건만 수집되는 상황 → success 금지 (exit 2)
    first = (FIX / "bizinfo_page.html").read_text(encoding="utf-8", errors="replace")

    def fake_fetch(url, retries=3):
        return first if ("cpage=1&" in url or url.endswith("cpage=1")) else "<html></html>"
    monkeypatch.setattr(sources_crawl, "fetch", fake_fetch)
    args = Args(output=str(tmp_path / "b.jsonl"), delay=0.5)
    rc = sources_crawl.cmd_list(args)
    assert rc == 2


def test_sbiz_merge_into_not_found_returns_2(monkeypatch, tmp_path):
    detail = json.loads((FIX / "sbiz24_detail.json").read_text())
    files = json.loads((FIX / "sbiz24_files.json").read_text())

    def fake_post(path, body, **kw):
        return detail if path.startswith("/api/pbanc/") else files
    monkeypatch.setattr(sbiz_crawl, "post", fake_post)
    monkeypatch.setattr(sbiz_crawl.time, "sleep", lambda s: None)
    jsonl = tmp_path / "sbiz24.jsonl"
    jsonl.write_text(json.dumps({"source": "sbiz24", "source_id": "999",
                                 "title": "다른 공고"}) + "\n")
    args = Args(pbanc_sn="679", output=None, download_dir=None,
                merge_into=str(jsonl), delay=0.5)
    assert sbiz_crawl.cmd_detail(args) == 2


def test_merge_requires_matching_source(tmp_path):
    # (source, source_id) 복합 키 — combine 레코드는 sbiz24 병합에 걸리면 안 된다
    rec = {"source": "sbiz24_combine", "source_id": "679", "title": "t",
           "content_hash": None, "attachments": [], "attachments_complete": False}
    p = tmp_path / "all.jsonl"
    p.write_text(json.dumps(rec, ensure_ascii=False) + "\n")
    assert sbiz_crawl.merge_detail(str(p), "679", "h", [], True, source="sbiz24") is False


def test_hwp_attachment_extract_fail_means_incomplete(monkeypatch, tmp_path):
    # 리뷰 블로커 2 회귀: 다운로드 성공 + 추출 실패(HWP) → attachments_complete=False
    detail = json.loads((FIX / "sbiz24_detail.json").read_text())
    files = json.loads((FIX / "sbiz24_files.json").read_text())
    files["data"]["default"]["list"][0]["fileNm"] = "공고문.hwp"

    def fake_post(path, body, **kw):
        return detail if path.startswith("/api/pbanc/") else files

    def fake_download(url, path):
        path.write_bytes(bytes.fromhex("D0CF11E0A1B11AE1") + b"\x00" * 32)
        return 40
    monkeypatch.setattr(sbiz_crawl, "post", fake_post)
    monkeypatch.setattr(sbiz_crawl, "download_capped", fake_download)
    monkeypatch.setattr(sbiz_crawl.time, "sleep", lambda s: None)
    out = tmp_path / "d.json"
    args = Args(pbanc_sn="679", output=str(out), download_dir=str(tmp_path / "att"),
                merge_into=None, delay=0.5)
    assert sbiz_crawl.cmd_detail(args) == 0
    result = json.loads(out.read_text())
    assert result["attachments_complete"] is False
    att = result["attachments"][0]
    assert att["download_status"] == "ok"
    assert att["extract_status"] == "unsupported"
    assert att["extract_reason"] == "hwp_binary_unsupported"
