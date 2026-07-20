# sole-search — Korean Small-Business Support Program Finder

> An AI skill for **owners of existing small businesses** in Korea (restaurants, cafés, salons,
> online sellers…). It exhaustively collects open announcements from Sbiz24 (소상공인24) and
> Bizinfo (기업마당), screens every record against your shop profile (industry, region, business
> age, employees, revenue), and reports **"what you can apply for today"** sorted by deadline.

[한국어](README.md) · Sister skill: [ir-search](https://github.com/djfksjd/ir-search) (for startups and project-based founders)

## Highlights

- **Exhaustive within declared sources** — every report ships a `coverage_manifest` with
  collection/screening counts per source; nothing is silently dropped
- **5-state eligibility verdicts** — Confirmed / Conditional / Needs-check / Ineligible /
  Pivot-candidate, each backed by quotes from the official announcement text
- **Attachment verification** — PDF/HWPX auto-extraction; a candidate whose attachment could not
  be read is never marked Confirmed
- **Policy loans handled honestly** — loan programs are clearly labeled "money you must repay",
  with rate (as-of date), limit, term, and the office to call for round status
- **Incremental re-survey** — diff mode reports only new/changed/expired items; changing your
  profile invalidates carried-over verdicts

Crawlers use the Python standard library only, with a minimum 0.5 s delay between requests.
Collection is rule-based; all judgment is done by the LLM agent.

## Install

```bash
curl -fsSL https://raw.githubusercontent.com/djfksjd/sole-search/main/install.sh | bash
```

Detects Claude Code / Codex / agy (Antigravity) / Gemini CLI; falls back to
`~/.agents/skills/sole-search` for file-based hosts (Cursor, Grok Build, etc.).

## Quick start

1. Open a fresh session in a dedicated folder (survey data and reports are written there):

   ```bash
   mkdir -p ~/my-shop && cd ~/my-shop && claude
   ```

2. Say **"우리 가게에 맞는 지원사업 찾아줘"** (find support programs for my shop).
3. First run only: answer a short plain-language interview (6–9 questions — what you sell,
   district, registration date, employees, revenue band, what you need). Answers are saved
   to `sole-profile.md`; you can also inline them in your first message to skip the interview.
4. Wait ~2–3 min of crawling (~1,700 Sbiz24 + ~1,400 Bizinfo records), then screening and
   verification produce `survey-YYYYMMDD/report.md`: ① apply-today list ② coverage_manifest
   ③ grants / loans / education ④ pivot candidates.
5. Later, ask **"지난번 이후 새로 나온 거 있어?"** (anything new since last time?) for an
   incremental diff — only new / changed (rate, deadline) / expired items are re-verified.

See the Korean README for a sample report excerpt, the profile schema, and FAQ.

Korea-only. Reports are written in Korean for shop owners, with bureaucratic terms explained.

## Coverage & limits

- Automated: Sbiz24 (SEMAS announcements + integrated feed incl. local governments) and all
  open Bizinfo listings. "Exhaustive" means exhaustive **within these declared sources**.
- Manual-guidance only: per-round policy-loan intake status (ols), regional credit guarantee
  foundations, non-federated local portals — see `skills/sole-search/references/region-registry.md`.
- Binary HWP attachments cannot be auto-extracted (HWPX/PDF can); affected candidates are
  reported as Needs-check with the source link. Blocked crawls (403/CAPTCHA) are never
  circumvented — the source switches to a documented manual procedure.
- The skill verifies **application eligibility only**; it does not predict selection or loan
  approval. Calling the intake office for final confirmation is recommended.

## License

MIT
