#!/usr/bin/env python3
"""두 조사 폴더의 jsonl을 비교해 증분(재조사) 대상을 뽑는다 — sole-search.

ir-search의 diff를 확장했다: 마감일만이 아니라 제목·상태·기간·기관·
content_hash(정규화 본문+첨부 해시)를 비교한다. 정책자금은 마감일이 그대로여도
예산소진(status)·본문이 바뀌므로 필드 비교가 필수다.
(금리·한도는 수집 스키마에 없어 비교하지 않는다 — 상세 검증 단계에서 확인.)

사용법:
  python3 diff_surveys.py <old_dir> <new_dir> --out new_items.jsonl \
      [--old-profile old.md --new-profile new.md] \
      [--incremental-sources fanfandaero,seoulshinbo]

출력(jsonl): {"kind": ..., "diff_status": ..., "changed_fields": [...], "record": {...}}
  kind = NEW | CHANGED | NEEDS_REHASH | GONE (diff_status는 kind와 동일 — 신규 소비자용).
NEEDS_REHASH = 직전 조사에 content_hash가 있었는데 새 조사에 없음, **또는 두 조사의
hash_version이 다름**(예: v1↔v2 — 해시 산식이 바뀌어 비교 불가) — 상세 재수집(merge) 후 재분류.
GONE = 직전에 있었는데 새 조사에 없음 — --out에도 기록된다(기회 소멸 알림 재료).
--incremental-sources: --since 컷오프로 수집하는 증분 소스(예: fanfandaero,seoulshinbo).
  이전 레코드 부재가 소멸이 아니므로 해당 소스의 GONE은 계산하지 않는다.
  ("fanfan"은 "fanfandaero"의 별칭으로 수용.)
stderr 요약: NEW/CHANGED/UNCHANGED/GONE 카운트, 미갱신 소스 WARNING.
프로필 fingerprint가 다르면 전체를 NEW로 강등한다(판정 승계 무효화).
"""
import argparse
import glob
import hashlib
import json
import os
import pathlib
import sys

COMPARE_FIELDS = ["title", "status", "apply_start", "apply_end",
                  "agency", "content_hash"]

# --incremental-sources 별칭 — CLI 축약명 → 레코드의 source 필드 값
SOURCE_ALIASES = {"fanfan": "fanfandaero", "ssb": "seoulshinbo"}

# 판정에 영향을 주는 프로필 필드만 fingerprint에 넣는다
PROFILE_FIELDS = ["entity_type", "business_status", "closure_date", "industry_text",
                  "industry_code_candidates", "employee_count_as_of", "sales_period",
                  "registration_date", "regular_employee_count", "headcount",
                  "sbiz_certificate", "sbiz_certificate_valid_until",
                  "sales_band", "sales_basis", "sales_vs_industry_threshold",
                  "threshold_industry_code", "sales_threshold_as_of",
                  "province", "district", "hq_district",
                  "owner_age_band", "owner_gender", "needs"]


SKIP_FILES = {"screening.jsonl", "new_items.jsonl"}
# 에이전트가 조사 폴더에 만드는 작업 파일 — 수집 원본이 아니므로 로드하지 않는다
SKIP_PREFIXES = ("screen", "verdicts", "judge_input", "merged", "new_items")


def load_dir(path):
    """원시 수집 jsonl만 로드. 스키마 위반·중복 키는 즉시 실패한다 (fail-closed)."""
    records = {}
    sources = set()
    for f in sorted(glob.glob(os.path.join(path, "*.jsonl"))):
        base = os.path.basename(f)
        if base in SKIP_FILES or base.startswith(SKIP_PREFIXES):
            continue
        for ln, line in enumerate(open(f, encoding="utf-8"), 1):
            line = line.strip()
            if not line:
                continue
            r = json.loads(line)
            if "kind" in r and "record" in r:
                continue  # 이전 diff 산출물 혼입 방어
            if "screening" in r and "title" not in r:
                continue  # 선별 작업 파일 혼입 방어
            src, sid = r.get("source"), r.get("source_id")
            if not src or sid in (None, "", "None"):
                print(f"ERROR: {f}:{ln} source/source_id 누락 — 스키마 위반", file=sys.stderr)
                sys.exit(1)
            key = (src, str(sid))
            if key in records:
                print(f"ERROR: {f}:{ln} 중복 키 {key} — 수집 단계 버그", file=sys.stderr)
                sys.exit(1)
            records[key] = r
            sources.add(src)
    return records, sources


def classify(old, new):
    fields = [f for f in COMPARE_FIELDS if f != "content_hash"]
    changed = [f for f in fields if (old.get(f) or None) != (new.get(f) or None)]
    old_h, new_h = old.get("content_hash"), new.get("content_hash")
    old_v, new_v = old.get("hash_version"), new.get("hash_version")
    hash_incomparable = bool(old_h and new_h and old_v != new_v)
    if old_h and new_h and not hash_incomparable and old_h != new_h:
        changed.append("content_hash")
    if changed:
        return {"kind": "CHANGED", "changed_fields": changed}
    if hash_incomparable:
        # 해시 산식 버전이 다르면(v1↔v2) 값 비교가 무의미. NEEDS_REHASH로 두면
        # 재수집해도 old가 여전히 v1이라 영구 루프 — 1회 CHANGED(상세 재검증)로
        # 전환시켜 이번 조사부터 양쪽 v2로 수렴하게 한다.
        return {"kind": "CHANGED", "changed_fields": ["hash_version(산식 전환 — 1회 상세 재검증)"]}
    if old_h and not new_h:
        # 목록 필드는 동일하지만 새 조사에 해시가 없다 — 상세 재수집 후 재분류해야 한다
        return {"kind": "NEEDS_REHASH", "changed_fields": []}
    return {"kind": "UNCHANGED", "changed_fields": []}


def parse_profile_frontmatter(path):
    """sole-profile.md의 YAML 프론트매터를 단순 key: value로 읽는다 (외부 의존성 없이)."""
    fields = {}
    try:
        text = open(path, encoding="utf-8").read()
    except OSError:
        return fields
    if not text.startswith("---"):
        return fields
    body = text.split("---", 2)[1]
    current = None
    for line in body.splitlines():
        if ":" in line and not line.startswith((" ", "-", "#")):
            k, v = line.split(":", 1)
            current = k.strip()
            # 인라인 주석 제거 — "regular_employee_count: 2  # 4대보험" 류
            fields[current] = v.split("#", 1)[0].strip()
        elif current and line.startswith((" ", "-")) and line.strip():
            cont = line.strip().split("#", 1)[0].strip()
            if cont:
                fields[current] = (fields[current] + " | " + cont).strip(" |")
    return fields


def profile_fingerprint(fields):
    payload = json.dumps({k: fields.get(k) for k in PROFILE_FIELDS},
                         ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha256(payload.encode()).hexdigest()[:16]


def carryover_valid(old_fp, new_fp):
    return old_fp == new_fp


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("old_dir")
    ap.add_argument("new_dir")
    ap.add_argument("--out", required=True)
    ap.add_argument("--old-profile")
    ap.add_argument("--new-profile")
    ap.add_argument("--incremental-sources", default="",
                    help="쉼표 구분 — --since 컷오프 수집 소스는 GONE 판정 불가 "
                         "(예: fanfandaero,seoulshinbo — fanfan 별칭 수용)")
    args = ap.parse_args()
    incremental = {SOURCE_ALIASES.get(s.strip(), s.strip())
                   for s in args.incremental_sources.split(",") if s.strip()}

    old, old_sources = load_dir(args.old_dir)
    new, new_sources = load_dir(args.new_dir)
    if not new:
        print("ERROR: new_dir에 jsonl이 없다", file=sys.stderr)
        sys.exit(1)

    invalidate = False
    if bool(args.old_profile) != bool(args.new_profile):
        invalidate = True
        print("WARNING: 프로필 인자가 한쪽만 지정됐다 — 승계 무효(fail-closed)", file=sys.stderr)
    elif args.old_profile and args.new_profile:
        old_fields = parse_profile_frontmatter(args.old_profile)
        new_fields = parse_profile_frontmatter(args.new_profile)
        # 판정 필드가 하나도 없는(잘린) 프로필은 파싱 실패와 같다 — 빈 프로필 두 개가
        # "동일 fingerprint"로 승계를 통과하면 안 된다 (fail-closed)
        old_axes = any(old_fields.get(k) for k in PROFILE_FIELDS)
        new_axes = any(new_fields.get(k) for k in PROFILE_FIELDS)
        if not old_fields or not new_fields or not old_axes or not new_axes:
            invalidate = True  # fail-closed: 프로필을 못 읽으면 승계하지 않는다
            print("WARNING: 프로필 파일을 읽지 못했거나 판정 필드가 비어 있다 — "
                  "승계 무효(fail-closed), 전체 재검토", file=sys.stderr)
        elif not carryover_valid(profile_fingerprint(old_fields),
                                 profile_fingerprint(new_fields)):
            invalidate = True
            print("WARNING: 프로필이 바뀌었다 — 직전 판정 승계 무효, 전체를 NEW로 재검토",
                  file=sys.stderr)
    else:
        print("NOTE: 프로필 미지정 — 판정 승계 유효성(fingerprint)이 검증되지 않았다. "
              "--old-profile/--new-profile 지정 권장", file=sys.stderr)

    missing_sources = old_sources - new_sources
    for s in missing_sources:
        print(f"WARNING: 소스 '{s}'가 재크롤되지 않았다 — 보고서에 '미갱신'으로 명시할 것",
              file=sys.stderr)

    counts = {"NEW": 0, "CHANGED": 0, "NEEDS_REHASH": 0, "UNCHANGED": 0, "GONE": 0}
    # GONE은 검토 대상(NEW/CHANGED/NEEDS_REHASH)과 소비 방식이 다르다 — --out에 섞으면
    # 기존 소비자가 소멸 공고를 상세검증 대상으로 오인하므로 별도 파일로 분리한다.
    out_path = pathlib.Path(args.out)
    gone_path = out_path.with_name("gone_" + out_path.name)
    with open(args.out, "w", encoding="utf-8") as out, \
            open(gone_path, "w", encoding="utf-8") as gone_out:
        def emit(kind, changed_fields, rec, fh):
            fh.write(json.dumps({"kind": kind, "diff_status": kind,
                                 "changed_fields": changed_fields,
                                 "record": rec}, ensure_ascii=False) + "\n")

        for key, rec in new.items():
            if invalidate or key not in old:
                counts["NEW"] += 1
                emit("NEW", [], rec, out)
                continue
            r = classify(old[key], rec)
            counts[r["kind"]] += 1
            if r["kind"] in ("CHANGED", "NEEDS_REHASH"):
                emit(r["kind"], r["changed_fields"], rec, out)
        gone = [k for k in old if k not in new and old[k].get("source") in new_sources]
        skipped_incremental = [k for k in gone if k[0] in incremental]
        if skipped_incremental:
            print(f"NOTE: 증분 소스 {sorted({k[0] for k in skipped_incremental})} — "
                  f"{len(skipped_incremental)}건 GONE 판정 불가(증분 소스)", file=sys.stderr)
        for key in gone:
            if key[0] in incremental:
                continue  # --since 컷오프 수집 — 이전 레코드 부재는 소멸이 아니다
            counts["GONE"] += 1
            emit("GONE", [], old[key], gone_out)
            print(f"GONE: {key[0]}/{key[1]} — {old[key].get('title', '')[:50]}",
                  file=sys.stderr)
    print(f"GONE {counts['GONE']}건 → {gone_path}", file=sys.stderr)

    print("SUMMARY " + json.dumps(counts, ensure_ascii=False), file=sys.stderr)


if __name__ == "__main__":
    main()
