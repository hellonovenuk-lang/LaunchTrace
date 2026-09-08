# Privacy notice

**Operational draft — prepared during the build, not reviewed by a lawyer.
Fill in the bracketed details and take advice before publishing it as your
live notice.**

Last updated: [DATE]. Controller: [YOUR COMPANY NAME], [REGISTERED ADDRESS],
company number [NUMBER]. Contact: [PRIVACY EMAIL].

## In short

LaunchTrace is a business-to-business sales-intelligence service. Almost
everything we process is company information published by the UK government.
We hold personal data in only two narrow places: the business contact details
of people at subscribing and prospective supplier companies, and — unavoidably
— the names of trade mark applicants who are individuals rather than companies.

## 1. The data in the product feed

**What.** Trade mark records (application number, mark, dates, applicant name,
classes, goods text) from the UK Intellectual Property Office. Company records
(name, number, status, incorporation date, SIC codes, registered-office
town/county) from Companies House. Public web evidence about a brand (website
URL, contact page URL, whether it appears at retailers), stored as links and
short snippets.

**Personal data within it.** Most trade mark applicants are companies. Some are
sole traders or individuals, whose names appear on the public register. We
minimise this deliberately:

- Natural-person applicants are **downranked** and are not the product's focus
- We never collect officer records, persons with significant control, dates of
  birth, or **directors' home addresses**
- We collect no personal phone numbers and no individuals' email addresses from
  the register

**Lawful basis.** Legitimate interests (UK GDPR Article 6(1)(f)) — providing a
business information service built from official public registers. See
`LEGITIMATE_INTERESTS_ASSESSMENT.md` for the balancing test.

**Source.** UK IPO and Companies House, both published under the Open
Government Licence v3.0. See `ATTRIBUTION.md`.

## 2. If you are a subscriber

**What we hold.** Your company name, the work email addresses that receive the
feed, an optional contact name, your supplier type, and your subscription
status. Payment is processed by Stripe; we store only the Stripe customer and
subscription identifiers, never card details.

**Why.** To perform our contract with you (Article 6(1)(b)) and to keep proper
business records (Article 6(1)(c)).

**How long.** For the life of the subscription and for [6] years afterwards for
accounting purposes.

## 3. If you asked for a sample, or we contacted you

**What we hold.** Your work email address, company name, optional contact name,
supplier type, and a salted hash of the IP address the request came from (used
only to limit abuse of the form — we do not store the address itself).

**Why.** Legitimate interests (Article 6(1)(f)) — responding to a business
enquiry, and B2B marketing of a directly relevant service to corporate
contacts. Under PECR, direct marketing email to corporate subscribers
(companies and LLPs) does not require prior consent, but you may object at any
time and we will stop.

**How long.** [24] months from the last meaningful interaction, then deleted.
Opt-out records are kept indefinitely — that is the only way to guarantee we do
not contact you again.

**How to stop it.** Every message carries an opt-out link, or email
[PRIVACY EMAIL]. We act on opt-outs immediately and never re-add a suppressed
address.

## 4. Who we share data with

Only the processors that run the service:

| Processor | Purpose | Where |
| --- | --- | --- |
| [Supabase / your database host] | Database hosting | [region] |
| Resend | Sending email | EU/US |
| Stripe | Payments and subscriptions | EU/US |
| [Your search API provider] | Public web lookups | [region] |
| [Your LLM provider] | Classifying trade mark filings | [region] |
| [Your website host] | Serving the website | [region] |

We do not sell personal data. We do not share it for anyone else's marketing.
Where a processor is outside the UK we rely on the UK International Data
Transfer Addendum or an adequacy decision.

**Note on the LLM.** The classifier is sent the trade mark filing text only
(mark, applicant name, classes, goods description) — all of it already public
on the IPO register. It is never sent subscriber or prospect contact details.

## 5. Your rights

You can ask us to give you a copy of your data, correct it, delete it, restrict
or object to our use of it, or provide it in a portable form. Contact
[PRIVACY EMAIL] and we will respond within one month.

If a company or individual named in the LaunchTrace feed asks us to remove
them, we add them to our suppression list and they stop appearing in future
feeds. We cannot alter the public register itself — that is a matter for the
IPO or Companies House.

You can complain to the Information Commissioner's Office: <https://ico.org.uk/>,
0303 123 1113.

## 6. Cookies

The LaunchTrace website sets no tracking or advertising cookies and runs no
third-party analytics.

## 7. Security

Credentials are held as environment variables and never committed to source
control. Database access uses least-privilege credentials. Stripe webhooks are
signature-verified. The operator view is protected by a token. Form input is
validated and sanitised, and database access is parameterised.

## 8. Changes

We will update this notice as the service develops and change the date at the
top.
