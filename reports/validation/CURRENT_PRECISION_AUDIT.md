# Current precision audit — journals 2026-036 and 2026-037

Dated 11 September 2026. Measures how many of the opportunities LaunchTrace
would actually send a paying supplier are genuinely useful and factually
trustworthy. It is a measurement, not a target: nothing in the pipeline was
changed after these numbers were known.

## How this was produced

Both journals were rerun through the complete current-data pipeline — UKIPO
weekly journal, current goods and services text, Companies House, web
enrichment, entity verification, classification, maturity assessment,
LaunchTrace Score, company-level consolidation.

Two things about the environment have to be stated plainly, because they
bound what these figures prove.

**Companies House is live and complete.** The free bulk snapshot of
2026-09-01 was downloaded and indexed: 5,689,367 companies. Every company
match below is a real register record.

**Web evidence was recorded, not fetched live.** This validation environment
holds no `SEARCH_API_KEY`, so the configured Tavily provider could not run.
Live web search results were instead gathered for every candidate in both
weeks capable of reaching a delivery band, stored in `data/web_evidence/`,
and replayed by `scripts/replay_validation.py`. The evidence is genuine and
current; the retrieval channel differs from production.

That turns out to be the better experiment. Replaying fixed evidence through
the *old* code reproduces journal 2026-037's previously reported headline
exactly — 14 HIGH + 13 MEDIUM — and 2026-036 within one record (14 + 7
against the reported 13 + 7). So the before/after below isolates the code
changes from week-to-week movement in the web.

Candidates scoring below 40 without web evidence were not researched: with
every web indicator firing at full weight, the arithmetic caps them at 56,
below the MEDIUM floor of 60. 61 candidates were researched across the two
weeks.

## Before and after, by week

### 2026-036

| Metric | Before | After |
| --- | ---: | ---: |
| Records parsed | 2946 | 2946 |
| Food-class candidates | 274 | 274 |
| Packaged-food candidates | 84 | 79 |
| Companies House matches | 62 | 35 |
| Emerging candidates | 62 | 64 |
| Enriched records | 62 | 64 |
| HIGH | 14 | 2 |
| MEDIUM | 7 | 12 |
| Suppressed | 41 | 50 |
| Verified websites | not measured | 3 |
| Unverified websites | not measured | 18 |
| Out-of-scope products removed | not measured | 5 |
| Established brands removed | not measured | 1 |
| Duplicate marks consolidated | not measured | 2 |
| Final unique customer-facing companies | not measured | 14 |
| **Delivered opportunities** | **21** | **14** |

### 2026-037

| Metric | Before | After |
| --- | ---: | ---: |
| Records parsed | 3973 | 3973 |
| Food-class candidates | 350 | 350 |
| Packaged-food candidates | 97 | 85 |
| Companies House matches | 68 | 39 |
| Emerging candidates | 77 | 73 |
| Enriched records | 77 | 73 |
| HIGH | 14 | 2 |
| MEDIUM | 13 | 18 |
| Suppressed | 50 | 53 |
| Verified websites | not measured | 4 |
| Unverified websites | not measured | 16 |
| Out-of-scope products removed | not measured | 12 |
| Established brands removed | not measured | 1 |
| Duplicate marks consolidated | not measured | 2 |
| Final unique customer-facing companies | not measured | 20 |
| **Delivered opportunities** | **27** | **20** |

## What stopped qualifying, and why

Every row below was delivered to a customer under the old code. The
`website shown` column is the URL that would have been printed next to the
brand name in the feed.

### 2026-036

| Brand | Company | Was | Website shown | Now | Why |
| --- | --- | ---: | --- | ---: | --- |
| Hive Chocolate | HIVE CHOCOLATE LTD | 89 | https://www.tripadvisor.com | 79 | folded into the company's other mark |
| Hawkstone Farms | HAWKSTONE FARMS LTD | 88 | https://hawkstone.com | 56 | below the delivery band; brand is already established |
| LOMA frite house | JPDM IP CO LTD | 73 | https://www.cameronhouse.co.uk | 73 | folded into the company's other mark |
| Biogena HämoRutin | BIOGENA UK LIMITED | 73 | — | — | removed before scoring (out of scope for LaunchTrace Food) |
| Biogena VenoSafe | BIOGENA UK LIMITED | 73 | — | — | removed before scoring (out of scope for LaunchTrace Food) |
| HALF BREW | HALF BREW LTD | 72 | — | — | removed before scoring (out of scope for LaunchTrace Food) |
| Laxmi Avyan | LAXMI AVYAN LIMITED | 63 | https://indiashopping.io | 56 | below the delivery band |

### 2026-037

| Brand | Company | Was | Website shown | Now | Why |
| --- | --- | ---: | --- | ---: | --- |
| your everyday squeeze | TOSS WORLD LIMITED | 89 | https://foodanddrinknetwork.co.uk | 79 | folded into the company's other mark |
| Fast Eats | ISLA DELICE LTD | 87 | https://www.tariqhalalmeats.com | 30 | below the delivery band; brand is already established |
| HAMPSHIRE HARVEST | FRESH & CO GROUP LIMITED | 78 | https://en.wikipedia.org | 51 | below the delivery band |
| GOLDEN ROOTS | HEAVENLY FOODS & BEVERAGES LTD | 72 | https://en.wikipedia.org | 71 | folded into the company's other mark |
| Veya Labs | VEYA LABORATORIES LTD | 71 | — | — | removed before scoring (out of scope for LaunchTrace Food) |
| Wahey | STREAMWORKS STREAMERS LIMITED | 63 | — | — | removed before scoring (out of scope for LaunchTrace Food) |
| Pingummi | Mederer GmbH | 60 | https://trolli.de | 41 | below the delivery band |

## Every delivered opportunity, audited

Judged on the underlying real-world evidence — Companies House record,
goods and services text, and what the web does or does not show about the
brand. The LaunchTrace Score is reported but was not treated as evidence
that an opportunity is good.

**EXCELLENT** — a genuinely emerging brand, clearly relevant to LaunchTrace
Food suppliers, early enough for outreach to matter. **GOOD** — commercially
plausible; I would be comfortable putting it in a paid feed. **WEAK** —
technically qualifies, but I would rather not send it. **WRONG** — a false
positive.

### 2026-036 — 14 companies

#### ARAW — **GOOD**

- **Company** — ARAW LIMITED (14387387, incorporated 2022-09-29, LONDON)
- **Brand / product** — ARAW; Biscuits, cookies, crackers and bakery; Nice class 29, 30, 35, 43
- **LaunchTrace Score** — 83 (HIGH)
- **Website verification** — Verified: https://www.arawlondon.com
- **Brand maturity** — emerging
- **Buying-intent categories** — flexible packaging, labels, cartons, contract manufacturing, copacking, distribution, brokerage, fulfilment, marketing
- **Trade mark** — UK00004435180
- **Audit** — Real London ice-cream maker (SIC 10520, own manufacture), trading since 2021 with two outlets and now protecting packaged formats — sandwiches, cakes, lollies, sorbets. Website verified from its own pages. Marked GOOD not EXCELLENT because the feed labels it 'Biscuits, cookies, crackers and bakery', which would send a packaging supplier to the wrong conversation.

#### MANTA — **GOOD**

- **Company** — CHOCOLATE HOUSE LTD (15848158, incorporated 2024-07-19, LONDON)
- **Brand / product** — MANTA; Sauces, condiments, spreads and seasonings; Nice class 29, 30
- **LaunchTrace Score** — 81 (HIGH)
- **Website verification** — Verified: https://www.mantachocolate.com
- **Brand maturity** — emerging
- **Buying-intent categories** — labels, cartons, contract manufacturing, copacking, distribution, brokerage, fulfilment, marketing
- **Trade mark** — UK00004433393
- **Audit** — Two-year-old London chocolate and nut-spread wholesaler with a focused class 29/30 range. mantachocolate.com carries the brand, the city and the right products, but names no legal entity anywhere on the site, so the link to Chocolate House Ltd is strong inference rather than proof.

#### Cactus Joe's Kitchen — **GOOD**

- **Company** — NEWCO BRANDS LTD (17296659, incorporated 2026-06-23, ASCOT)
- **Brand / product** — Cactus Joe's Kitchen; Ambient and shelf-stable prepared foods; Nice class 29, 30
- **LaunchTrace Score** — 79 (MEDIUM)
- **Website verification** — Not found
- **Brand maturity** — unknown
- **Buying-intent categories** — flexible packaging, labels, cartons, contract manufacturing, copacking, distribution, brokerage, fulfilment
- **Trade mark** — UK00004422947
- **Audit** — Canned meat and pies, a clean single-category filing, from a company six weeks old at filing. Cans, labels and pie cartons are exactly the spend a packaging supplier wants to reach early. 'NEWCO BRANDS LTD' with a non-specialised wholesale SIC and no footprint is a mild flag that this may be a brand-holding vehicle.

#### Fatboy's Cocoa — **WEAK**

- **Company** — FATBOY'S COCOA LTD (17260212, incorporated 2026-06-03, LONDON)
- **Brand / product** — Fatboy's Cocoa; Biscuits, cookies, crackers and bakery; Nice class 30, 43
- **LaunchTrace Score** — 79 (MEDIUM)
- **Website verification** — Not found
- **Brand maturity** — unknown
- **Buying-intent categories** — flexible packaging, labels, cartons, contract manufacturing, copacking, distribution, brokerage, fulfilment, marketing
- **Trade mark** — UK00004430919
- **Audit** — Registered SICs are unlicensed cafes, market stalls and cocoa manufacture, and the goods are drinks served plus 'serving food and drink in doughnut shops'. The cocoa-manufacture SIC is real, but the filing describes a food-service business, and the packaged-product spend behind it is speculative.

#### NORI Kitchen — **GOOD**

- **Company** — THE SUSHI FACTORY & BEYOND LTD (16527273, incorporated 2025-06-18, LONDON)
- **Brand / product** — NORI Kitchen; Snacks; Nice class 29, 30
- **LaunchTrace Score** — 79 (MEDIUM)
- **Website verification** — Not found
- **Brand maturity** — unknown
- **Buying-intent categories** — flexible packaging, labels, cartons, contract manufacturing, copacking, distribution, brokerage, fulfilment, marketing
- **Trade mark** — UK00004433645
- **Audit** — Chilled sushi, sashimi and ramen from a one-year-old London producer. Chilled prepared food is among the most packaging-intensive categories there is — trays, film, labels — and the applicant name suggests production rather than a single restaurant.

#### RICEGAINS — **GOOD**

- **Company** — RICE GAINS LTD (17410414, incorporated 2026-08-20, LONDON)
- **Brand / product** — RICEGAINS; Snacks; Nice class 29, 30
- **LaunchTrace Score** — 79 (MEDIUM)
- **Website verification** — Not found
- **Brand maturity** — unknown
- **Buying-intent categories** — flexible packaging, labels, cartons, contract manufacturing, copacking, distribution, brokerage, fulfilment, marketing
- **Trade mark** — UK00004433317
- **Audit** — Incorporated on the same day it filed, with a range of prepared ready meals. That is the earliest point at which a co-packer or tray supplier could usefully be in the room. The feed labels the category 'Snacks', which is wrong for ready meals.

#### HIVE — **EXCELLENT**

- **Company** — HIVE CHOCOLATE LTD (17292222, incorporated 2026-06-21, Y FELINHELI)
- **Brand / product** — HIVE; Confectionery, chocolate and sweets; Nice class 30, 35
- **Also filed by this company** — Hive Chocolate (UK00004405580)
- **LaunchTrace Score** — 79 (MEDIUM)
- **Website verification** — Probable (withheld)
- **Brand maturity** — unknown
- **Buying-intent categories** — flexible packaging, labels, cartons, contract manufacturing, copacking, distribution, brokerage, fulfilment, marketing
- **Trade mark** — UK00004405594
- **Audit** — A chocolate manufacturer (SIC 10821) incorporated three months before filing, in Gwynedd, with a focused chocolate and honey range. New manufacturer, wrappers and bars to specify, nothing bought yet. Two marks correctly consolidated into one company. The unrelated smart-heating company at hive.com was rejected rather than published.

#### Daio — **GOOD**

- **Company** — SHUDHCO. LTD (17047067, incorporated 2026-02-23, ST. ALBANS)
- **Brand / product** — Daio; Chilled and frozen packaged food; Nice class 29
- **LaunchTrace Score** — 79 (MEDIUM)
- **Website verification** — Not found
- **Brand maturity** — unknown
- **Buying-intent categories** — flexible packaging, labels, cartons, contract manufacturing, copacking, distribution, brokerage, marketing
- **Trade mark** — UK00004428273
- **Audit** — A dairy and plant-milk range from a company seven months old. Cartons, bottles and labels are the whole cost base of that category. The retail SIC leaves open whether they will manufacture or factor, which is the only reservation.

#### SUMMER JUICE — **WEAK**

- **Company** — LONDON ORIENT&UK BRIDGING LTD (15804875, incorporated 2024-06-26, LONDON)
- **Brand / product** — SUMMER JUICE; Coffee, tea and hot drinks; Nice class 30, 32, 43
- **LaunchTrace Score** — 79 (MEDIUM)
- **Website verification** — Probable (withheld)
- **Brand maturity** — unknown
- **Buying-intent categories** — flexible packaging, labels, cartons, contract manufacturing, copacking, distribution, fulfilment, marketing
- **Trade mark** — UK00004403711
- **Audit** — SIC profile is licensed restaurants plus general business support and property management, class 43 is on the filing, and the goods are served drinks and smoothies. This is a drinks bar, not a packaged-food brand, and LaunchTrace Food v1 does not cover drinks anyway.

#### 김 SEAWEED ROASTERY — **EXCELLENT**

- **Company** — SEAWEED ROASTERY LIMITED (17280315, incorporated 2026-06-18, LONDON)
- **Brand / product** — 김 SEAWEED ROASTERY; Snacks; Nice class 29, 30, 35, 43
- **LaunchTrace Score** — 79 (MEDIUM)
- **Website verification** — Not found
- **Brand maturity** — unknown
- **Buying-intent categories** — flexible packaging, labels, cartons, contract manufacturing, copacking, distribution, brokerage, fulfilment, marketing
- **Trade mark** — UK00004435235
- **Audit** — A seaweed snack producer with a food-manufacturing SIC (10890), incorporated two months before filing, with a tightly focused range. Directly comparable to Morish, the London seaweed snack brand that launched into the same market. Flexible packaging is the category's dominant input.

#### dialect — **GOOD**

- **Company** — NESH BRANDS LTD (17247785, incorporated 2026-05-28, LONDON)
- **Brand / product** — dialect; Sauces, condiments, spreads and seasonings; Nice class 29, 30, 31
- **LaunchTrace Score** — 79 (MEDIUM)
- **Website verification** — Probable (withheld)
- **Brand maturity** — unknown
- **Buying-intent categories** — labels, cartons, contract manufacturing, copacking, distribution, brokerage, fulfilment, marketing
- **Trade mark** — UK00004434758
- **Audit** — Roasted, salted and spiced nuts from a three-month-old London brand company. Nut snacks live and die on flexible packaging. The feed labels the category 'Sauces, condiments, spreads and seasonings', which is wrong.

#### St.Demeter — **WEAK**

- **Company** — GDR SPIRITS COMPANY LIMITED (14550602, incorporated 2022-12-20, BIRMINGHAM)
- **Brand / product** — St.Demeter; Sauces, condiments, spreads and seasonings; Nice class 30
- **LaunchTrace Score** — 75 (MEDIUM)
- **Website verification** — Probable (withheld)
- **Brand maturity** — unknown
- **Buying-intent categories** — labels, cartons, contract manufacturing, copacking, distribution, brokerage, fulfilment, marketing
- **Trade mark** — UK00004404081
- **Audit** — A wholesaler registered as a spirits company filing a scattergun grocery range — coffee, tea, honey, cakes, rice, flour, noodles, chocolate. No product focus, no web presence, a non-specialised wholesale SIC. A supplier would not know what to pitch.

#### LOMA frite house — **WRONG**

- **Company** — JPDM IP CO LTD (17339741, incorporated 2026-07-14, LONDON)
- **Brand / product** — LOMA frite house; Sauces, condiments, spreads and seasonings; Nice class 18, 25, 29, 30
- **Also filed by this company** — LOMA frite house (UK00004428550)
- **LaunchTrace Score** — 73 (MEDIUM)
- **Website verification** — Probable (withheld)
- **Brand maturity** — unknown
- **Buying-intent categories** — labels, cartons, contract manufacturing, copacking, distribution, brokerage, fulfilment, marketing
- **Trade mark** — UK00004428587
- **Audit** — An intellectual-property holding vehicle (SIC 77400, leasing of IP) for a chip-shop brand. The goods are tote bags, backpacks, T-shirts and hoodies with food classes attached. There is no packaged food product here, and the feed labels it 'Sauces, condiments, spreads and seasonings'.

#### CHACOO — **WRONG**

- **Company** — CHACOO LIMITED (17279736, incorporated 2026-06-17, BRIGHTON)
- **Brand / product** — CHACOO; Coffee, tea and hot drinks; Nice class 30, 43
- **LaunchTrace Score** — 65 (MEDIUM)
- **Website verification** — Probable (withheld)
- **Brand maturity** — unknown
- **Buying-intent categories** — flexible packaging, labels, cartons, contract manufacturing, copacking, distribution, fulfilment, marketing
- **Trade mark** — UK00004434663
- **Audit** — Both registered SICs are unlicensed cafes and takeaways, class 43 is on the filing, and the goods are drinks and crepes served to customers. A Brighton cafe, delivered to a packaging supplier as an emerging food brand.


### 2026-037 — 20 companies

#### kWh Coffee — **EXCELLENT**

- **Company** — MORNING PEOPLE LTD (16660094, incorporated 2025-08-19, YORK)
- **Brand / product** — kWh Coffee; Coffee, tea and hot drinks; Nice class 30
- **LaunchTrace Score** — 88 (HIGH)
- **Website verification** — Verified: https://kwhcoffee.com
- **Brand maturity** — emerging
- **Buying-intent categories** — flexible packaging, labels, cartons, contract manufacturing, copacking, distribution, fulfilment, marketing
- **Trade mark** — UK00004435554
- **Audit** — The strongest record in either week. An electric-powered speciality coffee roastery in York, incorporated a year before filing, with a coffee-processing SIC. Its own site states 'kWh Coffee by Morning People Ltd, Companies House No. 16660094' — the exact company on the record. Coffee bags, valves and labels are an immediate, recurring spend.

#### Sami's Superfoods — **EXCELLENT**

- **Company** — SAMIS SUPERFOODS LTD (13274599, incorporated 2021-03-18, SHEFFIELD)
- **Brand / product** — Sami's Superfoods; Sauces, condiments, spreads and seasonings; Nice class 29, 30, 43
- **LaunchTrace Score** — 83 (HIGH)
- **Website verification** — Verified: https://samissuperfoods.com
- **Brand maturity** — emerging
- **Buying-intent categories** — labels, cartons, contract manufacturing, copacking, distribution, brokerage, fulfilment, marketing
- **Trade mark** — UK00004435509
- **Audit** — A Sheffield importer and packer founded in 2021, now past 20,000 customers, 100 tonnes of nuts and 200,000 units of its own chocolate line. Small enough to still be choosing suppliers, large enough to be worth a call. Website verified from four of its own pages plus a third-party company record.

#### Rose & Grind — **GOOD**

- **Company** — ROSE & GRIND LTD (17410336, incorporated 2026-08-20, DARLINGTON)
- **Brand / product** — Rose & Grind; Coffee, tea and hot drinks; Nice class 21, 25, 30
- **LaunchTrace Score** — 79 (MEDIUM)
- **Website verification** — Not found
- **Brand maturity** — unknown
- **Buying-intent categories** — flexible packaging, labels, cartons, contract manufacturing, copacking, distribution, fulfilment, marketing
- **Trade mark** — UK00004433315
- **Audit** — Incorporated on the day it filed, with a coffee wholesale SIC and a complete coffee range including beans, ground, bags, pods and capsules. The merchandise classes on the same filing are noise around a real product.

#### Cray Sauces — **EXCELLENT**

- **Company** — CRAY SAUCES LTD (17383940, incorporated 2026-08-05, LONDON)
- **Brand / product** — Cray Sauces; Sauces, condiments, spreads and seasonings; Nice class 30
- **LaunchTrace Score** — 79 (MEDIUM)
- **Website verification** — Not found
- **Brand maturity** — unknown
- **Buying-intent categories** — labels, cartons, contract manufacturing, copacking, distribution, brokerage, fulfilment, marketing
- **Trade mark** — UK00004435936
- **Audit** — A sauce brand and nothing else: the company was formed three weeks before filing and the goods are sauces, condiments and ketchup end to end. Bottles, caps and labels, specified by someone who has not chosen a supplier yet.

#### BULLITT — **EXCELLENT**

- **Company** — COLDHARBOUR FOODS LIMITED (17035779, incorporated 2026-02-16, MARLOW)
- **Brand / product** — BULLITT; Cereal, protein and energy bars; Nice class 29
- **LaunchTrace Score** — 79 (MEDIUM)
- **Website verification** — Not found
- **Brand maturity** — unknown
- **Buying-intent categories** — flexible packaging, labels, cartons, contract manufacturing, copacking, distribution, brokerage, fulfilment, marketing
- **Trade mark** — UK00004437089
- **Audit** — Goods text reads, in full, 'Energy Bar.' A six-month-old company with a confectionery wholesale SIC and one product. Bar wrappers are a pure flexible-packaging sale and the timing could not be better.

#### BEAKY — **EXCELLENT**

- **Company** — BEAKY FOODS LTD (17308892, incorporated 2026-06-30, LONDON)
- **Brand / product** — BEAKY; Sauces, condiments, spreads and seasonings; Nice class 30
- **LaunchTrace Score** — 79 (MEDIUM)
- **Website verification** — Not found
- **Brand maturity** — unknown
- **Buying-intent categories** — labels, cartons, contract manufacturing, copacking, distribution, brokerage, fulfilment, marketing
- **Trade mark** — UK00004436958
- **Audit** — A focused seasonings and marinades range from a two-month-old London company with a food wholesale SIC. Jars, sachets, shakers and labels.

#### GIGGLECHOC — **GOOD**

- **Company** — JOKE IT LTD (16951622, incorporated 2026-01-08, PORTSMOUTH)
- **Brand / product** — GIGGLECHOC; Confectionery, chocolate and sweets; Nice class 16, 25, 28, 30, 35
- **LaunchTrace Score** — 79 (MEDIUM)
- **Website verification** — Not found
- **Brand maturity** — unknown
- **Buying-intent categories** — flexible packaging, labels, cartons, contract manufacturing, copacking, distribution, brokerage, fulfilment, marketing
- **Trade mark** — UK00004437597
- **Audit** — Chocolate confectionery with a wholesale-confectionery SIC, sold alongside collectible toys and trading cards. The confectionery line is genuine and needs wrappers and boxes; the toy and clothing classes make the business harder to read.

#### چاى مامه CHAI MAMA — **GOOD**

- **Company** — FROND FOODS LTD (16798042, incorporated 2025-10-20, LIVERPOOL)
- **Brand / product** — چاى مامه CHAI MAMA; Coffee, tea and hot drinks; Nice class 30
- **LaunchTrace Score** — 79 (MEDIUM)
- **Website verification** — Not found
- **Brand maturity** — unknown
- **Buying-intent categories** — flexible packaging, labels, cartons, contract manufacturing, copacking, distribution, fulfilment, marketing
- **Trade mark** — UK00004435320
- **Audit** — A tea brand and nothing else — black tea, chai, leaves, bags — from a Liverpool food wholesaler incorporated ten months before filing. Cartons and tea bags. The brand is rendered with Arabic script in the feed, which needs handling before a customer sees it.

#### TOSS — **EXCELLENT**

- **Company** — TOSS WORLD LIMITED (17030618, incorporated 2026-02-12, HASLEMERE)
- **Brand / product** — TOSS; Sauces, condiments, spreads and seasonings; Nice class 18, 21, 25, 30
- **Also filed by this company** — your everyday squeeze (UK00004437945)
- **LaunchTrace Score** — 79 (MEDIUM)
- **Website verification** — Not found
- **Brand maturity** — unknown
- **Buying-intent categories** — labels, cartons, contract manufacturing, copacking, distribution, brokerage, fulfilment, marketing
- **Trade mark** — UK00004437777
- **Audit** — Confirmed launching: the trade press covered TOSS entering the UK salad dressing aisle with four vegan, gluten-free dressings. The company manufactures condiments and seasonings (SIC 10840) and was formed six months before filing. Two marks consolidated into one prospect.

#### PITHY — **EXCELLENT**

- **Company** — PITHY LTD (17422672, incorporated 2026-08-26, LONDON)
- **Brand / product** — PITHY; Sauces, condiments, spreads and seasonings; Nice class 29, 30, 32
- **LaunchTrace Score** — 79 (MEDIUM)
- **Website verification** — Not found
- **Brand maturity** — unknown
- **Buying-intent categories** — labels, cartons, contract manufacturing, copacking, distribution, brokerage, fulfilment, marketing
- **Trade mark** — UK00004437400
- **Audit** — Incorporated two days before filing, with three food-manufacturing SICs and a culinary citrus and flavourings range. There is no earlier point at which to reach a brand.

#### CLOVER & FLINT — **WEAK**

- **Company** — CLOVER & FLINT LIMITED (17389132, incorporated 2026-08-09, HALSTEAD)
- **Brand / product** — CLOVER & FLINT; Coffee, tea and hot drinks; Nice class 3, 4, 24, 30
- **LaunchTrace Score** — 79 (MEDIUM)
- **Website verification** — Not found
- **Brand maturity** — unknown
- **Buying-intent categories** — flexible packaging, labels, cartons, contract manufacturing, copacking, distribution, fulfilment, marketing
- **Trade mark** — UK00004431100
- **Audit** — Body lotions, reed diffusers, soap, candles, throws and pillowcases, with chocolate, biscuits, tea and coffee at the end. A home-fragrance and textiles gifting brand with a small food line, wholesaling household goods. Marginal for a food packaging supplier.

#### LEAH'S — **GOOD**

- **Company** — EARTH BRANDS LTD (17243715, incorporated 2026-05-26, MANCHESTER)
- **Brand / product** — LEAH'S; Chilled and frozen packaged food; Nice class 29, 30
- **LaunchTrace Score** — 79 (MEDIUM)
- **Website verification** — Probable (withheld)
- **Brand maturity** — unknown
- **Buying-intent categories** — flexible packaging, labels, cartons, contract manufacturing, copacking, distribution, marketing
- **Trade mark** — UK00004437844
- **Audit** — A focused chilled dairy launch — labneh, yoghurt, kefir, yoghurt drinks — from a company three months old. Pots, lids, sleeves and labels. The holding-company SIC means there is no trading history behind it yet.

#### GOLDEN ROOTS — **GOOD**

- **Company** — HEAVENLY FOODS & BEVERAGES LTD (14547529, incorporated 2022-12-19, LEICESTER)
- **Brand / product** — GOLDEN ROOTS; Snacks; Nice class 29
- **Also filed by this company** — GOLDEN ROOTS (UK00004435534)
- **LaunchTrace Score** — 77 (MEDIUM)
- **Website verification** — Not found
- **Brand maturity** — unknown
- **Buying-intent categories** — flexible packaging, labels, cartons, contract manufacturing, copacking, distribution, brokerage, fulfilment, marketing
- **Trade mark** — UK00004435549
- **Audit** — A Leicester food wholesaler of four years launching a root-vegetable snack range. Flexible packaging, an established route to market, and two marks correctly consolidated into one prospect.

#### Flavour Fellas — **WRONG**

- **Company** — FLAVOUR FELLAS LIMITED (16756411, incorporated 2025-10-01, BURY ST. EDMUNDS)
- **Brand / product** — Flavour Fellas; Sauces, condiments, spreads and seasonings; Nice class 8, 16, 21, 25, 29, 30, 35, 41, 43
- **LaunchTrace Score** — 76 (MEDIUM)
- **Website verification** — Not found
- **Brand maturity** — unknown
- **Buying-intent categories** — labels, cartons, contract manufacturing, copacking, distribution, brokerage, fulfilment, marketing
- **Trade mark** — UK00004426831
- **Audit** — Nine classes, a motion-picture production SIC, and goods dominated by kitchen knives, cookware, cookery books, posters and clothing. This is a food media and merchandise brand, not a food producer.

#### AE ARAVALLI ESSENCE — **GOOD**

- **Company** — ARRAVALI ESSENCE LTD (16408046, incorporated 2025-04-25, LEICESTER)
- **Brand / product** — AE ARAVALLI ESSENCE; Sauces, condiments, spreads and seasonings; Nice class 30
- **LaunchTrace Score** — 69 (MEDIUM)
- **Website verification** — Probable (withheld)
- **Brand maturity** — unknown
- **Buying-intent categories** — flexible packaging, labels, cartons, contract manufacturing, copacking, distribution, marketing
- **Trade mark** — UK00004436191
- **Audit** — A real trading Leicester spice business with an online shop, an eBay store and a 5-star hygiene rating from November 2025. Pouches, jars and labels. Its website was withheld because the registered name spells the brand differently, which is the conservative rule working as designed but costing a useful field.

#### FAÏO — **WEAK**

- **Company** — ETALIAN CUISINE LTD (16625214, incorporated 2025-08-04, LONDON)
- **Brand / product** — FAÏO; Biscuits, cookies, crackers and bakery; Nice class 30, 35, 43
- **LaunchTrace Score** — 67 (MEDIUM)
- **Website verification** — Not found
- **Brand maturity** — unknown
- **Buying-intent categories** — flexible packaging, labels, cartons, contract manufacturing, copacking, distribution, brokerage, fulfilment, marketing
- **Trade mark** — UK00004436501
- **Audit** — Registered SICs are take-away food shops and other food service, and class 43 is on the filing. A London bakery and dessert counter; the packaged-product need stops at takeaway boxes.

#### PROJECT FROYO SIGNATURE — **WRONG**

- **Company** — PROJECT FROYO LTD (SC845355, incorporated 2025-04-15, GLASGOW)
- **Brand / product** — PROJECT FROYO SIGNATURE; Chilled and frozen packaged food; Nice class 29, 30, 32, 35
- **LaunchTrace Score** — 65 (MEDIUM)
- **Website verification** — Not found
- **Brand maturity** — unknown
- **Buying-intent categories** — flexible packaging, labels, cartons, contract manufacturing, copacking, distribution, marketing
- **Trade mark** — UK00004435334
- **Audit** — A single frozen-yogurt shop at 17 Old Dumbarton Road in Glasgow's West End, with an 'other service activities' SIC. Confirmed from its own social coverage. Not a packaged-food brand.

#### BOBA BY SUSHI TIGERS — **WRONG**

- **Company** — IRO SUSHI LONDON LIMITED (14878425, incorporated 2023-05-18, ROMFORD)
- **Brand / product** — BOBA BY SUSHI TIGERS; Ambient and shelf-stable prepared foods; Nice class 30
- **LaunchTrace Score** — 65 (MEDIUM)
- **Website verification** — Not found
- **Brand maturity** — unknown
- **Buying-intent categories** — flexible packaging, labels, cartons, contract manufacturing, copacking, distribution, brokerage, fulfilment
- **Trade mark** — UK00004436558
- **Audit** — Boba Tigers is a bubble tea and sushi takeaway chain with shops in Seven Kings and Barkingside, trading since 2021 on Uber Eats and Just Eat, with a food-service SIC. A hospitality chain in a packaged-food feed.

#### Jailato — **WEAK**

- **Company** — HERITAGE & ROOTS HOLDINGS LTD (17053693, incorporated 2026-02-25, LONDON)
- **Brand / product** — Jailato; Biscuits, cookies, crackers and bakery; Nice class 30, 43
- **LaunchTrace Score** — 64 (MEDIUM)
- **Website verification** — Probable (withheld)
- **Brand maturity** — unknown
- **Buying-intent categories** — flexible packaging, labels, cartons, contract manufacturing, copacking, distribution, brokerage, fulfilment, marketing
- **Trade mark** — UK00004437239
- **Audit** — Ice cream with 'ice cream parlors' on the same filing and a head-office SIC. Most likely a gelato counter rather than a packaged product, and there is nothing on the web to tell either way.

#### NAJORA — **GOOD**

- **Company** — RAADL GROUP LTD (13123687, incorporated 2021-01-11, PETERBOROUGH)
- **Brand / product** — NAJORA; Sauces, condiments, spreads and seasonings; Nice class 30
- **LaunchTrace Score** — 64 (MEDIUM)
- **Website verification** — Not found
- **Brand maturity** — unknown
- **Buying-intent categories** — labels, cartons, contract manufacturing, copacking, distribution, brokerage, fulfilment, marketing
- **Trade mark** — UK00004425387
- **Audit** — A clean class 30 condiments filing backed by SIC 10840, manufacture of condiments and seasonings. The company also lists software and recruitment activities, which makes the group hard to read, but the food line itself is specific and real.


## Precision

    useful precision = (EXCELLENT + GOOD) / all delivered opportunities

| Week | Delivered | EXCELLENT | GOOD | WEAK | WRONG | Useful precision |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| 2026-036 | 14 | 2 (14.3%) | 7 (50.0%) | 3 (21.4%) | 2 (14.3%) | **64.3%** |
| 2026-037 | 20 | 7 (35.0%) | 7 (35.0%) | 3 (15.0%) | 3 (15.0%) | **70.0%** |
| **Combined** | **34** | **9 (26.5%)** | **14 (41.2%)** | **6 (17.6%)** | **5 (14.7%)** | **67.6%** |

Useful precision is **67.6%** combined, against a desired benchmark of
roughly 80%. It is below the benchmark and is reported as measured.

## Score and band changes caused by corrected entity evidence

Counted over the records that were scored under both codebases, on identical
web evidence.

| | 2026-036 | 2026-037 |
| --- | ---: | ---: |
| Records scored under both | 58 | 67 |
| Score changed | 47 | 57 |
| Band changed | 13 | 14 |
| HIGH → MEDIUM | 11 | 11 |
| HIGH → SUPPRESS | 1 | 1 |
| MEDIUM → SUPPRESS | 1 | 2 |
| Companies House match withdrawn as a namesake | 17 | 18 |
| Changed by web evidence alone (company match unchanged) | 36 | 44 |
| Unproven website withdrawn from customer output | 25 | 29 |

The band movement is almost entirely HIGH → MEDIUM, and that is the honest
correction rather than a loss. The HIGH band means "we checked, and this brand
still looks early". Under the old code that check was passing on evidence about
somebody else: `no_major_retail_listings` scored as a positive whenever the
search had simply returned a different company, and `early_stage_website`
scored on a website nobody had shown belonged to the applicant. With evidence
that cannot be attributed to the applicant now treated as evidence of nothing,
those records fall to the top of MEDIUM, where the cap says what is actually
known. No threshold was changed to produce this.

## Four failure classes this pass corrected

**Websites that belonged to someone else.** Enrichment took the best-looking
search result as the brand's site. Across the two weeks the old code would have
printed `tripadvisor.com`, `en.wikipedia.org` (twice), `trolli.de`,
`cameronhouse.co.uk`, `indiashopping.io`, `tariqhalalmeats.com` and a trade
magazine as brand websites. 54 unproven websites were withdrawn and 7 survived
verification, 4 of them on records that reach the feed. All 4 were opened and
read by hand. Three are certain: kWh Coffee's own site states "kWh Coffee by
Morning People Ltd, Companies House No. 16660094", the exact company on the
record, and Araw and Sami's Superfoods match on brand, city, founder and
product. The fourth, mantachocolate.com, carries the brand, the city and the
right products but names no legal entity anywhere on the site, so the tie to
Chocolate House Ltd is strong inference rather than proof. That one is the
current edge of the rule.

**Scoring that reasoned about the wrong company.** The same guess drove
maturity, retail presence and launch signals. It now derives only from results
attributable to the applicant, and "we found nothing about them" is scored as
what it is rather than as a clean bill of health.

**Companies House matches that were namesakes.** Found while listing the
records that reach enrichment: a candidate whose distinctive words were a
subset of the applicant's scored as a strong match however small that subset
was. "Melissa Bent" matched MELISSA 27 LIMITED, "The Secretary of State for
Defence" matched SECRETARY LTD, "Lion's Gate Hot Sauce Ltd" matched LION & CO.,
LTD. This was worse than the website problem, because a wrong website is
visibly wrong while a wrong company match silently supplies the incorporation
date the score reads as the brand's age. 35 fabricated matches withdrawn across
the two weeks.

**Supplements classified as normal packaged food.** Biogena HämoRutin and
Biogena VenoSafe were being delivered at 73 — the observed failure, caught by
a general rule that reads the goods and services text rather than the Nice
class. 17 out-of-scope records were removed across the two weeks, including
Applied Nutrition, Veya Labs, Soul Herbals and four BRIVA marks. No exclusion
is keyed to any brand name.

**New companies fronting established brands.** Hawkstone Farms Ltd was
incorporated in November 2025 and scored 88. The brand it fronts is the UK's
fastest-growing beer brand, has its own encyclopaedia entry and is stocked
nationally. It now scores 56 and does not ship. Isla Delice Ltd, filing "Fast
Eats", is the French market leader in halal charcuterie, created in 1990 and
stocked in Morrisons; it fell from 87 to 30. Both were found by attributing
evidence about the *brand as it trades* and about the *registered company*,
not by any rule naming either of them.

## What is still wrong, and not fixed in this pass

These are documented rather than corrected, because the brief is explicit that
the audit is a measurement and that a further correctness problem should be
written down rather than trigger another redesign.

**1. Hospitality and food service still reaches the feed. This is the single
largest remaining defect.** Eight of the eleven WEAK and WRONG records are
restaurants, cafés, takeaways, dessert counters or their IP vehicles: CHACOO
(SIC 56102/56103, a Brighton café), LOMA frite house (an IP-leasing company for
a chip shop), PROJECT FROYO (one frozen-yogurt shop in Glasgow), BOBA BY SUSHI
TIGERS (a bubble tea and sushi takeaway chain), FAÏO, Fatboy's Cocoa, SUMMER
JUICE and Jailato. The `service_business_sic_only` indicator does fire on
several of them — it is worth −25 — but it is not enough to clear the MEDIUM
floor, and a filing that pairs class 43 with classes 29 or 30 carries no
penalty at all. All eight are already WEAK or WRONG, so removing them would
move combined useful precision from 67.6% to 88.5% on this sample (23 useful
of 26 delivered). That is one specific, bounded change: treat
class 43 alongside a food class, and a wholly food-service SIC profile, as
disqualifying rather than merely negative.

**2. Merchandise-led filings.** Flavour Fellas is a food-media brand with a
motion-picture production SIC whose goods are knives, cookware, cookery books
and clothing across nine classes; CLOVER & FLINT is a home-fragrance and
textiles brand with chocolate at the end of the list. The product filter counts
in-scope keyword hits without weighing them against the out-of-scope classes
sharing the same filing.

**3. Product categories are mislabelled on records that are otherwise right.**
ARAW, an ice cream maker, is labelled "Biscuits, cookies, crackers and bakery"
because its goods mention ice cream *cakes*. "dialect", a roasted-nut brand, is
labelled "Sauces, condiments, spreads and seasonings". RICEGAINS, a ready-meal
brand, is labelled "Snacks". The category is chosen by raw keyword-hit count,
so an incidental word beats the dominant one. This does not make an opportunity
wrong, but it misdirects the supplier reading it, and it is visible in the
customer file.

**4. Incoherent goods lists are not detected.** One record in 2026-037,
"TestoUP", listed pharmacopoeia terms, babies' nappy-pants and five obscure
foods on the same filing. It fell below the band once its namesake company
match was withdrawn, so it did not ship — but nothing in the pipeline noticed
that the goods list was incoherent.

**5. Conservative verification costs real fields.** AE ARAVALLI ESSENCE is a
genuine trading spice business whose site was withheld because the registered
name spells the brand differently from the mark ("Arravali" against
"Aravalli"). This is the intended trade-off — blank beats wrong — but it is a
cost, and it is why 30 of the 34 delivered records ship with no website
at all.

## Customer-facing sample

`reports/validation/current/sample/2026-037/` holds the actual customer output
for the stronger week, generated and **not sent**. Nothing has been delivered
to any supplier, and no outreach of any kind was sent during this work.
