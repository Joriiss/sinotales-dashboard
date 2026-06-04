# SinoTales Traffic Growth Audit

Date: 2026-04-24

## Scope

End-to-end audit of the current content system across:
- Content production and publication (dashboard + WordPress export)
- Organic search performance (GSC)
- On-site behavior (Plausible)
- Automation reliability (n8n workflows)

Primary optimization goal: **organic traffic growth**.

---

## Data Reviewed

- `feedback/dashboard-data/*.json`
- `feedback/Google Search Console/sinotales.com-Performance-on-Search-2026-04-24/*.csv`
- `feedback/Google Search Console/sinotales.com-Coverage-2026-04-24/*.csv`
- `feedback/Plausible_sinotales_com_20260424/*.csv`
- `feedback/n8n workflows/*.json`
- `feedback/sinotales.WordPress.2026-04-24.xml`
- `feedback/sinotales-website-deep-dive-audit-2026-04-24.md`

---

## Executive Summary

SinoTales has strong niche positioning and solid publishing throughput, but the biggest growth ceiling is currently **SERP CTR + engagement quality**, not content quantity.

The 90-day organic baseline shows meaningful impression volume with weak click capture, and landing behavior indicates high single-page sessions. The most immediate upside is from:
1. CTR optimization on high-impression pages,
2. rank-lift work on pages in positions 8-20,
3. stronger internal journey design to reduce bounce and increase page depth.

---

## Baseline Metrics

## Content and production baseline

- Total exported dashboard blog posts: **151**
- Published: **138**
- Draft/unpublished: **13**
- Posts with meta title + description populated: **151/151**
- Posts with tags: **151/151**
- Posts with FAQ blocks: **151/151**
- Posts with images: **145/151**

Post ideas pipeline:
- Total ideas: **558**
- Ideas without associated post: **407**
- Idea-to-post conversion coverage: **~27.06%**

Source inventory:
- YouTube sources: **5**
- Blog sources: **5**

## GSC baseline (last 3 months export)

- Total clicks: **157**
- Total impressions: **40,327**
- Global CTR: **0.39%**
- Weighted average position: **8.68**

Coverage snapshot (chart sample):
- Indexed pages around sample start: **~38**
- Non-indexed pages around sample start: **~306**

Critical issues in coverage export:
- Crawled - currently not indexed: **101 pages**
- Page with redirect: **5 pages**

## Plausible baseline (~111 days)

- Visitors: **1,422**
- Visits: **1,464**
- Pageviews: **1,920**
- Bounce rate: **~87.98%**
- Pages per visit: **~1.31**
- Avg visit duration: **~41.6s**

---

## Funnel Diagnosis

Approximate observed funnel:

`Published content -> Indexed subset -> Impressions -> Clicks -> Mostly single-page sessions`

Main leakage points:
- **Leak #1 (SERP):** very low aggregate CTR vs impression base.
- **Leak #2 (On-site):** high bounce / low page depth on key entry pages.
- **Leak #3 (Indexability):** large non-indexed inventory suggests crawl-value/quality architecture issues.

---

## Quick-Win Opportunities

## A) High-impression, low-CTR pages (highest priority)

1. `/ressources/apps-tech/china-travel-apps-guide-2026/`  
   - 3,927 impressions, 0 clicks, position 7.64
2. `/ressources/money-costs/china-travel-cost-guide-2026/`  
   - 3,336 impressions, 0 clicks, position 8.02
3. `/ressources/budget-logistics/china-travel-tips-family-budget-cost/`  
   - 3,228 impressions, CTR 0.06%, position 7.55
4. `/destinations/beijing/beijing-capital-airport-express-guide/`  
   - 3,181 impressions, CTR 0.25%, position 5.84
5. `/ressources/transport/xiamen-gulangyu-ferry-guide-foreigners/`  
   - 1,962 impressions, CTR 0.61%, position 5.74

## B) Position 8-20 pages (rank-lift opportunities)

- `/ressources/transport/beijing-to-shanghai-train-vs-flight/`
- `/destinations/chengdu/sanxingdui-museum-guide-chengdu/`
- `/destinations/gansu/china-travel-tips-qinghai-gansu-loop/`
- `/destinations/shanghai/where-to-stay-shanghai-first-time-guide/`
- `/destinations/chongqing/chongqing-metro-guide-travel-tips/`

## C) Decayed entry pages (last 30d vs previous 30d)

- `/ressources/apps-tech/china-travel-apps-guide-2026/` (approx -58%)
- `/ressources/budget-logistics/china-travel-tips-family-budget-cost/` (approx -40%)

---

## Behavior and Traffic Source Insights

Top sources by visits:
- Google (largest)
- ChatGPT.com
- Bing

Top entry pages show generally high bounce, indicating weak transition to second action/page for many articles.

Implication:
- Traffic acquisition is working better than traffic retention.
- Conversion architecture and internal linking/journey design are under-optimized.

---

## n8n Workflow Reliability Audit

Workflows detected:
- `gimme ideas` (active)
- `generate blog post` (active)
- `publish posts` (active)
- `YouTube` (active)
- `Blogs` (inactive)

Observed risks:
- Hardcoded tokens/secrets in workflow JSON (dashboard token/proxy fragments).
- Error notifications mostly email-based, but no structured reliability telemetry export was provided.
- Publish flow is complex and chained (images/tags/categories/ACF), increasing failure surface without strong observability.
- One ingestion workflow inactive (`Blogs`), potentially reducing source freshness coverage.

---

## Prioritized Upgrade Roadmap

## Fast Wins (1-2 weeks)

1. Rewrite title/meta for top 15 high-impression low-CTR pages.
2. Refresh intros and intent match for top 10 landing pages.
3. Add explicit internal next-step blocks on top entry pages.
4. Fix quality/trust hygiene issues (`ressources` typo and other UX trust frictions).
5. Add FAQ/schema consistency checks on top performers.

Expected effect:
- CTR uplift and immediate click gains without new content creation.

## Medium Projects (1-2 months)

1. Build/strengthen 4 pillar hubs:
   - Visa/Entry
   - Transport
   - Apps/Payments
   - Budget/Costs
2. Establish mandatory internal linking SOP per new post:
   - 1 pillar + 3-5 related guides.
3. Refresh decaying pages on rolling cadence with year-specific updates.
4. Segment lead capture offers by intent cluster.
5. Tighten taxonomy consistency to reduce crawl dilution/cannibalization.

Expected effect:
- Better rankings distribution, improved page depth, stronger topic authority.

## Structural Upgrades (Quarter)

1. Add content quality gate before publish (intent, uniqueness, internal-link plan).
2. Add workflow telemetry (step-level success/failure/duration) for all n8n pipelines.
3. Move all secrets to secure credentials/env vars (remove hardcoded tokens from workflow definitions).
4. Automate refresh candidate identification (GSC trend + Plausible entry decay + content age).
5. Add indexability QA checks pre-publish.

Expected effect:
- Higher reliability, more stable growth compounding, less operational risk.

---

## KPI Targets (Next 90 Days)

- GSC CTR: from ~0.39% to **0.7%-1.0%**
- Pages/visit: from ~1.31 to **>=1.6**
- Bounce rate on top entry pages: **-10 to -20 points**
- Clicks on top 10 opportunity pages: **+50%**
- Indexed/known ratio: improve via refresh/pruning/internal linking

---

## Recommended Next Execution Step

Implement a **15-page optimization sprint** first (metadata + intro intent + internal links + CTA/next-step block), then measure 2-4 week movement in:
- impressions
- CTR
- clicks
- entrances
- bounce
- pages/visit

This is the fastest path to validate lift before deeper structural work.

