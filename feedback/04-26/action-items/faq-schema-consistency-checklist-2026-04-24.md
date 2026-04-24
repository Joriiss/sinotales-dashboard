# SinoTales FAQ + Schema Consistency Checklist (Top Performers)

Date: 2026-04-24  
Goal: Ensure FAQ quality, on-page consistency, and valid FAQPage schema on top-performing pages

## How to use

For each URL:
- Check FAQ copy quality (intent match, clarity, no contradictions).
- Validate schema in:
  - Google Rich Results Test: [https://search.google.com/test/rich-results](https://search.google.com/test/rich-results)
  - Schema Markup Validator: [https://validator.schema.org/](https://validator.schema.org/)
- Log warnings/errors and final fix date.

---

## QA Rules (Pass Criteria)

- FAQ section has 3-5 useful questions (not filler).
- Questions reflect real travel intent (cost, timing, booking, logistics, requirements).
- Answers are concise and consistent with article body.
- Schema type is `FAQPage`.
- Each item has valid `Question` + `acceptedAnswer`.
- No empty fields, duplicates, or malformed JSON-LD.
- Sensitive topics (visa/rules/safety) include "verify official sources" caveat where needed.

---

## Audit Table

| # | URL | Priority | FAQ Copy QA | FAQ Count (3-5) | Rich Results Test | Schema Validator | Warnings / Errors | Fix Needed | Date Checked | Date Fixed |
|---|---|---|---|---|---|---|---|---|---|---|
| 1 | [https://sinotales.com/ressources/apps-tech/china-travel-apps-guide-2026/](https://sinotales.com/ressources/apps-tech/china-travel-apps-guide-2026/) | High | [x] Pass | [x] | [x] Pass | [x] Pass | None | [ ] | 2026-04-24 | 2026-04-24 |
| 2 | [https://sinotales.com/ressources/money-costs/china-travel-cost-guide-2026/](https://sinotales.com/ressources/money-costs/china-travel-cost-guide-2026/) | High | [x] Pass | [x] | [x] Pass | [x] Pass | None | [ ] | 2026-04-24 | 2026-04-24 |
| 3 | [https://sinotales.com/ressources/budget-logistics/china-travel-tips-family-budget-cost/](https://sinotales.com/ressources/budget-logistics/china-travel-tips-family-budget-cost/) | High | [x] Pass | [x] | [x] Pass | [x] Pass | None | [ ] | 2026-04-24 | 2026-04-24 |
| 4 | [https://sinotales.com/destinations/beijing/beijing-capital-airport-express-guide/](https://sinotales.com/destinations/beijing/beijing-capital-airport-express-guide/) | High | [x] Pass | [x] | [x] Pass | [x] Pass | None | [ ] | 2026-04-24 | 2026-04-24 |
| 5 | [https://sinotales.com/ressources/transport/xiamen-gulangyu-ferry-guide-foreigners/](https://sinotales.com/ressources/transport/xiamen-gulangyu-ferry-guide-foreigners/) | High | [x] Pass | [x] | [x] Pass | [x] Pass | None | [ ] | 2026-04-24 | 2026-04-24 |
| 6 | [https://sinotales.com/destinations/shanghai/shanghai-french-concession-walking-guide/](https://sinotales.com/destinations/shanghai/shanghai-french-concession-walking-guide/) | High | [x] Pass | [x] | [x] Pass | [x] Pass | None | [ ] | 2026-04-24 | 2026-04-24 |
| 7 | [https://sinotales.com/interests/water-towns/wuzhen-vs-tongli-suzhou-water-towns-guide/](https://sinotales.com/interests/water-towns/wuzhen-vs-tongli-suzhou-water-towns-guide/) | Medium | [x] Pass | [x] | [x] Pass | [x] Pass | None | [ ] | 2026-04-24 | 2026-04-24 |
| 8 | [https://sinotales.com/destinations/guangxi/yangshuo/cycling-yulong-river-yangshuo-guide/](https://sinotales.com/destinations/guangxi/yangshuo/cycling-yulong-river-yangshuo-guide/) | Medium | [x] Pass | [x] | [x] Pass | [x] Pass | None | [ ] | 2026-04-24 | 2026-04-24 |
| 9 | [https://sinotales.com/destinations/beijing/beijing-hutong-food-guide/](https://sinotales.com/destinations/beijing/beijing-hutong-food-guide/) | Medium | [x] Pass | [x] | [x] Pass | [x] Pass | None | [ ] | 2026-04-24 | 2026-04-24 |
| 10 | [https://sinotales.com/ressources/apps-tech/china-travel-tips-hema-freshippo-guide-2026/](https://sinotales.com/ressources/apps-tech/china-travel-tips-hema-freshippo-guide-2026/) | Medium | [x] Pass | [x] | [x] Pass | [x] Pass | None | [ ] | 2026-04-24 | 2026-04-24 |

---

## Common Fix Patterns

- Remove duplicate FAQ entries with slightly different wording.
- Ensure each FAQ answer is plain, direct, and <= 2-4 short paragraphs.
- Align FAQ wording with title/meta promise (avoid off-topic Q&A).
- Add/update caution line for visa/policy-sensitive answers:
  - `Requirements can change. Always verify with official sources before travel.`
- Revalidate after every change and record the new status/date.

