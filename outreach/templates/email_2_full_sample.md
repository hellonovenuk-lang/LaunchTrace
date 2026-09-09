# Email 2 — the full sample

Only send this once they have replied asking to see more.

Generate it, with the sample built and the count filled in:

    python -m src.admin prepare-sample --prospect-id P001

Attach the CSV that command writes. The HTML report is there if you would rather
send something they can read without opening a spreadsheet.

---

**Subject:** This week's full LaunchTrace sample

Hi {{GREETING}},

Attached is the full sample — {{SAMPLE_COUNT}} emerging UK food brands from this
week's trade mark journal, each checked against Companies House and scored on
how early-stage they look.

The idea is simple: every Friday you'd get the strongest new UK food-brand
signals we've detected, rather than having someone manually monitor trade mark
filings and research each company.

A note on the columns — the relevance bands (packaging, contract manufacturing,
distribution and so on) indicate what a brand at that stage typically needs.
They're not a claim that the company is currently buying. The reasons column
shows exactly why each brand scored the way it did, so you can disagree with any
of it.

I'm opening the first ten subscriptions at £79/month while I validate it with
suppliers. No contract — cancel whenever it stops being useful.

If you'd like to keep receiving it, I can send you the subscription link.

Best,
{{OPERATOR_NAME}}
{{OPERATOR_COMPANY}} · [PHONE] · [WEBSITE]

*Sources: UK Intellectual Property Office and Companies House, used under the
Open Government Licence v3.0. Reply "no thanks" and I won't contact you again.*

---

## Before you send

- [ ] The CSV attached is a real recent week, not the fixture output
- [ ] The count in the email matches the row count in the CSV
- [ ] You have read the HTML report as the customer would

## After you send

    python -m src.admin prospects set-status --prospect-id P001 --status SAMPLE_SENT
