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

출력은 sole-search 공통 스키마(source_id=PBLN_*, canonical_url, agency, status...)다.
상세: `sources_crawl.py detail <URL> --merge-into bizinfo.jsonl` — 본문 텍스트·첨부 링크를
저장하고 목록 레코드에 content_hash·attachments를 병합한다. 첨부 링크가 있는데 아직
추출하지 않았으면 `attachments_complete: false`로 남는다 (그 후보는 '확인됨' 금지).

## 3. 소진공 정책자금 — 필수

**1차 경로 (자동화)**: 소상공인24 소진공 공고 목록에 "소상공인 정책자금 융자계획 공고"가
게시된다 (2026-07-20 확인: pbancSn=679, 첨부 PDF에 융자계획 전문). 즉 sbiz_crawl이 수집하는
범위에 이미 포함되며, 첨부 PDF 추출로 세부(금리·한도·대상)를 읽는다.

**2차 경로 (수동확인)**: 회차별 실시간 접수상태·예산소진은 소상공인정책자금 사이트
(ols.semas.or.kr)에서 확인해야 한다. 로그인 장벽이 있으므로 크롤링하지 않고, 보고서의 loan
항목에 "회차 접수상태는 ols.semas.or.kr 또는 ☎1357에서 확인" 문구를 필수 표기하고
coverage_manifest에 `semas_loan_status: manual`로 기록한다.

## 4. 지역신용보증재단 — 지역 레지스트리 (서울=자동, 그 외 manual)

17개 지역재단은 `region-registry.md` 참조. **서울신보는 자동화 검증 완료(§7)** —
프로필 province가 서울이면 `region_crawl.py list seoulshinbo`로 수집한다.
그 외 16개 재단은 `manual`: 재단 URL과 확인 절차를 보고서에 첨부한다.

## 5. 지자체·경제진흥원 포털 — 지역 레지스트리 (manual, 한계 고지)

기업마당·소상공인24 통합조회가 지자체 공고 상당수를 커버하지만 전부는 아니다.
미등록 지역 포털은 보고서 한계 섹션에 명시한다. `region-registry.md`의 주요 광역 URL 참조.

## 6. 판판대로 (fanfandaero.kr) — 권장, 자동화 검증 완료 (2026-07-20)

소진공 중소기업유통센터의 온라인판로 포털. **대표 공고는 sbiz24/bizinfo에도 실리지만
세부·수시 모집(메뉴판 사업 세부공고, 소담스퀘어·홈쇼핑 회차 모집)은 여기에만 게시**되는
경우가 많다 — 실전조사에서 '확인 필요' 판정의 확인처가 대부분 이 사이트였다.

- robots.txt: Googlebot의 `/search.do`만 제한 — 일반 크롤 허용 (검증 2026-07-20)
- 사업 목록: `POST /portal/v2/selectSupportInfoListAjax.do` (무인증 JSON,
  body `sprtBizTyCd=&sprtBizYr=<연도>`; 빈 연도 → 기본 연도, 응답 `years`로 전 연도 순회)
  → `sprtBizCd, sprtBizNm, sprtBizTyNm(유형), sprtBizTrgtNm(대상), rcritBgngYmd/rcritEndYmd(모집기간)`
- 공지사항 게시판(세부·수시 모집공고): `POST /portal/v2/readUcenterNtcBbs.do`
  body `pageIndex=N` (10건/페이지, `totalRecordCount` 마커로 총건수 검증),
  행 `detailPage('nttId')` + `span.date`. 상세 `readUcenterNtcBbsView.do?nttId=`
- 첨부: 상세의 `download.do?fileName=...` 직링크 (뷰어 호출 인자에서 추출)
- 실행: `region_crawl.py list fanfan -o fanfandaero.jsonl [--since YYYY-MM-DD]`
  게시판 레코드는 접수기간이 목록에 없어 status `불명` → 상세 확인 대상
- 중복: 대표 공고는 sbiz24/bizinfo와 제목+기관 기준 교차 중복 제거

## 7. 서울신용보증재단 (seoulshinbo.co.kr) — 서울 프로필 권장, 자동화 검증 완료 (2026-07-20)

서울시 소상공인 지원사업의 실집행 기관. 고용보험료·산재보험료 지원, 자영업클리닉,
폐업지원, 프렙 아카데미 등 **서울시 사업 공고의 원출처**다.

- robots.txt: Googlebot 한정 제한(`User-agent: *` 규칙 없음) — 일반 크롤 허용 (검증 2026-07-20)
- TLS: 서버가 중간 인증서를 체인에 안 실어줌 → `region_crawl.py`가 DigiCert 중간 인증서를
  내장해 **검증 유지** (검증 비활성화 아님)
- 목록: `GET /wbase/contents/bbs/list.do?mng_cd=STRY9788&pageIndex=N`
  (공지사항 게시판이 지원사업 공고 게시판. 'STRY0006 사업공고'는 입찰·행정 위주라 수집 제외)
  행 `bbs.goView('page','bno')` + 부서·날짜 td. 페이지당 신규 10건 + 상단고정 반복분
- 상세: `GET /wbase/contents/bbs/view/{bno}.do?mng_cd=STRY9788`
- 첨부: `common.download(bno,'serial')` → `GET /download/{bno}/{serial}.do?mng_cd=STRY9788`
- 실행: `region_crawl.py list seoulshinbo -o seoulshinbo.jsonl --since <컷오프>`
  **게시판이 수년치 누적(수백 페이지)이라 --since 지정을 권장** (전수는 10분 이상 소요)

## 8. 크롤 제외 판정 (2026-07-20 robots 검증)

아래는 robots.txt가 수집을 불허해 **크롤하지 않는다** (우회 금지 원칙). 재검토 시 robots부터 재확인.

| 사이트 | 판정 근거 |
|---|---|
| seoulsbdc.or.kr (서울시 자영업지원센터) | `User-agent: * Disallow: /` 전면 불허 |
| ggbaro.kr (경기바로) | `User-agent: * Disallow: /` 전면 불허 |
| gmr.or.kr (경기도시장상권진흥원) | `/base/board*` 불허 (공고 게시판) |
| gov.kr 보조금24 | 사실상 전면 불허 + 오픈 API는 키 필수 (선택 소스 후보로만 유보) |
