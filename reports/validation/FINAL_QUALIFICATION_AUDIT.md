# Final qualification audit — journals 2026-036 and 2026-037

Dated 11 September 2026. The bounded correctness pass that follows
`CURRENT_PRECISION_AUDIT.md`, measured once on the same recorded web evidence
so the difference is the rules and nothing else.

## What changed, and what deliberately did not

The previous audit's proposed next action was to treat Nice class 43
alongside a food class as disqualifying. That would have been wrong, and the
sample says so plainly: three of the strongest opportunities in these two
weeks carry class 43 — a seaweed manufacturer, an ice cream maker and a nut
packer, each covering its own counter — and three of the clearest false
positives do not. A frozen-yogurt shop filed in classes 29, 30, 32 and 35; a
bubble tea and sushi takeaway chain filed in class 30 alone.

So class 43 is now worth exactly one point of service evidence and can never
decide anything by itself. Three general rules changed instead.

**1. Commercial mode.** Every record is assessed as PACKAGED_PRODUCT,
MIXED_BUT_PRODUCT_RELEVANT, FOOD_SERVICE, NON_PRODUCT or UNCERTAIN, from the
evidence weighed together: what the goods actually are, what proportion of the
filing is food against hospitality against merchandise, what Companies House
says the company does, and what corroborated web evidence shows. A
food-manufacturing SIC is the strongest product evidence available and a
restaurant SIC the strongest service evidence; neither settles it alone,
because a real manufacturer can run a shop and a shop can register a code it
never uses. Only the two product-relevant modes reach a customer. Where
relevance cannot be established the record is withheld, because a supplier can
act on 'we did not find one' and cannot act on a guess.

**2. Dominant product.** The customer-facing category is now the product group
that owns the most goods items, not the one with the most keyword hits. Each
item is assigned to a single group by the most specific keyword that matches
it, with a bonus for matching the item's head noun — which is the whole
difference between reading 'ice cream cakes' as ice cream and reading it as
cake, and between reading 'spiced nuts' as nuts and reading it as spices.
Where a filing genuinely spans several packaged-food categories it now says so
rather than confidently naming one.

**3. Incidental food.** Where non-food goods dominate a filing AND Companies
House shows no food manufacturing or trade, the food lines are treated as
incidental. A cookware and cookery-book brand with a few prepared meals is not
a food opportunity; a coffee roaster that also protects mugs and T-shirts is.
The food SIC is what separates them.

No thresholds moved. No brand is named in any rule. The evidence replayed is
byte-identical to the previous audit's.

## Result

| | 2026-036 | 2026-037 | Combined |
| --- | ---: | ---: | ---: |
| Delivered before this pass | 14 | 20 | 34 |
| Delivered after this pass | 10 | 14 | 24 |
| HIGH | 2 | 2 | 4 |
| MEDIUM | 8 | 12 | 20 |
| Removed as not a packaged-product business | 5 | 5 | 10 |
| Removed as incidental / non-food | 0 | 1 | 1 |
| Marks consolidated into a company | 1 | 2 | 3 |
| Verified websites | 3 | 4 | 7 |

## Product-category corrections

Records that survive, whose customer-facing category was wrong before:

| Brand | Was | Now |
| --- | --- | --- |
| ARAW | Biscuits, cookies, crackers and bakery | Chilled and frozen packaged food |
| MANTA | Sauces, condiments, spreads and seasonings | Confectionery, chocolate and sweets |
| Fatboy's Cocoa | Biscuits, cookies, crackers and bakery | Several packaged-food categories: coffee, tea and hot drinks, confectionery, chocolate and sweets |
| NORI Kitchen | Snacks | Several packaged-food categories: ambient and shelf-stable prepared foods, snacks |
| RICEGAINS | Snacks | Ambient and shelf-stable prepared foods |
| dialect | Sauces, condiments, spreads and seasonings | Snacks |
| Sami's Superfoods | Sauces, condiments, spreads and seasonings | Snacks |

## Regression cases

The fifteen records the review named, and what the general rules did with
them. None is referred to by name anywhere in the code or configuration.

| Record | Nice classes | Commercial mode | Outcome |
| --- | --- | --- | --- |
| ARAW | 29, 30, 35, 43 | Mixed, but product relevant | **Delivered** — Chilled and frozen packaged food |
| 김 SEAWEED ROASTERY | 29, 30, 35, 43 | Mixed, but product relevant | **Delivered** — Snacks |
| Sami's Superfoods | 29, 30, 43 | Mixed, but product relevant | **Delivered** — Snacks |
| CHACOO | 30, 43 | Food service | **Removed** — not a packaged-product business |
| PROJECT FROYO SIGNATURE | 29, 30, 32, 35 | Uncertain | **Removed** — not a packaged-product business |
| BOBA BY SUSHI TIGERS | 30 | Food service | **Removed** — not a packaged-product business |
| LOMA frite house | 18, 25, 29, 30 | Uncertain | **Removed** — not a packaged-product business |
| Fatboy's Cocoa | 30, 43 | Mixed, but product relevant | **Delivered** — Several packaged-food categories: coffee, tea and hot drinks, confectionery, chocolate and sweets |
| SUMMER JUICE | 30, 32, 43 | Food service | **Removed** — not a packaged-product business |
| FAÏO | 30, 35, 43 | Food service | **Removed** — not a packaged-product business |
| Jailato | 30, 43 | Food service | **Removed** — not a packaged-product business |
| Flavour Fellas | 8, 16, 21, 25, 29, 30, 35, 41, 43 | Uncertain | **Removed** — not a packaged-product business |
| CLOVER & FLINT | 3, 4, 24, 30 | Non-product | **Removed** — food incidental to a non-food filing |
| RICEGAINS | 29, 30 | Packaged product | **Delivered** — Ambient and shelf-stable prepared foods |
| dialect | 29, 30, 31 | Packaged product | **Delivered** — Snacks |

## Every delivered opportunity

Same definitions as the previous audit. **EXCELLENT** — a genuinely emerging
brand, clearly relevant to LaunchTrace Food suppliers, early enough for
outreach to matter. **GOOD** — commercially plausible; I would be comfortable
putting it in a paid feed. **WEAK** — technically qualifies, but I would rather
not send it. **WRONG** — a false positive. Judged on the underlying evidence,
never on the record's own score.

### 2026-036 — 10 companies

#### ARAW — **EXCELLENT**

- **Company** — ARAW LIMITED (14387387, incorporated 2022-09-29, LONDON); SIC 10520
- **Brand / product** — ARAW; Chilled and frozen packaged food; Nice class 29, 30, 35, 43
- **Commercial mode** — Mixed, but product relevant
- **LaunchTrace Score** — 83 (HIGH)
- **Website** — Confirmed: https://www.arawlondon.com
- **Brand maturity** — emerging
- **Buying-intent categories** — flexible packaging, labels, cartons, contract manufacturing, copacking, distribution, brokerage, fulfilment, marketing
- **Trade mark** — UK00004435180
- **Audit** — A London ice cream manufacturer (SIC 10520), trading since 2021, protecting packaged formats — sandwiches, cakes, lollies, sorbets, non-dairy — alongside its own parlour. Website verified from its own pages. Its category now reads 'Chilled and frozen packaged food' rather than 'bakery', so a supplier reading the feed is pointed at the right conversation.

#### MANTA — **GOOD**

- **Company** — CHOCOLATE HOUSE LTD (15848158, incorporated 2024-07-19, LONDON); SIC 46360, 46370
- **Brand / product** — MANTA; Confectionery, chocolate and sweets; Nice class 29, 30
- **Commercial mode** — Mixed, but product relevant
- **LaunchTrace Score** — 81 (HIGH)
- **Website** — Confirmed: https://www.mantachocolate.com
- **Brand maturity** — emerging
- **Buying-intent categories** — flexible packaging, labels, cartons, contract manufacturing, copacking, distribution, brokerage, fulfilment, marketing
- **Trade mark** — UK00004433393
- **Audit** — A two-year-old London chocolate and nut-spread wholesaler with a focused class 29/30 range, now correctly categorised as confectionery rather than condiments. mantachocolate.com carries the brand, the city and the right products but names no legal entity anywhere on the site, so the tie to Chocolate House Ltd is strong inference rather than proof.

#### Cactus Joe's Kitchen — **GOOD**

- **Company** — NEWCO BRANDS LTD (17296659, incorporated 2026-06-23, ASCOT); SIC 46900
- **Brand / product** — Cactus Joe's Kitchen; Ambient and shelf-stable prepared foods; Nice class 29, 30
- **Commercial mode** — Packaged product
- **LaunchTrace Score** — 79 (MEDIUM)
- **Website** — Not found
- **Brand maturity** — unknown
- **Buying-intent categories** — flexible packaging, labels, cartons, contract manufacturing, copacking, distribution, brokerage, fulfilment
- **Trade mark** — UK00004422947
- **Audit** — Canned meat and pies, a clean single-category filing, from a company six weeks old at filing. Cans, labels and pie cartons are exactly the spend worth reaching early. A generic company name, a non-specialised wholesale SIC and no web footprint are mild flags that this may be a brand-holding vehicle.

#### Fatboy's Cocoa — **WEAK**

- **Company** — FATBOY'S COCOA LTD (17260212, incorporated 2026-06-03, LONDON); SIC 10821, 47810, 56102
- **Brand / product** — Fatboy's Cocoa; Several packaged-food categories: coffee, tea and hot drinks, confectionery, chocolate and sweets; Nice class 30, 43
- **Commercial mode** — Mixed, but product relevant
- **LaunchTrace Score** — 79 (MEDIUM)
- **Website** — Not found
- **Brand maturity** — unknown
- **Buying-intent categories** — flexible packaging, labels, cartons, contract manufacturing, copacking, distribution, fulfilment, marketing
- **Trade mark** — UK00004430919
- **Audit** — The general rule kept this on the strength of a chocolate-manufacturing SIC (10821), and that is defensible. My own reading is not: the goods are five beverages served plus 'serving food and drink in doughnut shops', with no packaged retail format anywhere in the filing, and two of the three registered activities are a market stall and an unlicensed café. The packaged-product spend behind it is speculative.

#### NORI Kitchen — **GOOD**

- **Company** — THE SUSHI FACTORY & BEYOND LTD (16527273, incorporated 2025-06-18, LONDON); SIC 47290
- **Brand / product** — NORI Kitchen; Several packaged-food categories: ambient and shelf-stable prepared foods, snacks; Nice class 29, 30
- **Commercial mode** — Packaged product
- **LaunchTrace Score** — 79 (MEDIUM)
- **Website** — Not found
- **Brand maturity** — unknown
- **Buying-intent categories** — flexible packaging, labels, cartons, contract manufacturing, copacking, distribution, brokerage, fulfilment
- **Trade mark** — UK00004433645
- **Audit** — Chilled sushi, sashimi and ramen from a one-year-old London producer registered for food retail rather than food service — which is the whole reason it survives where a visually identical takeaway filing does not. Chilled prepared food is among the most packaging-intensive categories there is. The closest call in this week's feed.

#### RICEGAINS — **GOOD**

- **Company** — RICE GAINS LTD (17410414, incorporated 2026-08-20, LONDON); SIC 47290, 47910
- **Brand / product** — RICEGAINS; Ambient and shelf-stable prepared foods; Nice class 29, 30
- **Commercial mode** — Packaged product
- **LaunchTrace Score** — 79 (MEDIUM)
- **Website** — Not found
- **Brand maturity** — unknown
- **Buying-intent categories** — flexible packaging, labels, cartons, contract manufacturing, copacking, distribution, brokerage, fulfilment
- **Trade mark** — UK00004433317
- **Audit** — Incorporated the same day it filed, with a range of prepared ready meals now correctly categorised as ambient and shelf-stable prepared foods rather than snacks. Trays, film and sleeves, specified by someone who has chosen nothing yet.

#### HIVE — **EXCELLENT**

- **Company** — HIVE CHOCOLATE LTD (17292222, incorporated 2026-06-21, Y FELINHELI); SIC 10821
- **Brand / product** — HIVE; Confectionery, chocolate and sweets; Nice class 30, 35
- **Also filed by this company** — Hive Chocolate (UK00004405580)
- **Commercial mode** — Packaged product
- **LaunchTrace Score** — 79 (MEDIUM)
- **Website** — Not confirmed
- **Brand maturity** — unknown
- **Buying-intent categories** — flexible packaging, labels, cartons, contract manufacturing, copacking, distribution, brokerage, fulfilment, marketing
- **Trade mark** — UK00004405594
- **Audit** — A chocolate manufacturer (SIC 10821) incorporated three months before filing, in Gwynedd, with a focused chocolate and honey range. New manufacturer, wrappers and bars to specify, nothing bought yet. Two marks correctly consolidated into one prospect, and the unrelated smart-heating company at hive.com stays rejected.

#### Daio — **GOOD**

- **Company** — SHUDHCO. LTD (17047067, incorporated 2026-02-23, ST. ALBANS); SIC 47290
- **Brand / product** — Daio; Chilled and frozen packaged food; Nice class 29
- **Commercial mode** — Packaged product
- **LaunchTrace Score** — 79 (MEDIUM)
- **Website** — Not found
- **Brand maturity** — unknown
- **Buying-intent categories** — flexible packaging, labels, cartons, contract manufacturing, copacking, distribution, brokerage, marketing
- **Trade mark** — UK00004428273
- **Audit** — A dairy and plant-milk range from a company seven months old. Cartons, bottles and labels are the whole cost base of that category. The retail SIC leaves open whether they will manufacture or factor, which is the only reservation.

#### 김 SEAWEED ROASTERY — **EXCELLENT**

- **Company** — SEAWEED ROASTERY LIMITED (17280315, incorporated 2026-06-18, LONDON); SIC 10890, 46390, 47910, 56103
- **Brand / product** — 김 SEAWEED ROASTERY; Snacks; Nice class 29, 30, 35, 43
- **Commercial mode** — Mixed, but product relevant
- **LaunchTrace Score** — 79 (MEDIUM)
- **Website** — Not found
- **Brand maturity** — unknown
- **Buying-intent categories** — flexible packaging, labels, cartons, contract manufacturing, copacking, distribution, brokerage, fulfilment, marketing
- **Trade mark** — UK00004435235
- **Audit** — A seaweed snack producer with a food-manufacturing SIC (10890) and a food wholesale SIC, incorporated two months before filing, with a long and tightly focused range. It carries class 43 for its own counter and survives anyway, which is the point of assessing the business rather than the class list.

#### dialect — **GOOD**

- **Company** — NESH BRANDS LTD (17247785, incorporated 2026-05-28, LONDON); SIC 11070
- **Brand / product** — dialect; Snacks; Nice class 29, 30, 31
- **Commercial mode** — Packaged product
- **LaunchTrace Score** — 79 (MEDIUM)
- **Website** — Not confirmed
- **Brand maturity** — unknown
- **Buying-intent categories** — flexible packaging, labels, cartons, contract manufacturing, copacking, distribution, brokerage, fulfilment, marketing
- **Trade mark** — UK00004434758
- **Audit** — Roasted, salted and spiced nuts from a three-month-old London brand company, now correctly categorised as snacks rather than seasonings. Nut snacks live and die on flexible packaging. The soft-drinks SIC does not match the goods, which is a mild flag on a very new registration.


### 2026-037 — 14 companies

#### kWh Coffee — **EXCELLENT**

- **Company** — MORNING PEOPLE LTD (16660094, incorporated 2025-08-19, YORK); SIC 10832, 56103
- **Brand / product** — kWh Coffee; Coffee, tea and hot drinks; Nice class 30
- **Commercial mode** — Mixed, but product relevant
- **LaunchTrace Score** — 88 (HIGH)
- **Website** — Confirmed: https://kwhcoffee.com
- **Brand maturity** — emerging
- **Buying-intent categories** — flexible packaging, labels, cartons, contract manufacturing, copacking, distribution, fulfilment, marketing
- **Trade mark** — UK00004435554
- **Audit** — The strongest record in either week. An electric-powered speciality coffee roastery in York with a coffee-processing SIC, incorporated a year before filing. Its own site states 'kWh Coffee by Morning People Ltd, Companies House No. 16660094' — the exact company on the record. Coffee bags, valves and labels are an immediate, recurring spend.

#### Sami's Superfoods — **EXCELLENT**

- **Company** — SAMIS SUPERFOODS LTD (13274599, incorporated 2021-03-18, SHEFFIELD); SIC 46170, 46390, 56290
- **Brand / product** — Sami's Superfoods; Snacks; Nice class 29, 30, 43
- **Commercial mode** — Mixed, but product relevant
- **LaunchTrace Score** — 83 (HIGH)
- **Website** — Confirmed: https://samissuperfoods.com
- **Brand maturity** — emerging
- **Buying-intent categories** — flexible packaging, labels, cartons, contract manufacturing, copacking, distribution, brokerage, fulfilment, marketing
- **Trade mark** — UK00004435509
- **Audit** — A Sheffield importer and packer founded in 2021, past 20,000 customers, 100 tonnes of nuts and 200,000 units of its own chocolate line. Small enough to still be choosing suppliers, large enough to be worth the call. Carries class 43 and survives on its packing and wholesale evidence; now correctly categorised as snacks.

#### Rose & Grind — **GOOD**

- **Company** — ROSE & GRIND LTD (17410336, incorporated 2026-08-20, DARLINGTON); SIC 46370, 47910
- **Brand / product** — Rose & Grind; Coffee, tea and hot drinks; Nice class 21, 25, 30
- **Commercial mode** — Packaged product
- **LaunchTrace Score** — 79 (MEDIUM)
- **Website** — Not found
- **Brand maturity** — unknown
- **Buying-intent categories** — flexible packaging, labels, cartons, contract manufacturing, copacking, distribution, fulfilment, marketing
- **Trade mark** — UK00004433315
- **Audit** — Incorporated on the day it filed, with a coffee wholesale SIC and a complete coffee range including beans, ground, bags, pods and capsules. Two thirds of the filing is drinkware and clothing, and it survives because Companies House shows the company trades coffee — which is the distinction between a coffee brand protecting merchandise and a merchandise brand mentioning coffee.

#### Cray Sauces — **EXCELLENT**

- **Company** — CRAY SAUCES LTD (17383940, incorporated 2026-08-05, LONDON); SIC 47290
- **Brand / product** — Cray Sauces; Sauces, condiments, spreads and seasonings; Nice class 30
- **Commercial mode** — Packaged product
- **LaunchTrace Score** — 79 (MEDIUM)
- **Website** — Not found
- **Brand maturity** — unknown
- **Buying-intent categories** — labels, cartons, contract manufacturing, copacking, distribution, brokerage, fulfilment, marketing
- **Trade mark** — UK00004435936
- **Audit** — A sauce brand and nothing else: the company was formed three weeks before filing and the goods are sauces, condiments and ketchup end to end. Bottles, caps and labels, specified by someone who has not chosen a supplier yet.

#### BULLITT — **EXCELLENT**

- **Company** — COLDHARBOUR FOODS LIMITED (17035779, incorporated 2026-02-16, MARLOW); SIC 46360
- **Brand / product** — BULLITT; Cereal, protein and energy bars; Nice class 29
- **Commercial mode** — Packaged product
- **LaunchTrace Score** — 79 (MEDIUM)
- **Website** — Not found
- **Brand maturity** — unknown
- **Buying-intent categories** — flexible packaging, labels, cartons, contract manufacturing, copacking, distribution, brokerage, fulfilment, marketing
- **Trade mark** — UK00004437089
- **Audit** — The goods text reads, in full, 'Energy Bar.' A six-month-old company with a confectionery wholesale SIC and one product. Bar wrappers are a pure flexible-packaging sale and the timing could not be better.

#### BEAKY — **EXCELLENT**

- **Company** — BEAKY FOODS LTD (17308892, incorporated 2026-06-30, LONDON); SIC 46380, 47910
- **Brand / product** — BEAKY; Sauces, condiments, spreads and seasonings; Nice class 30
- **Commercial mode** — Packaged product
- **LaunchTrace Score** — 79 (MEDIUM)
- **Website** — Not found
- **Brand maturity** — unknown
- **Buying-intent categories** — labels, cartons, contract manufacturing, copacking, distribution, brokerage, fulfilment, marketing
- **Trade mark** — UK00004436958
- **Audit** — A focused seasonings and marinades range from a two-month-old London company with a food wholesale SIC. Jars, sachets, shakers and labels.

#### GIGGLECHOC — **GOOD**

- **Company** — JOKE IT LTD (16951622, incorporated 2026-01-08, PORTSMOUTH); SIC 46360
- **Brand / product** — GIGGLECHOC; Confectionery, chocolate and sweets; Nice class 16, 25, 28, 30, 35
- **Commercial mode** — Packaged product
- **LaunchTrace Score** — 79 (MEDIUM)
- **Website** — Not found
- **Brand maturity** — unknown
- **Buying-intent categories** — flexible packaging, labels, cartons, contract manufacturing, copacking, distribution, brokerage, fulfilment, marketing
- **Trade mark** — UK00004437597
- **Audit** — Chocolate confectionery with a confectionery wholesale SIC, sold alongside collectible toys and trading cards. The confectionery line is genuine and needs wrappers and boxes, and the food SIC is why the merchandise classes do not sink it; the toy weighting still makes the business harder to read than the rest of this feed.

#### چاى مامه CHAI MAMA — **GOOD**

- **Company** — FROND FOODS LTD (16798042, incorporated 2025-10-20, LIVERPOOL); SIC 46390, 47250
- **Brand / product** — چاى مامه CHAI MAMA; Coffee, tea and hot drinks; Nice class 30
- **Commercial mode** — Packaged product
- **LaunchTrace Score** — 79 (MEDIUM)
- **Website** — Not found
- **Brand maturity** — unknown
- **Buying-intent categories** — flexible packaging, labels, cartons, contract manufacturing, copacking, distribution, fulfilment, marketing
- **Trade mark** — UK00004435320
- **Audit** — A tea brand and nothing else — black tea, chai, leaves, bags — from a Liverpool food wholesaler incorporated ten months before filing. Cartons and tea bags. The brand renders with Arabic script in the feed, which needs handling before a customer sees it.

#### TOSS — **EXCELLENT**

- **Company** — TOSS WORLD LIMITED (17030618, incorporated 2026-02-12, HASLEMERE); SIC 10840, 46390, 47910
- **Brand / product** — TOSS; Sauces, condiments, spreads and seasonings; Nice class 18, 21, 25, 30
- **Also filed by this company** — your everyday squeeze (UK00004437945)
- **Commercial mode** — Packaged product
- **LaunchTrace Score** — 79 (MEDIUM)
- **Website** — Not found
- **Brand maturity** — unknown
- **Buying-intent categories** — labels, cartons, contract manufacturing, copacking, distribution, brokerage, fulfilment, marketing
- **Trade mark** — UK00004437777
- **Audit** — Confirmed launching: the trade press covered TOSS entering the UK salad dressing aisle with four vegan, gluten-free dressings. The company manufactures condiments and seasonings (SIC 10840) and was formed six months before filing. Two marks consolidated into one prospect, and its tote bags and lunchboxes correctly count as brand protection rather than evidence against it.

#### PITHY — **EXCELLENT**

- **Company** — PITHY LTD (17422672, incorporated 2026-08-26, LONDON); SIC 10320, 10390, 11060, 46341
- **Brand / product** — PITHY; Sauces, condiments, spreads and seasonings; Nice class 29, 30, 32
- **Commercial mode** — Packaged product
- **LaunchTrace Score** — 79 (MEDIUM)
- **Website** — Not found
- **Brand maturity** — unknown
- **Buying-intent categories** — labels, cartons, contract manufacturing, copacking, distribution, brokerage, fulfilment, marketing
- **Trade mark** — UK00004437400
- **Audit** — Incorporated two days before filing, with three food-manufacturing SICs and a culinary citrus and flavourings range. There is no earlier point at which to reach a brand.

#### LEAH'S — **GOOD**

- **Company** — EARTH BRANDS LTD (17243715, incorporated 2026-05-26, MANCHESTER); SIC 64209
- **Brand / product** — LEAH'S; Chilled and frozen packaged food; Nice class 29, 30
- **Commercial mode** — Mixed, but product relevant
- **LaunchTrace Score** — 79 (MEDIUM)
- **Website** — Not confirmed
- **Brand maturity** — unknown
- **Buying-intent categories** — flexible packaging, labels, cartons, contract manufacturing, copacking, distribution, marketing
- **Trade mark** — UK00004437844
- **Audit** — A focused chilled dairy launch — labneh, yoghurt, kefir, yoghurt drinks — from a company three months old. Pots, lids, sleeves and labels. The holding-company SIC means there is no trading history behind it yet, which is why this is good rather than excellent.

#### GOLDEN ROOTS — **GOOD**

- **Company** — HEAVENLY FOODS & BEVERAGES LTD (14547529, incorporated 2022-12-19, LEICESTER); SIC 46310, 46380, 47250, 47290
- **Brand / product** — GOLDEN ROOTS; Snacks; Nice class 29
- **Also filed by this company** — GOLDEN ROOTS (UK00004435534)
- **Commercial mode** — Mixed, but product relevant
- **LaunchTrace Score** — 77 (MEDIUM)
- **Website** — Not found
- **Brand maturity** — unknown
- **Buying-intent categories** — flexible packaging, labels, cartons, contract manufacturing, copacking, distribution, brokerage, fulfilment, marketing
- **Trade mark** — UK00004435549
- **Audit** — A Leicester food wholesaler of four years launching a root-vegetable snack range. Flexible packaging, an established route to market, and two marks correctly consolidated into one prospect.

#### AE ARAVALLI ESSENCE — **GOOD**

- **Company** — ARRAVALI ESSENCE LTD (16408046, incorporated 2025-04-25, LEICESTER); SIC 46190
- **Brand / product** — AE ARAVALLI ESSENCE; Sauces, condiments, spreads and seasonings; Nice class 30
- **Commercial mode** — Packaged product
- **LaunchTrace Score** — 69 (MEDIUM)
- **Website** — Not confirmed
- **Brand maturity** — unknown
- **Buying-intent categories** — flexible packaging, labels, cartons, contract manufacturing, copacking, distribution, marketing
- **Trade mark** — UK00004436191
- **Audit** — A real trading Leicester spice business with an online shop, an eBay store and a 5-star hygiene rating from November 2025. Pouches, jars and labels. Its website is still withheld because the registered name spells the brand differently from the mark — the conservative rule working as designed, at the cost of a useful field.

#### NAJORA — **GOOD**

- **Company** — RAADL GROUP LTD (13123687, incorporated 2021-01-11, PETERBOROUGH); SIC 10840, 62012, 78200, 78300
- **Brand / product** — NAJORA; Sauces, condiments, spreads and seasonings; Nice class 30
- **Commercial mode** — Packaged product
- **LaunchTrace Score** — 64 (MEDIUM)
- **Website** — Not found
- **Brand maturity** — unknown
- **Buying-intent categories** — labels, cartons, contract manufacturing, copacking, distribution, brokerage, fulfilment, marketing
- **Trade mark** — UK00004425387
- **Audit** — A clean class 30 condiments filing backed by SIC 10840, manufacture of condiments and seasonings. The company also lists software and recruitment activities, which makes the group harder to read, but the food line itself is specific and real.


## Precision

    useful precision = (EXCELLENT + GOOD) / all delivered opportunities

| Week | Delivered | EXCELLENT | GOOD | WEAK | WRONG | Useful precision |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| 2026-036 | 10 | 3 (30.0%) | 6 (60.0%) | 1 (10.0%) | 0 (0.0%) | **90.0%** |
| 2026-037 | 14 | 7 (50.0%) | 7 (50.0%) | 0 (0.0%) | 0 (0.0%) | **100.0%** |
| **Combined** | **24** | **10 (41.7%)** | **13 (54.2%)** | **1 (4.2%)** | **0 (0.0%)** | **95.8%** |

Measured once, after the corrections, on the same recorded evidence. Nothing
was changed after these numbers were known. The previous audit measured 67.6%
across 34 delivered records; this measures 95.8% across 24.

## Honest limits on this number

**One sample, two weeks, twenty-four records.** 95.8% on 24 records has a wide
confidence interval. A single bad week would move it several points. It says
the generalised rules produce a customer-worthy feed on this evidence; it does
not say the feed will hold at 95% indefinitely.

**The one WEAK record is the rule disagreeing with me, not a bug.** Fatboy's
Cocoa survives on a chocolate-manufacturing SIC, and that is a defensible read
of the register. My own reading of the filing — five beverages served plus
doughnut-shop services, no packaged format anywhere — says the packaging spend
behind it is speculative. The rule is not wrong to keep it and I am not wrong
to doubt it; that is what WEAK means. It has not been tuned away, because
tuning a rule until it agrees with one judgement on one record is how a sample
gets overfitted.

**Volume fell by 29%, and that is the point.** Ten records left the feed: five
cafés, takeaways, dessert counters and juice bars; two IP or holding vehicles
with no trading activity; one scattergun grocery range from a spirits
wholesaler; one food-media merchandise brand; one home-fragrance brand with a
chocolate line. A supplier paying £79 a month for fourteen companies they can
call is better served than one paying for twenty of which six waste the call.

**What withholding costs.** Twenty of the twenty-four delivered records ship
with no website, because the conservative verification rule publishes a domain
only when evidence ties it to that company. Two records that are probably
right — a spice business whose registered name spells the brand differently,
and a chilled dairy launch — have their sites withheld on that rule. That is
the intended trade-off and it is still a cost.

**What this audit does not establish.** Whether a supplier will pay. Precision
measures whether the records are worth reading; only a customer test measures
whether the feed is worth buying. Nothing here has been sent to anyone.
