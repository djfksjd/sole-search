#!/usr/bin/env python3
"""판판대로·서울신용보증재단 공고 크롤러 — sole-search 보조 소스.

2026-07-20 소스 확장 조사에서 크롤 계약을 검증한 두 곳만 다룬다:
  - 판판대로(fanfandaero.kr): robots가 검색 페이지 외 제한 없음.
    사업 목록은 무인증 JSON API(selectSupportInfoListAjax.do),
    세부·수시 모집공고는 공지사항 게시판(readUcenterNtcBbs.do, 서버렌더).
  - 서울신보(seoulshinbo.co.kr): robots가 Googlebot 한정 제한(그 외 UA 무제한).
    공지사항 게시판(mng_cd=STRY9788)이 지원사업 공고 게시판이다.
    ('사업공고' STRY0006는 입찰·행정 공고 위주라 수집하지 않는다.)

seoulsbdc·ggbaro·gmr·보조금24(gov.kr)는 robots 전면/게시판 불허로 제외했다 —
우회하지 않는다(스킬 원칙 3). 재검토하려면 robots.txt부터 다시 확인할 것.

게시판형 소스는 총건수 마커가 있으면 검증하고, 없으면 "새 레코드 없음" 종료를
정상으로 본다(마감·연도 필터가 없는 누적 게시판이라 bizinfo와 달리 총건수 계약이 약함).
공고 접수기간이 목록에 없는 레코드는 status "불명"으로 남긴다 — 상세 확인 대상.

사용법:
  python3 region_crawl.py list fanfan -o fanfandaero.jsonl [--since YYYY-MM-DD]
  python3 region_crawl.py list seoulshinbo -o seoulshinbo.jsonl [--since YYYY-MM-DD]
  python3 region_crawl.py detail <canonical_url>... -o details/ [--merge-into X.jsonl]

종료 코드: 0 성공 / 2 부분·실패 / 3 수동전환(차단 신호).
stderr 마지막 줄: PAGES <n> COLLECTED <m> (fanfan은 BIZ/NTC 구분 포함)
"""
import argparse
import hashlib
import html as htmllib
import json
import os
import re
import ssl
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone, timedelta

MIN_DELAY = 0.5
KST = timezone(timedelta(hours=9))
UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
      "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.4 Safari/605.1.15")
BLOCK_MARKERS = ("captcha", "로그인이 필요", "접근이 차단", "비정상적인 접근")

FANFAN = "https://fanfandaero.kr"
SSB = "https://www.seoulshinbo.co.kr"
SSB_MNG = "STRY9788"  # 공지사항(지원사업 공고 게시판)


# 서울신보 서버는 중간 인증서(DigiCert Global G2 TLS RSA SHA256 2020 CA1,
# 2031-03 만료)를 체인에 포함하지 않는다. 검증을 끄는 대신 공개 중간 인증서를
# 내장해 기본 신뢰 저장소에 보태 검증한다.
_DIGICERT_G2_INTERMEDIATE = """\
-----BEGIN CERTIFICATE-----
MIIEyDCCA7CgAwIBAgIQDPW9BitWAvR6uFAsI8zwZjANBgkqhkiG9w0BAQsFADBh
MQswCQYDVQQGEwJVUzEVMBMGA1UEChMMRGlnaUNlcnQgSW5jMRkwFwYDVQQLExB3
d3cuZGlnaWNlcnQuY29tMSAwHgYDVQQDExdEaWdpQ2VydCBHbG9iYWwgUm9vdCBH
MjAeFw0yMTAzMzAwMDAwMDBaFw0zMTAzMjkyMzU5NTlaMFkxCzAJBgNVBAYTAlVT
MRUwEwYDVQQKEwxEaWdpQ2VydCBJbmMxMzAxBgNVBAMTKkRpZ2lDZXJ0IEdsb2Jh
bCBHMiBUTFMgUlNBIFNIQTI1NiAyMDIwIENBMTCCASIwDQYJKoZIhvcNAQEBBQAD
ggEPADCCAQoCggEBAMz3EGJPprtjb+2QUlbFbSd7ehJWivH0+dbn4Y+9lavyYEEV
cNsSAPonCrVXOFt9slGTcZUOakGUWzUb+nv6u8W+JDD+Vu/E832X4xT1FE3LpxDy
FuqrIvAxIhFhaZAmunjZlx/jfWardUSVc8is/+9dCopZQ+GssjoP80j812s3wWPc
3kbW20X+fSP9kOhRBx5Ro1/tSUZUfyyIxfQTnJcVPAPooTncaQwywa8WV0yUR0J8
osicfebUTVSvQpmowQTCd5zWSOTOEeAqgJnwQ3DPP3Zr0UxJqyRewg2C/Uaoq2yT
zGJSQnWS+Jr6Xl6ysGHlHx+5fwmY6D36g39HaaECAwEAAaOCAYIwggF+MBIGA1Ud
EwEB/wQIMAYBAf8CAQAwHQYDVR0OBBYEFHSFgMBmx9833s+9KTeqAx2+7c0XMB8G
A1UdIwQYMBaAFE4iVCAYlebjbuYP+vq5Eu0GF485MA4GA1UdDwEB/wQEAwIBhjAd
BgNVHSUEFjAUBggrBgEFBQcDAQYIKwYBBQUHAwIwdgYIKwYBBQUHAQEEajBoMCQG
CCsGAQUFBzABhhhodHRwOi8vb2NzcC5kaWdpY2VydC5jb20wQAYIKwYBBQUHMAKG
NGh0dHA6Ly9jYWNlcnRzLmRpZ2ljZXJ0LmNvbS9EaWdpQ2VydEdsb2JhbFJvb3RH
Mi5jcnQwQgYDVR0fBDswOTA3oDWgM4YxaHR0cDovL2NybDMuZGlnaWNlcnQuY29t
L0RpZ2lDZXJ0R2xvYmFsUm9vdEcyLmNybDA9BgNVHSAENjA0MAsGCWCGSAGG/WwC
ATAHBgVngQwBATAIBgZngQwBAgEwCAYGZ4EMAQICMAgGBmeBDAECAzANBgkqhkiG
9w0BAQsFAAOCAQEAkPFwyyiXaZd8dP3A+iZ7U6utzWX9upwGnIrXWkOH7U1MVl+t
wcW1BSAuWdH/SvWgKtiwla3JLko716f2b4gp/DA/JIS7w7d7kwcsr4drdjPtAFVS
slme5LnQ89/nD/7d+MS5EHKBCQRfz5eeLjJ1js+aWNJXMX43AYGyZm0pGrFmCW3R
bpD0ufovARTFXFZkAdl9h6g4U5+LXUZtXMYnhIHUfoyMo5tS58aI7Dd8KvvwVVo4
chDYABPPTHPbqjc1qCmBaZx2vN4Ye5DUys/vZwP9BFohFrH/6j/f3IL16/RZkiMN
JCqVJUzKoZHm1Lesh3Sz8W2jmdv51b2EQJ8HmA==
-----END CERTIFICATE-----
"""

_SSL_CTX = ssl.create_default_context()
_SSL_CTX.load_verify_locations(cadata=_DIGICERT_G2_INTERMEDIATE)


class ManualEscalation(RuntimeError):
    """401/403/CAPTCHA — 우회하지 않고 manual로 전환하라는 신호."""


def fetch(url, data=None, retries=3):
    body_bytes = urllib.parse.urlencode(data).encode() if data else None
    req = urllib.request.Request(url, data=body_bytes, headers={"User-Agent": UA})
    last = None
    for i in range(retries):
        try:
            with urllib.request.urlopen(req, timeout=30, context=_SSL_CTX) as resp:
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
                break
            retry_after = e.headers.get("Retry-After") if e.headers else None
            wait = float(retry_after) if retry_after and retry_after.isdigit() \
                else MIN_DELAY * (i + 1) * 2
            time.sleep(min(wait, 30))
        except (urllib.error.URLError, TimeoutError) as e:
            last = e
            time.sleep(MIN_DELAY * (i + 1) * 2)
    raise RuntimeError(f"{url[:80]} failed after {retries} tries: {last}")


def clean(s):
    return re.sub(r"\s+", " ", htmllib.unescape(s or "")).strip()


def now_kst():
    return datetime.now(KST).strftime("%Y-%m-%dT%H:%M:%S+09:00")


def _status(apply_end):
    if not apply_end:
        return "불명"
    try:
        end = datetime.strptime(apply_end[:10], "%Y-%m-%d").replace(tzinfo=KST)
    except ValueError:
        return "불명"
    return "접수중" if end >= datetime.now(KST) - timedelta(days=1) else "마감"


def base_record(source, source_id, url, title, agency, region_scope=None,
                apply_start=None, apply_end=None, tags=None, raw=None):
    return {
        "source": source, "source_id": source_id, "announce_no": source_id,
        "canonical_url": url, "title": title, "agency": agency,
        "region_scope": region_scope,
        "apply_start": apply_start, "apply_end": apply_end,
        "status": _status(apply_end),
        "primary_type": None, "tags": tags or [],
        "attachments": [], "attachments_complete": False,
        "crawled_at": now_kst(), "content_hash": None, "raw": raw or {},
    }


# ---------------- 판판대로 ----------------

def parse_fanfan_biz(payload):
    """selectSupportInfoListAjax.do JSON 응답 → 공통 스키마."""
    items = []
    for it in payload.get("list", []):
        code = str(it.get("sprtBizCd") or "")
        if not code:
            continue
        items.append(base_record(
            "fanfandaero", f"biz-{code}",
            f"{FANFAN}/portal/v2/preSprtBizPbancDetail.do?sprtBizCd={code}",
            clean(it.get("sprtBizNm")), "중소기업유통센터(판판대로)",
            region_scope=clean(it.get("sprtBizCtpvNm")) or None,
            apply_start=clean(it.get("rcritBgngYmd")) or None,
            apply_end=clean(it.get("rcritEndYmd")) or None,
            tags=[t for t in [clean(it.get("sprtBizTyNm"))] if t],
            raw={"target": clean(it.get("sprtBizTrgtNm")),
                 "biz_year": clean(it.get("sprtBizYr"))},
        ))
    return items


NTC_ROW = re.compile(
    r"detailPage\('(\d+)'\);?\"[^>]*class=\"title\"[^>]*>([\s\S]*?)</a>"
    r"[\s\S]*?<span class=\"date\">([^<]*)</span>")


def parse_fanfan_ntc(h):
    items = []
    for ntt, title, date in NTC_ROW.findall(h):
        posted = clean(date)[:10]
        items.append(base_record(
            "fanfandaero", f"ntc-{ntt}",
            f"{FANFAN}/portal/v2/readUcenterNtcBbsView.do?nttId={ntt}",
            clean(title), "중소기업유통센터(판판대로)",
            raw={"posted": posted, "board": "공지사항"},
        ))
    return items


def fanfan_ntc_total(h):
    m = re.search(r'totalRecordCount\s*=\s*"(\d+)"', h)
    return int(m.group(1)) if m else None


def cmd_list_fanfan(args):
    collected = {}
    # 1) 사업 목록 JSON — 응답의 years 전 연도를 수집한다
    first = json.loads(fetch(f"{FANFAN}/portal/v2/selectSupportInfoListAjax.do",
                             data={"sprtBizTyCd": "", "sprtBizYr": ""}))
    years = first.get("years") or []
    for it in parse_fanfan_biz(first):
        collected[it["source_id"]] = it
    for y in years:
        time.sleep(args.delay)
        payload = json.loads(fetch(f"{FANFAN}/portal/v2/selectSupportInfoListAjax.do",
                                   data={"sprtBizTyCd": "", "sprtBizYr": y}))
        for it in parse_fanfan_biz(payload):
            collected.setdefault(it["source_id"], it)
    biz_n = len(collected)
    print(f"[sole-search] fanfan biz: {biz_n} (years {years})", file=sys.stderr)
    if biz_n == 0:
        print("WARNING fanfan: 사업 목록 0건 — API 구조 변경 가능성, failed로 기록",
              file=sys.stderr)
        return 2

    # 2) 공지사항 게시판(세부·수시 모집공고 포함) — 서버렌더, pageIndex POST
    page, pages, total, stop = 1, 0, None, False
    while not stop:
        h = fetch(f"{FANFAN}/portal/v2/readUcenterNtcBbs.do",
                  data={"pageIndex": page, "searchMode": "title", "searchTxt": ""})
        if total is None:
            total = fanfan_ntc_total(h)
        items = parse_fanfan_ntc(h)
        if page == 1 and not items:
            print("WARNING fanfan: 게시판 첫 페이지 파싱 0건 — 구조 변경 가능성, partial",
                  file=sys.stderr)
            return 2
        new = [i for i in items if i["source_id"] not in collected]
        for i in new:
            collected[i["source_id"]] = i
        pages = page
        print(f"[sole-search] fanfan ntc p{page}: {len(items)} parsed, {len(new)} new",
              file=sys.stderr)
        if not new:
            break
        # 컷오프는 신규 행 기준 — 상단고정글은 매 페이지 반복되므로 items로 보면 안 멈춘다
        if args.since and all((i["raw"].get("posted") or "9999") < args.since
                              for i in new):
            stop = True  # 게시판은 최신순 — 컷오프 이전 페이지만 남음
        page += 1
        time.sleep(args.delay)

    ntc_n = len(collected) - biz_n
    write_jsonl(args.output, collected.values())
    print(f"PAGES {pages} COLLECTED {len(collected)} BIZ {biz_n} NTC {ntc_n} "
          f"NTC_TOTAL {total if total is not None else '?'}", file=sys.stderr)
    if not args.since and total is not None and ntc_n < total:
        print(f"WARNING fanfan: 게시판 총 {total}건 대비 {ntc_n}건 — partial",
              file=sys.stderr)
        return 2
    return 0


# ---------------- 서울신보 ----------------

SSB_ROW = re.compile(
    r"goView\('\d+',\s*'(\d+)'\)\"><span class=\"ellipsis\">([\s\S]*?)</span>"
    r"</a></td>([\s\S]*?)</tr>")


def parse_ssb(h):
    items = []
    for bno, title, rest in SSB_ROW.findall(h):
        tds = [clean(re.sub(r"<[^>]+>", " ", td))
               for td in re.findall(r"<td[^>]*>([\s\S]*?)</td>", rest)]
        posted = next((t for t in tds if re.fullmatch(r"\d{4}-\d{2}-\d{2}", t)), "")
        dept = tds[0] if tds else ""
        sid = f"ntc-{bno}"
        if any(i["source_id"] == sid for i in items):
            continue  # 데스크톱/모바일 이중 마크업 중복
        items.append(base_record(
            "seoulshinbo", sid,
            f"{SSB}/wbase/contents/bbs/view/{bno}.do?mng_cd={SSB_MNG}&pageIndex=1",
            clean(title), f"서울신용보증재단/{dept}" if dept else "서울신용보증재단",
            region_scope="서울",
            raw={"posted": posted, "board": "공지사항(STRY9788)"},
        ))
    return items


def cmd_list_ssb(args):
    collected, page, pages, stop = {}, 1, 0, False
    while not stop:
        h = fetch(f"{SSB}/wbase/contents/bbs/list.do?mng_cd={SSB_MNG}&pageIndex={page}")
        items = parse_ssb(h)
        if page == 1 and not items:
            print("WARNING seoulshinbo: 첫 페이지 파싱 0건 — 구조 변경 가능성, partial",
                  file=sys.stderr)
            return 2
        new = [i for i in items if i["source_id"] not in collected]
        for i in new:
            collected[i["source_id"]] = i
        pages = page
        print(f"[sole-search] seoulshinbo p{page}: {len(items)} parsed, {len(new)} new",
              file=sys.stderr)
        if not new:
            break
        # 컷오프는 신규 행 기준 — 상단고정글은 매 페이지 반복되므로 items로 보면 안 멈춘다
        if args.since and all((i["raw"].get("posted") or "9999") < args.since
                              for i in new):
            stop = True
        page += 1
        time.sleep(args.delay)
    write_jsonl(args.output, collected.values())
    print(f"PAGES {pages} COLLECTED {len(collected)}", file=sys.stderr)
    return 0


# ---------------- 상세 ----------------

def strip_html(text):
    text = re.sub(r"<script[\s\S]*?</script>|<style[\s\S]*?</style>", "", text)
    text = re.sub(r"<[^>]+>", "\n", text)
    text = htmllib.unescape(text)
    return re.sub(r"\n\s*\n+", "\n", text)


def merge_detail(jsonl_path, source, source_id, content_hash, attachments, complete):
    tmp = jsonl_path + ".tmp"
    found = False
    with open(jsonl_path, encoding="utf-8") as src, open(tmp, "w", encoding="utf-8") as dst:
        for line in src:
            if not line.strip():
                continue
            r = json.loads(line)
            if r.get("source") == source and str(r.get("source_id")) == str(source_id):
                r["content_hash"] = content_hash
                r["attachments"] = attachments
                r["attachments_complete"] = complete
                found = True
            dst.write(json.dumps(r, ensure_ascii=False) + "\n")
    os.replace(tmp, jsonl_path)
    return found


def detail_target(url):
    """url → (source, source_id, 본문 시작 마커 regex, 첨부 추출 함수)"""
    if "fanfandaero.kr" in url:
        m = re.search(r"nttId=(\d+)", url) or re.search(r"sprtBizCd=(\d+)", url)
        kind = "ntc" if "nttId=" in url else "biz"
        return ("fanfandaero", f"{kind}-{m.group(1)}" if m else None,
                r'<div[^>]+class="[^"]*contents[^"]*"', fanfan_attachments)
    if "seoulshinbo.co.kr" in url:
        m = re.search(r"/bbs/view/(\d+)\.do", url)
        return ("seoulshinbo", f"ntc-{m.group(1)}" if m else None,
                r'<div[^>]+class="[^"]*sub_cont_wrap[^"]*"', ssb_attachments)
    return (None, None, None, None)


# 본문 끝 마커 — 컨테이너 div를 regex로 균형 매칭할 수 없으므로(중첩)
# 시작 마커부터 푸터/다음글 내비게이션까지를 본문으로 자른다
END_MARKERS = (r'<div[^>]+id="footer"', r'<footer\b', r'<div[^>]+class="[^"]*footer',
               r'다음글|이전글')


def extract_body(h, start_pattern):
    """시작 마커 ~ 첫 끝 마커 구간의 텍스트. 마커를 못 찾으면 전체 폴백."""
    sm = re.search(start_pattern, h)
    seg = h[sm.start():] if sm else h
    ends = [m.start() for p in END_MARKERS for m in [re.search(p, seg)] if m]
    if ends:
        seg = seg[:min(ends)]
    text = strip_html(seg)
    # 조회수·등록일시각 등 변동 라인은 해시 안정성을 위해 제거
    lines = [ln for ln in text.splitlines()
             if ln.strip() and not re.fullmatch(r"\s*(조회수?|등록일|작성일)\s*", ln)
             and not re.fullmatch(r"\s*\d{1,7}\s*", ln)]
    return "\n".join(lines)


def fanfan_attachments(h, url):
    out, seen = [], set()
    for enc in re.findall(r'(https://fanfandaero\.kr/download\.do\?fileName=[^"&\\\s]+)', h):
        u = htmllib.unescape(enc)
        if u in seen:
            continue
        seen.add(u)
        out.append({"url": u, "filename": u.rsplit("/", 1)[-1]})
    # 원본 파일명이 viewer 호출 인자에 있으면 붙인다
    for m in re.finditer(r'fileName=([^"&]+)[^"]*"\s*,\s*"\d+"\s*,\s*"([^"]+)"', h):
        path = htmllib.unescape(m.group(1))
        for a in out:
            if a["url"].endswith(path.rsplit("/", 1)[-1]):
                a["filename"] = clean(m.group(2))
    return out


def ssb_attachments(h, url):
    m = re.search(r"/bbs/view/(\d+)\.do", url)
    bno = m.group(1) if m else ""
    out, seen = [], set()
    for serial in re.findall(r"common\.download\(\s*\d+\s*,\s*'([a-f0-9-]+)'", h):
        if serial in seen:
            continue
        seen.add(serial)
        # 상세: <span class="ellipsis">파일명</span> / 목록: <img alt=""> 파일명
        name_m = re.search(
            r"common\.download\(\s*\d+\s*,\s*'" + serial +
            r"'\)\"[^>]*>\s*<span[^>]*>([^<]+)", h) or re.search(
            r"common\.download\(\s*\d+\s*,\s*'" + serial +
            r"'\)\"[^>]*>[\s\S]*?alt=\"\"\s*>\s*([^<]+)", h)
        name = urllib.parse.unquote_plus(clean(name_m.group(1))) if name_m else serial
        out.append({
            "url": f"{SSB}/download/{bno}/{serial}.do?mng_cd={SSB_MNG}",
            "filename": name,
        })
    return out


def cmd_detail(args):
    os.makedirs(args.output, exist_ok=True)
    failures = 0
    for url in args.urls:
        source, sid, start_pat, attach_fn = detail_target(url)
        if not source or not sid:
            print(f"[sole-search] skip 알 수 없는 url: {url[:70]}", file=sys.stderr)
            failures += 1
            continue
        if sid.startswith("biz-"):
            # 판판대로 사업 상세는 JS 렌더라 정적 수집 불가(2026-07-20 검증,
            # 전용 AJAX 없음). 목록 JSON 필드가 전부이며 세부 공고문은 공지
            # 게시판(ntc-*)에 실린다 — 조용히 잘못된 해시를 만들지 않는다.
            print(f"[sole-search] SKIP {sid}: biz 상세는 정적 수집 미지원 — "
                  "목록 필드로 판정하고 세부는 게시판 공고(ntc)를 참조할 것",
                  file=sys.stderr)
            failures += 1
            continue
        try:
            h = fetch(url)
        except ManualEscalation as e:
            print(f"MANUAL {source} detail: {e}", file=sys.stderr)
            return 3
        except (RuntimeError, urllib.error.URLError, TimeoutError) as e:
            print(f"[sole-search] {url[:70]}: {e}", file=sys.stderr)
            failures += 1
            time.sleep(args.delay)
            continue
        text = extract_body(h, start_pat)
        digest = hashlib.sha256(text.encode()).hexdigest()
        attachments = attach_fn(h, url)
        name = re.sub(r"\W+", "_", f"{source}_{sid}")[:80]
        path = f"{args.output}/{name}.txt"
        with open(path, "w", encoding="utf-8") as f:
            f.write(url + "\n")
            f.write("CONTENT_HASH: " + digest + "\n")
            f.write("ATTACHMENTS: " + json.dumps(attachments, ensure_ascii=False) + "\n\n")
            f.write(text)
        print(f"[sole-search] saved: {path}", file=sys.stderr)
        if args.merge_into:
            merged = merge_detail(args.merge_into, source, sid, digest, attachments,
                                  complete=not attachments)  # 링크만 수집 단계
            if not merged:
                print(f"[sole-search] WARNING: {source}/{sid} 레코드를 "
                      f"{args.merge_into}에서 못 찾음", file=sys.stderr)
                failures += 1
        time.sleep(args.delay)
    if failures:
        print(f"WARNING region detail: {failures}건 실패/미병합", file=sys.stderr)
        return 2
    return 0


def write_jsonl(path, items):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        for i in items:
            f.write(json.dumps(i, ensure_ascii=False) + "\n")
    os.replace(tmp, path)


def positive_delay(v):
    f = float(v)
    if f < MIN_DELAY:
        raise argparse.ArgumentTypeError(f"딜레이는 최소 {MIN_DELAY}초 (예의상 강제)")
    return f


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    sub = ap.add_subparsers(dest="cmd", required=True)
    lp = sub.add_parser("list", help="소스별 목록 수집")
    lp.add_argument("source", choices=["fanfan", "seoulshinbo"])
    lp.add_argument("-o", "--output", required=True)
    lp.add_argument("--since", help="YYYY-MM-DD — 게시판을 이 날짜까지만 거슬러 수집")
    lp.add_argument("--delay", type=positive_delay, default=MIN_DELAY)
    dp = sub.add_parser("detail", help="상세 본문 해시+첨부 링크, --merge-into로 병합")
    dp.add_argument("urls", nargs="+")
    dp.add_argument("-o", "--output", default="details")
    dp.add_argument("--merge-into")
    dp.add_argument("--delay", type=positive_delay, default=MIN_DELAY)
    args = ap.parse_args()
    try:
        if args.cmd == "list":
            code = cmd_list_fanfan(args) if args.source == "fanfan" else cmd_list_ssb(args)
        else:
            code = cmd_detail(args)
    except ManualEscalation as e:
        print(f"MANUAL: {e} — 우회하지 말고 수동확인으로 전환", file=sys.stderr)
        code = 3
    except (RuntimeError, urllib.error.URLError, TimeoutError, json.JSONDecodeError) as e:
        print(f"WARNING region_crawl: {e}", file=sys.stderr)
        code = 2
    sys.exit(code)


if __name__ == "__main__":
    main()
