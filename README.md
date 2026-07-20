# sole-search — 소상공인 지원사업 조사 스킬

> 치킨집·카페·미용실·온라인셀러… **이미 장사하고 있는 사장님**을 위한 정부·지자체 지원사업
> 조사 AI 스킬. 소상공인24·기업마당의 모집중 공고를 전수 수집하고, 가게 프로필(업종·지역·업력·
> 직원수·매출)로 자격을 판정해 **"오늘 신청할 것"** 부터 마감순으로 보고서를 만든다.

[English](README.en.md) · 형제 스킬: [ir-search](https://github.com/djfksjd/ir-search) (아이템·창업팀용)

## 무엇이 다른가

| | 네이버 검색 | sole-search |
|---|---|---|
| 커버리지 | 유명한 사업 위주 | 정의된 공식 소스(소상공인24·기업마당) **모집중 전수** + 수집·검토 카운트 공개 |
| 자격 판정 | 직접 공고 읽어야 함 | 업종·업력·직원수·매출·지역 기준 5단계 판정 (`확인됨`/`조건부`/`확인 필요`/`신청 불가`/`사업전환 후보`) |
| 첨부파일 | PDF·HWP 직접 열람 | PDF/HWPX 자동 추출 검증 — 못 읽은 첨부는 "확인 필요"로 정직하게 보고 |
| 정책자금 | 흩어진 정보 | 융자계획 공고 원문 기반 + "갚아야 하는 돈" 명시, 회차 상태 확인처 안내 |
| 재조사 | 처음부터 다시 | diff 모드 — 신규·변경(금리·마감)·소멸만 증분 보고 |

세 가지 원칙: **조용한 누락 금지** (coverage_manifest 필수) · **추정 금지** (원문에 없으면 '불명') ·
**허위신청 유도 금지** ("변형하면 가능" 프레이밍 없음).

## 설치

```bash
curl -fsSL https://raw.githubusercontent.com/djfksjd/sole-search/main/install.sh | bash
```

Claude Code / Codex / agy / Gemini CLI를 자동 감지해 설치하고, 없으면
`~/.agents/skills/sole-search`로 클론한다(Cursor 등 파일 기반 호스트용).

수동 설치 (Claude Code):

```bash
claude plugin marketplace add djfksjd/sole-search
claude plugin install sole-search@djfksjd
```

## 사용법

새 세션에서:

- "**우리 가게에 맞는 지원사업 찾아줘**" — 최초 1회 쉬운 인터뷰(6~9문항) 후 전수조사
- "**소상공인 정책자금 알아봐줘**" — 대출·보증 포함 조사
- "**지난번 이후 새로 나온 거 있어?**" — diff 모드 증분 재조사

프로필은 `sole-profile.md`로 저장돼 다음부터는 "바뀐 것 있나요?" 확인만 한다.

## 파이프라인

```
0. 프로필     쉬운 말 인터뷰 → sole-profile.md (원자 필드 스키마)
1. 수집       sbiz_crawl.py   소상공인24 소진공 공고 + 통합조회(지자체 포함)
              sources_crawl.py 기업마당 모집중 전체 전 페이지
1.5 선별      LLM이 전 건 검토 → screening.jsonl (감사 가능)
2. 판정       상세 원문 + 첨부(attach_extract.py) 검증 → 5단계 판정 + 근거 인용
3. 보고서     ① 오늘 신청할 것 ② coverage_manifest ③ 💰받는 돈/🏦빌리는 돈/🎓배우고 돕기 ④ 사업전환 후보
재조사        diff_surveys.py  필드·해시 비교, 프로필 변경 시 전체 재판정
```

크롤러는 Python 표준 라이브러리만 사용한다 (`curl_cffi`는 선택). 판단 로직은 스크립트에 없다 —
수집은 룰베이스, 판정은 LLM.

## 커버리지와 한계

- **자동**: 소상공인24(소진공 공고+통합조회), 기업마당 모집중 전체. "전수조사"는 이 정의된
  소스 범위 안에서의 전수이며, 모든 보고서에 소스별 수집·검토 상태가 명시된다
- **수동 안내**: 소진공 정책자금 회차별 실시간 접수상태(ols), 지역신용보증재단 보증상품,
  미등록 지자체 포털 — `references/region-registry.md` 참조
- 이 스킬은 **신청자격 확인**까지만 한다. 선정 가능성·대출 심사 통과를 예측하지 않으며,
  최종 확인은 접수기관 유선확인을 권장한다

## 개발

```bash
python3 -m pytest tests/   # fixture 골든 테스트 (네트워크 불필요)
```

설계 스펙: `docs/superpowers/specs/` · 구현 계획: `docs/superpowers/plans/`

## License

MIT
