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

Crawlers use the Python standard library only (`curl_cffi` optional). Collection is rule-based;
all judgment is done by the LLM agent.

## Install

```bash
curl -fsSL https://raw.githubusercontent.com/djfksjd/sole-search/main/install.sh | bash
```

Detects Claude Code / Codex / agy / Gemini CLI; falls back to `~/.agents/skills/sole-search`
for file-based hosts (Cursor etc.).

## Usage

In a fresh session:

- "우리 가게에 맞는 지원사업 찾아줘" (find support programs for my shop) — a short, plain-language
  interview builds `sole-profile.md`, then the full survey runs
- "소상공인 정책자금 알아봐줘" — includes policy loans/guarantees
- "지난번 이후 새로 나온 거 있어?" — incremental diff mode

Korea-only. Reports are written in Korean for shop owners, with bureaucratic terms explained.

## Development

```bash
python3 -m pytest tests/   # fixture-based golden tests, no network needed
```

Design spec: `docs/superpowers/specs/` · Implementation plan: `docs/superpowers/plans/`

## License

MIT
