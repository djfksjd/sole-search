"""attach_download 공용 슬라이스 — sources/region이 공유하는 보안 계약 단위 검증."""
import importlib

import pytest

ad = importlib.import_module("attach_download")


def test_shared_module_is_single_source(sources, region):
    """두 크롤러가 같은 슬라이스 객체를 쓰는지 — 복제본 divergence 방지 회귀."""
    assert sources.download_attachment.__module__ == "sources_crawl"  # 얇은 위임
    assert sources.host_allowed is ad.host_allowed
    assert region.ManualEscalation is ad.ManualEscalation
    assert region.RedirectBlocked is ad.RedirectBlocked


@pytest.mark.parametrize("url,ok", [
    ("https://www.seoulshinbo.co.kr/download/1/a.do", True),
    ("https://seoulshinbo.co.kr/download/1/a.do", True),
    ("https://evilseoulshinbo.co.kr/x", False),
    ("https://seoulshinbo.co.kr.evil.example/x", False),
    ("https://seoulshinbo.co.kr@evil.example/x", False),
    ("http://www.seoulshinbo.co.kr/x", False),
    ("https://evil.example/?next=seoulshinbo.co.kr", False),
])
def test_host_allowed(url, ok):
    assert ad.host_allowed(url, ("seoulshinbo.co.kr",)) is ok


def test_fix_mojibake_roundtrip():
    assert ad.fix_mojibake("공고문.hwpx".encode("utf-8").decode("latin-1")) == "공고문.hwpx"
    assert ad.fix_mojibake("plain.pdf") == "plain.pdf"
    assert ad.fix_mojibake("%EA%B3%B5%EA%B3%A0.pdf") == "공고.pdf"


@pytest.mark.parametrize("name,expect", [
    ("../../etc/passwd", "00_passwd"),
    ("..\\..\\win.ini", "00_win.ini"),
    ("공고문 (최종).hwp", "00_공고문 (최종).hwp"),
    ("", "00_attach"),
])
def test_safe_filename_neutralizes_paths(name, expect):
    assert ad.safe_filename(name, 0) == expect
