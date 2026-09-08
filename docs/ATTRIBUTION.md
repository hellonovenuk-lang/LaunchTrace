# Data sources, licensing and attribution

LaunchTrace is built on published public data. This page records where each
piece comes from, what licence it is used under, and where we are not certain.

**This is an operational draft prepared during the build. It has not been
reviewed by a lawyer. Read it, correct anything that does not match how you
actually intend to operate, and take advice before making public claims about
your licensing position.**

## UK Intellectual Property Office — trade mark data

**What we use.** The weekly Trade Marks Journal (published every Friday) and
the IPO's Trade Mark Data Release ("Open Data"). From each record we take the
application number, word mark, filing and publication dates, applicant name and
stated country, Nice classes, goods and services text where published, mark
type and status.

**Where it comes from.**

- Trade Marks Journal: <https://www.ipo.gov.uk/t-tmj.htm>
- Journal directories: `https://www.ipo.gov.uk/types/tm/t-os/t-tmj/tm-journals/<YYYY-NNN>/`
- Open Data release: <https://www.gov.uk/government/publications/ipo-trade-mark-data-release>
- Dataset entry: <https://www.data.gov.uk/dataset/fc8a832f-b5e2-4c03-9ae4-10a5e74b467c/ipo-tmj>

**Licence.** The IPO's Open Data release publication states that it "is licensed
under the terms of the Open Government Licence v3.0 except where otherwise
stated". The OGL permits copying, publishing, adapting and commercially
exploiting the information, provided the source is acknowledged.

**Attribution we give.** Every customer email, every CSV and the website footer
carry: *"Trade mark data from the UK Intellectual Property Office, used under
the Open Government Licence v3.0. LaunchTrace is not affiliated with or endorsed
by the Intellectual Property Office."*

**Where we are not certain.**

- The "except where otherwise stated" carve-out in the OGL notice is not
  itemised. We have assumed it covers third-party material such as trade mark
  *images*, and we therefore do not redistribute any trade mark image or
  figurative representation. Version 1 handles word marks only.
- We have not obtained written confirmation from the IPO that the weekly
  Journal XML carries the same OGL terms as the Open Data release. **Action for
  the owner: email the IPO to confirm, and record their answer in this file.**
- Trade mark records name applicants. Where an applicant is an individual
  rather than a company that name is personal data, which is why LaunchTrace
  downranks natural-person applicants and does not build the product around
  them (see the privacy notice).

## Companies House — company data

**What we use.** Company name, company number, status, category, incorporation
and dissolution dates, SIC codes, registered-office town/county/country, and
accounts category. Two official routes:

- The Public Data API: <https://developer.company-information.service.gov.uk/>
- The free bulk snapshot: <https://download.companieshouse.gov.uk/en_output.html>

**Licence.** Companies House publishes this data under the Open Government
Licence v3.0, which permits commercial reuse with acknowledgement.

**Attribution we give.** *"Company data from Companies House, used under the
Open Government Licence v3.0. LaunchTrace is not affiliated with or endorsed by
Companies House."*

**What we deliberately do not collect.**

- Officer and director records, including **directors' home addresses**
- Persons with significant control
- Dates of birth, or any other personal identifier
- Filing history documents

Only corporate qualification data is stored. This is both a data-minimisation
choice and the reason the product is defensible as B2B information.

## Web search and website evidence

**What we use.** A search API (Tavily, Serper or Brave, depending on
`SEARCH_PROVIDER`) to find a brand's own website, its contact page, and whether
it appears on marketplaces or major retailer sites.

**How we use it.** We store **URLs and short snippets as evidence**, plus our
own derived judgements (early-stage or established, retail presence, launch
stage). We do not copy substantial portions of anyone's website into the
product, and we do not republish page content to customers — the CSV carries
links so the customer can look for themselves.

**We do not scrape search engines.** Programmatic access goes through an API
whose terms permit it. If no search API is configured the pipeline runs without
web enrichment and says so, rather than falling back to scraping.

## Our own derived data

The LaunchTrace Score, the product categorisation, the launch-stage assessment
and the supplier buying-intent bands are LaunchTrace's own work product. They
are opinions derived from public data, not statements of fact about any
company, and the product copy says so.

## What LaunchTrace explicitly is not

- Not trade mark legal advice
- Not a trade mark watching or infringement-monitoring service
- Not a trade mark database or a substitute for the official register
- Not affiliated with the Intellectual Property Office or Companies House

## Review checklist for the owner

1. Confirm with the IPO in writing that the weekly Journal XML is OGL-licensed,
   and record the reply here.
2. Confirm the terms of whichever search API you connect permit commercial
   B2B use of the results in the way described above.
3. Have a solicitor review this file, `PRIVACY.md`, `TERMS.md` and
   `LEGITIMATE_INTERESTS_ASSESSMENT.md` before you take a paying customer.
