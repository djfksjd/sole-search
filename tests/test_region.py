"""region_crawl(판판대로·서울신보) — 목록 파싱, 첫 페이지 0건 partial, detail_target 검증."""
import argparse
import email.message
import io
import json
import urllib.error
import urllib.request

import pytest


def make_redirect(url, location, code=302):
    hdrs = email.message.Message()
    if location:
        hdrs["Location"] = location
    return urllib.error.HTTPError(url, code, "Found", hdrs, io.BytesIO(b""))


class FakeBody:
    def __init__(self, body):
        self._bio = io.BytesIO(body)

    def read(self, n=-1):
        return self._bio.read(n)

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


# ---------------- 판판대로 ----------------

def test_parse_fanfan_biz(region, fixtures_dir):
    payload = json.loads((fixtures_dir / "fanfan_biz.json").read_text())
    items = region.parse_fanfan_biz(payload)
    assert len(items) == 1
    r = items[0]
    assert r["source"] == "fanfandaero"
    assert r["source_id"] == "biz-2026001"
    assert r["canonical_url"].endswith("sprtBizCd=2026001")
    assert r["title"] == "온라인판로 지원사업"
    assert (r["apply_start"], r["apply_end"]) == ("2026-07-01", "2027-08-01")
    assert r["status"] == "접수중"
    assert r["tags"] == ["판로"]


def test_parse_fanfan_ntc(region, fixtures_dir):
    h = (fixtures_dir / "fanfan_ntc.html").read_text()
    items = region.parse_fanfan_ntc(h)
    assert [i["source_id"] for i in items] == ["ntc-9001", "ntc-9002"]
    r = items[0]
    assert r["title"] == "2026년 소상공인 라이브커머스 참여기업 모집 공고"
    assert r["canonical_url"].endswith("nttId=9001")
    assert r["raw"]["posted"] == "2026-07-10"
    assert r["status"] == "불명"  # 접수기간이 목록에 없음 — 상세 확인 대상
    assert region.fanfan_ntc_total(h) == 2


def test_fanfan_board_first_page_zero_is_partial(monkeypatch, region, fixtures_dir,
                                                 tmp_path, no_network):
    biz_json = (fixtures_dir / "fanfan_biz.json").read_text()

    def fake_fetch(url, data=None, retries=3):
        if "selectSupportInfoListAjax" in url:
            return biz_json
        return "<html><body>구조 변경</body></html>"  # 게시판 0건

    monkeypatch.setattr(region, "fetch", fake_fetch)
    args = argparse.Namespace(output=str(tmp_path / "fanfan.jsonl"),
                              since=None, delay=0.5)
    assert region.cmd_list_fanfan(args) == 2
    # biz 수집분은 조용히 버리지 않고 저장된다
    recs = [json.loads(l) for l in (tmp_path / "fanfan.jsonl").read_text().splitlines()]
    assert [r["source_id"] for r in recs] == ["biz-2026001"]


# ---------------- 서울신보 ----------------

def test_parse_ssb(region, fixtures_dir):
    h = (fixtures_dir / "ssb_list.html").read_text()
    items = region.parse_ssb(h)
    assert [i["source_id"] for i in items] == ["ntc-5001", "ntc-5002"]
    r = items[0]
    assert r["source"] == "seoulshinbo"
    assert r["title"] == "2026년 서울 소상공인 특별보증 공고"
    assert r["canonical_url"].endswith("/bbs/view/5001.do?mng_cd=STRY9788&pageIndex=1")
    assert r["agency"] == "서울신용보증재단/보증지원부"
    assert r["region_scope"] == "서울"
    assert r["raw"]["posted"] == "2026-07-15"


def test_ssb_first_page_zero_is_partial(monkeypatch, region, tmp_path, no_network):
    monkeypatch.setattr(region, "fetch",
                        lambda url, data=None, retries=3: "<html><body></body></html>")
    args = argparse.Namespace(output=str(tmp_path / "ssb.jsonl"), since=None, delay=0.5)
    assert region.cmd_list_ssb(args) == 2


# ---------------- detail_target host 검증 회귀 ----------------

@pytest.mark.parametrize("url", [
    "https://evil.example/?next=fanfandaero.kr&nttId=123",   # 쿼리스트링 위장
    "https://fanfandaero.kr.evil.example/portal?nttId=1",    # 서브도메인 위장
    "https://fanfandaero.kr@evil.example/portal?nttId=1",    # userinfo 위장
    "http://fanfandaero.kr/portal/v2/readUcenterNtcBbsView.do?nttId=1",  # http
    "https://evilseoulshinbo.co.kr/wbase/contents/bbs/view/1.do",
])
def test_detail_target_rejects_disguised(region, url):
    assert region.detail_target(url) == (None, None, None, None)


# ---------------- 리다이렉트 사전 검증 (P0) ----------------

def test_fetch_redirect_to_foreign_host_is_not_requested(monkeypatch, region):
    """302 Location이 외부 호스트면 외부로 요청이 나가지 않아야 한다."""
    requested = []

    def fake_urlopen(req, timeout=30):
        requested.append(req.full_url)
        raise make_redirect(req.full_url, "https://evil.example/steal")

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    with pytest.raises(RuntimeError, match="불허"):
        region.fetch("https://fanfandaero.kr/portal/v2/readUcenterNtcBbs.do",
                     data={"pageIndex": 1}, retries=1)
    assert requested == ["https://fanfandaero.kr/portal/v2/readUcenterNtcBbs.do"]


def test_fetch_cross_source_redirect_is_blocked(monkeypatch, region):
    """허용 소스라도 다른 소스로의 리다이렉트는 차단 — 소스별 허용 호스트."""
    requested = []

    def fake_urlopen(req, timeout=30):
        requested.append(req.full_url)
        raise make_redirect(req.full_url,
                            "https://www.seoulshinbo.co.kr/wbase/contents/bbs/view/1.do")

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    with pytest.raises(RuntimeError, match="불허"):
        region.fetch("https://fanfandaero.kr/portal/v2/readUcenterNtcBbs.do", retries=1)
    assert requested == ["https://fanfandaero.kr/portal/v2/readUcenterNtcBbs.do"]


def test_fetch_follows_allowed_redirect_chain(monkeypatch, region):
    """같은 소스 내부 리다이렉트(상대 Location 포함)는 수동 추적해 성공한다."""
    requested = []

    def fake_urlopen(req, timeout=30):
        requested.append(req.full_url)
        if len(requested) == 1:
            raise make_redirect(req.full_url, "/wbase/contents/bbs/list2.do")
        return FakeBody("<html>final ok</html>".encode())

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    body = region.fetch("https://www.seoulshinbo.co.kr/wbase/contents/bbs/list.do")
    assert "final ok" in body
    assert requested == ["https://www.seoulshinbo.co.kr/wbase/contents/bbs/list.do",
                         "https://www.seoulshinbo.co.kr/wbase/contents/bbs/list2.do"]


def test_detail_target_accepts_real_urls(region):
    src, sid, pat, fn = region.detail_target(
        "https://fanfandaero.kr/portal/v2/readUcenterNtcBbsView.do?nttId=9001")
    assert (src, sid) == ("fanfandaero", "ntc-9001") and pat and fn
    src, sid, pat, fn = region.detail_target(
        "https://www.seoulshinbo.co.kr/wbase/contents/bbs/view/5001.do"
        "?mng_cd=STRY9788&pageIndex=1")
    assert (src, sid) == ("seoulshinbo", "ntc-5001") and pat and fn
