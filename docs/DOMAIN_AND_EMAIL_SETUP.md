# Domain and email setup

**A checklist for the owner. No technical background assumed.**

Nothing in this repository has bought a domain or created an account. This
document is what to do when you decide to, in the order to do it, with the
jargon explained where it appears.

Work through it in one sitting of about ninety minutes, most of which is
waiting.

---

## Words you will meet

| Word | What it actually means |
| --- | --- |
| **Domain** | Your web address, e.g. `launchtrace.co.uk`. You rent it, yearly. |
| **Registrar** | The shop you rent the domain from. |
| **DNS** | The address book of the internet. It says "`launchtrace.co.uk` lives at this server" and "email claiming to be from this domain is genuine". |
| **DNS record** | One line in that address book. You add them in your registrar's control panel, in a table with columns like Type, Name and Value. |
| **A record** | Points a name at a server's numeric address. |
| **CNAME record** | Points one name at another name. |
| **TXT record** | Holds a piece of text. Used to prove you control the domain, and for the three email records below. |
| **Subdomain** | Something in front of your domain, e.g. `mail.launchtrace.co.uk`. Free — you create as many as you like. |
| **Propagation** | The wait after adding a record, while the rest of the internet notices. Usually minutes; occasionally a few hours. |

**The three email records, in plain English.** Spam filters need proof that
email claiming to come from you really does. These three records are that proof
and, without them, a good proportion of your email lands in junk.

* **SPF** — a list of who is allowed to send email as you.
* **DKIM** — a cryptographic signature on each message, so it cannot be forged.
* **DMARC** — what a receiving server should do with a message that fails the
  other two, and where to send reports.

You will not write these by hand. Resend generates them; you copy and paste.

---

## Step 1 — Choose and buy a domain

**Pick:** something short you can say on the phone. `.co.uk` is right for a UK
B2B product. Roughly £10–15 a year.

**Buy from:** any mainstream registrar. Cloudflare, Namecheap and Gandi all
have clear control panels. Avoid a registrar that charges extra to edit DNS
records — you will be editing them.

**Do not** buy a hosting or email bundle. You need the domain only.

**Turn on WHOIS privacy** if it is offered free. It keeps your home address out
of a public database.

☐ Domain bought
☐ Login details in your password manager

---

## Step 2 — Decide your addresses

Decide these now, because they go in several places:

| Purpose | Suggestion | Where it is used |
| --- | --- | --- |
| The weekly feed comes from | `feed@launchtrace.co.uk` | `EMAIL_FROM` |
| Replies reach you at | `hello@launchtrace.co.uk` | `EMAIL_REPLY_TO` |
| Privacy and data requests | `privacy@launchtrace.co.uk` | The legal documents |
| Alerts when a run fails | your own address | `ADMIN_EMAIL` |

**Sending subdomain — recommended.** Send the feed from a subdomain such as
`mail.launchtrace.co.uk` rather than the domain itself.

*Why:* sending reputation is tracked per domain. If bulk sending ever goes
wrong, it damages the subdomain's reputation and your ordinary
`hello@launchtrace.co.uk` correspondence keeps working. It costs nothing and
cannot be retrofitted easily later.

Doing this means the From address becomes `feed@mail.launchtrace.co.uk`.

☐ Addresses decided

---

## Step 3 — Somewhere to read replies

The domain does not come with a mailbox. Two options:

**Forwarding (free, start here).** Most registrars forward
`hello@launchtrace.co.uk` to your existing personal address. Replies then come
*from* your personal address, which is slightly untidy but perfectly workable
for your first ten customers.

**A real mailbox (about £5/month).** Google Workspace or Microsoft 365 gives
you a proper mailbox that sends as well as receives.

Forwarding is the right answer until you have paying customers.

☐ Replies reach a mailbox you read
☐ Tested: sent yourself a message and it arrived

---

## Step 4 — Point the domain at the website

Only once the site is deployed (`fly deploy`, or your host's equivalent). Your
host tells you exactly which records to add; typically:

| Type | Name | Value |
| --- | --- | --- |
| A | `@` | the address your host gives you |
| CNAME | `www` | your host's address |

`@` means "the domain itself".

Then set `SITE_URL=https://launchtrace.co.uk` in `.env` and as a GitHub
variable.

☐ `https://your-domain/healthz` shows `{"status":"ok"}`

---

## Step 5 — Resend, and the three email records

1. Create a free account at <https://resend.com/>. Free tier: 3,000 emails a
   month, far more than you need.
2. **Domains → Add Domain.** Enter `mail.launchtrace.co.uk` if you chose a
   sending subdomain, otherwise `launchtrace.co.uk`.
3. Resend shows a table of records to add. Copy each one into your registrar's
   DNS panel exactly — including any trailing dot, if shown.

You will get roughly:

| Type | Name | Purpose |
| --- | --- | --- |
| TXT | `send` or similar | SPF — who may send as you |
| TXT | a long random name | DKIM — the signing key |
| MX | `send` | so bounces come back |

4. Click **Verify** in Resend. If it fails, wait ten minutes and try again —
   this is propagation, not a mistake.

5. **Add DMARC yourself.** Resend may not prompt for it. Add:

| Type | Name | Value |
| --- | --- | --- |
| TXT | `_dmarc` | `v=DMARC1; p=none; rua=mailto:hello@launchtrace.co.uk` |

   `p=none` means "monitor only, do not reject anything". That is the correct
   place to start: it gathers reports without any risk of your own email being
   blocked. Tighten it to `p=quarantine` after a couple of months of clean
   reports, and only then.

6. **API Keys → Create API Key**, with sending permission. Copy it into `.env`
   as `RESEND_API_KEY=`. You will not be shown it again.

☐ Domain verified in Resend
☐ SPF, DKIM and DMARC all present
☐ `RESEND_API_KEY` in `.env`
☐ Tested: approved a run and sent it to yourself first

**Test before you send to a customer.** Send one to yourself, and check it
landed in the inbox rather than junk. If it went to junk, the three records are
not right yet — fix that before anyone else sees a LaunchTrace email.

---

## Step 6 — Everything else that needs the domain

Once the domain works, update these. Each one is a real reference to it:

* `SITE_URL` in `.env` and in GitHub → Settings → Secrets and variables →
  Actions → Variables.
* `EMAIL_FROM` and `EMAIL_REPLY_TO`.
* The `[BRACKET]` placeholders in `docs/PRIVACY.md` and `docs/TERMS.md` — they
  contain a contact address you must fill in.
* Your Stripe webhook endpoint: `https://your-domain/billing/webhook`.
* The footer of the outreach templates in `outreach/templates/` — `[WEBSITE]`
  and `[PHONE]`.

☐ All of the above updated

---

## Step 7 — Before you send outreach from it

A brand-new domain has no sending reputation. Sending fifty cold emails on day
one is the fastest way to get filtered permanently.

* **Send your outreach from your own established mailbox**, not through Resend.
  This is what the playbook already tells you to do, and this is why. Resend is
  for the product — the weekly feed and transactional messages to people who
  asked for them.
* Give the domain two or three weeks of ordinary email before any volume.
* Never buy a list. Never import addresses you did not verify yourself.

---

## Quick reference

| Setting | Value |
| --- | --- |
| `SITE_URL` | `https://launchtrace.co.uk` |
| `EMAIL_FROM` | `LaunchTrace <feed@mail.launchtrace.co.uk>` |
| `EMAIL_REPLY_TO` | `hello@launchtrace.co.uk` |
| `ADMIN_EMAIL` | your own address |
| Stripe webhook | `https://launchtrace.co.uk/billing/webhook` |
| Health check | `https://launchtrace.co.uk/healthz` |

**Check it all worked:**

```bash
python -m src.pipeline check-config
```

Every line should say what you expect. If one does not, that line names the
setting to fix.
