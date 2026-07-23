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
    절단되던 방식)과 비교 불가 — diff_surveys.py가 hash_version 불일치를 NEEDS_REHASH로
    처리한다. detail 병합 레코드에 `hash_version: 2` 필드를 부여한다.

사용법:
  python3 sources_crawl.py list -o bizinfo.jsonl [--delay 0.5]
  python3 sources_crawl.py detail <URL>... -o details/ [--merge-into bizinfo.jsonl]

종료 코드: 0 성공(전 페이지) / 2 부분·실패 / 3 수동전환(차단 신호).
stderr 마지막 줄: PAGES <expected> CRAWLED <n> COLLECTED <m>
"""
import argparse
import hashlib
import html as htmllib
import json
import os
import re
import sys
import time
import urllib.error
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


def fetch(url, retries=3):
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    last = None
    for i in range(retries):
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
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


def merge_detail(jsonl_path, source_id, content_hash, attachments, complete, source="bizinfo"):
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
                r["hash_version"] = HASH_VERSION
                r["attachments"] = attachments
                r["attachments_complete"] = complete
                found = True
            dst.write(json.dumps(r, ensure_ascii=False) + "\n")
    os.replace(tmp, jsonl_path)
    return found


def cmd_detail(args):
    os.makedirs(args.output, exist_ok=True)
    failures = 0
    for url in args.urls:
        host = re.sub(r"^https?://([^/]+).*", r"\1", url)
        if not host.endswith("bizinfo.go.kr"):
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
        # 본문 컨테이너 시작~푸터 마커 구간만 해시 (hash v2 — 메뉴·조회수 등 변동값 배제)
        text = extract_body(h)
        digest = hashlib.sha256(text.encode()).hexdigest()
        name = re.sub(r"\W+", "_", url.split("://", 1)[1])[:80]
        path = f"{args.output}/{name}.txt"
        with open(path, "w", encoding="utf-8") as f:
            f.write(url + "\n")
            f.write("CONTENT_HASH: " + digest + "\n")
            f.write(f"HASH_VERSION: {HASH_VERSION}\n")
            f.write("ATTACHMENTS: " + json.dumps(
                [BASE + a for a in attach], ensure_ascii=False) + "\n\n")
            f.write(text)
        print(f"[sole-search] saved: {path}", file=sys.stderr)
        if args.merge_into:
            m = re.search(r"pblancId=(PBLN_\d+)", url)
            if m:
                merged = merge_detail(
                    args.merge_into, m.group(1), digest,
                    [{"url": BASE + a, "filename": a.rsplit("/", 1)[-1]} for a in attach],
                    complete=not attach)  # 링크만 수집: 첨부가 있으면 아직 미추출
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
    dp.add_argument("--delay", type=positive_delay, default=MIN_DELAY)
    args = ap.parse_args()
    sys.exit(cmd_list(args) if args.cmd == "list" else cmd_detail(args))


if __name__ == "__main__":
    main()
