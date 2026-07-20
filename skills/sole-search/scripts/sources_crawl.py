#!/usr/bin/env python3
"""기업마당(bizinfo.go.kr) 모집중 공고 전수 크롤러 — sole-search.

ir-search의 검증된 bizinfo 파서를 이식·개조했다. 차이점:
  - 분야 필터 없음: 모집중(schEndAt=N) 전체 공고를 전 페이지 순회한다
    (소상공인 대상 사업이 '소상공인 분야' 밖에도 흩어져 있으므로 선별은 LLM이 한다)
  - 페이지 상한 없음: 1페이지의 페이지네이션에서 마지막 페이지를 읽어 끝까지 순회
  - 종료 시 stderr에 `PAGES <last> CRAWLED <n> COLLECTED <m>` 출력 (coverage 검증용)

사용법:
  python3 sources_crawl.py list -o bizinfo.jsonl [--delay 0.4]
  python3 sources_crawl.py detail <URL>... -o details/

공개 공고 페이지만 접근하며 로그인 영역은 다루지 않는다.
"""
import argparse
import html as htmllib
import json
import re
import sys
import time

DELAY = 0.4
BASE = "https://www.bizinfo.go.kr"
UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
      "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.4 Safari/605.1.15")


def make_fetcher():
    """curl_cffi가 있으면 사용(TLS 지문), 없으면 urllib."""
    try:
        from curl_cffi import requests as cr
        sess = cr.Session(impersonate="safari")

        def fetch(url):
            r = sess.get(url, timeout=30)
            return r.status_code, r.text

        return fetch, "curl_cffi"
    except ImportError:
        import urllib.request

        def fetch(url):
            req = urllib.request.Request(url, headers={"User-Agent": UA})
            with urllib.request.urlopen(req, timeout=30) as resp:
                return resp.status, resp.read().decode("utf-8", "replace")

        return fetch, "urllib"


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


def build_list_url(page):
    # 분야(pldirCd)·해시태그 필터를 걸지 않는다 — 모집중 전체
    return f"{BASE}/sii/siia/selectSIIA200View.do?rows=15&cpage={page}&schEndAt=N"


def parse_bizinfo_page(h):
    items = []
    for row in re.findall(r"<tr>[\s\S]*?</tr>", h):
        m = re.search(r'href\s*=\s*"([^"]*pblancId=(PBLN_\d+)[^"]*)"[^>]*>\s*([\s\S]*?)</a>', row)
        if not m:
            continue
        tds = [clean(re.sub(r"<[^>]+>", " ", td))
               for td in re.findall(r"<td[^>]*>([\s\S]*?)</td>", row)]
        # tds: [no, 분야, 제목셀, 접수기간, 소관부처, 수행기관, 등록일, 조회]
        start, end = split_period(tds[3]) if len(tds) > 3 else ("", "")
        items.append({
            "source": "bizinfo",
            "id": m.group(2),
            "title": clean(m.group(3)),
            "field": tds[1] if len(tds) > 1 else "",
            "org": " / ".join(x for x in tds[4:6] if x) if len(tds) > 5 else "",
            "apply_start": start,
            "apply_end": end,
            "reg_date": tds[6] if len(tds) > 6 else "",
            "url": f"{BASE}/sii/siia/selectSIIA200Detail.do?pblancId={m.group(2)}",
        })
    return items, bool(items)


def last_page(h):
    pages = [int(p) for p in re.findall(r"cpage=(\d+)", h)]
    return max(pages) if pages else 1


def collect_all_pages(fetch_page, max_page=None, delay=0.0):
    """fetch_page(n) -> (items, has_more)를 새 항목이 없거나 끝 페이지까지 순회."""
    seen = {}
    page = 1
    while True:
        items, has_more = fetch_page(page)
        new = [i for i in items if i["id"] not in seen]
        for i in items:
            seen[i["id"]] = i
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


def cmd_list(args):
    fetch, backend = make_fetcher()
    print(f"[sole-search] fetch backend: {backend}", file=sys.stderr)

    status, first = fetch(build_list_url(1))
    if status != 200:
        print(f"WARNING bizinfo: first page HTTP {status}", file=sys.stderr)
        return 2
    expected_pages = last_page(first)
    crawled_pages = 0

    def fetch_page(page):
        nonlocal crawled_pages
        h = first if page == 1 else None
        if h is None:
            s, h = fetch(build_list_url(page))
            if s != 200:
                return [], False
        crawled_pages = page
        return parse_bizinfo_page(h)

    items = collect_all_pages(fetch_page, max_page=expected_pages, delay=args.delay)
    with open(args.output, "w", encoding="utf-8") as f:
        for i in items:
            f.write(json.dumps(i, ensure_ascii=False) + "\n")
    print(f"PAGES {expected_pages} CRAWLED {crawled_pages} COLLECTED {len(items)}",
          file=sys.stderr)
    if crawled_pages < expected_pages:
        print(f"WARNING bizinfo: {expected_pages - crawled_pages} pages not crawled "
              "(중복 조기종료 또는 오류) — coverage_manifest에 partial로 기록할 것",
              file=sys.stderr)
        return 2
    return 0


def cmd_detail(args):
    fetch, _ = make_fetcher()
    import os
    os.makedirs(args.output, exist_ok=True)
    for url in args.urls:
        host = re.sub(r"^https?://([^/]+).*", r"\1", url)
        if not host.endswith("bizinfo.go.kr"):
            print(f"[sole-search] skip non-bizinfo url: {url[:60]}", file=sys.stderr)
            continue
        try:
            status, h = fetch(url)
            if status != 200:
                print(f"[sole-search] {url[:60]}: HTTP {status}", file=sys.stderr)
                continue
            # 첨부파일 링크 수집 (자격요건이 첨부에만 있는 공고 대비)
            attach = [htmllib.unescape(u) for u in
                      re.findall(r'href="(/cmm/fms/[^"]+|/uploads/[^"]+)"', h)]
            name = re.sub(r"\W+", "_", url.split("://", 1)[1])[:80]
            path = f"{args.output}/{name}.txt"
            with open(path, "w", encoding="utf-8") as f:
                f.write(url + "\n")
                f.write("ATTACHMENTS: " + json.dumps(
                    [BASE + a for a in attach], ensure_ascii=False) + "\n\n")
                f.write(strip_html(h))
            print(f"[sole-search] saved: {path}", file=sys.stderr)
        except Exception as e:  # noqa: BLE001 — 개별 실패는 건너뛰되 기록한다
            print(f"[sole-search] {url[:60]}: error {e}", file=sys.stderr)
        time.sleep(args.delay)
    return 0


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    sub = ap.add_subparsers(dest="cmd", required=True)
    lp = sub.add_parser("list", help="모집중 전체 공고 전 페이지 수집")
    lp.add_argument("-o", "--output", default="bizinfo.jsonl")
    lp.add_argument("--delay", type=float, default=DELAY)
    dp = sub.add_parser("detail", help="상세 페이지 텍스트+첨부링크 저장")
    dp.add_argument("urls", nargs="+")
    dp.add_argument("-o", "--output", default="details")
    dp.add_argument("--delay", type=float, default=DELAY)
    args = ap.parse_args()
    sys.exit(cmd_list(args) if args.cmd == "list" else cmd_detail(args))


if __name__ == "__main__":
    main()
