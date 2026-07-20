import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).parent.parent / "skills/sole-search/scripts"))
import sbiz_crawl

FIX = pathlib.Path(__file__).parent / "fixtures"

REQUIRED_KEYS = [
    "source", "source_id", "canonical_url", "title", "agency",
    "apply_start", "apply_end", "status", "crawled_at",
]


def load(name):
    return json.loads((FIX / name).read_text())


def test_parse_list_returns_all_items_with_required_fields():
    data = load("sbiz24_list.json")
    items = sbiz_crawl.parse_list_page(data)
    assert len(items) == 10
    rec = sbiz_crawl.to_record(items[0], mode="pbanc")
    for key in REQUIRED_KEYS:
        assert rec.get(key) not in (None, ""), f"missing {key}"
    assert rec["source"] == "sbiz24"
    assert rec["canonical_url"].startswith("https://www.sbiz24.kr/#/pbanc/")


def test_parse_combine_list_uses_pbanc_id():
    data = load("sbiz24_combine_list.json")
    items = sbiz_crawl.parse_list_page(data)
    assert len(items) == 10
    rec = sbiz_crawl.to_record(items[0], mode="combine")
    for key in ["source", "source_id", "title", "agency", "status", "crawled_at"]:
        assert rec.get(key) not in (None, ""), f"missing {key}"
    assert rec["source"] == "sbiz24_combine"


def test_total_count_extracted():
    assert sbiz_crawl.total_count(load("sbiz24_list.json")) == 493
    assert sbiz_crawl.total_count(load("sbiz24_combine_list.json")) == 1291


def test_status_maps_to_enum():
    for fixture, mode in [("sbiz24_list.json", "pbanc"), ("sbiz24_combine_list.json", "combine")]:
        for item in sbiz_crawl.parse_list_page(load(fixture)):
            rec = sbiz_crawl.to_record(item, mode=mode)
            assert rec["status"] in {"접수중", "예산소진", "상시", "회차예정", "마감"}, rec["status"]


def test_detail_extracts_body_text():
    detail = sbiz_crawl.parse_detail(load("sbiz24_detail.json"))
    assert detail["source_id"] == "679"
    assert "정책자금" in detail["title"]
    assert "<p" not in detail["body_text"]
    assert len(detail["body_text"]) > 50


def test_attachments_parsed():
    files = sbiz_crawl.parse_files(load("sbiz24_files.json"), pbanc_sn="679")
    assert len(files) == 1
    f = files[0]
    assert f["filename"].endswith(".pdf")
    assert f["url"] == "https://www.sbiz24.kr/api/cmmn/file/" + f["file_id"]
