"""sources_crawl(기업마당) — 목록 파싱, fail-closed, host 검증, 첨부 vertical slice."""
import argparse
import email.message
import hashlib
import io
import json
import urllib.error
import urllib.request
import zipfile

import pytest


def make_redirect(url, location, code=302):
    """자동 리다이렉트 비활성 시 urlopen이 던지는 3xx HTTPError를 흉내낸다."""
    hdrs = email.message.Message()
    if location:
        hdrs["Location"] = location
    return urllib.error.HTTPError(url, code, "Found", hdrs, io.BytesIO(b""))


# ---------------- 목록 파싱 ----------------

def test_parse_list_fields(sources, fixtures_dir):
    h = (fixtures_dir / "bizinfo_list.html").read_text()
    items, has_more = sources.parse_bizinfo_page(h)
    assert has_more and len(items) == 2
    r = items[0]
    assert r["source"] == "bizinfo"
    assert r["source_id"] == "PBLN_000000000111111"
    assert r["canonical_url"].endswith("pblancId=PBLN_000000000111111")
    assert r["title"] == "2026년 소상공인 스마트상점 기술보급사업 공고"
    assert r["agency"] == "중소벤처기업부 / 소상공인시장진흥공단"
    assert (r["apply_start"], r["apply_end"]) == ("2026-07-01", "2027-08-01")
    assert r["status"] == "접수중"
    assert r["tags"] == ["금융"]
    assert items[1]["status"] == "마감"
    assert sources.parse_total_count(h) == 2
    assert sources.last_page(h) == 1


def test_list_zero_items_fail_closed(monkeypatch, sources, tmp_path, no_network):
    monkeypatch.setattr(sources, "fetch", lambda url, retries=3: "<html><body></body></html>")
    args = argparse.Namespace(output=str(tmp_path / "bizinfo.jsonl"), delay=0.5)
    assert sources.cmd_list(args) == 2


# ---------------- host 검증 회귀 ----------------

@pytest.mark.parametrize("url", [
    "https://evilbizinfo.go.kr/cmm/fms/FileDown.do",      # 접미사 위장
    "https://bizinfo.go.kr.evil.example/cmm/fms/x",       # 서브도메인 위장
    "https://bizinfo.go.kr@evil.example/cmm/fms/x",       # userinfo 위장
    "http://www.bizinfo.go.kr/cmm/fms/x",                 # http 강등
    "https://evil.example/?next=bizinfo.go.kr",           # 쿼리스트링 위장
])
def test_host_allowed_rejects(sources, url):
    assert not sources.host_allowed(url, sources.BIZINFO_DETAIL_HOSTS)


@pytest.mark.parametrize("url", [
    "https://www.bizinfo.go.kr/sii/siia/selectSIIA200Detail.do?pblancId=PBLN_1",
    "https://bizinfo.go.kr/cmm/fms/FileDown.do?atchFileId=F&fileSn=0",
])
def test_host_allowed_accepts(sources, url):
    assert sources.host_allowed(url, sources.BIZINFO_DETAIL_HOSTS)


# ---------------- 첨부 다운로드 vertical slice ----------------

DETAIL_URL = ("https://www.bizinfo.go.kr/sii/siia/selectSIIA200Detail.do"
              "?pblancId=PBLN_000000000111111")


def make_hwpx_bytes(text="지원대상: 상시근로자 5명 미만"):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("mimetype", "application/hwp+zip")
        z.writestr("Contents/section0.xml",
                   f'<hp:p xmlns:hp="urn:hwpx"><hp:t>{text}</hp:t></hp:p>')
    return buf.getvalue()


class FakeResponse:
    def __init__(self, body, final_url, filename=None):
        self._bio = io.BytesIO(body)
        self._final = final_url
        self.headers = email.message.Message()
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


def _detail_args(tmp_path, download=True):
    return argparse.Namespace(
        urls=[DETAIL_URL], output=str(tmp_path / "details"),
        merge_into=str(tmp_path / "bizinfo.jsonl"),
        download_dir=str(tmp_path / "att") if download else None, delay=0.5)


def _seed_list(tmp_path):
    rec = {"source": "bizinfo", "source_id": "PBLN_000000000111111", "title": "t",
           "content_hash": None, "attachments": [], "attachments_complete": False}
    (tmp_path / "bizinfo.jsonl").write_text(json.dumps(rec, ensure_ascii=False) + "\n")


def test_detail_download_stamps_hash_v3(monkeypatch, sources, fixtures_dir, tmp_path):
    html = (fixtures_dir / "bizinfo_detail.html").read_text()
    hwpx = make_hwpx_bytes()
    monkeypatch.setattr(sources, "fetch", lambda url, retries=3: html)
    monkeypatch.setattr(urllib.request, "urlopen",
                        lambda req, timeout=60: FakeResponse(
                            hwpx, req.full_url, filename="공고문.hwpx"))
    _seed_list(tmp_path)
    rc = sources.cmd_detail(_detail_args(tmp_path))
    assert rc == 0
    r = json.loads((tmp_path / "bizinfo.jsonl").read_text().splitlines()[0])
    assert r["hash_version"] == 3
    assert r["attachments_complete"] is True
    a = r["attachments"][0]
    assert a["download_status"] == "ok" and a["extract_status"] == "ok"
    assert a["sha256"] == hashlib.sha256(hwpx).hexdigest()
    body = sources.extract_body(html)
    assert r["content_hash"] == sources.content_hash_of(body, [a["sha256"]])
    # 추출 텍스트 파일 존재
    assert "상시근로자" in open(a["text_path"], encoding="utf-8").read()


def test_detail_without_download_keeps_hash_v2(monkeypatch, sources, fixtures_dir,
                                               tmp_path, no_network):
    html = (fixtures_dir / "bizinfo_detail.html").read_text()
    monkeypatch.setattr(sources, "fetch", lambda url, retries=3: html)
    _seed_list(tmp_path)
    rc = sources.cmd_detail(_detail_args(tmp_path, download=False))
    assert rc == 0
    r = json.loads((tmp_path / "bizinfo.jsonl").read_text().splitlines()[0])
    assert r["hash_version"] == 2
    body = sources.extract_body(html)
    assert r["content_hash"] == hashlib.sha256(body.encode()).hexdigest()
    assert r["attachments_complete"] is False  # 링크만 수집 — 미추출


def test_download_rejects_disguised_url_without_network(sources, tmp_path, no_network):
    d = tmp_path / "att"
    d.mkdir()
    for url in ("https://evil.example/cmm/fms/x?next=bizinfo.go.kr",
                "https://evilbizinfo.go.kr/cmm/fms/x",
                "http://www.bizinfo.go.kr/cmm/fms/x"):
        with pytest.raises(RuntimeError, match="불허"):
            sources.download_attachment(url, d.resolve(), "f.hwp", 0)
    assert list(d.iterdir()) == []


def test_download_rejects_redirect_to_foreign_host(monkeypatch, sources, tmp_path):
    monkeypatch.setattr(urllib.request, "urlopen",
                        lambda req, timeout=60: FakeResponse(
                            b"x", "https://evil.example/f.hwp"))
    d = tmp_path / "att"
    d.mkdir()
    with pytest.raises(RuntimeError, match="리다이렉트"):
        sources.download_attachment(
            "https://www.bizinfo.go.kr/cmm/fms/FileDown.do", d.resolve(), "f.hwp", 0)
    assert list(d.iterdir()) == []  # 부분 파일 잔존 금지


def test_fetch_redirect_to_foreign_host_is_not_requested(monkeypatch, sources):
    """302 Location이 외부 호스트면 **요청 자체가 나가지 않아야** 한다 (P0)."""
    requested = []

    def fake_urlopen(req, timeout=30):
        requested.append(req.full_url)
        raise make_redirect(req.full_url, "https://evil.example/steal")

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    with pytest.raises(RuntimeError, match="불허"):
        sources.fetch("https://www.bizinfo.go.kr/sii/siia/x.do", retries=1)
    assert requested == ["https://www.bizinfo.go.kr/sii/siia/x.do"]  # 외부 요청 없음


def test_fetch_follows_allowed_redirect_chain(monkeypatch, sources):
    """허용 호스트 내부 리다이렉트(상대 Location 포함)는 수동 추적해 성공한다."""
    requested = []

    def fake_urlopen(req, timeout=30):
        requested.append(req.full_url)
        if len(requested) == 1:
            raise make_redirect(req.full_url, "/sii/siia/final.do")
        return FakeResponse("<html>final ok</html>".encode(), req.full_url)

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    body = sources.fetch("https://www.bizinfo.go.kr/sii/siia/x.do")
    assert "final ok" in body
    assert requested == ["https://www.bizinfo.go.kr/sii/siia/x.do",
                         "https://www.bizinfo.go.kr/sii/siia/final.do"]


def test_attachment_redirect_blocked_before_request_keeps_body_hash(
        monkeypatch, sources, fixtures_dir, tmp_path):
    """첨부 302→외부: 외부로 요청 없이 차단(blocked_redirect)하고,
    본문 v2 해시는 유지한다 (P0 + P1 지적 3)."""
    html = (fixtures_dir / "bizinfo_detail.html").read_text()
    requested = []

    def fake_urlopen(req, timeout=60):
        requested.append(req.full_url)
        raise make_redirect(req.full_url, "https://evil.example/f.hwp")

    monkeypatch.setattr(sources, "fetch", lambda url, retries=3: html)
    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    _seed_list(tmp_path)
    rc = sources.cmd_detail(_detail_args(tmp_path))
    assert rc == 2  # 첨부 미검증 — partial
    assert requested and all("evil.example" not in u for u in requested)
    r = json.loads((tmp_path / "bizinfo.jsonl").read_text().splitlines()[0])
    assert r["attachments"][0]["download_status"] == "blocked_redirect"
    assert r["attachments_complete"] is False
    body = sources.extract_body(html)
    assert r["content_hash"] == hashlib.sha256(body.encode()).hexdigest()
    assert r["hash_version"] == 2


def test_merge_none_hash_removes_stale_hash_version(sources, tmp_path):
    """기존 v3 레코드에 content_hash=None 병합 시 낡은 hash_version이 남으면 안 된다."""
    rec = {"source": "bizinfo", "source_id": "PBLN_000000000111111", "title": "t",
           "content_hash": "oldhash", "hash_version": 3,
           "attachments": [], "attachments_complete": True}
    p = tmp_path / "bizinfo.jsonl"
    p.write_text(json.dumps(rec, ensure_ascii=False) + "\n")
    assert sources.merge_detail(str(p), "PBLN_000000000111111", None, [],
                                complete=False, hash_version=None)
    r = json.loads(p.read_text().splitlines()[0])
    assert r["content_hash"] is None
    assert "hash_version" not in r  # {content_hash: null, hash_version: 3} 오염 금지


def test_download_size_cap_removes_partial_file(monkeypatch, sources, tmp_path):
    big = b"A" * (sources.MAX_ATTACH_BYTES + 1)
    resp = FakeResponse(big, "https://www.bizinfo.go.kr/cmm/fms/f.bin")
    del resp.headers["Content-Length"]  # 스트리밍 카운트 경로 강제
    monkeypatch.setattr(urllib.request, "urlopen", lambda req, timeout=60: resp)
    d = tmp_path / "att"
    d.mkdir()
    with pytest.raises(RuntimeError, match="상한"):
        sources.download_attachment(
            "https://www.bizinfo.go.kr/cmm/fms/FileDown.do", d.resolve(), "f.bin", 0)
    assert list(d.iterdir()) == []


def _robots_skipped_html(fixtures_dir):
    return (fixtures_dir / "bizinfo_detail.html").read_text().replace(
        "/cmm/fms/FileDown.do?atchFileId=FILE_000000000012345&amp;fileSn=0",
        "/uploads/notice/2026/plan.pdf")


def test_detail_uploads_link_skipped_by_robots_keeps_body_v2_hash(
        monkeypatch, sources, fixtures_dir, tmp_path, no_network):
    """robots 불허 첨부는 다운로드하지 않되(exit 2·complete=false),
    본문만의 v2 해시는 유지한다 — 반복 실패가 본문 변경을 숨기지 않도록."""
    html = _robots_skipped_html(fixtures_dir)
    monkeypatch.setattr(sources, "fetch", lambda url, retries=3: html)
    _seed_list(tmp_path)
    rc = sources.cmd_detail(_detail_args(tmp_path))
    assert rc == 2  # 다운로드 불가 첨부 — partial
    r = json.loads((tmp_path / "bizinfo.jsonl").read_text().splitlines()[0])
    assert r["attachments"][0]["download_status"] == "skipped_robots"
    assert r["attachments_complete"] is False
    body = sources.extract_body(html)
    assert r["content_hash"] == hashlib.sha256(body.encode()).hexdigest()
    assert r["hash_version"] == 2


def test_repeated_attach_failure_body_change_is_diff_changed(
        monkeypatch, sources, diff, fixtures_dir, tmp_path, no_network):
    """두 런 모두 첨부 실패(robots skip)여도 본문이 바뀌면 diff가 CHANGED로 감지한다."""
    def run(subdir, html):
        d = tmp_path / subdir
        d.mkdir()
        monkeypatch.setattr(sources, "fetch", lambda url, retries=3: html)
        _seed_list(d)
        assert sources.cmd_detail(_detail_args(d)) == 2
        return json.loads((d / "bizinfo.jsonl").read_text().splitlines()[0])

    html1 = _robots_skipped_html(fixtures_dir)
    html2 = html1.replace("상시근로자", "상시근로자(개정)")
    old = run("run1", html1)
    new = run("run2", html2)
    assert old["content_hash"] and new["content_hash"]
    assert old["content_hash"] != new["content_hash"]
    r = diff.classify(old, new)
    assert r["kind"] == "CHANGED" and "content_hash" in r["changed_fields"]


def test_download_repairs_mojibake_filename(monkeypatch, sources, tmp_path):
    """UTF-8 바이트를 latin-1로 잘못 디코드한 Content-Disposition 파일명 복원."""
    mojibake = "공고문.hwpx".encode("utf-8").decode("latin-1")
    monkeypatch.setattr(urllib.request, "urlopen",
                        lambda req, timeout=60: FakeResponse(
                            make_hwpx_bytes(), req.full_url, filename=mojibake))
    d = tmp_path / "att"
    d.mkdir()
    path = sources.download_attachment(
        "https://www.bizinfo.go.kr/cmm/fms/FileDown.do", d.resolve(), "fb.hwpx", 0)
    assert path.name == "00_공고문.hwpx"


def test_hash_v3_formula_matches_sbiz(sources, sbiz):
    assert sources.content_hash_of("본문", ["b", "a"]) == \
        sbiz.content_hash_of("본문", ["a", "b"])
    assert sources.HASH_VERSION_ATTACH == 3
