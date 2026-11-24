# Portal and billing

What clients see in their portal, in which language, and how proposals, contracts,
invoices and payments work.

## The client portal

A client signs in with a magic link (BrightBean's, valid 30 days). Each section appears only
when it has something to show and the person's portal role allows it:

| Section | Shows | Portal permission |
|---|---|---|
| Home | What needs their action (notices), **Coming up** (shoots, scheduled posts, invoices due), counts | any |
| Calendar | Approved, scheduled and recently published posts, by day, in the client's time | `portal_view_content` |
| Approvals | Posts waiting for them; review a week or a month at once; pinned comments | `portal_approve` |
| Shoots | Upcoming shoots, the plan for the day, a preparation checklist they can tick | `portal_view_content` (ticking: `portal_approve`) |
| Brand Hub, Projects, Getting started | From Phase 3 | `portal_view_content` |
| Billing | Proposals to accept, contracts to sign, invoices to pay, receipts, credit notes | `portal_view_billing` |
| Messages | Conversations with the studio, each about one item; billing topics need `portal_view_billing` | `portal_view_content` |

Role presets (Phase 2): **Client admin** sees everything; **Client reviewer** approves content
but sees no billing; **Client billing contact** sees billing only. Drafts, internal notes,
internal comments and other clients' records never appear; the tests check each section.

## English and Spanish

Each person chooses their language (English or Español) from the account menu in the studio or
the switch in the portal header. Until a client user chooses, the portal uses the language the
studio set on the client record (**Portal language**); signed-out pages follow the browser.
Emails (invitations, reminders, invoices, proposals, contracts, messages) go out in the
recipient's language. In Spanish, dates are written day first and numbers use a decimal point
(1,800.00), as in Belize and Guatemala.

Translated so far: the whole portal and every email to clients. The studio's own screens are in
English. To change a translation, edit `locale/es/LC_MESSAGES/django.po` and run
`python manage.py compilemessages` (the Docker build does this). After adding strings, run
`python manage.py makemessages -l es` and translate the new entries.

## Proposals and contracts

1. **Billing → Catalog** holds the services you sell and their usual prices.
2. From a client's **Billing** tab, **New proposal**: add services (monthly or one-off), the
   deposit percentage, the contract to send on acceptance, and whether to start onboarding.
   **Send to the client** numbers it (P-0001) and emails their billing people.
3. The client reads it in the portal and accepts by typing their name, or declines with a note.
   Accepting creates, in one step: the contract (sent for signature), the deposit invoice
   (sent), the service lines and the monthly retainer, and onboarding for a new client (or the
   projects, for a client already onboarded). The onboarding steps for proposal, contract and
   deposit tick themselves as each happens.
4. The client signs the contract by typing their name and agreeing. We keep their name, email,
   the time, their IP address and a SHA-256 fingerprint of the exact text. Templates
   (**Billing → Settings**) are a starting point: have them reviewed by a lawyer, and confirm
   that typed signatures suit your contracts in Belize.

## Invoices

- **Kinds:** deposit, milestone, one-time, monthly retainer, extras; and credit notes against a
  sent invoice.
- **Amounts:** line discounts, a discount on the whole invoice (spread over lines before tax),
  tax per line (the default comes from settings). Rounded half-up to the cent.
- **Currencies:** BZD by default (`BILLING_CURRENCIES` adds others). Another currency needs the
  exchange rate on the day it is issued; the rate is stored on the invoice and the finance view
  converts with it.
- **Statuses:** Draft, Sent, Viewed (the client opened it), Partly paid, Paid, Overdue, Void.
  Only drafts can be edited or deleted; a sent invoice is corrected with a credit note or
  voided (only if nothing was paid).
- **Numbers:** INV-0001, CN-0001, P-0001 by default, sequential per studio (prefixes in settings).
- **Retainers:** on the 1st of each month a draft is prepared for each active retainer; the
  studio checks and sends it.
- **Reminders:** sent to the client's billing people on the schedule in settings (by default 3
  days before, on the due date, and 7 and 14 days after), once each.
- **Share link:** billing contacts without a portal login get a read-only link to the invoice
  and its PDF.
- **Tax documents:** an app-made invoice is never presented as a certified tax document; its PDF
  says so. If the studio must issue certified documents, connect an authorised provider
  (`BILLING_TAX_PROVIDER`) and the official number is stored on the invoice.

## Payments

- **Bank transfer (always):** the portal shows the bank details and the invoice number to quote.
  The client uploads the receipt (photo, screenshot or PDF; scanned for malware) with the
  amount. The studio sees **Payments to confirm** on Today and in Billing, then confirms it or
  marks it not received with a reason the client sees.
- **Recorded by the studio:** cash, cheque, transfer or other, with an optional receipt.
- **Online card payments:** behind `BILLING_PAYMENT_PROVIDER`. Only the `mock` provider exists so
  far: it shows a test checkout page that says no money moves, and records a test payment once
  per reference.

## Who sees what in the studio

Seeing a client's billing needs `view_billing`; changing it needs `manage_billing` (owners by
default; managers only if granted). The finance view needs the studio-wide `view_finance`; billing
settings and the catalog need `manage_billing_settings`; templates need `manage_templates`. Every
proposal, contract, invoice and payment change is in the audit log.

## Messages

Clients write from the portal, from any invoice, proposal, contract or shoot ("Ask about
this…"), or a general question. The studio replies from the client's **Messages** tab. Clients see
everything in a conversation; team notes belong in internal comments. Staff get a notification
for client messages; clients get an email for studio replies.

## Trying it

```bash
python manage.py seed_demo --reset
```

- As the demo owner: **Billing** (finance view), Casa Maya → Billing (a receipt to confirm, an
  overdue invoice with a credit note, a draft retainer invoice), Luma → a proposal waiting.
- Portal links are printed by the seed command: Ana (Casa Maya, portal in Spanish), Lia (Luma,
  English: accept the proposal), Marco (Vista Realty, Spanish: sign the contract, pay the deposit).

## Settings

| Variable | Values | Default |
|---|---|---|
| `BILLING_CURRENCIES` | e.g. `BZD,USD` (first is the default) | `BZD` |
| `BILLING_PAYMENT_PROVIDER` | `none`, `mock` | `none` |
| `BILLING_SIGNATURE_PROVIDER` | `builtin`, `mock` | `builtin` |
| `BILLING_TAX_PROVIDER` | `none`, `mock` | `none` |

## Not tested against real services

- **Email delivery** through a real provider (tests use Django's mail backend).
- **Online card payments:** no real provider is connected; only the mock exists.
- **E-signature services:** only the built-in typed signature and a mock.
- **Certified tax documents (Belize GST invoices, Guatemala FEL):** no provider; ask your
  accountant what the studio must issue.
- **ClamAV** scanning of receipts (tests use the mock scanner).
