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

# 3) 공고 상세 (pbancDtlCn = 본문 HTML) — pbanc + combine 비PBLN·비대출 공용.
#    combine 상세 라우팅 계약(2026-07-23 SPA 번들 PtCombinePbancList + 실호출 검증):
#      pbancGubun A(공단지원사업)  → /#/pbanc/{pbancSn}      → 본 API 그대로
#      pbancGubun D(지방정부사업)  → /#/lcgPbanc/{pbancSn}   → 본 API 그대로 (검증: sn 799·800)
#      pbancGubun B(PBLN_*, 기업마당) → /#/extldPbanc/{pbancId} → sources_crawl.py detail로
#      pbancGubun C(대출상품)      → /#/loanProduct/{pbancSn} → **계약 미확인, fail-closed**
#    주의: 대출상품의 pbancSn은 별도 네임스페이스다 — 같은 숫자가 소진공 공고와 겹친다
#    (실측: combine sn 413 '미소금융 재기자금' vs pbanc sn 413 소공인특화지원센터 공고).
#    그래서 sbiz_crawl.py detail --source sbiz24_combine 은 --merge-into(목록 jsonl) 필수:
#    raw.bizType=='대출상품' 거부 + 상세 응답 제목 vs 목록 제목 정규화 비교(불일치 시 exit 2).
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
bizType(유관기관지원사업 등, 통합조회), pbancId(통합조회), pbancGubun(A/B/C/D — 상세
라우팅 분기, raw에 수집), hstgNm(해시태그), regionNmList`

### combine 상세 스파이크 계측 (2026-07-23, 라이브 1,710건)

| 구분 | 건수 | 상세 경로 |
|---|---|---|
| pbanc(소진공 자체) | 498 | `/api/pbanc/{sn}` (기존) |
| combine PBLN_*(기업마당 위임) | 548 | bizinfo `sources_crawl.py detail` |
| combine 비PBLN | 664 | 아래 세분 |
| — 공단지원사업(A) | 341 | `/api/pbanc/{sn}` — 476건은 pbanc 목록과 sn 중복 |
| — 지방정부사업(D) | 19 | `/api/pbanc/{sn}` (실호출 검증) |
| — 대출상품(C) | 302 | **fail-closed** (별도 sn 네임스페이스, 계약 미확인) |
| — 유관기관(E) 등 | 2 | sn이 pbanc 네임스페이스(7자리) — 제목 비교 게이트로 판정 |

첨부도 pbanc와 동일: `groupId=pbancdoc-{sn}` (검증: sn 799, hwp+pdf 2건).
시도했으나 기각한 후보: `/api/combinePbanc/` 하위 상세 엔드포인트는 SPA 번들에
존재하지 않음(목록 `list`·`sbrCnt`뿐) — combine 상세 화면 자체가 pbanc/extldPbanc/
loanProduct 라우트로 위임한다.

주의: 목록 필터 UI에 업력(bhis)·근로자수(wrkr)·매출액(sls) 서버측 필터가 있으나
**전수 수집 시에는 사용하지 않는다** (필터 신뢰성 미검증 — 선별은 LLM이 전체를 읽고 한다).

## 2. 기업마당 (bizinfo.go.kr) — 필수

서버 렌더링 HTML. ir-search의 검증된 파서를 이식하되 **분야 필터 없이 모집중 전체를 전 페이지
순회**한다 (스펙 4장). 소상공인24 통합조회(combinePbanc)가 기업마당 연계분(`PBLN_*`)을 일부
포함하므로, 중복 제거는 `pbancId`/`pblancId` 기준으로 교차 소스 병합한다.

출력은 sole-search 공통 스키마(source_id=PBLN_*, canonical_url, agency, status...)다.
상세: `sources_crawl.py detail <URL> --merge-into bizinfo.jsonl` — 본문 텍스트·첨부 링크를
저장하고 목록 레코드에 content_hash·attachments를 병합한다. content_hash는 **hash v2**
(시작 마커~푸터 마커 절단, `hash_version: 2` 병합 — v1과 비교 불가, diff는 1회 CHANGED로 전환).
`detail --download-dir DIR`로 첨부를 **전부** 다운로드에 성공하면 **hash v3**(본문 +
정렬된 첨부 sha256, `hash_version: 3`)를 스탬프한다 — v2와 비교 불가라 diff가 1회 CHANGED로
흡수한다. 다운로드하지 않거나 일부 첨부가 실패·생략되면 **본문만의 v2 해시를 유지**하고
`attachments_complete: false` + exit 2로 첨부 미검증을 표현한다(해시를 None으로 지우면
반복 실패 시 본문 변경이 diff에서 숨는다). robots.txt가 `/upload`·`/download`
프리픽스를 불허하므로 `/uploads/` 첨부는 링크만 수집하고 다운로드하지 않는다(skipped_robots).
첨부 링크가 있는데 아직 추출하지 않았으면 `attachments_complete: false`로 남는다
(그 후보는 '확인됨' 금지).

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
- 첨부: 상세의 `download.do?fileName=...` 직링크 (뷰어 호출 인자에서 추출).
  `detail --download-dir DIR`로 다운로드+추출 — bizinfo와 동일한 공용 슬라이스
  (attach_download.py: 리다이렉트 사전 검증·fanfandaero.kr 한정·50MB·sha256·mojibake
  복구). 전부 성공 시 hash v3 스탬프, 실패·생략 시 본문 v2 유지 +
  `attachments_complete: false` + exit 2. robots(2026-07-23 재확인): 일반 UA에
  download.do 불허 규칙 없음. 라이브 검증(2026-07-23): ntc-20238 pdf 1건 v3 성공
- 실행: `region_crawl.py list fanfan -o fanfandaero.jsonl [--since YYYY-MM-DD]`
  게시판 레코드는 접수기간이 목록에 없어 status `불명` → 상세 확인 대상
- **biz-\* 사업 상세는 정적 수집 미지원** (JS 렌더, 전용 AJAX 없음 — 2026-07-20 검증):
  목록 JSON 필드(모집기간·대상·유형)로 판정하고, 세부 공고문은 게시판(ntc-\*) 공고를
  참조한다. `detail`에 biz URL을 넘기면 SKIP 경고 후 실패 카운트
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
- 첨부: `common.download(bno,'serial')` → `GET /download/{bno}/{serial}.do?mng_cd=STRY9788`.
  `detail --download-dir DIR`로 다운로드+추출 (공용 슬라이스, seoulshinbo.co.kr 한정,
  내장 중간 인증서 SSL 컨텍스트 유지). 전부 성공 시 hash v3, 아니면 본문 v2 +
  `attachments_complete: false` + exit 2. robots: Googlebot 한정 제한 — 일반 UA 허용.
  라이브 검증(2026-07-23, ntc-22718): 첫 첨부는 정상 다운로드+추출됐으나 연속 다운로드
  시 서버가 `http://…/common/message.do`(http)로 302 — https-only 정책상
  blocked_redirect로 fail-closed(partial). 다건 첨부는 재실행 또는 수동 확인 필요
- 실행: `region_crawl.py list seoulshinbo -o seoulshinbo.jsonl --since <컷오프>`
  **게시판이 수년치 누적(수백 페이지)이라 --since 지정을 권장** (전수는 10분 이상 소요)

## 8. 크롤 제외 판정 (2026-07-20 robots 검증)

아래는 robots.txt가 수집을 불허해 **크롤하지 않는다** (우회 금지 원칙). 재검토 시 robots부터 재확인.

| 사이트 | 판정 근거 |
|---|---|
| seoulsbdc.or.kr (서울시 자영업지원센터) | `User-agent: * Disallow: /` 전면 불허 |
| ggbaro.kr (경기바로) | `User-agent: * Disallow: /` 전면 불허 |
| gmr.or.kr (경기도시장상권진흥원) | `/base/board*` 불허 (공고 게시판) |
| gov.kr 보조금24 | 사실상 전면 불허 — **크롤 대신 오픈 API 선택 소스로 지원 (§9)** |

## 9. 보조금24 오픈 API — 선택 소스, 실계정 live 검증 완료 (2026-07-20)

행안부 "대한민국 공공서비스(혜택) 정보" (data.go.kr/data/15113968). gov.kr은 robots가
크롤을 불허하므로 API가 유일한 원칙적 경로다. **키가 있어야 동작하는 선택 소스**:

- 커버리지 갭: sbiz24/bizinfo의 "모집 공고"와 달리 **상시 수혜 제도**(요금감면·수당·
  지자체 소규모 혜택) 중심 — 겹침이 아니라 보완
- 활성화(무료·자동승인): data.go.kr 활용신청 → 키를 환경변수 `DATA_GO_KR_API_KEY`
  또는 `~/.config/sole-search/api_key`에 저장. **프로필·보고서 폴더·리포에 저장 금지**
- 엔드포인트: `https://api.odcloud.kr/api/gov24/v3/{serviceList,serviceDetail,supportConditions}`
  page/perPage 페이지네이션, 응답 `{page,perPage,totalCount,currentCount,data:[...]}`.
  필드는 한글명(서비스ID/서비스명/신청기한/지원대상/지원내용/소관기관명...) —
  크롤러가 v1(SVC_ID)/v3(서비스ID) 변형 모두 수용, 첫 페이지에서 필드 검증 실패 시 fail-closed
- 실행: `gov24_crawl.py list -o gov24.jsonl --filter-target 소상공인`
  (서버측 cond 필터는 신뢰성 미검증 — 클라이언트측 키워드 필터 사용, MATCHED 카운트 보고)
- **종료 코드 4 = 미활성(키 미등록)**: coverage_manifest에
  `gov24: 미활성(선택 소스, API 키 미등록)`으로 기록하고 보고서 한계 섹션에 활성화 방법 안내.
  키 인증 실패는 2(failed)로 구분 — "키 확인 필요" 명시
- 신청기한이 자유 텍스트라 status는 상시/접수중/마감/불명만 확정 판정.
  **보고서에서 상시 제도는 "상시 혜택(보조금24)" 별도 섹션** — 마감 정렬 목록과 섞지 않는다
- 트래픽 상한: 개발계정 10,000/일 — 전량 수집(10,979건, perPage 500 = 22요청)도 여유
- live 검증(2026-07-20, 실키): 전량 10,979건 수집·소상공인 필터 250건 매칭·
  serviceDetail `cond[서비스ID::EQ]` 병합·content_hash 생성 전부 정상.
  v3 필드에 `사용자구분`(개인/법인)·`서비스분야` 존재 — raw에 수집됨(선별 신호로 활용)

## 10. CI first-page smoke canary (2026-07-24)

`.github/workflows/smoke.yml` — 주 2회(월·목 03:00 UTC) + workflow_dispatch.
6개 크롤러(bizinfo / sbiz pbanc / sbiz combine / fanfan / seoulshinbo / gov24)를
`--max-pages 1`(첫 페이지만)로 실행해 API·HTML 계약 파손을 조기 감지한다.
전수 크롤 금지 — `--max-pages`가 걸리면 각 크롤러는 coverage 검증(총건수 대조)을
생략하고 첫 페이지 파싱 성공만 판정한다(0건 파싱은 여전히 실패). gov24는 선택
소스라 exit 4(키 미등록)를 skip으로 처리한다. push(main)에는 fixture pytest만 돈다.
