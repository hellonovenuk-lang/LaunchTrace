# The public weekly feed

A free, delayed sample of the weekly lead list: a handful of new UK food brands
per Trade Marks Journal week, company level only. It exists to show prospective
customers what the paid list looks like. The full CSV is never published.

```bash
python -m src.pipeline build-feed                 # writes public/
python -m src.pipeline build-feed --out site/     # elsewhere
python -m src.pipeline build-feed --journal 2026-036   # just that week's page and JSON
```

The same content is served by the website (`src/web/app.py`), generated from
the database on each request with the same functions:

| URL | content |
| --- | --- |
| `/feed/` (also `/feed/index.html`) | index of public weeks |
| `/feed/<journal>.html` or `/feed/<journal>` | one week |
| `/feed/<journal>.json` | one week, JSON |
| `/feed.xml` (also `/feed/feed.xml`) | Atom 1.0 |
| `/feed/rss.xml` | RSS 2.0 |
| `/feed.json` (also `/feed/feed.json`) | every public week, JSON |

Code: `src/feed/build.py` (what may be published), `src/feed/render.py` (HTML,
Atom, RSS, JSON), `src/feed/command.py` (the CLI), `src/feed/web.py` (routes),
`src/feed/templates/`. Settings: `config/public_feed.json`.

## What is published

Per entry, and nothing else:

| field | source |
| --- | --- |
| `brand_name` | the mark text |
| `company_name`, `company_number` | the confirmed Companies House match (never the applicant name as filed) |
| `product_category` | the category label from `config/food_taxonomy.json` |
| `launch_stage` | human wording ("Pre-launch", "Early launch", …), the CSV's mapping |
| `filing_date` | trade mark filing date |
| `region` | the company's region only (see below) |
| `reason` | one short reason (see below) |
| `trademark_number`, `ukipo_url` | link to the UKIPO record |
| `companies_house_url` | link to the company's Companies House page |
| `id` | stable `urn:launchtrace:feed:<journal>:<brand_uid>` |

Never published: applicant names, directors or officers, websites, contact
pages, email addresses, post towns, postcodes, addresses, scores, bands,
buying-intent ratings, evidence URLs, the CSV.

## The rules (enforced in `src.feed.build.publishable`)

A lead is published only if **all** hold:

1. `applicant_type` is `corporate`.
2. It has a company number and company name, **and** the stored Companies
   House match for it says `matched` with the same number.
3. It is not suppressed, and its review state is not `rejected` or `suppressed`.
4. Its band is in `bands_allowed` and at or above `min_band`. `SUPPRESS` is
   never published, whatever the config says.
5. It matches no active `suppression_rules` row (case-insensitive):
   `company` rules against company name and number, `applicant` rules against
   the applicant and company name, `mark` rules against mark text and trade
   mark number; and no company on the `prospect_suppressions` opt-out list
   (normalised company name). `email` rules have nothing to match: no email is
   ever published.
6. Its journal week is public: the week's publication date is at least
   `delay_weeks` weeks before the **newest journal in the database**.

Then, per week: one entry per company (its best-scoring mark), highest score
first, at most `top_n_per_week`. The index and feeds list the
`max_weeks_in_index` most recent public weeks.

**Region.** The company's region as stored. Dropped if it contains a digit,
looks like a postcode, or equals the company's post town.

**Reason.** The first of the lead's stored reason texts that was rendered from
an indicator in `reason_keys_allowed` (matched against the templates in
`config/scoring.json`), contains no URL, `www.` or `@`, and does not contain
the applicant name. Otherwise `fallback_reason`. Indicators whose wording can
carry a website or web evidence are not on the list.

**UKIPO link.** The stored record URL only if it is `https` on an
`ipo.gov.uk` host and names the trade mark number; otherwise the standard
case-details URL. (Locally processed journals store a `file://` path, which is
never published.)

## Determinism

Output depends only on the database and the config: entries and weeks are
sorted, there is no build timestamp, and the delay is measured against the
newest journal held rather than the clock. Atom `updated` (and RSS
`lastBuildDate`) is the newest public week's publication date; an empty feed
uses `1970-01-01T00:00:00Z`. Building twice gives byte-identical files (tested).

The static build writes a `.launchtrace-feed` marker. When the output
directory carries it (or is new or empty), `.html`/`.json`/`.xml` files that
are no longer part of the feed are removed; otherwise nothing is deleted.

## JSON format

`feed.json`:

```json
{
  "version": "1.0",
  "title": "…", "description": "…",
  "updated": "2026-09-04",            // newest public week, or null
  "delay_weeks": 1, "top_n_per_week": 5,
  "attribution": "Trade mark data from the UK Intellectual Property Office …",
  "licence_url": "https://www.nationalarchives.gov.uk/doc/open-government-licence/version/3/",
  "home_page_url": "https://example/feed/",
  "weeks": [
    {
      "journal_number": "2026-035",
      "publication_date": "2026-08-28",
      "url": "https://example/feed/2026-035.html",
      "json_url": "https://example/feed/2026-035.json",
      "qualifying_count": 14,           // companies that passed the rules, before top N
      "entries": [ { "id": "…", "brand_name": "…", "company_name": "…",
                     "company_number": "…", "product_category": "…" | null,
                     "launch_stage": "…", "filing_date": "YYYY-MM-DD" | null,
                     "region": "…" | null, "reason": "…",
                     "trademark_number": "…", "ukipo_url": "…",
                     "companies_house_url": "…" } ]
    }
  ]
}
```

`<journal>.json` has the same top-level metadata (without `home_page_url` and
`weeks`) plus that week's fields.

## Links

Pages link to each other relatively (`index.html`, `<journal>.html`,
`feed.xml`), so the directory works from any host. Feed readers need absolute
links, so Atom, RSS and JSON use `public_base_url` from the config, or
`SITE_URL + "/feed/"` when it is empty.

The footer (privacy notice, terms, data and licensing, opt-out) and the call
to action ("Get the full weekly list" → `SITE_URL/#sample`) are absolute
`SITE_URL` links in the static build and site-relative on the website. The
footer reuses the website's existing data-source wording. The static pages
inline their stylesheet; the website links `/feed/feed.css` because its
Content-Security-Policy forbids inline styles.

## CI

`.github/workflows/weekly-pipeline.yml` runs `build-feed --out public/` after
the weekly step (`if: always()`, `continue-on-error`), so it runs even when the
weekly job stopped at "already processed", and uploads `public/` as the
artifact `public-feed-<run number>`. It never fails the run. It does **not**
publish anything.

## Publishing via GitHub Pages (a human action)

Nothing is published automatically. To publish the static build:

1. Decide where the feed lives (GitHub Pages, or only the website's `/feed`
   routes). If Pages: set `public_base_url` in `config/public_feed.json` to the
   Pages address (e.g. `https://<owner>.github.io/<repo>/`), and make sure the
   `SITE_URL` repository variable is set so the footer and call to action point
   at the website.
2. Settings → Pages → Source: **GitHub Actions**.
3. Add a deploy job (`actions/upload-pages-artifact` with `path: public/`
   followed by `actions/deploy-pages`, with `pages: write` and
   `id-token: write` permissions) after the build step — or download a
   `public-feed-*` artifact and publish it by hand the first few times.
4. Review a built feed before the first publication.
