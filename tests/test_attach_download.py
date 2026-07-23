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


# ---------------- no-clobber 증거 보존 (Codex NO-GO 4) ----------------

import email.message
import io
import urllib.request


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


HOSTS = ("seoulshinbo.co.kr",)
URL = "https://www.seoulshinbo.co.kr/download/1/a.do"


def _dl(monkeypatch, body, d, filename="공고문.hwp", idx=0):
    monkeypatch.setattr(urllib.request, "urlopen",
                        lambda req, timeout=60: FakeResponse(
                            body, req.full_url, filename=filename))
    return ad.download_attachment(URL, d, "fb.hwp", idx, HOSTS, "ua")


def test_same_name_different_content_gets_suffix(monkeypatch, tmp_path):
    """동명 첨부가 내용이 다르면 덮어쓰지 않고 접미사로 보존한다."""
    d = tmp_path.resolve()
    p1 = _dl(monkeypatch, b"content-A", d)
    p2 = _dl(monkeypatch, b"content-B", d)
    assert p1 != p2
    assert p1.read_bytes() == b"content-A"  # 기존 파일 무손상
    assert p2.read_bytes() == b"content-B"
    assert p2.name == "00_공고문-1.hwp"


def test_same_name_same_content_reuses_existing(monkeypatch, tmp_path):
    d = tmp_path.resolve()
    p1 = _dl(monkeypatch, b"identical", d)
    p2 = _dl(monkeypatch, b"identical", d)
    assert p1 == p2
    assert sorted(x.name for x in d.iterdir()) == ["00_공고문.hwp"]  # 임시파일 잔존 없음


def test_failed_download_leaves_no_partial_or_temp_files(monkeypatch, tmp_path):
    d = tmp_path.resolve()
    _dl(monkeypatch, b"keep-me", d)
    big = b"A" * (ad.MAX_ATTACH_BYTES + 1)

    class NoLenResponse(FakeResponse):
        def __init__(self, body, final_url):
            super().__init__(body, final_url, filename="공고문.hwp")
            del self.headers["Content-Length"]

    monkeypatch.setattr(urllib.request, "urlopen",
                        lambda req, timeout=60: NoLenResponse(big, req.full_url))
    with pytest.raises(RuntimeError, match="상한"):
        ad.download_attachment(URL, d, "fb.hwp", 0, HOSTS, "ua")
    assert sorted(x.name for x in d.iterdir()) == ["00_공고문.hwp"]
    assert (d / "00_공고문.hwp").read_bytes() == b"keep-me"  # 기존 파일 무손상


def test_process_attachments_subdir_isolates_records(monkeypatch, tmp_path):
    """subdir(공고 식별자)로 공고별 폴더 분리 — 동명 첨부가 서로를 덮지 않는다."""
    bodies = iter([b"record-one-file", b"record-two-file"])
    monkeypatch.setattr(urllib.request, "urlopen",
                        lambda req, timeout=60: FakeResponse(
                            next(bodies), req.full_url, filename="공고문.pdf"))
    a1 = [{"url": URL, "filename": "공고문.pdf"}]
    a2 = [{"url": URL, "filename": "공고문.pdf"}]
    ad.process_attachments(a1, tmp_path, 0.5, HOSTS, "ua", subdir="seoulshinbo_ntc-1")
    ad.process_attachments(a2, tmp_path, 0.5, HOSTS, "ua", subdir="seoulshinbo_ntc-2")
    assert a1[0]["download_status"] == a2[0]["download_status"] == "ok"
    assert a1[0]["local_path"] != a2[0]["local_path"]
    assert "seoulshinbo_ntc-1" in a1[0]["local_path"]
    assert "seoulshinbo_ntc-2" in a2[0]["local_path"]
    # 각 레코드의 sha256이 실제 파일 내용과 일치 — 덮어쓰기 없음
    import hashlib as _h
    import pathlib as _p
    for a, body in ((a1[0], b"record-one-file"), (a2[0], b"record-two-file")):
        assert _p.Path(a["local_path"]).read_bytes() == body
        assert a["sha256"] == _h.sha256(body).hexdigest()


# ---------------- 사전 배치 symlink 차단 (Codex 재심사 HIGH) ----------------

def test_preplaced_subdir_symlink_is_rejected_without_request(monkeypatch, tmp_path):
    """예측 가능한 subdir 이름에 사전 배치된 symlink → 요청 없이 전건 실패,
    외부 디렉터리에 어떤 파일도 생기지 않아야 한다."""
    outside = tmp_path / "outside"
    outside.mkdir()
    base = tmp_path / "att"
    base.mkdir()
    (base / "seoulshinbo_ntc-5001").symlink_to(outside)
    requested = []

    def fake_urlopen(req, timeout=60):
        requested.append(req.full_url)
        return FakeResponse(b"x", req.full_url, filename="f.hwp")

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    attachments = [{"url": URL, "filename": "f.hwp"}]
    hashes = ad.process_attachments(attachments, base, 0.5, HOSTS, "ua",
                                    subdir="seoulshinbo_ntc-5001")
    assert hashes == []
    assert requested == []  # 네트워크 요청 자체가 없다
    assert attachments[0]["download_status"] == "failed"
    assert "symlink_subdir_blocked" in attachments[0]["extract_reason"]
    assert list(outside.iterdir()) == []  # 외부 디렉터리 무오염


def test_subdir_symlink_escape_realpath_recheck(tmp_path):
    """islink를 우회해도(예: 경로 구성요소) realpath 재검증이 잡는다."""
    outside = tmp_path / "outside"
    outside.mkdir()
    base = tmp_path / "att"
    base.mkdir()
    (base / "esc").symlink_to(outside)
    with pytest.raises(RuntimeError, match="symlink_subdir_blocked|subdir_escape"):
        ad._safe_record_dir(base, "esc")
    assert list(outside.iterdir()) == []


def test_preplaced_tmp_symlink_blocks_write_and_survives(monkeypatch, tmp_path):
    """임시파일명에 사전 배치된 symlink: O_EXCL이 열기를 거부하고,
    외부 목적지에 기록되지 않으며, 남의 링크를 지우지도 않는다."""
    import os as _os
    d = tmp_path.resolve()
    victim = tmp_path / "victim.txt"
    victim.write_bytes(b"original")
    tmp_name = f".part-{_os.getpid()}-0-00_공고문.hwp"[:200]
    (d / tmp_name).symlink_to(victim)
    monkeypatch.setattr(urllib.request, "urlopen",
                        lambda req, timeout=60: FakeResponse(
                            b"attacker-controlled", req.full_url, filename="공고문.hwp"))
    with pytest.raises(RuntimeError, match="tmp_preexists_blocked"):
        ad.download_attachment(URL, d, "fb.hwp", 0, HOSTS, "ua")
    assert victim.read_bytes() == b"original"  # 외부 파일 무손상
    assert (d / tmp_name).is_symlink()  # 우리가 만든 게 아니므로 지우지 않는다


def test_preplaced_target_symlink_is_rejected(monkeypatch, tmp_path):
    """최종 파일명에 사전 배치된 symlink: 판독(sha256)·대체 없이 거부."""
    d = tmp_path.resolve()
    victim = tmp_path / "victim.txt"
    victim.write_bytes(b"original")
    (d / "00_공고문.hwp").symlink_to(victim)
    monkeypatch.setattr(urllib.request, "urlopen",
                        lambda req, timeout=60: FakeResponse(
                            b"new-content", req.full_url, filename="공고문.hwp"))
    with pytest.raises(RuntimeError, match="symlink_blocked"):
        ad.download_attachment(URL, d, "fb.hwp", 0, HOSTS, "ua")
    assert victim.read_bytes() == b"original"
    assert (d / "00_공고문.hwp").is_symlink()  # 링크도 대체되지 않았다
    assert not [p for p in d.iterdir() if p.name.startswith(".part-")]  # 임시 정리됨


# ---- percent-인코딩 robots 우회 차단 (ir-search 게이트 동일 결함 선제 반영) ----

BIZ_PREFIXES = ("/upload", "/download")


@pytest.mark.parametrize("url", [
    "https://www.bizinfo.go.kr/%75ploads/a.hwp",       # /uploads
    "https://www.bizinfo.go.kr/%2575ploads/a.hwp",     # 이중 인코딩
    "https://www.bizinfo.go.kr/x/../uploads/a.hwp",    # normpath
    "https://www.bizinfo.go.kr/%2Fuploads/a.hwp",      # → //uploads (normpath 보존)
    "https://www.bizinfo.go.kr//uploads/a.hwp",        # 직접 이중 슬래시
    "https://www.bizinfo.go.kr/%2F%2Fuploads/a.hwp",   # ///uploads
    "https://www.bizinfo.go.kr/downloadFile.do",       # 평문 접두
])
def test_robots_encoded_bypass_blocked(url):
    assert not ad.robots_path_allowed(url, BIZ_PREFIXES)


def test_robots_normal_path_allowed():
    assert ad.robots_path_allowed(
        "https://www.bizinfo.go.kr/cmm/fms/getFile.do", BIZ_PREFIXES)
    assert ad.robots_path_allowed("https://x.kr/any/path", ())  # 불허 없음 → 허용


def test_robots_excessive_encoding_fail_closed():
    url = "https://www.bizinfo.go.kr/" + "%25" * 6 + "75ploads/a.hwp"
    deep = "uploads"
    for _ in range(7):
        deep = urllib.parse.quote(deep, safe="")
    assert not ad.robots_path_allowed(f"https://www.bizinfo.go.kr/{deep}/a.hwp",
                                      BIZ_PREFIXES)
