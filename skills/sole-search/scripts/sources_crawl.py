#!/usr/bin/env python3
"""기업마당(bizinfo.go.kr) 모집중 공고 전수 크롤러 — sole-search.

ir-search의 검증된 bizinfo 파서를 이식·개조했다. 차이점:
  - 분야 필터 없음: 모집중(schEndAt=N) 전체 공고를 전 페이지 순회
  - 출력은 sole-search 공통 스키마(source_id, canonical_url, agency, ...) — diff와 호환
  - 표준 라이브러리만 사용, 요청 간 딜레이 최소 0.5초 강제
  - fail-closed: 첫 페이지 0건 파싱·페이지네이션 미발견은 실패(2),
    401/403/CAPTCHA 의심은 MANUAL(3)로 종료
  - content_hash는 **hash v2**: 본문을 시작 마커(view_cont/print_area)부터 푸터성
    마커(또는 문서 끝)까지 절단해 태그 제거 후 해시한다. v1(중첩 div의 첫 </div>에서
    절단되던 방식)과 비교 불가 — diff_surveys.py가 hash_version 불일치를 1회 CHANGED
    (상세 재검증)로 전환한다. detail 병합 레코드에 `hash_version: 2` 필드를 부여한다.
  - detail --download-dir 로 첨부까지 **전부** 다운로드에 성공하면 content_hash는
    **hash v3**(본문 + 정렬된 첨부 sha256, sbiz_crawl.content_hash_of와 동일 산식)로
    `hash_version: 3`을 스탬프한다 — v2와 비교 불가, diff가 1회 CHANGED로 흡수한다.
    다운로드하지 않거나 일부 첨부가 실패·생략(robots 등)되면 **본문만의 v2 해시를
    유지**하고 `attachments_complete: false` + exit 2로 첨부 미검증을 표현한다 —
    None으로 지우면 반복 실패 시 본문 변경이 diff에서 숨기 때문.
  - 모든 HTTP 요청은 자동 리다이렉트를 끄고 각 Location을 요청 전에
    https+허용 호스트(bizinfo.go.kr)로 검증해 최대 5홉만 수동 추적한다 —
    위반 시 요청을 보내지 않고 차단(첨부는 download_status "blocked_redirect").

사용법:
  python3 sources_crawl.py list -o bizinfo.jsonl [--delay 0.5]
  python3 sources_crawl.py detail <URL>... -o details/ [--merge-into bizinfo.jsonl]
      [--download-dir DIR]

종료 코드: 0 성공(전 페이지) / 2 부분·실패 / 3 수동전환(차단 신호).
stderr 마지막 줄: PAGES <expected> CRAWLED <n> COLLECTED <m>
"""
import argparse
import hashlib
import html as htmllib
import json
import os
import pathlib
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone, timedelta

MIN_DELAY = 0.5
BASE = "https://www.bizinfo.go.kr"
KST = timezone(timedelta(hours=9))
UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
      "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.4 Safari/605.1.15")
BLOCK_MARKERS = ("captcha", "로그인이 필요", "접근이 차단", "비정상적인 접근")


class ManualEscalation(RuntimeError):
    """401/403/CAPTCHA — 우회하지 않고 manual로 전환하라는 신호."""


class RedirectBlocked(RuntimeError):
    """리다이렉트 대상이 https+허용 호스트 검사를 통과하지 못함 — 요청 전에 차단."""


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """자동 리다이렉트 금지 — open_validated가 각 Location을 요청 전에 검증한다."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


# 전역 opener에 리다이렉트 비활성 핸들러 설치 — urlopen이 3xx를 HTTPError로 던지게 한다
urllib.request.install_opener(urllib.request.build_opener(_NoRedirect))

MAX_REDIRECTS = 5
_REDIRECT_CODES = (301, 302, 303, 307, 308)


def open_validated(url, allowed_hosts, timeout):
    """자동 리다이렉트 없이 열고, 각 Location을 **요청을 보내기 전에** 절대 URL로
    해석해 https+허용 호스트 검사를 통과할 때만 최대 5홉 수동 추적한다.
    위반 시 RedirectBlocked — 외부 호스트로는 요청 자체가 나가지 않는다."""
    if not host_allowed(url, allowed_hosts):
        raise RedirectBlocked(f"URL host/scheme 불허: {url[:80]}")
    for _ in range(MAX_REDIRECTS + 1):
        req = urllib.request.Request(url, headers={"User-Agent": UA})
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
            url = nxt
    raise RedirectBlocked(f"리다이렉트 {MAX_REDIRECTS}홉 초과: {url[:80]}")


def fetch(url, retries=3):
    last = None
    for i in range(retries):
        try:
            with open_validated(url, BIZINFO_DETAIL_HOSTS, timeout=30) as resp:
                body = resp.read().decode("utf-8", "replace")
            low = body[:4000].lower()
            if any(m in low for m in BLOCK_MARKERS):
                raise ManualEscalation("차단/로그인 페이지 감지 (HTTP 200)")
            return body
        except urllib.error.HTTPError as e:
            if e.code in (401, 403):
                raise ManualEscalation(f"HTTP {e.code} — 차단/인증 요구") from e
            last = e
            if not (e.code == 429 or e.code >= 500):
                break  # 그 외 4xx는 재시도 무의미
            retry_after = e.headers.get("Retry-After") if e.headers else None
            wait = float(retry_after) if retry_after and retry_after.isdigit() \
                else MIN_DELAY * (i + 1) * 2
            time.sleep(min(wait, 30))
        except (urllib.error.URLError, TimeoutError) as e:
            last = e
            time.sleep(MIN_DELAY * (i + 1) * 2)
    raise RuntimeError(f"GET {url[:80]} failed after {retries} tries: {last}")


def clean(s):
    return re.sub(r"\s+", " ", htmllib.unescape(s or "")).strip()


def norm_date(s):
    s = clean(s)
    m = re.search(r"(\d{4})[.\-/\s]+(\d{1,2})[.\-/\s]+(\d{1,2})", s)
    if m:
        return f"{m.group(1)}-{int(m.group(2)):02d}-{int(m.group(3)):02d}"
    m = re.search(r"(\d{2})[.\-/](\d{1,2})[.\-/](\d{1,2})", s)
    if m:
        return f"20{m.group(1)}-{int(m.group(2)):02d}-{int(m.group(3)):02d}"
    return s


def split_period(s):
    parts = re.split(r"~|∼", s)
    if len(parts) == 2:
        return norm_date(parts[0]), norm_date(parts[1])
    return "", norm_date(s)


def now_kst():
    return datetime.now(KST).strftime("%Y-%m-%dT%H:%M:%S+09:00")


def _status(apply_end):
    """기업마당 목록은 모집중(schEndAt=N)만 요청하지만 마감일로 재검증한다."""
    if not apply_end:
        return "불명"
    try:
        end = datetime.strptime(apply_end[:10], "%Y-%m-%d").replace(tzinfo=KST)
    except ValueError:
        return "불명"
    return "접수중" if end >= datetime.now(KST) - timedelta(days=1) else "마감"


def build_list_url(page):
    # 분야(pldirCd)·해시태그 필터를 걸지 않는다 — 모집중 전체
    return f"{BASE}/sii/siia/selectSIIA200View.do?rows=15&cpage={page}&schEndAt=N"


def parse_bizinfo_page(h):
    """공통 스키마 레코드 목록과 has_more를 반환한다."""
    items = []
    for row in re.findall(r"<tr>[\s\S]*?</tr>", h):
        m = re.search(r'href\s*=\s*"([^"]*pblancId=(PBLN_\d+)[^"]*)"[^>]*>\s*([\s\S]*?)</a>', row)
        if not m:
            continue
        tds = [clean(re.sub(r"<[^>]+>", " ", td))
               for td in re.findall(r"<td[^>]*>([\s\S]*?)</td>", row)]
        # tds: [no, 분야, 제목셀, 접수기간, 소관부처, 수행기관, 등록일, 조회]
        start, end = split_period(tds[3]) if len(tds) > 3 else ("", "")
        pblanc_id = m.group(2)
        items.append({
            "source": "bizinfo",
            "source_id": pblanc_id,
            "announce_no": pblanc_id,
            "canonical_url": f"{BASE}/sii/siia/selectSIIA200Detail.do?pblancId={pblanc_id}",
            "title": clean(m.group(3)),
            "agency": " / ".join(x for x in tds[4:6] if x) if len(tds) > 5 else "",
            "region_scope": None,  # 목록에 없음 — 상세에서 판단
            "apply_start": start or None,
            "apply_end": end or None,
            "status": _status(end),
            "primary_type": None,
            "tags": [tds[1]] if len(tds) > 1 and tds[1] else [],
            "attachments": [],
            "attachments_complete": False,
            "crawled_at": now_kst(),
            "content_hash": None,
            "raw": {"field": tds[1] if len(tds) > 1 else "",
                    "reg_date": tds[6] if len(tds) > 6 else ""},
        })
    return items, bool(items)


def parse_total_count(h):
    """전체 분야 탭(hashAll)의 총건수. 못 찾으면 None (coverage 검증 불가로 보고)."""
    seg = re.search(r'분야\((\d[\d,]*)\) 공고보기"[^>]{0,120}id="hashAll"', h)
    if not seg:
        seg = re.search(r'id="hashAll"[^>]{0,200}?분야\((\d[\d,]*)\)', h)
    if not seg:
        counts = [int(c.replace(",", "")) for c in re.findall(r'분야\((\d[\d,]*)\) 공고보기', h)]
        return max(counts) if counts else None
    return int(seg.group(1).replace(",", ""))


def last_page(h):
    pages = [int(p) for p in re.findall(r"cpage=(\d+)", h)]
    return max(pages) if pages else 0  # 0 = 페이지네이션 미발견(실패 신호)


def collect_all_pages(fetch_page, max_page=None, delay=0.0):
    seen = {}
    page = 1
    while True:
        items, has_more = fetch_page(page)
        new = [i for i in items if i["source_id"] not in seen]
        for i in items:
            seen[i["source_id"]] = i
        print(f"[sole-search] bizinfo p{page}: {len(items)} parsed, {len(new)} new, "
              f"total {len(seen)}", file=sys.stderr)
        if not has_more or not new:
            break
        if max_page and page >= max_page:
            break
        page += 1
        if delay:
            time.sleep(delay)
    return list(seen.values())


def strip_html(text):
    text = re.sub(r"<script[\s\S]*?</script>|<style[\s\S]*?</style>", "", text)
    text = re.sub(r"<[^>]+>", "\n", text)
    text = htmllib.unescape(text)
    return re.sub(r"\n\s*\n+", "\n", text)


HASH_VERSION = 2

# 본문 시작 마커 — 여는 태그만 잡는다 (중첩 div를 regex로 균형 매칭할 수 없으므로)
START_MARKERS = (r'<div[^>]+class="[^"]*view_cont[^"]*"', r'<div[^>]+id="print_area"')
# 본문 끝 마커 — 시작 마커부터 푸터/다음 주요 섹션까지를 본문으로 자른다
END_MARKERS = (r'<div[^>]+id="footer"', r'<footer\b', r'<div[^>]+class="[^"]*footer',
               r'<div[^>]+class="[^"]*btn_area', r'<div[^>]+class="[^"]*paging',
               r'목록으로|이전글|다음글')


def extract_body(h):
    """시작 마커 ~ 첫 끝 마커(없으면 문서 끝) 구간의 텍스트. 마커 미발견 시 전체 폴백."""
    sm = None
    for p in START_MARKERS:
        sm = re.search(p, h)
        if sm:
            break
    seg = h[sm.start():] if sm else h
    ends = [m.start() for p in END_MARKERS for m in [re.search(p, seg)] if m]
    if ends:
        seg = seg[:min(ends)]
    return strip_html(seg)


def cmd_list(args):
    try:
        first = fetch(build_list_url(1))
    except ManualEscalation as e:
        print(f"MANUAL bizinfo: {e} — region-registry/수동확인으로 전환", file=sys.stderr)
        return 3
    except (RuntimeError, urllib.error.URLError, TimeoutError) as e:
        print(f"WARNING bizinfo: 첫 페이지 실패 {e}", file=sys.stderr)
        return 2

    expected_pages = last_page(first)
    first_items, _ = parse_bizinfo_page(first)
    if not first_items or expected_pages == 0:
        print("WARNING bizinfo: 첫 페이지 파싱 0건 또는 페이지네이션 미발견 — "
              "사이트 구조 변경 가능성, failed로 기록할 것", file=sys.stderr)
        return 2

    crawled_pages = 0

    def fetch_page(page):
        nonlocal crawled_pages
        if page == 1:
            h = first
        else:
            try:
                h = fetch(build_list_url(page))
            except ManualEscalation:
                raise  # 차단 신호는 partial로 강등하지 않는다 — main에서 exit 3
            except (RuntimeError, urllib.error.URLError, TimeoutError) as e:
                print(f"WARNING bizinfo p{page}: {e}", file=sys.stderr)
                return [], False
        crawled_pages = page
        return parse_bizinfo_page(h)

    try:
        items = collect_all_pages(fetch_page, max_page=expected_pages, delay=args.delay)
    except ManualEscalation as e:
        print(f"MANUAL bizinfo: {e} — 수동확인으로 전환", file=sys.stderr)
        return 3

    tmp = args.output + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        for i in items:
            f.write(json.dumps(i, ensure_ascii=False) + "\n")
    os.replace(tmp, args.output)

    expected_items = parse_total_count(first)
    print(f"PAGES {expected_pages} CRAWLED {crawled_pages} "
          f"EXPECTED {expected_items if expected_items is not None else '?'} "
          f"COLLECTED {len(items)}", file=sys.stderr)
    if crawled_pages < expected_pages:
        print(f"WARNING bizinfo: {expected_pages - crawled_pages}p 미수집 — partial",
              file=sys.stderr)
        return 2
    if expected_items is not None and len(items) != expected_items:
        print(f"WARNING bizinfo: 총건수 {expected_items} 대비 {len(items)}건 수집 — "
              "partial (행 파싱 누락 가능)", file=sys.stderr)
        return 2
    if expected_items is None:
        print("WARNING bizinfo: 총건수 마커 미발견 — 수집률 미검증(사이트 변경?), "
              "partial로 기록할 것", file=sys.stderr)
        return 2
    return 0


def merge_detail(jsonl_path, source_id, content_hash, attachments, complete,
                 source="bizinfo", hash_version=HASH_VERSION):
    """목록 jsonl의 해당 레코드에 상세 검증 결과를 병합한다 (원자적 교체)."""
    tmp = jsonl_path + ".tmp"
    found = False
    with open(jsonl_path, encoding="utf-8") as src, open(tmp, "w", encoding="utf-8") as dst:
        for line in src:
            if not line.strip():
                continue
            r = json.loads(line)
            if r.get("source") == source and str(r.get("source_id")) == str(source_id):
                r["content_hash"] = content_hash
                if content_hash is not None:
                    r["hash_version"] = hash_version
                else:
                    # 해시 없음 = 산식 버전도 무의미 — 낡은 version 잔존 금지
                    r.pop("hash_version", None)
                r["attachments"] = attachments
                r["attachments_complete"] = complete
                found = True
            dst.write(json.dumps(r, ensure_ascii=False) + "\n")
    os.replace(tmp, jsonl_path)
    return found


def host_allowed(url, allowed_hosts):
    """https + 정확한 호스트/서브도메인 경계 검사 — endswith/부분 문자열 매칭은
    evilbizinfo.go.kr, userinfo(@)·쿼리스트링 위장에 뚫린다 (ir-search와 동일 패턴)."""
    try:
        parts = urllib.parse.urlsplit(url)
    except ValueError:
        return False
    if parts.scheme != "https":
        return False
    host = (parts.hostname or "").lower()
    return any(host == a or host.endswith("." + a) for a in allowed_hosts)


BIZINFO_DETAIL_HOSTS = ("bizinfo.go.kr",)

MAX_ATTACH_BYTES = 50 * 1024 * 1024  # 첨부 다운로드 상한 50MB (sbiz_crawl과 동일)
HASH_VERSION_ATTACH = 3  # 본문 + 정렬된 첨부 sha256 (sbiz_crawl.content_hash_of 산식)

# robots.txt(2026-07-23 확인)가 /upload·/download 프리픽스를 불허한다 —
# /uploads/ 첨부 링크는 수집하되 다운로드하지 않는다(skipped_robots).
ROBOTS_DISALLOWED_PREFIXES = ("/upload", "/download", "/super", "/html", "/images",
                              "/agspa", "/error", "/common", "/lib", "/WEB-INF",
                              "/direct_do")


def content_hash_of(body_text, attachment_hashes):
    """hash v3 산식 — sbiz_crawl.content_hash_of와 동일해야 한다."""
    payload = body_text + "\n" + "\n".join(sorted(attachment_hashes))
    return hashlib.sha256(payload.encode()).hexdigest()


def safe_filename(name, idx):
    """서버 제공 파일명을 신뢰하지 않는다 — basename + 문자 정제 + 순번 프리픽스."""
    base = re.sub(r"[^\w.\-가-힣()\[\] ]", "_",
                  (name or "").replace("\\", "/").rsplit("/", 1)[-1])
    return f"{idx:02d}_{base[:120]}" if base else f"{idx:02d}_attach"


def robots_allowed(url):
    try:
        path = urllib.parse.urlsplit(url).path
    except ValueError:
        return False
    return not any(path.startswith(p) for p in ROBOTS_DISALLOWED_PREFIXES)


def download_attachment(url, dirpath, fallback_name, idx):
    """보안 계약: 요청 전 host_allowed(https 강제 포함) + 각 리다이렉트 Location을
    **요청 전에** 검증(open_validated, 위반 시 RedirectBlocked)
    + 50MB 스트리밍 상한 + 실패 시 부분 파일 삭제. 저장 경로를 반환한다."""
    if not host_allowed(url, BIZINFO_DETAIL_HOSTS):
        raise RuntimeError(f"첨부 URL host/scheme 불허: {url[:80]}")
    path = None
    try:
        with open_validated(url, BIZINFO_DETAIL_HOSTS, timeout=60) as r:
            # 사전 검증이 1차 방어 — geturl 재검사는 심층 방어로 유지한다
            final = r.geturl() if hasattr(r, "geturl") else url
            if not host_allowed(final, BIZINFO_DETAIL_HOSTS):
                raise RuntimeError(f"리다이렉트 최종 URL host 불허: {final[:80]}")
            length = r.headers.get("Content-Length")
            if length and length.isdigit() and int(length) > MAX_ATTACH_BYTES:
                raise RuntimeError(f"첨부 Content-Length가 상한 초과: {length}")
            cd_name = r.headers.get_filename()  # Content-Disposition 파일명
            if cd_name:
                try:
                    # 서버가 UTF-8 바이트를 그대로 보내면 latin-1로 잘못 디코드된
                    # 모지바케가 온다 — 되돌려서 복원 (실측: bizinfo, 2026-07-23)
                    cd_name = cd_name.encode("latin-1").decode("utf-8")
                except (UnicodeEncodeError, UnicodeDecodeError):
                    pass
                if "%" in cd_name:
                    cd_name = urllib.parse.unquote(cd_name)
            path = (pathlib.Path(dirpath) /
                    safe_filename(cd_name or fallback_name, idx)).resolve()
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


def process_attachments(attachments, download_dir, delay):
    """첨부 목록을 다운로드+텍스트 추출하고 sha256 목록을 반환한다 (sbiz와 동일 계약)."""
    import attach_extract  # 같은 디렉토리의 추출기
    d = pathlib.Path(download_dir).resolve()
    d.mkdir(parents=True, exist_ok=True)
    attach_hashes = []
    for idx, f in enumerate(attachments):
        if not robots_allowed(f["url"]):
            f["download_status"] = "skipped_robots"
            f["extract_status"] = "skipped"
            f["extract_reason"] = "robots_disallowed_path"
            print(f"[sole-search] robots 불허 경로 — 다운로드 생략: {f['url'][:80]}",
                  file=sys.stderr)
            continue
        time.sleep(delay)
        try:
            path = download_attachment(f["url"], d, f.get("filename"), idx)
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


def cmd_detail(args):
    os.makedirs(args.output, exist_ok=True)
    failures = 0
    for url in args.urls:
        if not host_allowed(url, BIZINFO_DETAIL_HOSTS):
            print(f"[sole-search] skip non-bizinfo url: {url[:60]}", file=sys.stderr)
            failures += 1
            continue
        try:
            h = fetch(url)
        except ManualEscalation as e:
            print(f"MANUAL bizinfo detail: {e}", file=sys.stderr)
            return 3
        except (RuntimeError, urllib.error.URLError, TimeoutError) as e:
            print(f"[sole-search] {url[:60]}: {e}", file=sys.stderr)
            failures += 1
            time.sleep(args.delay)
            continue
        attach = [htmllib.unescape(u) for u in
                  re.findall(r'href="(/cmm/fms/[^"]+|/uploads/[^"]+)"', h)]
        attachments = [{"url": BASE + a, "filename": a.rsplit("/", 1)[-1].split("?")[0]}
                       for a in attach]
        # 본문 컨테이너 시작~푸터 마커 구간만 해시 (hash v2 — 메뉴·조회수 등 변동값 배제)
        text = extract_body(h)
        digest = hashlib.sha256(text.encode()).hexdigest()
        hash_version = HASH_VERSION
        complete = not attachments  # 링크만 수집: 첨부가 있으면 아직 미추출
        if args.download_dir and attachments:
            try:
                attach_hashes = process_attachments(attachments, args.download_dir,
                                                    args.delay)
            except ManualEscalation as e:
                print(f"MANUAL bizinfo attachment: {e}", file=sys.stderr)
                return 3
            downloads_ok = all(f.get("download_status") == "ok" for f in attachments)
            complete = all(f.get("extract_status") == "ok" for f in attachments)
            if downloads_ok:
                # hash v3: 본문 + 정렬된 첨부 sha256 — v2와 비교 불가(diff가 1회 CHANGED)
                digest = content_hash_of(text, attach_hashes)
                hash_version = HASH_VERSION_ATTACH
            # else: 첨부 다운로드 불완전 — 본문만의 v2 해시를 유지한다.
            # None으로 지우면 반복 실패 두 런 사이의 본문 변경이 UNCHANGED로 숨는다.
            # 첨부 미검증은 attachments_complete=false + exit 2(partial)로 표현.
            if not complete:
                bad = [f.get("filename", "?") for f in attachments
                       if f.get("extract_status") != "ok"]
                print(f"WARNING bizinfo detail: 첨부 {len(bad)}건 다운로드/추출 "
                      f"실패·생략 ({', '.join(bad[:5])}) — partial", file=sys.stderr)
                failures += 1
        name = re.sub(r"\W+", "_", url.split("://", 1)[1])[:80]
        path = f"{args.output}/{name}.txt"
        with open(path, "w", encoding="utf-8") as f:
            f.write(url + "\n")
            f.write("CONTENT_HASH: " + (digest or "") + "\n")
            f.write(f"HASH_VERSION: {hash_version if digest else ''}\n")
            f.write("ATTACHMENTS: " + json.dumps(attachments, ensure_ascii=False) + "\n\n")
            f.write(text)
        print(f"[sole-search] saved: {path}", file=sys.stderr)
        if args.merge_into:
            m = re.search(r"pblancId=(PBLN_\d+)", url)
            if m:
                merged = merge_detail(args.merge_into, m.group(1), digest, attachments,
                                      complete=complete, hash_version=hash_version)
                if not merged:
                    print(f"[sole-search] WARNING: {m.group(1)} 레코드를 "
                          f"{args.merge_into}에서 못 찾음", file=sys.stderr)
                    failures += 1
        time.sleep(args.delay)
    if failures:
        print(f"WARNING bizinfo detail: {failures}건 실패/미병합", file=sys.stderr)
        return 2
    return 0


def positive_delay(v):
    f = float(v)
    if f < MIN_DELAY:
        raise argparse.ArgumentTypeError(f"딜레이는 최소 {MIN_DELAY}초 (예의상 강제)")
    return f


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    sub = ap.add_subparsers(dest="cmd", required=True)
    lp = sub.add_parser("list", help="모집중 전체 공고 전 페이지 수집")
    lp.add_argument("-o", "--output", default="bizinfo.jsonl")
    lp.add_argument("--delay", type=positive_delay, default=MIN_DELAY)
    dp = sub.add_parser("detail", help="상세 텍스트+첨부링크 저장, --merge-into로 목록에 병합")
    dp.add_argument("urls", nargs="+")
    dp.add_argument("-o", "--output", default="details")
    dp.add_argument("--merge-into")
    dp.add_argument("--download-dir",
                    help="첨부를 이 폴더에 다운로드+추출 — 전부 성공 시 hash v3 스탬프")
    dp.add_argument("--delay", type=positive_delay, default=MIN_DELAY)
    args = ap.parse_args()
    sys.exit(cmd_list(args) if args.cmd == "list" else cmd_detail(args))


if __name__ == "__main__":
    main()
