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


# ---------------- detail --download-dir (#11) ----------------

SSB_DETAIL_URL = ("https://www.seoulshinbo.co.kr/wbase/contents/bbs/view/5001.do"
                  "?mng_cd=STRY9788&pageIndex=1")


def make_hwpx_bytes(text="보증한도: 최대 5,000만원"):
    import zipfile
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("mimetype", "application/hwp+zip")
        z.writestr("Contents/section0.xml",
                   f'<hp:p xmlns:hp="urn:hwpx"><hp:t>{text}</hp:t></hp:p>')
    return buf.getvalue()


class FakeResponse:
    def __init__(self, body, final_url, filename=None):
        import email.message as _em
        self._bio = io.BytesIO(body)
        self._final = final_url
        self.headers = _em.Message()
        self.headers["Content-Length"] = str(len(body))
        if filename:
            self.headers["Content-Disposition"] = f'attachment; filename="{filename}"'

    def geturl(self):
        return self._final

    def read(self, n=-1):
        return self._bio.read(n)

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def _seed_ssb_list(tmp_path):
    rec = {"source": "seoulshinbo", "source_id": "ntc-5001", "title": "t",
           "content_hash": None, "attachments": [], "attachments_complete": False}
    (tmp_path / "ssb.jsonl").write_text(json.dumps(rec, ensure_ascii=False) + "\n")


def _detail_dl_args(tmp_path, download=True):
    return argparse.Namespace(
        urls=[SSB_DETAIL_URL], output=str(tmp_path / "details"),
        merge_into=str(tmp_path / "ssb.jsonl"),
        download_dir=str(tmp_path / "att") if download else None, delay=0.5)


def test_detail_download_stamps_hash_v3(monkeypatch, region, fixtures_dir, tmp_path):
    import hashlib
    html = (fixtures_dir / "ssb_detail.html").read_text()
    hwpx = make_hwpx_bytes()
    monkeypatch.setattr(region, "fetch", lambda url, data=None, retries=3: html)
    monkeypatch.setattr(urllib.request, "urlopen",
                        lambda req, timeout=60: FakeResponse(
                            hwpx, req.full_url, filename="공고문.hwpx"))
    _seed_ssb_list(tmp_path)
    rc = region.cmd_detail(_detail_dl_args(tmp_path))
    assert rc == 0
    r = json.loads((tmp_path / "ssb.jsonl").read_text().splitlines()[0])
    assert r["hash_version"] == 3
    assert r["attachments_complete"] is True
    a = r["attachments"][0]
    assert a["download_status"] == "ok" and a["extract_status"] == "ok"
    assert a["sha256"] == hashlib.sha256(hwpx).hexdigest()
    _, _, pat, _ = region.detail_target(SSB_DETAIL_URL)
    body = region.extract_body(html, pat)
    assert r["content_hash"] == region.content_hash_of(body, [a["sha256"]])
    assert "보증한도" in open(a["text_path"], encoding="utf-8").read()


def test_detail_without_download_keeps_v2_and_incomplete(monkeypatch, region,
                                                         fixtures_dir, tmp_path,
                                                         no_network):
    import hashlib
    html = (fixtures_dir / "ssb_detail.html").read_text()
    monkeypatch.setattr(region, "fetch", lambda url, data=None, retries=3: html)
    _seed_ssb_list(tmp_path)
    rc = region.cmd_detail(_detail_dl_args(tmp_path, download=False))
    assert rc == 0
    r = json.loads((tmp_path / "ssb.jsonl").read_text().splitlines()[0])
    assert r["hash_version"] == 2
    assert r["attachments_complete"] is False  # 링크만 수집 — 미추출
    _, _, pat, _ = region.detail_target(SSB_DETAIL_URL)
    body = region.extract_body(html, pat)
    assert r["content_hash"] == hashlib.sha256(body.encode()).hexdigest()


def test_detail_download_redirect_to_foreign_host_keeps_v2(monkeypatch, region,
                                                           fixtures_dir, tmp_path):
    """첨부 302→외부 호스트: 요청이 나가지 않고 blocked_redirect,
    본문 v2 해시 유지 + exit 2 (bizinfo 슬라이스와 동일 계약)."""
    import hashlib
    html = (fixtures_dir / "ssb_detail.html").read_text()
    requested = []

    def fake_urlopen(req, timeout=60):
        requested.append(req.full_url)
        raise make_redirect(req.full_url, "https://evil.example/f.hwpx")

    monkeypatch.setattr(region, "fetch", lambda url, data=None, retries=3: html)
    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    _seed_ssb_list(tmp_path)
    rc = region.cmd_detail(_detail_dl_args(tmp_path))
    assert rc == 2  # 첨부 미검증 — partial
    assert requested and all("evil.example" not in u for u in requested)
    r = json.loads((tmp_path / "ssb.jsonl").read_text().splitlines()[0])
    assert r["attachments"][0]["download_status"] == "blocked_redirect"
    assert r["attachments_complete"] is False
    _, _, pat, _ = region.detail_target(SSB_DETAIL_URL)
    body = region.extract_body(html, pat)
    assert r["content_hash"] == hashlib.sha256(body.encode()).hexdigest()
    assert r["hash_version"] == 2


def test_detail_download_cross_source_host_blocked(monkeypatch, region, fixtures_dir,
                                                   tmp_path):
    """서울신보 상세의 첨부가 fanfandaero로 넘어가려 해도 차단 — 소스별 허용 호스트."""
    html = (fixtures_dir / "ssb_detail.html").read_text()
    requested = []

    def fake_urlopen(req, timeout=60):
        requested.append(req.full_url)
        raise make_redirect(req.full_url, "https://fanfandaero.kr/download.do?fileName=x")

    monkeypatch.setattr(region, "fetch", lambda url, data=None, retries=3: html)
    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    _seed_ssb_list(tmp_path)
    assert region.cmd_detail(_detail_dl_args(tmp_path)) == 2
    assert all("fanfandaero" not in u for u in requested)


def test_ssb_attachment_url_contract(region, fixtures_dir):
    """첨부 URL은 sources.md §7 계약(/download/{bno}/{serial}.do)을 따라야 한다."""
    html = (fixtures_dir / "ssb_detail.html").read_text()
    out = region.ssb_attachments(html, SSB_DETAIL_URL)
    assert len(out) == 1
    assert out[0]["url"] == ("https://www.seoulshinbo.co.kr/download/5001/"
                             "ab12cd34-5678-90ef-ab12-cd3456789012.do?mng_cd=STRY9788")
    assert out[0]["filename"] == "공고문.hwpx"


def test_hash_v3_formula_matches_other_sources(region, sources, sbiz):
    assert region.content_hash_of("본문", ["b", "a"]) == \
        sources.content_hash_of("본문", ["a", "b"]) == \
        sbiz.content_hash_of("본문", ["a", "b"])
    assert region.HASH_VERSION_ATTACH == 3


# ---------------- --max-pages smoke 계약 (#10) ----------------

def test_ssb_max_pages_caps_requests(monkeypatch, region, fixtures_dir, tmp_path,
                                     no_network):
    html = (fixtures_dir / "ssb_list.html").read_text()
    calls = []

    def fake_fetch(url, data=None, retries=3):
        calls.append(url)
        return html

    monkeypatch.setattr(region, "fetch", fake_fetch)
    args = argparse.Namespace(output=str(tmp_path / "ssb.jsonl"), since=None,
                              delay=0.5, max_pages=1)
    assert region.cmd_list_ssb(args) == 0
    assert len(calls) == 1  # 첫 페이지만


def test_fanfan_max_pages_skips_year_loop_and_caps_board(monkeypatch, region,
                                                         fixtures_dir, tmp_path,
                                                         no_network):
    biz_json = (fixtures_dir / "fanfan_biz.json").read_text()
    ntc_html = (fixtures_dir / "fanfan_ntc.html").read_text()
    calls = []

    def fake_fetch(url, data=None, retries=3):
        calls.append((url, data))
        if "selectSupportInfoListAjax" in url:
            return biz_json
        return ntc_html

    monkeypatch.setattr(region, "fetch", fake_fetch)
    args = argparse.Namespace(output=str(tmp_path / "fanfan.jsonl"), since=None,
                              delay=0.5, max_pages=1)
    assert region.cmd_list_fanfan(args) == 0
    ajax_calls = [c for c in calls if "selectSupportInfoListAjax" in c[0]]
    board_calls = [c for c in calls if "readUcenterNtcBbs" in c[0]]
    assert len(ajax_calls) == 1  # 연도 순회 없음
    assert len(board_calls) == 1  # 게시판 첫 페이지만


def test_detail_multi_url_same_download_dir_no_clobber(monkeypatch, region,
                                                       fixtures_dir, tmp_path):
    """여러 공고를 같은 --download-dir로 처리해도 동명 첨부가 서로를 덮지 않는다
    — 레코드별 sha256/local_path와 실제 파일 내용이 일치해야 한다 (Codex NO-GO 4)."""
    import hashlib
    import pathlib
    html1 = (fixtures_dir / "ssb_detail.html").read_text()
    html2 = html1.replace("view/5001.do", "view/5002.do")  # 같은 첨부 파일명
    url2 = SSB_DETAIL_URL.replace("view/5001.do", "view/5002.do")
    bodies = {"5001": make_hwpx_bytes("공고 5001 본문"),
              "5002": make_hwpx_bytes("공고 5002 본문")}

    monkeypatch.setattr(region, "fetch",
                        lambda url, data=None, retries=3:
                        html2 if "5002" in url else html1)

    def fake_urlopen(req, timeout=60):
        bno = "5002" if "/5002/" in req.full_url else "5001"
        return FakeResponse(bodies[bno], req.full_url, filename="공고문.hwpx")

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    recs = [{"source": "seoulshinbo", "source_id": f"ntc-{b}", "title": "t",
             "content_hash": None, "attachments": [], "attachments_complete": False}
            for b in ("5001", "5002")]
    (tmp_path / "ssb.jsonl").write_text(
        "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in recs))
    args = argparse.Namespace(
        urls=[SSB_DETAIL_URL, url2], output=str(tmp_path / "details"),
        merge_into=str(tmp_path / "ssb.jsonl"),
        download_dir=str(tmp_path / "att"), delay=0.5)
    assert region.cmd_detail(args) == 0
    merged = {json.loads(l)["source_id"]: json.loads(l)
              for l in (tmp_path / "ssb.jsonl").read_text().splitlines()}
    a1 = merged["ntc-5001"]["attachments"][0]
    a2 = merged["ntc-5002"]["attachments"][0]
    assert a1["local_path"] != a2["local_path"]  # 공고별 폴더 분리
    for a, bno in ((a1, "5001"), (a2, "5002")):
        data = pathlib.Path(a["local_path"]).read_bytes()
        assert data == bodies[bno]  # 파일 내용이 해당 레코드의 것 그대로
        assert a["sha256"] == hashlib.sha256(bodies[bno]).hexdigest()
    assert merged["ntc-5001"]["content_hash"] != merged["ntc-5002"]["content_hash"]
    assert all(m["hash_version"] == 3 for m in merged.values())
