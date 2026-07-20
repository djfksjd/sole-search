#!/usr/bin/env python3
"""두 조사 폴더의 jsonl을 비교해 증분(재조사) 대상을 뽑는다 — sole-search.

ir-search의 diff를 확장했다: 마감일만이 아니라 제목·상태·기간·금리·한도·기관·
content_hash(정규화 본문+첨부 해시)를 비교한다. 정책자금은 마감일이 그대로여도
금리·예산소진이 바뀌므로 필드 비교가 필수다.

사용법:
  python3 diff_surveys.py <old_dir> <new_dir> --out new_items.jsonl \
      [--old-profile old.md --new-profile new.md]

출력(jsonl): {"kind": NEW|CHANGED, "changed_fields": [...], "record": {...}}
stderr 요약: NEW/CHANGED/UNCHANGED/GONE 카운트, 미갱신 소스 WARNING.
프로필 fingerprint가 다르면 전체를 NEW로 강등한다(판정 승계 무효화).
"""
import argparse
import glob
import hashlib
import json
import os
import sys

COMPARE_FIELDS = ["title", "status", "apply_start", "apply_end",
                  "rate", "limit_amount", "agency", "content_hash"]

# 판정에 영향을 주는 프로필 필드만 fingerprint에 넣는다
PROFILE_FIELDS = ["entity_type", "business_status", "closure_date", "industry_text",
                  "registration_date", "regular_employee_count", "headcount",
                  "sbiz_certificate", "sbiz_certificate_valid_until",
                  "sales_band", "sales_basis", "sales_vs_industry_threshold",
                  "threshold_industry_code", "sales_threshold_as_of",
                  "province", "district", "hq_district",
                  "owner_age_band", "owner_gender", "needs"]


SKIP_FILES = {"screening.jsonl", "new_items.jsonl"}


def load_dir(path):
    """원시 수집 jsonl만 로드. 스키마 위반·중복 키는 즉시 실패한다 (fail-closed)."""
    records = {}
    sources = set()
    for f in sorted(glob.glob(os.path.join(path, "*.jsonl"))):
        if os.path.basename(f) in SKIP_FILES:
            continue
        for ln, line in enumerate(open(f, encoding="utf-8"), 1):
            line = line.strip()
            if not line:
                continue
            r = json.loads(line)
            if "kind" in r and "record" in r:
                continue  # 이전 diff 산출물 혼입 방어
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
    changed = [f for f in COMPARE_FIELDS
               if (old.get(f) or None) != (new.get(f) or None)]
    return {"kind": "CHANGED" if changed else "UNCHANGED", "changed_fields": changed}


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
    for line in body.splitlines():
        if ":" in line and not line.startswith((" ", "-", "#")):
            k, v = line.split(":", 1)
            fields[k.strip()] = v.strip()
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
    args = ap.parse_args()

    old, old_sources = load_dir(args.old_dir)
    new, new_sources = load_dir(args.new_dir)
    if not new:
        print("ERROR: new_dir에 jsonl이 없다", file=sys.stderr)
        sys.exit(1)

    invalidate = False
    if args.old_profile and args.new_profile:
        old_fields = parse_profile_frontmatter(args.old_profile)
        new_fields = parse_profile_frontmatter(args.new_profile)
        if not old_fields or not new_fields:
            invalidate = True  # fail-closed: 프로필을 못 읽으면 승계하지 않는다
            print("WARNING: 프로필 파일을 읽지 못했다 — 승계 무효(fail-closed), 전체 재검토",
                  file=sys.stderr)
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

    counts = {"NEW": 0, "CHANGED": 0, "UNCHANGED": 0, "GONE": 0}
    with open(args.out, "w", encoding="utf-8") as out:
        for key, rec in new.items():
            if invalidate or key not in old:
                counts["NEW"] += 1
                out.write(json.dumps({"kind": "NEW", "changed_fields": [],
                                      "record": rec}, ensure_ascii=False) + "\n")
                continue
            r = classify(old[key], rec)
            counts[r["kind"]] += 1
            if r["kind"] == "CHANGED":
                out.write(json.dumps({"kind": "CHANGED",
                                      "changed_fields": r["changed_fields"],
                                      "record": rec}, ensure_ascii=False) + "\n")
        gone = [k for k in old if k not in new and old[k].get("source") in new_sources]
        for key in gone:
            counts["GONE"] += 1
            print(f"GONE: {key[0]}/{key[1]} — {old[key].get('title', '')[:50]}",
                  file=sys.stderr)

    print("SUMMARY " + json.dumps(counts, ensure_ascii=False), file=sys.stderr)


if __name__ == "__main__":
    main()
