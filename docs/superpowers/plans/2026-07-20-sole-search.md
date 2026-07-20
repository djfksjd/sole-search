# sole-search Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 소상공인 지원사업 전수조사 스킬(sole-search)을 ir-search 구조로 구현해 `djfksjd/sole-search` 퍼블릭 리포로 배포한다.

**Architecture:** ir-search 리포 골격(플러그인 매니페스트 + skills/<name>/SKILL.md + scripts/)을 복제하고, 소스 어댑터 3종(소상공인24 API, 기업마당 전 페이지, 소진공 정책자금)과 첨부 추출·diff 유틸을 Python 표준 라이브러리로 구현한다. 크롤러는 스파이크로 저장한 fixture 기반 골든 테스트로 TDD한다.

**Tech Stack:** Python 3.9+ (표준 라이브러리 urllib/json/html.parser만 — ir-search와 동일하게 무의존성), pytest(개발 시에만), gh CLI, codex MCP(크로스 리뷰).

## Global Constraints

- 스펙: `docs/superpowers/specs/2026-07-20-sole-search-design.md` (v2.2) — 모든 계약의 원본. 충돌 시 스펙 우선
- Python 스크립트는 **서드파티 의존성 금지** (ir-search 원칙: 표준 라이브러리만, `python3 script.py`로 즉시 실행)
- 크롤러 공통: 요청 간 딜레이 ≥ 0.5초, User-Agent 명시, 403/CAPTCHA/로그인 요구 시 중단하고 manual 폴백 기록 (스펙 4장 중단선)
- jsonl 공통 필드 (스펙 3장 1단계): `source, source_id, announce_no, canonical_url, title, agency, region_scope, apply_start, apply_end, status, primary_type, tags, attachments, crawled_at, content_hash`
- status enum: `접수중|예산소진|상시|회차예정|마감` / coverage 상태: `success|partial|failed|manual` (수집·선별 상태 분리: `collection_status`, `screening_status`)
- 커밋 말미: `Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>`
- 각 Phase 종료 시 codex MCP(gpt-5.6-sol xhigh)에 리뷰 요청 → 블로커 반영 후 다음 Phase (부록 A 협업 계약)
- GPT 3차 리뷰의 비블로커 4건 반영 의무: ① coverage_manifest에 `collection_status`/`screening_status` 분리 ② `sales_periods[]`로 기준기간 표현 ③ diff에 `profile_fingerprint` 무효화 규칙 ④ 배치 중단·재개 및 `screened_count != collected_count` 차단 테스트

---

## Phase 1 — 리포 골격

### Task 1: 리포 스캐폴딩 (ir-search 구조 복제)

**Files:**
- Create: `plugin.json`, `.claude-plugin/plugin.json`, `.claude-plugin/marketplace.json`, `.codex-plugin/plugin.json`, `.codex-plugin/hooks.json`, `gemini-extension.json`, `install.sh`, `.gitignore`, `LICENSE`, `skills/sole-search/` 디렉토리

**Interfaces:**
- Produces: 이후 모든 태스크가 쓰는 디렉토리 구조. 스킬 이름 문자열은 어디서나 `sole-search`

- [ ] **Step 1: ir-search 매니페스트 복사 후 이름 치환**

```bash
cd "/Users/danny/개발 모음 폴더/sole-search"
SRC=~/.claude/skills/ir-search
mkdir -p skills/sole-search/scripts skills/sole-search/references evals
for f in plugin.json gemini-extension.json install.sh .gitignore LICENSE; do cp "$SRC/$f" .; done
cp -r "$SRC/.claude-plugin" "$SRC/.codex-plugin" .
grep -rl 'ir-search' plugin.json .claude-plugin .codex-plugin gemini-extension.json install.sh | xargs sed -i '' 's/ir-search/sole-search/g'
```

- [ ] **Step 2: 각 매니페스트를 열어 이름·설명·버전 검증**

`plugin.json`의 `name`이 `sole-search`, `description`이 소상공인 문구인지 확인. 버전은 `0.1.0`으로 초기화. 설명 필드는 직접 작성:
`"한국 소상공인 지원사업(보조금·바우처, 정책자금 대출·보증, 교육·컨설팅) 전수조사 및 자격 판정 스킬"`

- [ ] **Step 3: Commit**

```bash
git add -A && git commit -m "chore: ir-search 구조 기반 리포 스캐폴딩"
```

### Task 2: GitHub 리포 생성 + main 보호룰

- [ ] **Step 1: 퍼블릭 리포 생성·푸시**

```bash
cd "/Users/danny/개발 모음 폴더/sole-search"
gh repo create djfksjd/sole-search --public --source=. --push
```

- [ ] **Step 2: main 보호룰 설정**

```bash
gh api -X PUT repos/djfksjd/sole-search/branches/main/protection \
  --input - <<'EOF'
{
  "required_status_checks": null,
  "enforce_admins": false,
  "required_pull_request_reviews": {"required_approving_review_count": 0},
  "restrictions": null,
  "allow_force_pushes": false,
  "allow_deletions": false
}
EOF
```

- [ ] **Step 3: 검증**

```bash
gh api repos/djfksjd/sole-search/branches/main/protection --jq '{pr: .required_pull_request_reviews.required_approving_review_count, force: .allow_force_pushes.enabled, del: .allow_deletions.enabled}'
```
Expected: `{"pr":0,"force":false,"del":false}`

이후 작업은 feature 브랜치 → PR → 셀프머지 흐름으로 진행한다.

## Phase 2 — 소스 어댑터 (스파이크 → fixture → TDD)

### Task 3: 소상공인24 스파이크

**Files:**
- Create: `skills/sole-search/references/sources.md` (소상공인24 섹션), `tests/fixtures/sbiz24_list.json`, `tests/fixtures/sbiz24_detail.json`

**Interfaces:**
- Produces: `/api/combinePbanc/list`의 정확한 요청 스키마(메서드·페이로드·페이지네이션 파라미터), 응답 JSON의 필드 매핑표, 안정 ID 필드명 — Task 4가 소비

- [ ] **Step 1: Chrome DevTools(claude-in-chrome)로 목록 페이지 열고 XHR 캡처** — `https://www.sbiz24.kr/#/pbanc` 접속, 네트워크에서 `combinePbanc/list` 요청의 메서드·헤더·바디를 기록
- [ ] **Step 2: curl로 동일 요청 재현** — 세션/토큰 없이 성공하는 최소 요청 확인. 실패하면 필요한 헤더를 하나씩 추가해 최소 집합 도출
- [ ] **Step 3: 응답 2페이지분 + 상세 1건을 fixtures로 저장** — `tests/fixtures/sbiz24_list.json`(1·2페이지), `sbiz24_detail.json`
- [ ] **Step 4: 스파이크 완료조건 체크리스트를 sources.md에 기록** — 스펙 4장: 이용조건·robots, 호출속도, 인증, 총건수(totalCount) 필드, 상세·첨부 접근, status 필드, 안정 ID. 하나라도 미충족이면 여기서 멈추고 사용자에게 보고
- [ ] **Step 5: Commit** — `git add -A && git commit -m "spike: 소상공인24 API 계약 확인 + fixtures"`

### Task 4: `sbiz_crawl.py` — 소상공인24 어댑터

**Files:**
- Create: `skills/sole-search/scripts/sbiz_crawl.py`, `tests/test_sbiz_crawl.py`

**Interfaces:**
- Consumes: Task 3의 fixture와 요청 스키마
- Produces: `python3 sbiz_crawl.py list -o out.jsonl` CLI. 함수 `parse_list_page(data: dict) -> list[dict]`, `parse_detail(data: dict) -> dict`, `to_record(item: dict) -> dict`(Global Constraints의 jsonl 공통 필드 반환). 종료 시 stderr에 `TOTAL <totalCount> COLLECTED <n>` 출력 — coverage 검증용

- [ ] **Step 1: 실패하는 골든 테스트 작성**

```python
# tests/test_sbiz_crawl.py
import json, sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).parent.parent / "skills/sole-search/scripts"))
import sbiz_crawl

FIX = pathlib.Path(__file__).parent / "fixtures"

def test_parse_list_returns_all_items_with_required_fields():
    data = json.loads((FIX / "sbiz24_list.json").read_text())
    items = sbiz_crawl.parse_list_page(data)
    assert len(items) > 0
    rec = sbiz_crawl.to_record(items[0])
    for key in ["source", "source_id", "canonical_url", "title", "agency",
                "apply_start", "apply_end", "status", "crawled_at"]:
        assert rec.get(key) not in (None, ""), f"missing {key}"
    assert rec["source"] == "sbiz24"

def test_status_maps_to_enum():
    data = json.loads((FIX / "sbiz24_list.json").read_text())
    for item in sbiz_crawl.parse_list_page(data):
        assert sbiz_crawl.to_record(item)["status"] in \
            {"접수중", "예산소진", "상시", "회차예정", "마감"}
```

- [ ] **Step 2: 실패 확인** — `python3 -m pytest tests/test_sbiz_crawl.py -v` → Expected: FAIL (`ModuleNotFoundError` 또는 `AttributeError`)
- [ ] **Step 3: 구현** — Task 3에서 확정한 필드 매핑으로 `parse_list_page`/`parse_detail`/`to_record`/`main()` 작성. 페이지네이션은 totalCount 소진까지, 요청 간 `time.sleep(0.5)`. HTTP 오류 3회 재시도 후 stderr에 `WARNING` 출력하고 비정상 종료 코드 2(=partial 신호)
- [ ] **Step 4: 테스트 통과 확인** — `python3 -m pytest tests/test_sbiz_crawl.py -v` → PASS
- [ ] **Step 5: 실사이트 스모크** — `python3 skills/sole-search/scripts/sbiz_crawl.py list -o /tmp/sbiz.jsonl && wc -l /tmp/sbiz.jsonl` → stderr의 TOTAL == COLLECTED 확인
- [ ] **Step 6: Commit**

### Task 5: `sources_crawl.py` — 기업마당 전 페이지 개조

**Files:**
- Create: `skills/sole-search/scripts/sources_crawl.py` (ir-search에서 복사 후 개조), `tests/test_sources_crawl.py`, `tests/fixtures/bizinfo_page.html`

**Interfaces:**
- Consumes: ir-search의 `sources_crawl.py` 파서 (검증된 bizinfo HTML 파싱)
- Produces: `python3 sources_crawl.py list bizinfo -o out.jsonl --all-pages` CLI. **분야 필터 없음**(스펙 4장: 모집중 전체). stderr `TOTAL/COLLECTED` 동일 계약

- [ ] **Step 1: ir-search 원본 복사** — `cp ~/.claude/skills/ir-search/skills/ir-search/scripts/sources_crawl.py skills/sole-search/scripts/`
- [ ] **Step 2: 실사이트에서 bizinfo 목록 1페이지를 fixture로 저장** (기존 파서가 깨지지 않는지 기준선)
- [ ] **Step 3: 실패하는 테스트 작성** — `--all-pages`가 마지막 페이지(중복 첫 항목 재등장 또는 빈 페이지)에서 종료하는지, `--max-pages 30` 기본값 제거됐는지:

```python
# tests/test_sources_crawl.py
import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).parent.parent / "skills/sole-search/scripts"))
import sources_crawl

def test_pagination_stops_on_repeat(monkeypatch):
    pages = [["a1", "a2"], ["a3"], ["a3"]]  # 마지막 페이지 반복 → 종료
    calls = []
    def fake_fetch(page_no):
        calls.append(page_no)
        return pages[min(page_no - 1, len(pages) - 1)]
    ids = sources_crawl.collect_all_pages(fake_fetch)
    assert ids == ["a1", "a2", "a3"]
    assert len(calls) <= 4  # 무한 루프 금지

def test_no_category_filter_in_default_url():
    url = sources_crawl.build_list_url(page=1)
    assert "hashCode" not in url and "category" not in url.lower()
```

- [ ] **Step 4: 실패 확인** → FAIL (`collect_all_pages` 미정의)
- [ ] **Step 5: 개조 구현** — `collect_all_pages(fetch_fn)` 추가(직전 페이지와 동일 ID 집합이면 종료), 분야 파라미터 제거, 30페이지 상한을 `--all-pages` 기본으로 교체
- [ ] **Step 6: 테스트 통과 + 실사이트 스모크** (전 페이지 1회 완주, 소요시간·건수 기록)
- [ ] **Step 7: Commit**

### Task 6: 소진공 정책자금 스파이크 + `semas_loan_crawl.py`

**Files:**
- Create: `skills/sole-search/scripts/semas_loan_crawl.py`, `tests/test_semas_loan.py`, `tests/fixtures/semas_*.html|json`, `references/sources.md` 소진공 섹션

**Interfaces:**
- Produces: loan 레코드(공통 필드 + `loan_kind, rate, rate_as_of, limit_amount, loan_term, grace_period, repay_method, guarantee_fee, handling_orgs`). 접수상태(예산소진 포함) 필드 필수

- [ ] **Step 1: 스파이크** — ols.semas.or.kr 공고 게시판과 신청 시스템의 실시간 접수상태를 분리 확인 (스펙 4장). Task 3과 동일한 완료조건 체크리스트. **자동화 불가 판정 시**: `references/sources.md`에 수동확인 절차(연간 융자계획 URL, 회차 확인 방법)를 구조화해 쓰고 coverage `manual`로 계약 — 이 경우 크롤러 대신 수동절차 문서가 이 태스크의 산출물
- [ ] **Step 2~5: 자동화 가능 시** Task 4와 동일한 fixture→실패 테스트→구현→스모크 사이클. 테스트에 loan 전용 필드 존재 검증 추가:

```python
def test_loan_record_has_loan_fields():
    rec = semas_loan_crawl.to_record(sample_item())
    assert rec["primary_type"] == "loan"
    for key in ["loan_kind", "rate", "rate_as_of", "limit_amount", "handling_orgs"]:
        assert key in rec
```

- [ ] **Step 6: Commit**

### Task 7: `attach_extract.py` — 첨부 텍스트 추출

**Files:**
- Create: `skills/sole-search/scripts/attach_extract.py`, `tests/test_attach_extract.py`, `tests/fixtures/sample.pdf`, `tests/fixtures/sample.hwpx`, `tests/fixtures/sample.hwp`

**Interfaces:**
- Produces: `extract(path: str) -> dict` = `{"ok": bool, "text": str, "reason": str}`. HWPX는 zip+XML 파싱(표준 라이브러리 zipfile/xml), PDF는 `pdftotext` 있으면 사용, 없으면 `ok=False, reason="pdftotext_unavailable"`. HWP 바이너리는 **항상** `ok=False, reason="hwp_binary_unsupported"` (스펙: 명시적 실패). CLI: `python3 attach_extract.py <file> [-o out.txt]`

- [ ] **Step 1: 실패하는 테스트**

```python
# tests/test_attach_extract.py
import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).parent.parent / "skills/sole-search/scripts"))
import attach_extract
FIX = pathlib.Path(__file__).parent / "fixtures"

def test_hwpx_extracts_text():
    r = attach_extract.extract(str(FIX / "sample.hwpx"))
    assert r["ok"] and "지원" in r["text"]

def test_hwp_binary_fails_explicitly():
    r = attach_extract.extract(str(FIX / "sample.hwp"))
    assert r["ok"] is False and r["reason"] == "hwp_binary_unsupported"

def test_unknown_extension_fails_explicitly():
    r = attach_extract.extract("foo.zip")
    assert r["ok"] is False
```

fixture 생성: `sample.hwpx`는 hwpx 스킬로 "지원사업 테스트 문서" 1장짜리 생성, `sample.hwp`는 매직바이트 `D0 CF 11 E0` 더미 파일, `sample.pdf`는 `pdftotext` 검증용 1장.

- [ ] **Step 2: 실패 확인** → FAIL
- [ ] **Step 3: 구현** (HWPX: `zipfile`로 `Contents/section*.xml` 열어 텍스트 노드 수집; PDF: `shutil.which("pdftotext")` 분기; HWP: 매직바이트 감지 즉시 실패)
- [ ] **Step 4: 통과 확인 → Commit**

### Task 8: `diff_surveys.py` — 필드·해시 비교 확장

**Files:**
- Create: `skills/sole-search/scripts/diff_surveys.py` (ir-search에서 복사 후 확장), `tests/test_diff.py`

**Interfaces:**
- Consumes: 두 조사 폴더의 jsonl (공통 필드 + content_hash)
- Produces: CLI `python3 diff_surveys.py <old_dir> <new_dir> --profile sole-profile.md --out new_items.jsonl`. 분류: `NEW | CHANGED(변경 필드 목록) | UNCHANGED | GONE`. `--profile`의 fingerprint가 직전과 다르면 전체를 `NEW`로 강등(판정 승계 무효화)

- [ ] **Step 1: 실패하는 테스트**

```python
# tests/test_diff.py
import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).parent.parent / "skills/sole-search/scripts"))
import diff_surveys as d

BASE = {"source": "sbiz24", "source_id": "1", "title": "A사업", "status": "접수중",
        "apply_end": "2026-08-01", "rate": "2.0%", "content_hash": "h1"}

def test_rate_change_detected_even_if_deadline_same():
    old, new = dict(BASE), dict(BASE, rate="2.5%")
    r = d.classify(old, new)
    assert r["kind"] == "CHANGED" and "rate" in r["changed_fields"]

def test_content_hash_change_detected():
    r = d.classify(dict(BASE), dict(BASE, content_hash="h2"))
    assert r["kind"] == "CHANGED"

def test_profile_fingerprint_mismatch_invalidates_carryover():
    assert d.carryover_valid(old_fp="abc", new_fp="abc") is True
    assert d.carryover_valid(old_fp="abc", new_fp="xyz") is False
```

- [ ] **Step 2: 실패 확인 → 구현** — `classify(old, new)`는 `title,status,apply_start,apply_end,rate,limit_amount,agency,content_hash` 비교. `profile_fingerprint`는 프로필 YAML의 판정 관련 필드(entity/업종/지역/직원/매출/연령/성별)를 정렬 직렬화한 sha256
- [ ] **Step 3: 통과 확인 → Commit**

**Phase 2 게이트:** codex MCP 리뷰 (크롤러 견고성 집중) → 블로커 반영 → PR 머지

## Phase 3 — 스킬 본체

### Task 9: `SKILL.md` 작성

**Files:**
- Create: `skills/sole-search/SKILL.md`

**Interfaces:**
- Consumes: 스펙 전체 — SKILL.md는 스펙 3장(인터뷰→수집→선별→판정→보고서→diff)을 에이전트 실행 지시문으로 번역한 것
- Produces: frontmatter description (스펙 1.2 라우팅 규칙 포함)

- [ ] **Step 1: frontmatter description 작성** — 스펙 1.2의 sole-search 신호를 트리거로, ir-search 신호를 "이 경우 ir-search 사용" 네거티브로 명시
- [ ] **Step 2: 본문 작성** — 스펙 3장의 각 단계를 그대로 지시문화. 프로필 스키마(3.1) 전문 포함, 판정 5상태 표, 보고서 구조(오늘 신청할 것 → coverage_manifest(collection/screening 분리) → 유형별 → 사업전환 후보), screening.jsonl 계약, 개인정보 최소화 규칙, 중단선. 스크립트 호출 경로는 `${CLAUDE_PLUGIN_ROOT}/skills/sole-search/scripts/` 표기 (ir-search와 동일 규약)
- [ ] **Step 3: 셀프 체크** — 스펙 3·4장 각 계약이 SKILL.md에 문장으로 존재하는지 대조표로 확인
- [ ] **Step 4: Commit**

### Task 10: `references/` 완성

**Files:**
- Create: `skills/sole-search/references/sources.md` (전체 정리), `skills/sole-search/references/region-registry.md`

- [ ] **Step 1: sources.md** — 소스별: URL, 크롤러 명령, 스파이크 결과(요청 스키마), 수동확인 절차(소진공 폴백·지역신보), 중단선 재명시
- [ ] **Step 2: region-registry.md** — 17개 시·도별: 지역신용보증재단 명칭·URL, 광역 경제진흥원/소상공인지원기관 URL, 상태(`adapter | manual | unregistered`). 초기 릴리스는 전체 `manual`로 시작하되 URL은 전수 기입
- [ ] **Step 3: Commit**

### Task 11: `evals/evals.json` + 트리거 경계

**Files:**
- Create: `evals/evals.json`
- Modify: (별도 리포) `~/.claude/skills/ir-search/skills/ir-search/SKILL.md` description

- [ ] **Step 1: evals 작성** — 포지티브 5건("우리 치킨집 지원금 찾아줘", "소상공인 정책자금 알아봐줘", "가게 시설개선 지원 있나", "폐업하려는데 지원 있나", "재조사해줘"), 네거티브 3건("우리 아이템에 맞는 창업지원 찾아줘"→ir-search, "K-Startup 조사"→ir-search, "입주공간 알아봐"→ir-search), 모호 1건("온라인 셀러 지원금"→질문으로 분기)
- [ ] **Step 2: ir-search description 수정** — 스펙 1.2 라우팅 표를 양쪽에 반영: ir-search description에 "운영 중인 소상공인·가게·점포 지원은 sole-search 스킬 사용" 제외 문구 추가. ir-search 리포에서 브랜치 → 커밋 → PR 생성 (**릴리스 게이트**: 이 PR 머지 전 sole-search 배포 완료 선언 금지)
- [ ] **Step 3: 로컬에서 두 스킬 동시 설치 상태로 evals 시나리오 수동 확인** — 각 문구를 새 세션에서 입력해 트리거 스킬 기록
- [ ] **Step 4: Commit**

### Task 12: README 한/영 + install.sh 검증

- [ ] **Step 1: README.md** — ir-search README 구조(설치 3에이전트, 사용법, 파이프라인 다이어그램) 복제·개작. "전수조사" 표현에는 반드시 "정의된 공식 소스 범위 내" 한정 문구 (스펙 1장)
- [ ] **Step 2: README.en.md 번역**
- [ ] **Step 3: install.sh 실행 검증** — 깨끗한 임시 디렉토리에 설치해 `~/.claude/skills/sole-search` 생성 확인
- [ ] **Step 4: Commit**

## Phase 4 — 통합 검증

### Task 13: E2E 4종 (스펙 6장)

- [ ] **Step 1: 가상 프로필 4종 작성** — ① 전국형: 서울 마포구 3년차 카페(직원 1) ② 지역형: 부산 사하구 골목상권 음식점 ③ 정책자금형: 대구 5년차 미용실, needs=[loan] ④ 부적격형: 상시근로자 12명 도매업(소상공인 기준 초과)
- [ ] **Step 2: 각 프로필로 스킬 전체 파이프라인 실행** — 새 Claude Code 세션에서 "지원사업 찾아줘" → 보고서 산출. 검증: ④가 대부분 `신청 불가`로 나오는지, 보고서에 coverage_manifest·"오늘 신청할 것" 존재하는지, screening 카운트 일치하는지
- [ ] **Step 3: diff 모드 E2E** — ①번 프로필로 즉시 재조사 → 전량 UNCHANGED·승계 확인, 프로필의 지역을 바꾼 뒤 재조사 → fingerprint 무효화로 전체 재판정 확인
- [ ] **Step 4: 발견 결함 수정 → Commit**

### Task 14: 최종 크로스 리뷰 + 배포

- [ ] **Step 1: codex MCP 최종 리뷰** — 리포 전체(SKILL.md·스크립트·evals) 대상, 90점 미만이면 블로커 수정 루프
- [ ] **Step 2: ir-search description PR 머지 확인** (릴리스 게이트)
- [ ] **Step 3: 버전 태그 + 푸시**

```bash
git tag v0.1.0 && git push origin main --tags
```

- [ ] **Step 4: 로컬 설치** — `~/.claude/skills/sole-search`로 install.sh 실행, 새 세션 트리거 확인
