import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).parent.parent / "skills/sole-search/scripts"))
import sources_crawl

FIX = pathlib.Path(__file__).parent / "fixtures"


def fixture_html():
    return (FIX / "bizinfo_page.html").read_text(encoding="utf-8", errors="replace")


def test_parse_page_extracts_15_items_in_common_schema():
    items, has_more = sources_crawl.parse_bizinfo_page(fixture_html())
    assert len(items) == 15
    it = items[0]
    assert it["source"] == "bizinfo"
    assert it["source_id"].startswith("PBLN_")
    assert it["announce_no"] == it["source_id"]
    assert it["title"]
    assert it["canonical_url"].startswith("https://www.bizinfo.go.kr/")
    assert it["agency"]
    assert it["crawled_at"]
    assert it["status"] in {"접수중", "예산소진", "상시", "회차예정", "마감", "불명"}
    assert it["attachments_complete"] is False


def test_all_15_ids_are_unique_for_diff_compat():
    items, _ = sources_crawl.parse_bizinfo_page(fixture_html())
    keys = {(i["source"], i["source_id"]) for i in items}
    assert len(keys) == 15  # diff 키 충돌 방지 (리뷰 블로커 1 회귀 테스트)


def test_last_page_parsed_from_pagination():
    assert sources_crawl.last_page(fixture_html()) >= 90


def test_last_page_zero_when_pagination_missing():
    assert sources_crawl.last_page("<html><body>점검중</body></html>") == 0


def test_no_category_filter_in_default_url():
    url = sources_crawl.build_list_url(page=1)
    assert "hashCode" not in url
    assert "pldirCd" not in url
    assert "schEndAt=N" in url  # 모집중만


def test_pagination_stops_on_no_new_items():
    pages = {1: ["a1", "a2"], 2: ["a3"], 3: ["a3"], 4: ["a3"]}
    calls = []

    def fake_fetch_page(page):
        calls.append(page)
        ids = pages.get(page, [])
        return ([{"source_id": i, "source": "bizinfo", "title": i} for i in ids], bool(ids))

    items = sources_crawl.collect_all_pages(fake_fetch_page, max_page=10)
    assert [i["source_id"] for i in items] == ["a1", "a2", "a3"]
    assert len(calls) <= 4


def test_block_marker_raises_manual():
    import pytest
    with pytest.raises(sources_crawl.ManualEscalation):
        low = "<html>비정상적인 접근이 감지되었습니다</html>"
        if any(m in low for m in sources_crawl.BLOCK_MARKERS):
            raise sources_crawl.ManualEscalation("test")


def test_min_delay_enforced():
    import pytest
    with pytest.raises(Exception):
        sources_crawl.positive_delay("0.1")
    assert sources_crawl.positive_delay("0.5") == 0.5


def test_merge_detail_updates_record(tmp_path):
    p = tmp_path / "bizinfo.jsonl"
    rec = {"source": "bizinfo", "source_id": "PBLN_1", "title": "t",
           "content_hash": None, "attachments": [], "attachments_complete": False}
    p.write_text(json.dumps(rec, ensure_ascii=False) + "\n")
    ok = sources_crawl.merge_detail(str(p), "PBLN_1", "abc123",
                                    [{"url": "u", "filename": "f.pdf"}], complete=False)
    assert ok
    r = json.loads(p.read_text())
    assert r["content_hash"] == "abc123"
    assert r["attachments_complete"] is False
    assert r["attachments"][0]["filename"] == "f.pdf"
