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


_MAX_UNQUOTE = 5  # 반복 percent-디코딩 상한 (이중 인코딩 %2575… 커버)


def robots_path_allowed(url, disallowed_prefixes):
    """robots 불허 접두 검사 — 인코딩 위장에 fail-closed.

    단순 startswith는 /%75ploads(→/uploads), /%2Fuploads(→//uploads, POSIX
    normpath가 선행 '//'를 보존), /x/../uploads 로 우회된다. 원본·반복 unquote
    전 단계·normpath·선행 슬래시 단일화 형태 중 하나라도 불허 접두에 걸리면
    거부하고, 디코딩 불가·상한 초과·파싱 불가도 거부한다."""
    if not disallowed_prefixes:
        return True
    try:
        path = urllib.parse.urlsplit(url).path
    except ValueError:
        return False
    candidates = []
    cur = path
    for _ in range(_MAX_UNQUOTE + 1):
        candidates.append(cur)
        try:
            nxt = urllib.parse.unquote(cur)
        except (ValueError, UnicodeDecodeError):
            return False
        if nxt == cur:
            break
        cur = nxt
    else:
        return False  # 상한 내 고정점 미도달(과도한 다중 인코딩) — fail-closed
    for c in list(candidates):
        n = os.path.normpath(c)
        candidates.extend([n, re.sub(r"^/+", "/", c), re.sub(r"^/+", "/", n)])
    return not any(c.startswith(p) for c in candidates for p in disallowed_prefixes)


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


def _sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _deny_symlink(path):
    """기존 경로가 symlink면 거부 — 사전 배치된 링크를 통해 외부 파일을
    읽거나(sha256 비교) 대체하는 것을 막는다 (fail-closed)."""
    if os.path.islink(path):
        raise RuntimeError(f"symlink_blocked: {pathlib.Path(path).name}")


def _finalize_no_clobber(tmp, target):
    """tmp를 target으로 옮기되 기존 파일을 절대 덮어쓰지 않는다(증거 보존).

    - target 없음 → rename
    - target 있고 내용 동일 → tmp 폐기, 기존 경로 재사용
    - target 있고 내용 다름 → `이름-1.확장자`, `-2`… 접미사로 저장
    - target·후보가 symlink면 거부 (사전 배치 링크로의 기록/판독 차단)
    """
    _deny_symlink(target)
    if not target.exists():
        os.replace(tmp, target)
        return target
    if _sha256_file(target) == _sha256_file(tmp):
        tmp.unlink(missing_ok=True)
        return target
    stem, suffix = target.stem, target.suffix
    for i in range(1, 1000):
        cand = target.with_name(f"{stem}-{i}{suffix}")
        _deny_symlink(cand)
        if not cand.exists():
            os.replace(tmp, cand)
            return cand
        if _sha256_file(cand) == _sha256_file(tmp):
            tmp.unlink(missing_ok=True)
            return cand
    raise RuntimeError(f"동명 첨부 접미사 소진: {target.name}")


def _safe_record_dir(download_dir, subdir):
    """<download_dir>[/<정제된 subdir>] 를 만들고 경로를 반환한다 (fail-closed).

    subdir 이름(공고 식별자)은 예측 가능하다 — 사전 배치된 symlink가 mkdir(
    exist_ok=True)를 통과해 외부 디렉터리를 새 루트로 삼는 것을 막는다:
    ① 기존 subdir 경로가 symlink면 거부, ② mkdir 후 realpath가 download_dir
    realpath 내부인지 재검증(경로 구성요소를 통한 우회 포함).
    """
    base = pathlib.Path(download_dir).resolve()
    d = base
    if subdir:
        d = base / re.sub(r"[^\w.\-가-힣]", "_", str(subdir))[:80]
        if os.path.islink(d):
            raise RuntimeError(f"symlink_subdir_blocked: {d.name}")
    d.mkdir(parents=True, exist_ok=True)
    real_base = os.path.realpath(base)
    real_d = os.path.realpath(d)
    if os.path.commonpath([real_d, real_base]) != real_base:
        raise RuntimeError(f"subdir_escape_blocked: {d.name}")
    return pathlib.Path(real_d)


def download_attachment(url, dirpath, fallback_name, idx, allowed_hosts, ua):
    """보안 계약(모듈 docstring) 전체를 적용해 첨부를 저장하고 경로를 반환한다.

    스트리밍은 임시 파일로 받고, 최종 이름은 _finalize_no_clobber로 확정한다 —
    같은 폴더의 기존 파일(다른 공고의 동명 첨부 등)을 덮어쓰지 않는다.
    """
    if not host_allowed(url, allowed_hosts):
        raise RuntimeError(f"첨부 URL host/scheme 불허: {url[:80]}")
    dirpath = pathlib.Path(dirpath).resolve()
    tmp = None
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
            raw_path = dirpath / safe_filename(cd_name or fallback_name, idx)
            # resolve() 전에 검사한다 — resolve는 사전 배치된 symlink를 따라가
            # 외부 목적지를 '정상 경로'로 둔갑시킨다
            _deny_symlink(raw_path)
            path = raw_path.resolve()
            if os.path.commonpath([str(path), str(dirpath)]) != str(dirpath) \
                    or path.is_symlink():
                raise RuntimeError("path_escape_blocked")
            tmp_path = path.with_name(f".part-{os.getpid()}-{idx}-{path.name}"[:200])
            read = 0
            # O_CREAT|O_EXCL: 임시 경로에 사전 배치된 파일/symlink(dangling 포함)가
            # 있으면 열지 않고 실패한다 — 예측 가능한 이름을 통한 외부 기록 차단.
            # tmp(정리 대상)는 우리가 만든 뒤에만 설정 — 남의 파일을 지우지 않는다.
            try:
                fd = os.open(tmp_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644)
            except FileExistsError:
                raise RuntimeError(f"tmp_preexists_blocked: {tmp_path.name}") from None
            tmp = tmp_path
            with os.fdopen(fd, "wb") as fh:
                while True:
                    chunk = r.read(1 << 20)
                    if not chunk:
                        break
                    read += len(chunk)
                    if read > MAX_ATTACH_BYTES:
                        raise RuntimeError(
                            f"첨부가 {MAX_ATTACH_BYTES // (1 << 20)}MB 상한 초과")
                    fh.write(chunk)
            return _finalize_no_clobber(tmp, path)
    except urllib.error.HTTPError as e:
        if e.code in (401, 403):
            raise ManualEscalation(f"첨부 다운로드 HTTP {e.code}") from e
        raise
    except (RuntimeError, OSError):
        if tmp is not None:
            tmp.unlink(missing_ok=True)  # 부분 파일 잔존 방지
        raise


def process_attachments(attachments, download_dir, delay, allowed_hosts, ua,
                        robots_allowed=None, subdir=None):
    """첨부 목록을 다운로드+텍스트 추출하고 sha256 목록을 반환한다.

    각 항목 dict에 download_status/extract_status/extract_reason/local_path/
    sha256/text_path를 기록한다. robots_allowed(url)->bool이 주어지면 불허 경로는
    요청 없이 skipped_robots로 남긴다 (우회 금지 원칙).

    subdir(공고 식별자)를 주면 download_dir/<정제된 subdir>/ 아래에 저장한다 —
    여러 공고가 같은 폴더를 쓸 때 동명 첨부(00_공고문.hwp)가 서로 덮어써
    기존 레코드의 sha256/local_path와 실제 파일이 어긋나는 것을 막는다.
    (같은 폴더 안의 잔여 충돌은 download_attachment의 no-clobber 접미사가 막는다.)
    """
    import attach_extract  # 같은 디렉토리의 추출기 — 추출 성공까지 확인해야 complete
    try:
        d = _safe_record_dir(download_dir, subdir)
    except (RuntimeError, OSError) as e:
        # 하위 폴더가 사전 배치된 symlink 등 — 요청 없이 전건 실패로 기록(fail-closed)
        for f in attachments:
            f["download_status"] = "failed"
            f["extract_status"] = "failed"
            f["extract_reason"] = str(e)
        print(f"WARNING attachments: 다운로드 폴더 검증 실패 — {e}", file=sys.stderr)
        return []
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
