# Monetization & billing

Two revenue streams, both driven by config so you can dial fees to `0` and run the
whole platform free during a pilot.

- **Patients** — a per-booking fee (paid through an online gateway *before* the
  slot confirms) **or** a monthly premium subscription that waives booking fees.
- **Hospitals** — a monthly subscription. A lapsed hospital's doctors are hidden
  from patients ("block hospitals only" — patients are never blocked).

Everything patient-facing that involves money (pay URLs, upgrade cards) is
**deterministic UI chrome** streamed to the client — it never passes through the
LLM, so a payment link can't be hallucinated.

---

## Patient plans

| Tier | How you get it | Booking fee | AI voice (portal + phone) | AI (chat/voice) bookings | History | SMS reminders |
|---|---|---|---|---|---|---|
| **Trial** | Automatic, 30 days from signup | Waived | Yes | Unlimited | Full | Yes |
| **Premium** | `POST /patient/subscription/checkout` (৳99/mo) | Waived | Yes | Unlimited | Full | Yes |
| **Free** | After the trial lapses, unless subscribed | **Charged** | No (portal `402`; phone declined) | Capped/month (`FREE_AGENT_BOOKINGS_PER_MONTH`) | Last `FREE_HISTORY_LIMIT` | No |

- **Calling the platform phone number** is the same premium perk over PSTN:
  the caller is matched by caller-ID against a phone number verified **once**
  via SMS OTP (`POST /patient/phone/verify/{start,confirm}`, card on
  `/portal/account`). One verified number = one account (unique index — blocks
  re-verifying the same SIM on fresh accounts to farm trials). Non-premium
  callers hear an LLM-composed decline and get an SMS upgrade link. See
  [TELEPHONY.md](./TELEPHONY.md#who-can-call-the-premium-gate).

- Tier is **derived at read time** from `patient_accounts.premium_until` /
  `trial_ends_at` (`tools.database.patient_tier`): `premium > trial > free`.
- The free cap counts **agent (chat/voice) bookings per calendar month**
  (`patient_usage`). A capped patient can still book **directly** from the portal
  UI (which charges the per-booking fee). Anonymous callers on a hospital's
  **dedicated** number (no account) are never capped and always get reminders —
  the platform number itself admits no anonymous callers while
  `VOICE_PREMIUM_GATE` is on.
- Subscription periods are **prepaid and stack** (`GREATEST(now, premium_until) +
  30d`). BD has no card auto-debit, so renewal is a manual re-purchase.

## Booking-fee flow

1. `resolve_booking_fee(clinic_id, account_id)` → `0` for telephony/premium/trial,
   else the hospital's `booking_fee` (or `BOOKING_FEE_DEFAULT`).
2. Fee > 0 ⇒ the slot is **held** (`appointments.status = 'pending_payment'`,
   `payment_expires_at = now + PAYMENT_TTL_MINUTES`) and a `payments` row is created;
   the provider is asked to `initiate()`.
3. The patient pays; the gateway calls the **IPN** (`/payments/ipn/{provider}`),
   which re-validates server-side and calls `confirm_paid_booking` — idempotent
   (double-IPN safe), with **resurrection** (a late payment after the TTL swept the
   hold to `cancelled` is restored if the slot is still free) and a **refund-needed**
   flag if the slot was taken meanwhile.
4. Only on confirmation does the SMS + doctor notification + memory write happen.

A background sweep (60s) cancels expired holds and frees the slot.

## Hospital billing

- Self-signup: `POST /hospitals/signup` (public, no platform key) creates the
  hospital + clinic + admin and seeds a **free first month**
  (`start_hospital_free_trial`).
- The hourly sweep (`sweep_hospital_billing`, piggybacks the reminder loop) advances
  **one step per run**: `active → past_due` when the period ends, then
  `past_due → suspended` after `HOSPITAL_BILLING_GRACE_DAYS`. The state mirrors onto
  `hospitals.billing_status`, the single predicate patient search/browse/booking
  filter on (`h.status = 'active' AND h.billing_status <> 'suspended'`).
- Marking a subscription paid advances the period a month and reactivates the
  hospital.

## Hospital credit wallet (pass-through usage metering)

**OFF by default** (`CREDITS_ENABLED=false`) — when disabled nothing meters, so
enabling it is a deliberate rollout step. On top of the flat monthly subscription,
a hospital keeps a **prepaid credit wallet** that is drawn down per billable
event. Every meter is **fail-open**: a metering error never blocks the booking,
SMS, or call.

- **What draws credits** (platform-wide costs, `CREDIT_COST_*`): a confirmed
  booking (`CREDIT_COST_BOOKING`, default 5), each SMS (1), each voice minute
  (2, rounded up), each WhatsApp message (1). Booking charges are idempotent per
  appointment (`booking:{id}`), so a replayed payment IPN never double-charges.
- **Price per credit is per-hospital** — a negotiated ৳/credit rate stored on
  `hospital_wallets.credit_rate_bdt` (new wallets default to
  `DEFAULT_CREDIT_RATE_BDT`). Credits **never expire**.
- **Buying credits:** `GET /hospital/wallet` (balance, rate, ledger) and
  `POST /hospital/wallet/topup {credits}` — priced at the hospital's rate, creates
  a `credit_topup` payment, and loads the wallet when it confirms (gateway IPN or
  the manual provider's autopay), via the `credit_topup` branch of
  `confirm_paid_booking`.
- **Negative balances are allowed** — a patient is never turned away because a
  hospital forgot to top up. The wallet sweep (`sweep_wallet_debt`, piggybacks the
  reminder loop) hides a hospital from the marketplace only once its debt crosses
  `WALLET_DEBT_SUSPEND_CREDITS`, via `hospitals.wallet_status` — a **separate**
  flag from `billing_status` so the two sweeps never fight. Marketplace visibility
  now requires `h.status='active' AND h.billing_status<>'suspended' AND
  h.wallet_status='ok'`.

- **Source of truth** is the append-only `wallet_ledger`; `hospital_wallets.balance`
  is a cached running total updated in the same transaction (`SELECT … FOR UPDATE`).

## Profit / margin (superadmin only)

Because usage is metered, the platform dashboard shows a full P&L. **Gross
revenue** = booking fees + patient subscriptions + hospital subscriptions +
credit sales. Against it, `platform_revenue_stats` estimates the **real cost** of
consumed channels (`COST_SMS_BDT`, `COST_VOICE_MIN_BDT`, `COST_WHATSAPP_BDT`) and
the gateway's cut (`GATEWAY_FEE_PCT`):

> **net_margin = gross_revenue − estimated_channel_cost − gateway_fees**

These cost knobs are superadmin-only and never patient-facing. See
[DEPLOYMENT.md](./DEPLOYMENT.md#create-the-super-admin-platform_admin-account) to
create the super-admin who sees all of this.

## Platform-admin dashboard

`/platform` (frontend) → `/platform/*` (API, gated to the `platform_admin` JWT role,
**not** the `X-Platform-Key`):

| Endpoint | Purpose |
|---|---|
| `GET /platform/overview` | Full P&L: gross revenue (all streams), credit usage by channel, estimated cost, gateway fees, **net margin**, subscriber/trial counts, open escalations, per-hospital P&L table |
| `POST /platform/hospitals/{id}/subscription/mark-paid` | Advance the billing period + reactivate |
| `GET /platform/hospitals/{id}/wallet` | A hospital's wallet balance, rate, and ledger |
| `POST /platform/hospitals/{id}/wallet/rate` | Set the hospital's negotiated ৳/credit rate |
| `POST /platform/hospitals/{id}/wallet/grant` | Grant (or claw back, negative) credits — comp/goodwill/correction |
| `GET /platform/payments?kind&status&hospital_id` | Payment ledger (incl. `credit_topup`) |
| `POST /platform/payments/{id}/mark-paid` | Manually confirm a payment settled out-of-band (reuses the IPN path incl. SMS + wallet load) |
| `POST /platform/payments/{id}/refund?note=` | Flag a paid payment refunded (the actual bKash/Nagad refund is done by hand) |

The platform-admin login is `/platform-admin`; it redirects to `/platform`.
Platform admins have **no clinic**, so the dashboard is standalone (it does not
mount the clinic staff sidebar).

## Payment providers

Set `PAYMENT_PROVIDER`:

- **`manual`** (default) — no gateway account. With `PAYMENT_MANUAL_AUTOPAY=true`
  it confirms immediately (good for dev); with `false` it shows a pay-at-desk page an
  admin marks paid. Fully tested and the recommended default for a pilot.
- **`sslcommerz`** — the free BD sandbox gateway (aggregates bKash/Nagad/cards).
  Needs `SSLCOMMERZ_STORE_ID` / `SSLCOMMERZ_STORE_PASSWD`; `SSLCOMMERZ_SANDBOX=true`
  uses the sandbox endpoint. `PUBLIC_BASE_URL` must be internet-reachable (a tunnel
  in dev) so the gateway can call the IPN/redirect back. The validator is always
  re-checked server-side — the posted IPN payload is never trusted alone.

## Setting up SSLCommerz (sandbox → production)

`manual` needs no external setup at all — skip this section for a fee-free or
pay-at-desk pilot. This is only needed to take real online bKash/Nagad/card
payments through `PAYMENT_PROVIDER=sslcommerz`.

There is **no bKash-only or Nagad-only integration in this app** — SSLCommerz is
the gateway, and it aggregates bKash, Nagad, Rocket, and cards behind one
checkout page (`tools/payments.py::SSLCommerzProvider`). You register once with
SSLCommerz; the wallets show up as payment options on their hosted page.

### 1. Get sandbox credentials

1. Go to SSLCommerz's own developer/sandbox registration page
   (`developer.sslcommerz.com`) and register for a **sandbox** account. This is
   free and, unlike a live account, does not require trade-license/business
   documents — a name, email, and phone number is enough.
2. SSLCommerz emails (or shows in their sandbox merchant panel) a **Store ID**
   and **Store Password** for the sandbox store. These are separate from any
   live/production credentials and only work against `sandbox.sslcommerz.com`.
3. SSLCommerz's own sandbox testing guide documents the dummy card/bKash/Nagad
   test flows and OTP/PIN values to use on their hosted checkout page for a
   sandbox transaction — those values occasionally change, so pull them from
   SSLCommerz's current docs rather than from anywhere else. Nothing about the
   test flow is configured in this app; it's entirely on their checkout page.

Verify what you actually got before wiring it in — log into the sandbox
merchant panel with the credentials they gave you and confirm the store is
active. This app never talks to a merchant-panel UI, only the REST API, so a
credential typo will surface as a `SSLCommerz initiate failed: ...` log line
(see `tools/payments.py:107`) rather than anything visible in a browser.

### 2. App-side configuration (this is exact — verified from code)

Set in `.env`:

```bash
PAYMENT_PROVIDER=sslcommerz
SSLCOMMERZ_STORE_ID=<store id from step 1>
SSLCOMMERZ_STORE_PASSWD=<store password from step 1>
SSLCOMMERZ_SANDBOX=true                       # false only in production
PUBLIC_BASE_URL=https://<tunnel-or-domain>    # backend — must be reachable BY SSLCommerz's servers
PORTAL_BASE_URL=https://<tunnel-or-domain>    # frontend — the patient's browser lands here after paying
```

Unlike some SSLCommerz integrations, **you do not configure success/fail/cancel/IPN
URLs in the merchant panel** — this app passes them per-transaction in the
`initiate()` API call (`tools/payments.py:86-89`), built from `PUBLIC_BASE_URL`:

| URL | Route | Purpose |
|---|---|---|
| IPN | `{PUBLIC_BASE_URL}/payments/ipn/sslcommerz` | Server-to-server; SSLCommerz calls this to report the outcome. This is the **only** thing that actually confirms a booking (`api/routes/payments.py:65-75`). |
| Success/fail/cancel | `{PUBLIC_BASE_URL}/payments/redirect/{success,fail,cancel}` | Where the patient's browser bounces back to; just redirects on to `{PORTAL_BASE_URL}/portal/pay/result` for display. Closing the tab here does **not** skip payment — only the IPN confirms. |

Because SSLCommerz's servers must be able to reach `PUBLIC_BASE_URL` directly,
`localhost` will not work while developing — use a tunnel (e.g. `ngrok http
8000`) and put its `https://` URL in `PUBLIC_BASE_URL` for the duration of the
test.

### 3. Test it end to end

1. Start the stack with the sandbox env vars above and a tunnel running.
2. Book a slot with a booking fee configured (`BOOKING_FEE_DEFAULT` or a
   hospital's `booking_fee` > 0) through the portal.
3. You should land on SSLCommerz's hosted sandbox checkout page
   (`GatewayPageURL` returned by `initiate()`). Pay with one of their published
   sandbox test methods.
4. Confirm: the appointment flips from held to confirmed, an SMS goes out, and
   the payment ledger (`GET /platform/payments` / `/platform` dashboard) shows
   the payment `paid`. Check the backend log for the IPN hitting
   `/payments/ipn/sslcommerz` if it doesn't confirm — a validator mismatch or
   an unreachable `PUBLIC_BASE_URL` are the two most common causes.
5. Replay the same IPN (or pay twice) — confirmation is idempotent
   (`confirm_paid_booking`), so it should not double-book or double-charge.

### 4. Going live

1. Apply for a **live** SSLCommerz account (this does need business/trade
   documents — follow their onboarding, which is outside this app's scope).
2. Swap in the live `store_id`/`store_passwd`, set `SSLCOMMERZ_SANDBOX=false`,
   and point `PUBLIC_BASE_URL`/`PORTAL_BASE_URL` at your real production HTTPS
   domains (see the [deployment checklist](./DEPLOYMENT.md#5-production-checklist)
   — SSLCommerz will not call back to a plain-HTTP or non-public URL).
3. Set `PAYMENT_MANUAL_AUTOPAY=false` if you keep `manual` as a fallback
   anywhere, and double check `BOOKING_FEE_DEFAULT` / hospital `booking_fee`
   values reflect real pricing, not test values.
4. Do one real low-value transaction before announcing it live.

## Migrations

`0025` (unified-thread conversation_log/escalations) → `0026` (payments, held
bookings, hospital billing) → `0027` (patient plans + usage counter) → `0030`
(hospital credit wallet: `hospital_wallets`, `wallet_ledger`, `payments.credits`
+ `credit_topup` kind, `hospitals.wallet_status`). Run `alembic upgrade head`.

## Config reference

See the backend env-var table in the [README](../README.md#environment-variables)
for every `PAYMENT_*`, `*_FEE`, `*_TRIAL_DAYS`, `FREE_*`, `SSLCOMMERZ_*`,
`PUBLIC_BASE_URL`, and `PORTAL_BASE_URL` knob. Credit-wallet knobs (all in
`.env.example`): `CREDITS_ENABLED`, `CREDIT_COST_*`, `DEFAULT_CREDIT_RATE_BDT`,
`WALLET_DEBT_SUSPEND_CREDITS`, `WALLET_LOW_BALANCE_CREDITS`, and the
superadmin-only cost estimates `COST_SMS_BDT` / `COST_VOICE_MIN_BDT` /
`COST_WHATSAPP_BDT` / `GATEWAY_FEE_PCT`.
