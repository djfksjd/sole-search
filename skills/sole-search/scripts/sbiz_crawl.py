#!/usr/bin/env python3
"""소상공인24(sbiz24.kr) 공고 크롤러 — 표준 라이브러리만 사용.

두 목록을 수집한다:
  pbanc   — 소진공 자체 공고 (POST /api/pbanc/sbiz24PbancList)
  combine — 지원사업 통합조회, 지자체·유관기관 포함 (POST /api/combinePbanc/list)

사용법:
  python3 sbiz_crawl.py list [pbanc|combine|all] -o out.jsonl [--page-size 100] [--delay 0.5]
      [--max-pages N]
  python3 sbiz_crawl.py detail <pbancSn> [--source sbiz24|sbiz24_combine]
      [-o out.json] [--download-dir DIR] [--merge-into out.jsonl]

detail 분기 (fail-closed):
  - 대상 ID가 PBLN_* 이면 기업마당 공고다 — sources_crawl.py detail <bizinfo URL>로
    검증하라는 안내와 함께 exit 2.
  - --source sbiz24_combine (비PBLN): 2026-07-23 스파이크로 상세 계약 확인 —
    SPA 라우팅상 combine 행은 pbancGubun A(공단)·D(지방정부)가 /pbanc/{sn}(기존
    상세 API 그대로), C(대출상품)가 /loanProduct/{sn}, B(PBLN)가 /extldPbanc/{id}로
    간다. A/D는 소진공 pbancSn 네임스페이스를 공유하므로 POST /api/pbanc/{sn}으로
    상세를 읽는다. **대출상품(C)은 sn 네임스페이스가 달라(같은 숫자가 다른 공고)
    기존 API로 읽으면 조용히 다른 공고를 읽는다 — 계속 fail-closed(exit 2).**
    오독 방지를 위해 --merge-into(목록 jsonl)가 필수다: 레코드의 raw.bizType으로
    대출상품을 거르고, 상세 응답 제목이 목록 제목과 다르면 네임스페이스 불일치로
    exit 2 한다(조용한 오동작 금지). content_hash 산식·병합은 pbanc와 동일(v2/v3).
  - 상세 조회 기본은 --source sbiz24(pbanc API)다.

계약: references/sources.md 참조. 필수 헤더 Origin-Method: GET.
종료 시 stderr에 `TOTAL <n> COLLECTED <m>` 출력 (coverage 검증용).
수집 실패(부분 수집·첨부 다운로드/추출 실패 포함)면 종료 코드 2, 차단 신호는 3.
목록 정렬: sortModel의 pbancSn desc 정렬은 실호출 검증 실패(colId/field/sortColumn
전부 비단조 응답, 2026-07-23) — 정렬 없이 수집하며 DUPLICATES>0이면 삽입 경합
가능성을 stderr로 경고한다.
"""
import argparse
import hashlib
import html
import json
import os
import pathlib
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


class ManualEscalation(RuntimeError):
    """401/403 — 우회하지 않고 manual로 전환하라는 신호 (종료 코드 3)."""


def post(path, body, retries=3, delay=0.5):
    req = urllib.request.Request(BASE + path, data=json.dumps(body).encode(), headers=HEADERS)
    last = None
    for i in range(retries):
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                data = json.loads(r.read().decode())
            if data.get("result") is False or "data" not in data:
                raise RuntimeError(f"POST {path}: API result=false 또는 구조 변경 "
                                   f"({str(data)[:120]})")
            return data
        except urllib.error.HTTPError as e:
            if e.code in (401, 403):
                raise ManualEscalation(f"HTTP {e.code} — 차단/인증 요구. manual 전환") from e
            last = e
            if not (e.code == 429 or e.code >= 500):
                break  # 4xx는 재시도 무의미
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as e:
            last = e
        time.sleep(delay * (i + 1))
    raise RuntimeError(f"POST {path} failed after {retries} tries: {last}")


def now_kst():
    return datetime.now(KST).strftime("%Y-%m-%dT%H:%M:%S+09:00")


def total_count(data):
    return data["data"]["default"]["total"]


def parse_list_page(data):
    return data["data"]["default"]["list"]


def _period(p):
    if not isinstance(p, dict):
        return None, None
    return (p.get("from") or None), (p.get("to") or None)


def _status(item):
    """사이트 표현을 status enum으로 정규화. 판정 불가면 '불명' — 낙관 추정 금지."""
    aply = str(item.get("aplyPsbltySe") or "")
    if aply in ("Y", "신청가능"):
        return "접수중"
    if aply in ("N", "신청불가", "마감"):
        return "마감"
    if "상시" in aply:
        return "상시"
    if "예산" in aply or "소진" in aply:
        return "예산소진"
    if "예정" in aply:
        return "회차예정"
    # aplyPsbltySe가 비면 접수 마감일로 판정 (combine의 aplyPd는 "시작 ~ 끝" 문자열)
    end = _period(item.get("rcptPd"))[1] if item.get("rcptPd") else None
    if end is None and item.get("aplyPd"):
        parts = str(item.get("aplyPd")).split("~")
        end = parts[1].strip() if len(parts) == 2 else None
    if end:
        try:
            end_d = datetime.strptime(str(end)[:10], "%Y-%m-%d").replace(tzinfo=KST)
            return "접수중" if end_d >= datetime.now(KST) - timedelta(days=1) else "마감"
        except ValueError:
            pass
    return "불명"


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
        "tags": [s.strip() for s in str(item.get("hstgNm") or "").split(",") if s.strip()],
        "attachments": [],
        "attachments_complete": False,
        "crawled_at": crawled,
        "content_hash": None,
        "raw": {k: item.get(k) for k in ("rcrtTypeCdNm", "bizType", "pbancKindCd",
                                          "pbancGubun", "ddlnDayCnt", "bizYr")
                if item.get(k) is not None},
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
        if not fid:
            continue
        out.append({
            "file_id": fid,
            "filename": f.get("fileNm") or "",
            "size": f.get("fileSz"),
            "url": f"{BASE}/api/cmmn/file/{fid}",
            "pbanc_sn": str(pbanc_sn),
        })
    return out


def crawl_list(mode, out, page_size=100, delay=0.5, max_pages=None):
    """total(서버 고지 행수), collected(고유 저장 건수), fetched(실제 수신 행수)를 반환.

    서버가 같은 공고를 두 행으로 반환하는 경우가 있어(통합조회 이중 게재)
    collected < total이어도 fetched == total이면 전수 수집 성공이다.
    max_pages는 CI smoke용 페이지 상한 — 걸리면 coverage 검증은 호출부가 생략한다.
    """
    url = LIST_URLS[mode]
    start, total, collected, fetched, pages = 0, None, 0, 0, 0
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
        pages += 1
        if max_pages and pages >= max_pages:
            break
        if start >= total:
            break
        time.sleep(delay)
    return total, collected, fetched


def cmd_list(args):
    modes = ["pbanc", "combine"] if args.target == "all" else [args.target]
    grand_total = grand_collected = 0
    ok = True
    max_pages = getattr(args, "max_pages", None)
    tmp_path = args.output + ".tmp"
    with open(tmp_path, "w", encoding="utf-8") as out:
        for mode in modes:
            try:
                total, collected, fetched = crawl_list(mode, out, args.page_size,
                                                       args.delay, max_pages=max_pages)
            except ManualEscalation:
                raise  # 차단 신호 — main에서 exit 3
            except RuntimeError as e:
                print(f"WARNING {mode}: {e}", file=sys.stderr)
                ok = False
                continue
            dup = fetched - collected
            print(f"{mode}: TOTAL {total} COLLECTED {collected} DUPLICATES {dup}",
                  file=sys.stderr)
            if dup > 0:
                # 목록이 명시적 정렬 없이 페이지네이션된다(sortModel 검증 실패) —
                # 수집 중 신규 공고 삽입으로 경계가 밀려 중복/누락이 생길 수 있다
                print(f"WARNING {mode}: DUPLICATES {dup} — 삽입 경합 가능 — 재실행 권장",
                      file=sys.stderr)
            if total == 0 or collected == 0:
                print(f"WARNING {mode}: total={total} collected={collected} — "
                      "0건 수집은 API 구조 변경/장애 신호, failed로 기록할 것",
                      file=sys.stderr)
                ok = False
            grand_total += total
            grand_collected += collected
            if max_pages:
                continue  # smoke: 페이지 상한이 걸림 — coverage 검증 생략
            if fetched < total:  # 페이지 순회가 서버 총건수에 미달
                ok = False
            elif dup > 0 and collected < total:
                # fetched==total이어도 중복이 누락을 상쇄했을 수 있다(페이지 경계
                # 삽입 경합) — 고유 수집분이 총건수에 못 미치면 partial로 본다
                print(f"WARNING {mode}: 고유 {collected} < 총 {total} (중복 {dup}) — "
                      "누락 가능, partial", file=sys.stderr)
                ok = False
    os.replace(tmp_path, args.output)
    print(f"TOTAL {grand_total} COLLECTED {grand_collected}", file=sys.stderr)
    return 0 if ok else 2


MAX_ATTACH_BYTES = 50 * 1024 * 1024  # 첨부 다운로드 상한 50MB


def safe_filename(file_id, filename):
    """서버 제공 파일명을 신뢰하지 않는다 — basename + 문자 정제 + fileId 프리픽스."""
    base = re.sub(r"[^\w.\-가-힣()\[\] ]", "_", filename.replace("\\", "/").rsplit("/", 1)[-1])
    return f"{file_id[:8]}_{base[:120]}" if base else file_id


def download_capped(url, path):
    req = urllib.request.Request(url, headers=HEADERS)
    try:
        with urllib.request.urlopen(req, timeout=60) as r, open(path, "wb") as fh:
            length = r.headers.get("Content-Length")
            if length and int(length) > MAX_ATTACH_BYTES:
                raise RuntimeError(f"첨부 Content-Length가 상한 초과: {length}")
            read = 0
            while True:
                chunk = r.read(1 << 20)
                if not chunk:
                    return read
                read += len(chunk)
                if read > MAX_ATTACH_BYTES:
                    raise RuntimeError(f"첨부가 {MAX_ATTACH_BYTES // (1 << 20)}MB 상한 초과")
                fh.write(chunk)
    except urllib.error.HTTPError as e:
        if e.code in (401, 403):
            raise ManualEscalation(f"첨부 다운로드 HTTP {e.code}") from e
        raise
    except (RuntimeError, OSError):
        path.unlink(missing_ok=True)  # 부분 파일 잔존 방지
        raise


HASH_VERSION = 2  # sources/region_crawl과 동일 산식 세대 — diff의 산식 전환 감지에 필요


def content_hash_of(body_text, attachment_hashes):
    payload = body_text + "\n" + "\n".join(sorted(attachment_hashes))
    return hashlib.sha256(payload.encode()).hexdigest()


def _norm_title(t):
    return re.sub(r"\s+", " ", html.unescape(t or "")).strip()


def find_record(jsonl_path, source, source_id):
    with open(jsonl_path, encoding="utf-8") as f:
        for line in f:
            if not line.strip():
                continue
            r = json.loads(line)
            if r.get("source") == source and str(r.get("source_id")) == str(source_id):
                return r
    return None


def cmd_detail(args):
    sn = str(args.pbanc_sn)
    if sn.startswith("PBLN_"):
        print(f"ERROR: {sn} 은 기업마당 공고(PBLN_*) — sbiz24 상세 API 대상이 아니다. "
              "sources_crawl.py detail <bizinfo URL>로 검증하라 "
              f"(URL: https://www.bizinfo.go.kr/sii/siia/selectSIIA200Detail.do?pblancId={sn})",
              file=sys.stderr)
        return 2
    list_rec = None
    if args.source == "sbiz24_combine":
        # 계약(2026-07-23 실호출 검증): 비PBLN combine 행 중 공단(A)·지방정부(D)는
        # 소진공 pbancSn 네임스페이스를 공유해 POST /api/pbanc/{sn}으로 읽힌다.
        # 대출상품(C, /loanProduct/{sn})은 sn 네임스페이스가 달라 같은 숫자가 다른
        # 공고를 가리킨다 — 오독 방지를 위해 목록 레코드로 검증한다 (fail-closed).
        if not args.merge_into:
            print(f"ERROR: --source sbiz24_combine 은 --merge-into <combine 목록 jsonl> "
                  f"필수 — {sn} 이 대출상품(별도 네임스페이스)인지 목록 레코드 없이 "
                  "판정할 수 없다 (fail-closed)", file=sys.stderr)
            return 2
        list_rec = find_record(args.merge_into, "sbiz24_combine", sn)
        if list_rec is None:
            print(f"ERROR: {args.merge_into} 에 sbiz24_combine/{sn} 레코드 없음 — "
                  "combine 목록 jsonl로 다시 시도하라 (fail-closed)", file=sys.stderr)
            return 2
        if (list_rec.get("raw") or {}).get("bizType") == "대출상품":
            print(f"ERROR: {sn} 은 대출상품(/loanProduct 네임스페이스) — pbanc 상세 API로 "
                  "읽으면 다른 공고를 읽는다. 계약 미확인, canonical_url로 수동 확인하라 "
                  "(fail-closed)", file=sys.stderr)
            return 2
    detail_data = post(f"/api/pbanc/{args.pbanc_sn}", {})
    detail = parse_detail(detail_data)
    if list_rec is not None and _norm_title(detail["title"]) != _norm_title(
            list_rec.get("title")):
        print(f"ERROR: 상세 제목({detail['title'][:40]!r})이 목록 제목"
              f"({str(list_rec.get('title'))[:40]!r})과 불일치 — pbancSn 네임스페이스 "
              "불일치 의심, 기록하지 않는다 (fail-closed)", file=sys.stderr)
        return 2
    time.sleep(args.delay)
    files_data = post("/api/cmmn/file", {"search": {"groupId": f"pbancdoc-{args.pbanc_sn}",
                                                    "tmprStrgYn": "N", "delYn": False}})
    detail["attachments"] = parse_files(files_data, args.pbanc_sn)
    attach_hashes = []
    if args.download_dir:
        import attach_extract  # 같은 디렉토리의 추출기 — 추출 성공까지 확인해야 complete
        d = pathlib.Path(args.download_dir).resolve()
        d.mkdir(parents=True, exist_ok=True)
        for f in detail["attachments"]:
            time.sleep(args.delay)
            path = (d / safe_filename(f["file_id"], f["filename"])).resolve()
            if os.path.commonpath([str(path), str(d)]) != str(d) or path.is_symlink():
                f["download_status"] = "failed"
                f["extract_status"] = "failed"
                f["extract_reason"] = "path_escape_blocked"
                continue
            try:
                download_capped(f["url"], path)
                f["local_path"] = str(path)
                f["download_status"] = "ok"
                f["sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()
                attach_hashes.append(f["sha256"])
            except ManualEscalation:
                raise  # 차단 신호 — main에서 exit 3
            except (urllib.error.URLError, urllib.error.HTTPError, RuntimeError, OSError) as e:
                f["download_status"] = "failed"
                f["extract_status"] = "failed"
                f["extract_reason"] = str(e)
                print(f"WARNING attachment {f['filename']}: {e}", file=sys.stderr)
                continue
            r = attach_extract.extract(str(path))
            if r["ok"] and not r.get("reason"):
                f["extract_status"] = "ok"
                text_path = str(path) + ".txt"
                pathlib.Path(text_path).write_text(r["text"], encoding="utf-8")
                f["text_path"] = text_path
            elif r["ok"]:
                # 부분 추출(예: hwp_preview_only) — 텍스트는 저장하되 complete 아님,
                # SKILL.md 규칙: attachments_complete=false → '확인됨' 금지
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
    # complete = 첨부가 없거나, 모든 첨부의 추출까지 성공 (partial은 미완)
    complete = all(f.get("extract_status") == "ok" for f in detail["attachments"]) \
        if detail["attachments"] else True
    if not args.download_dir and detail["attachments"]:
        complete = False  # 다운로드/추출 안 함
    detail["attachments_complete"] = complete
    downloads_ok = all(f.get("download_status") == "ok" for f in detail["attachments"]) \
        if detail["attachments"] else True
    if args.download_dir and not downloads_ok:
        detail["content_hash"] = None  # 불완전 해시 대신 None — NEEDS_REHASH 계약과 일치
    elif not args.download_dir and detail["attachments"]:
        detail["content_hash"] = None  # 첨부 미다운로드 — 본문만으로는 완전한 해시가 아니다
    else:
        detail["content_hash"] = content_hash_of(detail["body_text"], attach_hashes)
    # 상세 JSON은 merge 성패와 무관하게 먼저 기록한다 (기록 없이 return 금지)
    text = json.dumps(detail, ensure_ascii=False, indent=2)
    if args.output:
        open(args.output, "w", encoding="utf-8").write(text)
    else:
        print(text)
    rc = 0
    if args.merge_into:
        merged = merge_detail(args.merge_into, detail["source_id"], detail["content_hash"],
                              detail["attachments"], complete, source=args.source)
        if not merged:
            print(f"WARNING: source_id={detail['source_id']} 레코드를 "
                  f"{args.merge_into}에서 못 찾음", file=sys.stderr)
            rc = 2
    # 첨부 다운로드/추출 실패·부분이 1건 이상이면 partial (조용한 누락 금지)
    if args.download_dir:
        bad = [f["filename"] for f in detail["attachments"]
               if f.get("extract_status") != "ok"]
        if bad:
            print(f"WARNING sbiz24 detail: 첨부 {len(bad)}건 다운로드/추출 실패·부분 "
                  f"({', '.join(bad[:5])}) — partial", file=sys.stderr)
            rc = 2
    return rc


def merge_detail(jsonl_path, source_id, content_hash, attachments, complete, source="sbiz24"):
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
                    r["hash_version"] = HASH_VERSION
                else:
                    # 해시 없음 = 산식 버전도 무의미 — 낡은 version 잔존 금지
                    r.pop("hash_version", None)
                r["attachments"] = attachments
                r["attachments_complete"] = complete
                found = True
            dst.write(json.dumps(r, ensure_ascii=False) + "\n")
    os.replace(tmp, jsonl_path)
    return found


def positive_int(v):
    n = int(v)
    if n <= 0:
        raise argparse.ArgumentTypeError("양수여야 한다")
    return n


def min_delay(v):
    f = float(v)
    if f < 0.5:
        raise argparse.ArgumentTypeError("딜레이는 최소 0.5초 (예의상 강제)")
    return f


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    sub = ap.add_subparsers(dest="cmd", required=True)
    lp = sub.add_parser("list")
    lp.add_argument("target", choices=["pbanc", "combine", "all"], nargs="?", default="all")
    lp.add_argument("-o", "--output", required=True)
    lp.add_argument("--page-size", type=positive_int, default=100)
    lp.add_argument("--max-pages", type=positive_int, default=None,
                    help="페이지 상한 (CI smoke용 — coverage 검증 생략)")
    lp.add_argument("--delay", type=min_delay, default=0.5)
    dp = sub.add_parser("detail")
    dp.add_argument("pbanc_sn")
    dp.add_argument("--source", choices=["sbiz24", "sbiz24_combine"], default="sbiz24",
                    help="레코드의 source 필드 값 — sbiz24_combine은 --merge-into 필수, "
                         "대출상품(bizType) 레코드는 fail-closed(exit 2)")
    dp.add_argument("-o", "--output")
    dp.add_argument("--download-dir")
    dp.add_argument("--merge-into", help="목록 jsonl에 content_hash·첨부 결과 병합")
    dp.add_argument("--delay", type=min_delay, default=0.5)
    args = ap.parse_args()
    try:
        sys.exit(cmd_list(args) if args.cmd == "list" else cmd_detail(args))
    except ManualEscalation as e:
        print(f"MANUAL sbiz24: {e}", file=sys.stderr)
        sys.exit(3)
    except (RuntimeError, KeyError, urllib.error.URLError, TimeoutError,
            json.JSONDecodeError) as e:
        # 계약 밖 traceback(exit 1) 대신 WARNING + partial — API 구조 변경/네트워크 장애
        print(f"WARNING sbiz24: {type(e).__name__}: {e} — failed/partial로 기록할 것",
              file=sys.stderr)
        sys.exit(2)


if __name__ == "__main__":
    main()
