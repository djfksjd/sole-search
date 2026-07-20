import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).parent.parent / "skills/sole-search/scripts"))
import sources_crawl

FIX = pathlib.Path(__file__).parent / "fixtures"


def fixture_html():
    return (FIX / "bizinfo_page.html").read_text(encoding="utf-8", errors="replace")


def test_parse_page_extracts_15_items_with_fields():
    items, has_more = sources_crawl.parse_bizinfo_page(fixture_html())
    assert len(items) == 15
    it = items[0]
    assert it["source"] == "bizinfo"
    assert it["id"].startswith("PBLN_")
    assert it["title"]
    assert it["url"].startswith("https://www.bizinfo.go.kr/")


def test_last_page_parsed_from_pagination():
    assert sources_crawl.last_page(fixture_html()) >= 90


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
        return [{"id": i, "source": "bizinfo", "title": i, "url": "u"} for i in ids], bool(ids)

    items = sources_crawl.collect_all_pages(fake_fetch_page, max_page=10)
    assert [i["id"] for i in items] == ["a1", "a2", "a3"]
    assert len(calls) <= 4
