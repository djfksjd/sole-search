#!/usr/bin/env python3
"""첨부 다운로드 공용 슬라이스 — sources_crawl(기업마당)·region_crawl(판판대로·서울신보) 공유.

보안 계약 (두 스크립트가 동일하게 적용):
  - 요청 전 host_allowed: https + 정확한 호스트/서브도메인 경계 검사
    (endswith/부분 문자열 매칭은 evil 호스트·userinfo·쿼리스트링 위장에 뚫린다)
  - 자동 리다이렉트 금지: 각 Location을 **요청을 보내기 전에** 절대 URL로 해석해
    같은 검사를 통과할 때만 최대 5홉 수동 추적 (위반 시 RedirectBlocked —
    외부 호스트로는 요청 자체가 나가지 않는다)
  - 50MB 스트리밍 상한 + 실패 시 부분 파일 삭제
  - 서버 제공 파일명 불신: basename + 문자 정제 + path escape/symlink 차단
  - Content-Disposition 파일명 mojibake 복구 (latin-1로 잘못 디코드된 UTF-8)
  - 401/403은 ManualEscalation — 우회하지 않고 수동확인으로 전환(종료 코드 3)

SSL 컨텍스트·전역 opener는 각 스크립트가 설치한다(서울신보 중간 인증서 내장 등) —
이 모듈은 urllib.request.urlopen을 호출 시점에 참조하므로 설치된 opener를 그대로 쓴다.
"""
import hashlib
import os
import pathlib
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

MAX_ATTACH_BYTES = 50 * 1024 * 1024  # 첨부 다운로드 상한 50MB
MAX_REDIRECTS = 5
_REDIRECT_CODES = (301, 302, 303, 307, 308)


class ManualEscalation(RuntimeError):
    """401/403/CAPTCHA — 우회하지 않고 manual로 전환하라는 신호."""


class RedirectBlocked(RuntimeError):
    """리다이렉트 대상이 https+허용 호스트 검사를 통과하지 못함 — 요청 전에 차단."""


class NoRedirect(urllib.request.HTTPRedirectHandler):
    """자동 리다이렉트 금지 — open_validated가 각 Location을 요청 전에 검증한다."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def host_allowed(url, allowed_hosts):
    """https + 정확한 호스트/서브도메인 경계 검사."""
    try:
        parts = urllib.parse.urlsplit(url)
    except ValueError:
        return False
    if parts.scheme != "https":
        return False
    host = (parts.hostname or "").lower()
    return any(host == a or host.endswith("." + a) for a in allowed_hosts)


def open_validated(url, allowed_hosts, timeout, ua, data=None):
    """자동 리다이렉트 없이 열고, 각 Location을 **요청을 보내기 전에** 절대 URL로
    해석해 https+허용 호스트 검사를 통과할 때만 최대 5홉 수동 추적한다.
    위반 시 RedirectBlocked — 외부 호스트로는 요청 자체가 나가지 않는다."""
    if not host_allowed(url, allowed_hosts):
        raise RedirectBlocked(f"URL host/scheme 불허: {url[:80]}")
    for _ in range(MAX_REDIRECTS + 1):
        req = urllib.request.Request(url, data=data, headers={"User-Agent": ua})
        try:
            return urllib.request.urlopen(req, timeout=timeout)
        except urllib.error.HTTPError as e:
            if e.code not in _REDIRECT_CODES:
                raise
            loc = e.headers.get("Location") if e.headers else None
            e.close()
            if not loc:
                raise RedirectBlocked(f"리다이렉트 Location 없음: {url[:80]}")
            nxt = urllib.parse.urljoin(url, loc)
            if not host_allowed(nxt, allowed_hosts):
                raise RedirectBlocked(f"리다이렉트 대상 불허 — 요청 차단: {nxt[:80]}")
            url, data = nxt, None  # 리다이렉트 추적은 GET
    raise RedirectBlocked(f"리다이렉트 {MAX_REDIRECTS}홉 초과: {url[:80]}")


def safe_filename(name, idx):
    """서버 제공 파일명을 신뢰하지 않는다 — basename + 문자 정제 + 순번 프리픽스."""
    base = re.sub(r"[^\w.\-가-힣()\[\] ]", "_",
                  (name or "").replace("\\", "/").rsplit("/", 1)[-1])
    return f"{idx:02d}_{base[:120]}" if base else f"{idx:02d}_attach"


def fix_mojibake(name):
    """서버가 UTF-8 바이트를 그대로 보내면 latin-1로 잘못 디코드된 모지바케가 온다 —
    되돌려서 복원 (실측: bizinfo, 2026-07-23). %-인코딩이면 unquote."""
    try:
        name = name.encode("latin-1").decode("utf-8")
    except (UnicodeEncodeError, UnicodeDecodeError):
        pass
    if "%" in name:
        name = urllib.parse.unquote(name)
    return name


def download_attachment(url, dirpath, fallback_name, idx, allowed_hosts, ua):
    """보안 계약(모듈 docstring) 전체를 적용해 첨부를 저장하고 경로를 반환한다."""
    if not host_allowed(url, allowed_hosts):
        raise RuntimeError(f"첨부 URL host/scheme 불허: {url[:80]}")
    dirpath = pathlib.Path(dirpath).resolve()
    path = None
    try:
        with open_validated(url, allowed_hosts, timeout=60, ua=ua) as r:
            # 사전 검증이 1차 방어 — geturl 재검사는 심층 방어로 유지한다
            final = r.geturl() if hasattr(r, "geturl") else url
            if not host_allowed(final, allowed_hosts):
                raise RuntimeError(f"리다이렉트 최종 URL host 불허: {final[:80]}")
            length = r.headers.get("Content-Length")
            if length and length.isdigit() and int(length) > MAX_ATTACH_BYTES:
                raise RuntimeError(f"첨부 Content-Length가 상한 초과: {length}")
            cd_name = r.headers.get_filename()  # Content-Disposition 파일명
            if cd_name:
                cd_name = fix_mojibake(cd_name)
            path = (dirpath / safe_filename(cd_name or fallback_name, idx)).resolve()
            if os.path.commonpath([str(path), str(dirpath)]) != str(dirpath) \
                    or path.is_symlink():
                raise RuntimeError("path_escape_blocked")
            read = 0
            with open(path, "wb") as fh:
                while True:
                    chunk = r.read(1 << 20)
                    if not chunk:
                        break
                    read += len(chunk)
                    if read > MAX_ATTACH_BYTES:
                        raise RuntimeError(
                            f"첨부가 {MAX_ATTACH_BYTES // (1 << 20)}MB 상한 초과")
                    fh.write(chunk)
            return path
    except urllib.error.HTTPError as e:
        if e.code in (401, 403):
            raise ManualEscalation(f"첨부 다운로드 HTTP {e.code}") from e
        raise
    except (RuntimeError, OSError):
        if path is not None:
            path.unlink(missing_ok=True)  # 부분 파일 잔존 방지
        raise


def process_attachments(attachments, download_dir, delay, allowed_hosts, ua,
                        robots_allowed=None):
    """첨부 목록을 다운로드+텍스트 추출하고 sha256 목록을 반환한다.

    각 항목 dict에 download_status/extract_status/extract_reason/local_path/
    sha256/text_path를 기록한다. robots_allowed(url)->bool이 주어지면 불허 경로는
    요청 없이 skipped_robots로 남긴다 (우회 금지 원칙).
    """
    import attach_extract  # 같은 디렉토리의 추출기 — 추출 성공까지 확인해야 complete
    d = pathlib.Path(download_dir).resolve()
    d.mkdir(parents=True, exist_ok=True)
    attach_hashes = []
    for idx, f in enumerate(attachments):
        if robots_allowed is not None and not robots_allowed(f["url"]):
            f["download_status"] = "skipped_robots"
            f["extract_status"] = "skipped"
            f["extract_reason"] = "robots_disallowed_path"
            print(f"[sole-search] robots 불허 경로 — 다운로드 생략: {f['url'][:80]}",
                  file=sys.stderr)
            continue
        time.sleep(delay)
        try:
            path = download_attachment(f["url"], d, f.get("filename"), idx,
                                       allowed_hosts, ua)
        except ManualEscalation:
            raise  # 차단 신호 — 호출부에서 exit 3
        except RedirectBlocked as e:
            f["download_status"] = "blocked_redirect"
            f["extract_status"] = "failed"
            f["extract_reason"] = str(e)
            print(f"WARNING attachment {f.get('filename', '?')}: 리다이렉트 차단 — {e}",
                  file=sys.stderr)
            continue
        except (urllib.error.URLError, urllib.error.HTTPError,
                RuntimeError, OSError, TimeoutError) as e:
            f["download_status"] = "failed"
            f["extract_status"] = "failed"
            f["extract_reason"] = str(e)
            print(f"WARNING attachment {f.get('filename', '?')}: {e}", file=sys.stderr)
            continue
        f["local_path"] = str(path)
        f["filename"] = path.name
        f["download_status"] = "ok"
        f["sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()
        attach_hashes.append(f["sha256"])
        r = attach_extract.extract(str(path))
        if r["ok"] and not r.get("reason"):
            f["extract_status"] = "ok"
            text_path = str(path) + ".txt"
            pathlib.Path(text_path).write_text(r["text"], encoding="utf-8")
            f["text_path"] = text_path
        elif r["ok"]:
            # 부분 추출(예: hwp_preview_only) — 텍스트는 저장하되 complete 아님
            f["extract_status"] = "partial"
            f["extract_reason"] = r["reason"]
            text_path = str(path) + ".txt"
            pathlib.Path(text_path).write_text(r["text"], encoding="utf-8")
            f["text_path"] = text_path
            print(f"WARNING extract {f['filename']}: 부분 추출 ({r['reason']})",
                  file=sys.stderr)
        else:
            f["extract_status"] = "unsupported" if r["reason"] in (
                "hwp_binary_unsupported", "unsupported_extension") else "failed"
            f["extract_reason"] = r["reason"]
            print(f"WARNING extract {f['filename']}: {r['reason']}", file=sys.stderr)
    return attach_hashes
