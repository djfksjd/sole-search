#!/usr/bin/env python3
"""소상공인24(sbiz24.kr) 공고 크롤러 — 표준 라이브러리만 사용.

두 목록을 수집한다:
  pbanc   — 소진공 자체 공고 (POST /api/pbanc/sbiz24PbancList)
  combine — 지원사업 통합조회, 지자체·유관기관 포함 (POST /api/combinePbanc/list)

사용법:
  python3 sbiz_crawl.py list [pbanc|combine|all] -o out.jsonl [--page-size 100] [--delay 0.5]
  python3 sbiz_crawl.py detail <pbancSn> [-o out.json] [--download-dir DIR]

계약: references/sources.md 참조. 필수 헤더 Origin-Method: GET.
종료 시 stderr에 `TOTAL <n> COLLECTED <m>` 출력 (coverage 검증용).
수집 실패(부분 수집)면 종료 코드 2.
"""
import argparse
import html
import json
import re
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone, timedelta

BASE = "https://www.sbiz24.kr"
KST = timezone(timedelta(hours=9))
HEADERS = {
    "Content-Type": "application/json",
    "Accept": "application/json",
    "Origin-Method": "GET",
    "User-Agent": "sole-search/0.1 (github.com/djfksjd/sole-search)",
}
EMPTY_SEARCH = {
    "searchValue": "", "rcrtTypeCdNmList": [], "rcrtTypeCdNmListDisplay": "",
    "regionNmList": [], "regionNmListDisplay": "", "tpbizCdList": [], "tpbizCdListDisplay": "",
    "bhis": {"from": None, "to": None}, "wrkr": {"from": None, "to": None},
    "sls": {"from": None, "to": None}, "aplySeYn": "N", "sbrPbancYn": "N", "itrstPbancYn": "N",
    "departNmList": None, "searchBox": None, "departNmListDisplay": "",
    "ptPbancSortBy": None, "pbancNm": None, "regionCdList": [],
}
LIST_URLS = {"pbanc": "/api/pbanc/sbiz24PbancList", "combine": "/api/combinePbanc/list"}


def post(path, body, retries=3, delay=0.5):
    req = urllib.request.Request(BASE + path, data=json.dumps(body).encode(), headers=HEADERS)
    last = None
    for i in range(retries):
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                return json.loads(r.read().decode())
        except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError) as e:
            last = e
            time.sleep(delay * (i + 1))
    raise RuntimeError(f"POST {path} failed after {retries} tries: {last}")


def now_kst():
    return datetime.now(KST).strftime("%Y-%m-%d %H:%M:%S+09:00")


def total_count(data):
    return data["data"]["default"]["total"]


def parse_list_page(data):
    return data["data"]["default"]["list"]


def _period(p):
    if not isinstance(p, dict):
        return None, None
    return (p.get("from") or None), (p.get("to") or None)


def _status(item):
    """사이트 표현을 status enum으로 정규화한다."""
    aply = str(item.get("aplyPsbltySe") or "")
    _, end = _period(item.get("rcptPd")) if item.get("rcptPd") else (None, item.get("aplyPd"))
    if aply in ("Y", "신청가능"):
        return "접수중"
    if aply in ("N", "신청불가", "마감"):
        return "마감"
    # aplyPsbltySe가 비어있으면 접수기간으로 판정
    if end:
        try:
            end_d = datetime.strptime(str(end)[:10], "%Y-%m-%d").replace(tzinfo=KST)
            return "접수중" if end_d >= datetime.now(KST) - timedelta(days=1) else "마감"
        except ValueError:
            pass
    return "접수중"


def strip_html(text):
    text = re.sub(r"<[^>]+>", " ", text or "")
    text = html.unescape(text)
    return re.sub(r"\s+", " ", text).strip()


def to_record(item, mode):
    crawled = now_kst()
    if mode == "pbanc":
        sn = str(item.get("pbancSn") or "")
        start, end = _period(item.get("rcptPd"))
        rec = {
            "source": "sbiz24",
            "source_id": sn,
            "announce_no": None,
            "canonical_url": f"{BASE}/#/pbanc/{sn}",
            "title": item.get("pbancNm") or "",
            "agency": "소상공인시장진흥공단",
            "region_scope": item.get("regionNmList") or "전국",
            "apply_start": start,
            "apply_end": end,
        }
    else:  # combine
        pid = str(item.get("pbancId") or item.get("pbancSn") or "")
        aply_pd = str(item.get("aplyPd") or "")
        parts = [p.strip() for p in aply_pd.split("~")] if "~" in aply_pd else [None, None]
        rec = {
            "source": "sbiz24_combine",
            "source_id": pid,
            "announce_no": pid if pid.startswith("PBLN") else None,
            "canonical_url": f"{BASE}/#/extldPbanc/{pid}" if pid.startswith("PBLN")
                             else f"{BASE}/#/pbanc/{pid}",
            "title": item.get("pbancNm") or "",
            "agency": item.get("departNm") or item.get("rcrtTypeCdNm") or "미상",
            "region_scope": item.get("regionNmList") or "전국",
            "apply_start": parts[0] or None,
            "apply_end": parts[1] or None,
        }
    rec.update({
        "status": _status(item),
        "primary_type": None,   # 판정 단계(LLM)에서 채운다
        "tags": [t for t in str(item.get("hstgNm") or "").split(",") if t],
        "attachments": [],
        "crawled_at": crawled,
        "content_hash": None,
        "raw": {k: item.get(k) for k in ("rcrtTypeCdNm", "bizType", "pbancKindCd",
                                          "ddlnDayCnt", "bizYr") if item.get(k) is not None},
    })
    return rec


def parse_detail(data):
    d = data["data"]["default"]
    return {
        "source_id": str(d.get("pbancSn") or ""),
        "title": d.get("pbancNm") or "",
        "body_text": strip_html(d.get("pbancDtlCn") or ""),
        "apply_start": _period(d.get("rcptPd"))[0],
        "apply_end": _period(d.get("rcptPd"))[1],
        "status_code": d.get("pbancSttsCd"),
    }


def parse_files(data, pbanc_sn):
    out = []
    for f in data["data"]["default"]["list"]:
        fid = f.get("fileId") or ""
        out.append({
            "file_id": fid,
            "filename": f.get("fileNm") or "",
            "size": f.get("fileSz"),
            "url": f"{BASE}/api/cmmn/file/{fid}",
            "pbanc_sn": str(pbanc_sn),
        })
    return out


def crawl_list(mode, out, page_size=100, delay=0.5):
    """total(서버 고지 행수), collected(고유 저장 건수), fetched(실제 수신 행수)를 반환.

    서버가 같은 공고를 두 행으로 반환하는 경우가 있어(통합조회 이중 게재)
    collected < total이어도 fetched == total이면 전수 수집 성공이다.
    """
    url = LIST_URLS[mode]
    start, total, collected, fetched = 0, None, 0, 0
    seen = set()
    while True:
        body = {"sortModel": [], "search": dict(EMPTY_SEARCH), "paging": True,
                "startRow": start, "endRow": start + page_size}
        data = post(url, body, delay=delay)
        if total is None:
            total = total_count(data)
        items = parse_list_page(data)
        if not items:
            break
        for item in items:
            fetched += 1
            rec = to_record(item, mode)
            if rec["source_id"] in seen:
                continue
            seen.add(rec["source_id"])
            out.write(json.dumps(rec, ensure_ascii=False) + "\n")
            collected += 1
        start += page_size
        if start >= total:
            break
        time.sleep(delay)
    return total, collected, fetched


def cmd_list(args):
    modes = ["pbanc", "combine"] if args.target == "all" else [args.target]
    grand_total = grand_collected = 0
    ok = True
    with open(args.output, "w", encoding="utf-8") as out:
        for mode in modes:
            try:
                total, collected, fetched = crawl_list(mode, out, args.page_size, args.delay)
            except RuntimeError as e:
                print(f"WARNING {mode}: {e}", file=sys.stderr)
                ok = False
                continue
            dup = fetched - collected
            print(f"{mode}: TOTAL {total} COLLECTED {collected} DUPLICATES {dup}",
                  file=sys.stderr)
            grand_total += total
            grand_collected += collected
            if fetched < total:  # 중복 제외 실누락만 실패로 본다
                ok = False
    print(f"TOTAL {grand_total} COLLECTED {grand_collected}", file=sys.stderr)
    return 0 if ok else 2


def cmd_detail(args):
    detail_data = post(f"/api/pbanc/{args.pbanc_sn}", {})
    detail = parse_detail(detail_data)
    time.sleep(args.delay)
    files_data = post("/api/cmmn/file", {"search": {"groupId": f"pbancdoc-{args.pbanc_sn}",
                                                    "tmprStrgYn": "N", "delYn": False}})
    detail["attachments"] = parse_files(files_data, args.pbanc_sn)
    if args.download_dir:
        import pathlib
        d = pathlib.Path(args.download_dir)
        d.mkdir(parents=True, exist_ok=True)
        for f in detail["attachments"]:
            time.sleep(args.delay)
            req = urllib.request.Request(f["url"], headers=HEADERS)
            try:
                with urllib.request.urlopen(req, timeout=60) as r:
                    path = d / f["filename"]
                    path.write_bytes(r.read())
                    f["local_path"] = str(path)
            except (urllib.error.URLError, urllib.error.HTTPError) as e:
                f["download_error"] = str(e)
                print(f"WARNING attachment {f['filename']}: {e}", file=sys.stderr)
    text = json.dumps(detail, ensure_ascii=False, indent=2)
    if args.output:
        open(args.output, "w", encoding="utf-8").write(text)
    else:
        print(text)
    return 0


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    sub = ap.add_subparsers(dest="cmd", required=True)
    lp = sub.add_parser("list")
    lp.add_argument("target", choices=["pbanc", "combine", "all"], nargs="?", default="all")
    lp.add_argument("-o", "--output", required=True)
    lp.add_argument("--page-size", type=int, default=100)
    lp.add_argument("--delay", type=float, default=0.5)
    dp = sub.add_parser("detail")
    dp.add_argument("pbanc_sn")
    dp.add_argument("-o", "--output")
    dp.add_argument("--download-dir")
    dp.add_argument("--delay", type=float, default=0.5)
    args = ap.parse_args()
    sys.exit(cmd_list(args) if args.cmd == "list" else cmd_detail(args))


if __name__ == "__main__":
    main()
