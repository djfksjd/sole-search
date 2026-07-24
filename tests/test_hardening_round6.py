"""라운드 6 하드닝 회귀 테스트 — Codex 교차검증(2026-07-24)이 지적한 실결함 수정 고정.

각 테스트는 수정 전이면 실패하고 수정 후 통과한다:
  #1 combine merge 네임스페이스 결속 (A/413 병합이 C/413 오염 금지)
  #2 bizinfo/region 응답 identity 결속 (요청 ID 부재 → fail-closed)
  #3 추출 sidecar O_EXCL (사전 배치 symlink 미추종)
  #4 신뢰불가 HWP DIFAT 사이클 종료 (무한 루프 방어)
  #5 robots가 리다이렉트 홉에서도 검사됨 (허용→불허 경로 유도 차단)
  #6 Content-Length 비숫자("NaN")가 exit 1로 새지 않음
"""
import argparse
import importlib
import json
import struct
import urllib.error
import urllib.parse

import pytest

attach_download = importlib.import_module("attach_download")


_A_ID = "PBLN_000000000111111"
_B_ID = "PBLN_000000000222222"


def _biz_alturl(pid):
    # 실측된 기업마당 altUrl 대입 문법
    return ("var altUrl = location.origin + location.pathname "
            f"+ '?pblancId={pid}';")


def _biz_ids(html):
    """None(=알 수 없는 문법 → 거부) 전파, 아니면 pblancId 집합."""
    sts, _, _ = attach_download.page_self_markers(html)
    out = set()
    for s in sts:
        r = attach_download.alturl_pblanc_ids(s)
        if r is None:
            return None
        out |= r
    return out


def _ntt_ids(html):
    sts, _, inp = attach_download.page_self_markers(html)
    out = set(inp)
    for s in sts:
        r = attach_download.js_var_int_ids(s, "nttId")
        if r is None:
            return None
        out |= r
    return out


@pytest.mark.parametrize("html,expected", [
    # 실 문법 → 그 id
    (f"<script>{_biz_alturl(_A_ID)}</script>", {_A_ID}),
    # 문자열 안 가짜 대입은 배제; 실 altUrl은 B → {B}
    (f'<script>var s="{_biz_alturl(_A_ID)}"; {_biz_alturl(_B_ID)}</script>', {_B_ID}),
    # 알 수 없는 문법(임의 JS) → None(fail-closed 거부):
    (f"<script>var altUrl = (function(){{return '?pblancId={_A_ID}'}})();</script>", None),
    (f"<script>var altUrl = (0, '?pblancId={_A_ID}');</script>", None),   # 콤마연산
    (f"<script>var altUrl = '?pblancId={_A_ID}';</script>", None),         # 단순 문자열(실문법 아님)
    (f"<script>var altUrl = cond ? '?pblancId={_A_ID}' : '';</script>", None),  # 삼항
    # => 화살표·정규식 리터럴·쪼개진 스크립트는 altUrl 대입이 아님 → set()(빈)
    # → 호출부에서 set()≠{요청} 으로 거부된다(None 아님).
    (f"<script>const f = altUrl => '?pblancId={_A_ID}';</script>", set()),
    (f"<script>var re = /altUrl=.?pblancId={_A_ID}/;</script>", set()),
    (f"<script>alt</script><script>Url='?pblancId={_A_ID}'</script>", set()),
])
def test_js_scanner_strict_grammar(html, expected):
    assert _biz_ids(html) == expected


def test_js_scanner_requires_var_declaration():
    # property 대입·var 없는 재대입은 권위 마커 아님 → 무시(빈 집합, 호출부가 거부)
    assert _ntt_ids("<script>related.nttId='5001';</script>") == set()
    assert _biz_ids("<script>obj.altUrl = location.origin + location.pathname"
                    f" + '?pblancId={_A_ID}';</script>") == set()
    assert _biz_ids("<script>altUrl = location.origin + location.pathname"
                    f" + '?pblancId={_A_ID}';</script>") == set()
    # 실제 var 선언은 (함수 내부여도) 인정 — 실 기업마당은 script 함수 안에 있다
    assert _biz_ids("<script>function ready(){ var altUrl = location.origin"
                    f" + location.pathname + '?pblancId={_A_ID}'; }}</script>") == {_A_ID}


def test_js_scanner_nttid_strict():
    assert _ntt_ids("<script>var nttId = '20260';</script>") == {20260}
    assert _ntt_ids("<script>var nttId = 20260;</script>") == {20260}
    # RHS 주석은 제거되고 실 정수값만 인정
    assert _ntt_ids("<script>var nttId = /* 5001 */ '5002';</script>") == {5002}
    # 임의 표현식(콤마연산 등) → None(거부)
    assert _ntt_ids("<script>var nttId = (5001,5002);</script>") is None


# ---- #1 combine merge 네임스페이스 결속 ----
def test_merge_detail_namespace_bound_no_cross_corruption(sbiz, tmp_path):
    p = tmp_path / "combine.jsonl"
    recs = [
        {"source": "sbiz24_combine", "source_id": "413", "title": "공단 A공고",
         "raw": {"pbancGubun": "A"}, "content_hash": "OLD_A"},
        {"source": "sbiz24_combine", "source_id": "413", "title": "대출 C상품",
         "raw": {"pbancGubun": "C"}, "content_hash": "OLD_C"},
    ]
    p.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in recs))
    merged = sbiz.merge_detail(str(p), "413", "NEW_A_HASH", [], True,
                               source="sbiz24_combine", expected_gubun="A")
    assert merged is True
    out = [json.loads(l) for l in p.read_text().splitlines() if l.strip()]
    a = next(r for r in out if r["raw"]["pbancGubun"] == "A")
    c = next(r for r in out if r["raw"]["pbancGubun"] == "C")
    assert a["content_hash"] == "NEW_A_HASH"       # A 갱신됨
    assert c["content_hash"] == "OLD_C"            # C는 절대 오염 안 됨


# ---- #2 bizinfo 응답 identity 결속 ----
class _FakeResp:
    def __init__(self, body, url, filename=None):
        self._b = body
        self._url = url
        self.headers = _H(filename)

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def geturl(self):
        return self._url

    def read(self, n=-1):
        b, self._b = self._b, b""
        return b


class _H:
    def __init__(self, filename):
        self._f = filename

    def get(self, k, default=None):
        if k == "Content-Length":
            return None
        return default

    def get_filename(self):
        return self._f


def _seed(tmp_path, pid="PBLN_000000000111111"):
    p = tmp_path / "list.jsonl"
    p.write_text(json.dumps({
        "source": "bizinfo", "source_id": pid, "title": "t",
        "content_hash": None, "attachments": [], "attachments_complete": False},
        ensure_ascii=False) + "\n")
    return p


def test_bizinfo_detail_wrong_identity_fail_closed(sources, tmp_path, monkeypatch):
    # 서버가 요청 pblancId가 없는(=다른 공고) HTML을 반환
    wrong_html = "<html><div class='view_cont'>다른 공고 본문 pblancId=PBLN_999</div></html>"
    monkeypatch.setattr(sources, "fetch", lambda url, retries=3: wrong_html)
    merge = _seed(tmp_path)
    args = argparse.Namespace(
        urls=["https://www.bizinfo.go.kr/sii/siia/selectSIIA200Detail.do"
              "?pblancId=PBLN_000000000111111"],
        output=str(tmp_path / "out"), merge_into=str(merge),
        download_dir=None, delay=0.0)
    rc = sources.cmd_detail(args)
    assert rc == 2  # fail-closed (기록/병합 안 함)
    rec = json.loads(merge.read_text().splitlines()[0])
    assert rec["content_hash"] is None          # 병합되지 않음
    assert rec["attachments_complete"] is False


@pytest.mark.parametrize("quote", ['"', "'"])
def test_bizinfo_identity_only_in_related_link_rejected(sources, tmp_path,
                                                        monkeypatch, quote):
    # 서버가 다른 공고(B)를 반환했는데 '관련공고' 링크(따옴표 종류 무관)에만 요청
    # ID(A)가 있는 경우 — 앵커 요소를 통째로 제거하므로 자기참조가 없어 거부.
    req = "PBLN_000000000111111"
    b_html = ("<html><div class='view_cont'>"
              "<script>var altUrl='...?pblancId=PBLN_000000000222222';</script>"
              "<h3>B 공고</h3>"
              f"<a href={quote}selectSIIA200Detail.do?pblancId={req}{quote}>관련 A</a>"
              "</div></html>")
    monkeypatch.setattr(sources, "fetch", lambda url, retries=3: b_html)
    merge = _seed(tmp_path)
    args = argparse.Namespace(
        urls=[f"https://www.bizinfo.go.kr/sii/siia/selectSIIA200Detail.do?pblancId={req}"],
        output=str(tmp_path / "out"), merge_into=str(merge),
        download_dir=None, delay=0.0)
    assert sources.cmd_detail(args) == 2  # 오귀속 방지
    rec = json.loads(merge.read_text().splitlines()[0])
    assert rec["content_hash"] is None


@pytest.mark.parametrize("b_html", [
    # altUrl은 B(222222). A가 아래 어디에 있어도 유일 자기ID는 B → 거부.
    "<a href='x?pblancId=PBLN_000000000111111'>미닫힘",   # 미닫힘 앵커
    "관련: PBLN_000000000111111 평문",                     # 평문
    "<!-- pblancId=PBLN_000000000111111 -->",             # HTML 주석
    # altUrl=B 안에 JS 블록 주석으로 A 주입(Codex 재현) → 주석 제거로 무력화
    "<script>var altUrl='?pblancId=PBLN_000000000222222' "
    "/* related pblancId=PBLN_000000000111111 */;</script>",
    # JS 라인 주석(//)으로 A 주입 — 실행 JS 아님 → 배제
    "<script>var altUrl='?pblancId=PBLN_000000000222222';\n"
    "// var altUrl='?pblancId=PBLN_000000000111111'\n</script>",
    # 미종료 블록 주석 뒤 A — 끝까지 주석 처리 → 배제
    "<script>var altUrl='?pblancId=PBLN_000000000222222';\n"
    "/* pblancId=PBLN_000000000111111 </script>",
    # A 마커가 <script> 밖(주석/평문) — 실행 JS 아님 → 배제
    "<script>var altUrl='?pblancId=PBLN_000000000222222';</script>"
    "<!-- var altUrl='?pblancId=PBLN_000000000111111' -->",
    # JS 문자열 리터럴 안의 /* — 인용부호 인식 렉서가 코드로 인식 → 두 altUrl 다 수집
    "<script>var altUrl='?pblancId=PBLN_000000000111111'; var x='/*';"
    " var altUrl='?pblancId=PBLN_000000000222222';</script>",
    # 서로 다른 altUrl 두 개(A와 B) → 집합 {A,B}≠{A} → 거부
    "<script>var altUrl='?pblancId=PBLN_000000000111111';</script>"
    "<script>var altUrl='?pblancId=PBLN_000000000222222';</script>",
    # 문자열 리터럴 안의 대입 모양 텍스트 — code-state 아님 → 무시(실 altUrl은 B)
    "<script>var altUrl='?pblancId=PBLN_000000000222222'; "
    "var snippet=\"var altUrl='?pblancId=PBLN_000000000111111'\";</script>",
    # <script> 이어붙이기 위조(alt + Url=…) — 스크립트별 독립 처리 → 대입 없음
    "<script>alt</script><script>Url='?pblancId=PBLN_000000000111111'</script>",
])
def test_bizinfo_identity_positive_extraction_rejects_noise(sources, tmp_path,
                                                            monkeypatch, b_html):
    req = "PBLN_000000000111111"
    # 대부분 케이스는 altUrl=B를 기본 포함; 마지막 두 케이스는 자체 script 포함
    if "altUrl" not in b_html:
        b_html = ("<script>var altUrl='?pblancId=PBLN_000000000222222';</script>"
                  + b_html)
    b_html = "<html><div class='view_cont'>" + b_html + "</div></html>"
    monkeypatch.setattr(sources, "fetch", lambda url, retries=3: b_html)
    merge = _seed(tmp_path)
    args = argparse.Namespace(
        urls=[f"https://www.bizinfo.go.kr/sii/siia/selectSIIA200Detail.do?pblancId={req}"],
        output=str(tmp_path / "out"), merge_into=str(merge),
        download_dir=None, delay=0.0)
    assert sources.cmd_detail(args) == 2
    assert json.loads(merge.read_text().splitlines()[0])["content_hash"] is None


def test_region_fake_link_in_script_rejected(region, tmp_path, monkeypatch):
    # 실제 canonical element는 B(5002), <script> 텍스트 안에 가짜 <link ...5001> 문자열.
    # HTMLParser는 script 텍스트를 element로 보지 않으므로 5001은 배제 → 요청 5001 거부.
    b_html = ('<html><head>'
              '<link rel="canonical" href="/wbase/contents/bbs/view/5002.do">'
              '<script>var s="<link rel=canonical href=/view/5001.do>";</script>'
              '</head><body><div class="sub_cont_wrap">B</div>'
              '<div id="footer">x</div></body></html>')
    monkeypatch.setattr(region, "fetch", lambda url, data=None, retries=3: b_html)
    (tmp_path / "ssb.jsonl").write_text(json.dumps(
        {"source": "seoulshinbo", "source_id": "ntc-5001", "title": "t",
         "content_hash": None, "attachments": [], "attachments_complete": False},
        ensure_ascii=False) + "\n")
    args = argparse.Namespace(
        urls=["https://www.seoulshinbo.co.kr/wbase/contents/bbs/view/5001.do"],
        output=str(tmp_path / "out"), merge_into=str(tmp_path / "ssb.jsonl"),
        download_dir=None, delay=0.0)
    assert region.cmd_detail(args) == 2  # 오귀속 방지
    assert json.loads((tmp_path / "ssb.jsonl").read_text().splitlines()[0])[
        "content_hash"] is None


def test_region_identity_only_in_related_link_rejected(region, tmp_path, monkeypatch):
    # ssb: 서버가 B를 반환, 요청 ID(5001)는 '이전글' 링크(view/5001.do)에만 있음 —
    # 앵커 제거 후엔 자기참조 없어 거부(문서 전역 매칭이었으면 오귀속).
    b_html = ('<html><head><link rel="canonical" '
              'href="/wbase/contents/bbs/view/5002.do"></head>'
              '<body><div class="sub_cont_wrap"><h3>B 공고</h3>'
              '<a href="/wbase/contents/bbs/view/5001.do">이전글 A</a>'
              '</div><div id="footer">이전글 다음글</div></body></html>')
    monkeypatch.setattr(region, "fetch", lambda url, data=None, retries=3: b_html)
    recs = [{"source": "seoulshinbo", "source_id": "ntc-5001", "title": "t",
             "content_hash": None, "attachments": [], "attachments_complete": False}]
    (tmp_path / "ssb.jsonl").write_text(
        json.dumps(recs[0], ensure_ascii=False) + "\n")
    args = argparse.Namespace(
        urls=["https://www.seoulshinbo.co.kr/wbase/contents/bbs/view/5001.do"
              "?mng_cd=STRY9788"],
        output=str(tmp_path / "out"), merge_into=str(tmp_path / "ssb.jsonl"),
        download_dir=None, delay=0.0)
    assert region.cmd_detail(args) == 2  # 오귀속 방지
    rec = json.loads((tmp_path / "ssb.jsonl").read_text().splitlines()[0])
    assert rec["content_hash"] is None


def test_combine_loanproduct_url_namespace(sbiz):
    # C(대출상품, pbancGubun=C) 레코드는 loanProduct 네임스페이스 URL이어야 한다
    item = {"pbancSn": "413", "pbancNm": "대출상품", "pbancGubun": "C",
            "bizType": "대출상품"}
    rec = sbiz.to_record(item, "combine")
    assert "/#/loanProduct/413" in rec["canonical_url"]
    item_a = {"pbancSn": "413", "pbancNm": "공단공고", "pbancGubun": "A"}
    assert "/#/pbanc/413" in sbiz.to_record(item_a, "combine")["canonical_url"]


# ---- #3 추출 sidecar O_EXCL ----
def test_write_sidecar_refuses_preplaced_symlink(sbiz, tmp_path):
    secret = tmp_path / "secret.txt"
    secret.write_text("SECRET")
    link = tmp_path / "att.hwp.txt"
    link.symlink_to(secret)
    ok = sbiz.write_sidecar(str(link), "MALICIOUS")
    assert ok is False                     # symlink 추종 거부
    assert secret.read_text() == "SECRET"  # 원본 미변경


def test_write_sidecar_new_file_ok(sbiz, tmp_path):
    tp = tmp_path / "att.pdf.txt"
    assert sbiz.write_sidecar(str(tp), "본문") is True
    assert tp.read_text() == "본문"


# ---- #4 신뢰불가 HWP DIFAT 사이클 종료 ----
def test_cfb_difat_cycle_terminates(attach):
    data = bytearray(1024)
    data[0:8] = attach.HWP_MAGIC
    struct.pack_into("<H", data, 30, 9)            # sector shift → ssz=512
    struct.pack_into("<H", data, 32, 6)            # mini sector shift
    struct.pack_into("<I", data, 48, 0xFFFFFFFE)   # dir_start = ENDOFCHAIN
    struct.pack_into("<I", data, 68, 0)            # difat_start = sector 0
    struct.pack_into("<I", data, 72, 0xFFFFFFFF)   # num_difat = 조작된 거대값
    # 조작이 무한 루프를 유발하지 않고 유한 시간에 종료(None)해야 한다
    assert attach._cfb_read_stream(bytes(data), "PrvText") is None


# ---- #5 robots가 리다이렉트 홉에서도 검사됨 ----
def test_open_validated_robots_blocks_redirect_to_disallowed(monkeypatch):
    requested = []

    def fake_urlopen(req, timeout):
        requested.append(req.full_url)
        # 허용 경로 요청 → 302로 불허 경로(/uploads)로 유도
        raise urllib.error.HTTPError(
            req.full_url, 302, "redir",
            {"Location": "https://bizinfo.go.kr/uploads/private.hwp"}, None)

    monkeypatch.setattr(attach_download.urllib.request, "urlopen", fake_urlopen)
    robots = lambda u: "/uploads" not in urllib.parse.urlsplit(u).path
    with pytest.raises(attach_download.RedirectBlocked):
        attach_download.open_validated(
            "https://bizinfo.go.kr/cmm/fms/FileDown.do", ("bizinfo.go.kr",),
            timeout=5, ua="UA", robots_allowed=robots)
    # 불허 경로로는 요청 자체가 나가면 안 된다
    assert not any("/uploads" in u for u in requested)


def test_open_validated_robots_blocks_initial(monkeypatch):
    monkeypatch.setattr(attach_download.urllib.request, "urlopen",
                        lambda req, timeout: (_ for _ in ()).throw(
                            AssertionError("요청이 나가면 안 됨")))
    with pytest.raises(attach_download.RedirectBlocked):
        attach_download.open_validated(
            "https://bizinfo.go.kr/uploads/x.hwp", ("bizinfo.go.kr",),
            timeout=5, ua="UA", robots_allowed=lambda u: False)


# ---- #6 Content-Length 비숫자 방어 ----
def test_download_capped_nonnumeric_content_length(sbiz, tmp_path, monkeypatch):
    class R:
        headers = {"Content-Length": "NaN"}

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self, n):
            return b""

    monkeypatch.setattr(sbiz, "_open_validated",
                        lambda url, timeout, data=None: R())
    # ValueError로 새지 않고 정상 종료해야 한다(스트리밍 상한이 최종 방어선)
    n = sbiz.download_capped("https://www.sbiz24.kr/api/cmmn/file/x", tmp_path / "f.bin")
    assert n == 0
