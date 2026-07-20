# sole-search 소스 계약

각 소스의 크롤링 방법·검증된 API 계약·수동확인 절차. 스펙 4장의 구현 기록이다.
**중단선**: 어떤 소스든 403·CAPTCHA·로그인 요구를 만나면 우회를 시도하지 않는다 —
수동확인 절차로 전환하고 coverage_manifest에 `manual`로 기록한다.

## 1. 소상공인24 (sbiz24.kr) — 필수, 자동화 검증 완료 (2026-07-20)

SPA(Vue) + JSON 백엔드. HTML에는 데이터가 없으므로 페이지가 쓰는 JSON API를 그대로 호출한다.

### 스파이크 결과 (완료조건 체크리스트)

| 항목 | 결과 |
|---|---|
| 인증 | **불필요** (익명). 단 `Origin-Method: GET` 헤더 없으면 500 |
| robots.txt | 없음 (SPA 폴백 HTML 반환) — 공개 API, 호출속도 자율 준수 |
| 호출속도 | 요청 간 0.5초 이상 (자율 상한) |
| 총건수 | 응답 `data.default.total` (2026-07-20 기준: 소진공 공고 493, 통합 1,291) |
| 페이지네이션 | 요청 바디 `startRow`/`endRow` (0-base) |
| 안정 ID | 소진공: `pbancSn` (정수) / 통합: `pbancId` (`PBLN_*` = 기업마당 연계 ID) |
| status 필드 | `aplyPsbltySe`(신청가능 여부), `pbancSttsCd`, `ddlnDayCnt`(D-day), `rcptPd`(접수기간) |
| 상세 | `pbancDtlCn`(본문 HTML) 포함 |
| 첨부 | 파일 목록 API + `fileId`로 다운로드 |

### API 계약

공통 헤더: `Content-Type: application/json`, `Origin-Method: GET` (필수)

```
# 1) 소진공 공고 목록 (소상공인시장진흥공단 자체 공고)
POST https://www.sbiz24.kr/api/pbanc/sbiz24PbancList
body: {"sortModel":[],"search":{...아래 참조...},"paging":true,"startRow":0,"endRow":100}

# 2) 지원사업 통합조회 (지자체·유관기관 포함 — 기업마당 연계분 포함)
POST https://www.sbiz24.kr/api/combinePbanc/list
body: 동일 구조

# search 객체 (전수 수집 시 필터를 모두 비운다):
{"searchValue":"","rcrtTypeCdNmList":[],"rcrtTypeCdNmListDisplay":"",
 "regionNmList":[],"regionNmListDisplay":"","tpbizCdList":[],"tpbizCdListDisplay":"",
 "bhis":{"from":null,"to":null},"wrkr":{"from":null,"to":null},"sls":{"from":null,"to":null},
 "aplySeYn":"N","sbrPbancYn":"N","itrstPbancYn":"N","departNmList":null,"searchBox":null,
 "departNmListDisplay":"","ptPbancSortBy":null,"pbancNm":null,"regionCdList":[]}

# 3) 소진공 공고 상세 (pbancDtlCn = 본문 HTML)
POST https://www.sbiz24.kr/api/pbanc/{pbancSn}
body: {}

# 4) 첨부파일 목록
POST https://www.sbiz24.kr/api/cmmn/file
body: {"search":{"groupId":"pbancdoc-{pbancSn}","tmprStrgYn":"N","delYn":false}}
→ list[].fileId, fileNm, fileSz

# 5) 첨부 다운로드
GET https://www.sbiz24.kr/api/cmmn/file/{fileId}
```

### 응답 구조

`{"result": true, "data": {"default": {"total": N, "list": [...], "page": {...}}}}`

목록 레코드 주요 필드: `pbancSn, pbancNm(제목), rcrtTypeCdNm(지원대상), bizPd(사업기간),
rcptPd(접수기간 from/to), aplyPsbltySe(Y/신청가능), ddlnDayCnt, departNm(기관, 통합조회),
bizType(유관기관지원사업 등, 통합조회), pbancId(통합조회), hstgNm(해시태그), regionNmList`

주의: 목록 필터 UI에 업력(bhis)·근로자수(wrkr)·매출액(sls) 서버측 필터가 있으나
**전수 수집 시에는 사용하지 않는다** (필터 신뢰성 미검증 — 선별은 LLM이 전체를 읽고 한다).

## 2. 기업마당 (bizinfo.go.kr) — 필수

서버 렌더링 HTML. ir-search의 검증된 파서를 이식하되 **분야 필터 없이 모집중 전체를 전 페이지
순회**한다 (스펙 4장). 소상공인24 통합조회(combinePbanc)가 기업마당 연계분(`PBLN_*`)을 일부
포함하므로, 중복 제거는 `pbancId`/`pblancId` 기준으로 교차 소스 병합한다.

상세·첨부: 공고 상세 페이지 HTML + 첨부파일 링크. (구현 시 갱신)

## 3. 소진공 정책자금 — 필수

**1차 경로 (자동화)**: 소상공인24 소진공 공고 목록에 "소상공인 정책자금 융자계획 공고"가
게시된다 (2026-07-20 확인: pbancSn=679, 첨부 PDF에 융자계획 전문). 즉 sbiz_crawl이 수집하는
범위에 이미 포함되며, 첨부 PDF 추출로 세부(금리·한도·대상)를 읽는다.

**2차 경로 (수동확인)**: 회차별 실시간 접수상태·예산소진은 소상공인정책자금 사이트
(ols.sbiz.or.kr)에서 확인해야 한다. 로그인 장벽이 있으므로 크롤링하지 않고, 보고서의 loan
항목에 "회차 접수상태는 ols.sbiz.or.kr 또는 ☎1357에서 확인" 문구를 필수 표기하고
coverage_manifest에 `semas_loan_status: manual`로 기록한다.

## 4. 지역신용보증재단 — 지역 레지스트리 (manual)

17개 지역재단은 `region-registry.md` 참조. 초기 릴리스는 전체 `manual`:
프로필의 시·도에 해당하는 재단 URL과 확인 절차를 보고서에 첨부한다.

## 5. 지자체·경제진흥원 포털 — 지역 레지스트리 (manual, 한계 고지)

기업마당·소상공인24 통합조회가 지자체 공고 상당수를 커버하지만 전부는 아니다.
미등록 지역 포털은 보고서 한계 섹션에 명시한다. `region-registry.md`의 주요 광역 URL 참조.
