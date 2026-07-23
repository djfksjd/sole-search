#!/usr/bin/env python3
"""보조금24(대한민국 공공서비스 정보) 오픈 API 크롤러 — sole-search 선택 소스.

gov.kr(보조금24)은 robots가 크롤을 불허한다 — 공공데이터포털 오픈 API가 유일한
원칙적 경로다(우회 금지). API 키가 필요하므로 **선택 소스**로 동작한다:
키가 없으면 종료 코드 4(INACTIVE)로 끝나고, coverage_manifest에
"미활성(선택 소스, API 키 미등록)"으로 기록한다 — 조용한 누락 금지.

키 발급(무료·자동승인): data.go.kr → "대한민국 공공서비스(혜택) 정보"(15113968)
활용신청 → 마이페이지에서 일반 인증키 확인.

키 등록 위치 (우선순위순 — 프로필/보고서 폴더에는 절대 저장하지 않는다):
  1. --key 인자
  2. 환경변수 DATA_GO_KR_API_KEY
  3. ~/.config/sole-search/api_key (첫 줄)

커버리지 특성: sbiz24/bizinfo가 잡는 "모집 공고"가 아니라 **상시 수혜 제도**
(요금감면·수당·지자체 혜택) 중심이다. 신청기한이 자유 텍스트라 상시/불명이 많다 —
보고서에서는 "상시 혜택" 별도 섹션으로 다룬다(오늘 신청할 것과 분리).

응답 필드는 공식 v1 스와거(한글 필드명·SVC_ID) 기준이되, v3의 서비스ID 변형도
수용한다. 목록 첫 페이지에서 식별자·서비스명 필드를 못 찾으면 즉시 실패(fail-closed).

사용법:
  python3 gov24_crawl.py list -o gov24.jsonl [--filter-target 소상공인] [--key KEY]
  python3 gov24_crawl.py detail <서비스ID>... -o details/ [--merge-into gov24.jsonl]

종료 코드: 0 성공 / 2 부분·실패(응답 코드상 키 인증 실패 포함) /
3 수동전환(HTTP 401/403 — 서버가 인증을 거부) / 4 미활성(키 미등록).
stderr 마지막 줄: TOTAL <n> COLLECTED <m> (--filter-target 시 MATCHED <k> 포함,
MATCHED는 dedup 후 카운트)
"""
import argparse
import hashlib
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone, timedelta

MIN_DELAY = 0.5
PER_PAGE = 500
BASE = "https://api.odcloud.kr/api/gov24/v3"
KST = timezone(timedelta(hours=9))
KEY_FILE = os.path.expanduser("~/.config/sole-search/api_key")

# 식별자·필드명은 버전에 따라 다를 수 있다 — 앞에서부터 먼저 발견되는 키를 쓴다
ID_KEYS = ("서비스ID", "SVC_ID", "svcId", "serviceId")
FIELD_KEYS = {
    "title": ("서비스명",),
    "purpose": ("서비스목적요약", "서비스목적"),
    "deadline": ("신청기한",),
    "target": ("지원대상",),
    "criteria": ("선정기준",),
    "content": ("지원내용",),
    "how": ("신청방법",),
    "docs": ("구비서류",),
    "recv_org": ("접수기관명", "접수기관"),
    "org": ("소관기관명",),
    "dept": ("부서명",),
    "type": ("지원유형",),
    "tel": ("문의처전화번호", "전화문의"),
    "url": ("온라인신청사이트URL", "상세조회URL"),
    "updated": ("수정일시",),
    "user_type": ("사용자구분",),   # 개인/법인 등 — v3 실응답 검증(2026-07-20)
    "field": ("서비스분야",),
}


class ManualEscalation(RuntimeError):
    """HTTP 401/403 — 서버가 인증을 거부. 우회하지 않고 manual 전환(종료 코드 3)."""


def pick(rec, keys):
    for k in keys:
        v = rec.get(k)
        if v not in (None, ""):
            return str(v).strip()
    return None


def resolve_key(arg_key):
    if arg_key:
        return arg_key.strip()
    env = os.environ.get("DATA_GO_KR_API_KEY", "").strip()
    if env:
        return env
    try:
        with open(KEY_FILE, encoding="utf-8") as f:
            return f.readline().strip()
    except OSError:
        return ""


def encode_key(key):
    """data.go.kr 키는 이미 URL 인코딩된 형태('%2B' 등)로 발급되기도 한다 —
    '%'가 있으면 그대로, 없으면 인코딩해서 이중 인코딩을 피한다."""
    return key if "%" in key else urllib.parse.quote(key, safe="")


def api_get(path, key, params):
    qs = "&".join([f"serviceKey={encode_key(key)}"] +
                  [f"{urllib.parse.quote(str(k))}={urllib.parse.quote(str(v))}"
                   for k, v in params.items()])
    url = f"{BASE}/{path}?{qs}"
    req = urllib.request.Request(url, headers={"Accept": "application/json"})
    last = None
    for i in range(3):
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                payload = json.loads(resp.read().decode("utf-8", "replace"))
            code = payload.get("code")
            if code in (-401, 401):
                raise PermissionError(f"인증 실패: {payload.get('msg')}")
            return payload
        except urllib.error.HTTPError as e:
            if e.code in (401, 403):
                raise ManualEscalation(f"HTTP {e.code} — 인증 거부") from e
            last = e
            if not (e.code == 429 or e.code >= 500):
                break
            time.sleep(MIN_DELAY * (i + 1) * 2)
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as e:
            last = e
            time.sleep(MIN_DELAY * (i + 1) * 2)
    raise RuntimeError(f"{path} failed after retries: {last}")


def status_from_deadline(text):
    """신청기한은 자유 텍스트다. 확실한 것만 판정하고 나머지는 불명으로 남긴다."""
    t = (text or "").strip()
    if not t:
        return "불명", None
    if re.search(r"상시|연중|수시", t):
        return "상시", None
    m = re.search(r"(\d{4})[.\-/년\s]*(\d{1,2})[.\-/월\s]*(\d{1,2})[일.]?\s*(?:까지|마감)?\s*$", t)
    if m:
        end = f"{m.group(1)}-{int(m.group(2)):02d}-{int(m.group(3)):02d}"
        try:
            dt = datetime.strptime(end, "%Y-%m-%d").replace(tzinfo=KST)
            return ("접수중" if dt >= datetime.now(KST) - timedelta(days=1)
                    else "마감"), end
        except ValueError:
            pass
    return "불명", None


def to_record(rec):
    sid = pick(rec, ID_KEYS)
    title = pick(rec, FIELD_KEYS["title"])
    if not sid or not title:
        return None
    status, apply_end = status_from_deadline(pick(rec, FIELD_KEYS["deadline"]))
    agency = " / ".join(x for x in [pick(rec, FIELD_KEYS["org"]),
                                    pick(rec, FIELD_KEYS["dept"])] if x)
    url = pick(rec, FIELD_KEYS["url"]) or \
        f"https://www.gov.kr/portal/rcvfvrSvc/dtlEx/{sid}"
    return {
        "source": "gov24", "source_id": sid, "announce_no": sid,
        "canonical_url": url, "title": title, "agency": agency,
        "region_scope": None,
        "apply_start": None, "apply_end": apply_end, "status": status,
        "primary_type": None,
        "tags": [t for t in [pick(rec, FIELD_KEYS["type"])] if t],
        "attachments": [], "attachments_complete": True,  # API 소스 — 첨부 없음
        "crawled_at": datetime.now(KST).strftime("%Y-%m-%dT%H:%M:%S+09:00"),
        "content_hash": None,
        "raw": {k: pick(rec, keys) for k, keys in FIELD_KEYS.items()
                if pick(rec, keys) is not None},
    }


def match_target(record, needle):
    """지원대상·서비스명·지원내용 텍스트에 키워드가 있으면 통과 (클라이언트측 1차 필터).
    서버측 cond 필터는 v3에서 신뢰성이 검증되지 않아 쓰지 않는다."""
    hay = " ".join(filter(None, [record["title"],
                                 record["raw"].get("target"),
                                 record["raw"].get("content"),
                                 record["raw"].get("purpose")]))
    return needle in hay


def cmd_list(args, key):
    page, total, collected, matched, raw_fetched = 1, None, {}, 0, 0
    while True:
        payload = api_get("serviceList", key, {"page": page, "perPage": PER_PAGE,
                                               "returnType": "JSON"})
        data = payload.get("data")
        if data is None or payload.get("totalCount") is None:
            print("WARNING gov24: 응답에 data/totalCount 없음 — API 구조 변경 가능성, "
                  "failed로 기록", file=sys.stderr)
            return 2
        if total is None:
            total = payload["totalCount"]
            if total == 0:
                print("WARNING gov24: totalCount=0 — API 구조 변경/장애 신호, "
                      "failed로 기록", file=sys.stderr)
                return 2
        if page == 1 and data:
            probe = to_record(data[0])
            if probe is None:
                print(f"WARNING gov24: 식별자/서비스명 필드 미발견 — 응답 키: "
                      f"{sorted(data[0].keys())[:12]} — failed로 기록", file=sys.stderr)
                return 2
        raw_fetched += len(data)
        for raw in data:
            rec = to_record(raw)
            if rec is None:
                continue
            if args.filter_target and not match_target(rec, args.filter_target):
                continue
            is_new = rec["source_id"] not in collected
            collected[rec["source_id"]] = rec
            if args.filter_target and is_new:
                matched += 1  # dedup 후 카운트 — 중복 행은 세지 않는다
        print(f"[sole-search] gov24 p{page}: {len(data)} fetched, "
              f"kept {len(collected)}/{total}", file=sys.stderr)
        if page * PER_PAGE >= total or not data:
            break
        page += 1
        time.sleep(args.delay)

    tmp = args.output + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        for r in collected.values():
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    os.replace(tmp, args.output)
    extra = f" MATCHED {matched}" if args.filter_target else ""
    print(f"TOTAL {total} COLLECTED {len(collected)}{extra}", file=sys.stderr)
    # 수집률 검증은 필터 전 원시 수신 건수로 — --filter-target 모드에서도 적용
    if total and raw_fetched < total:
        print(f"WARNING gov24: 총 {total} 대비 원시 수신 {raw_fetched}건 — partial",
              file=sys.stderr)
        return 2
    if not args.filter_target and total and len(collected) < total:
        print(f"WARNING gov24: 총 {total} 대비 {len(collected)}건 — partial "
              "(식별자 누락 레코드 존재 가능)", file=sys.stderr)
        return 2
    return 0


def merge_detail(jsonl_path, source_id, content_hash, raw_update):
    tmp = jsonl_path + ".tmp"
    found = False
    with open(jsonl_path, encoding="utf-8") as src, open(tmp, "w", encoding="utf-8") as dst:
        for line in src:
            if not line.strip():
                continue
            r = json.loads(line)
            if r.get("source") == "gov24" and str(r.get("source_id")) == str(source_id):
                r["content_hash"] = content_hash
                r["raw"].update(raw_update)
                found = True
            dst.write(json.dumps(r, ensure_ascii=False) + "\n")
    os.replace(tmp, jsonl_path)
    return found


def cmd_detail(args, key):
    os.makedirs(args.output, exist_ok=True)
    failures = 0
    for sid in args.service_ids:
        payload = None
        for id_key in ("서비스ID", "SVC_ID"):
            try:
                payload = api_get("serviceDetail", key,
                                  {"page": 1, "perPage": 1, "returnType": "JSON",
                                   f"cond[{id_key}::EQ]": sid})
            except ManualEscalation:
                raise  # 인증 거부 — main에서 exit 3
            except (RuntimeError, PermissionError) as e:
                print(f"[sole-search] gov24 {sid}: {e}", file=sys.stderr)
                payload = None
                break
            if payload.get("data"):
                break
        rows = (payload or {}).get("data") or []
        if not rows:
            print(f"[sole-search] gov24 {sid}: 상세 없음", file=sys.stderr)
            failures += 1
            time.sleep(args.delay)
            continue
        detail = rows[0]
        returned_sid = pick(detail, ID_KEYS)
        if returned_sid != str(sid):
            print(f"[sole-search] gov24 {sid}: 응답 서비스ID 불일치({returned_sid}) — "
                  "cond 필터 오동작 의심, 해당 건 실패 처리", file=sys.stderr)
            failures += 1
            time.sleep(args.delay)
            continue
        norm = json.dumps(
            {k: pick(detail, keys) for k, keys in sorted(FIELD_KEYS.items())},
            ensure_ascii=False, sort_keys=True)
        digest = hashlib.sha256(norm.encode()).hexdigest()
        path = f"{args.output}/gov24_{re.sub(r'[^A-Za-z0-9_-]', '_', sid)}.json"
        with open(path, "w", encoding="utf-8") as f:
            json.dump({"service_id": sid, "content_hash": digest, "detail": detail},
                      f, ensure_ascii=False, indent=1)
        print(f"[sole-search] saved: {path}", file=sys.stderr)
        if args.merge_into:
            raw_update = {k: pick(detail, keys) for k, keys in FIELD_KEYS.items()
                          if pick(detail, keys) is not None}
            if not merge_detail(args.merge_into, sid, digest, raw_update):
                print(f"[sole-search] WARNING: gov24/{sid} 레코드를 "
                      f"{args.merge_into}에서 못 찾음", file=sys.stderr)
                failures += 1
        time.sleep(args.delay)
    if failures:
        print(f"WARNING gov24 detail: {failures}건 실패", file=sys.stderr)
        return 2
    return 0


def positive_delay(v):
    f = float(v)
    if f < MIN_DELAY:
        raise argparse.ArgumentTypeError(f"딜레이는 최소 {MIN_DELAY}초")
    return f


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    sub = ap.add_subparsers(dest="cmd", required=True)
    lp = sub.add_parser("list", help="공공서비스 전체 목록 수집")
    lp.add_argument("-o", "--output", required=True)
    lp.add_argument("--filter-target", help="지원대상·제목·내용 키워드 필터 (예: 소상공인)")
    lp.add_argument("--key")
    lp.add_argument("--delay", type=positive_delay, default=MIN_DELAY)
    dp = sub.add_parser("detail", help="서비스ID 상세 조회, --merge-into로 병합")
    dp.add_argument("service_ids", nargs="+")
    dp.add_argument("-o", "--output", default="details")
    dp.add_argument("--merge-into")
    dp.add_argument("--key")
    dp.add_argument("--delay", type=positive_delay, default=MIN_DELAY)
    args = ap.parse_args()

    key = resolve_key(args.key)
    if not key:
        print("INACTIVE gov24: API 키 미등록 — 선택 소스 비활성. "
              "coverage_manifest에 '미활성(선택 소스, API 키 미등록)'으로 기록할 것. "
              f"활성화: data.go.kr에서 15113968 활용신청 후 키를 {KEY_FILE} 또는 "
              "환경변수 DATA_GO_KR_API_KEY에 저장", file=sys.stderr)
        sys.exit(4)

    try:
        code = cmd_list(args, key) if args.cmd == "list" else cmd_detail(args, key)
    except ManualEscalation as e:
        print(f"MANUAL gov24: 인증 거부 ({e}) — 우회하지 말고 manual로 기록", file=sys.stderr)
        code = 3
    except PermissionError as e:
        print(f"WARNING gov24: {e} — 키 확인 필요(만료·오타·미승인). "
              "coverage_manifest에 failed(인증 실패)로 기록", file=sys.stderr)
        code = 2
    except (RuntimeError, urllib.error.URLError, TimeoutError) as e:
        print(f"WARNING gov24: {e}", file=sys.stderr)
        code = 2
    sys.exit(code)


if __name__ == "__main__":
    main()
