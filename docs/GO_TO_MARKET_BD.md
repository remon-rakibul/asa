# Go-to-Market — Bangladesh (pilot playbook)

## Positioning
**A 24/7 Bangla voice + WhatsApp receptionist for your chamber.** It answers
patient calls and messages around the clock, books appointments into the
doctor's schedule, and sends reminders — so the doctor captures after-hours
patients and cuts no-shows without hiring more front-desk staff.

**Why us:** fully local / on-prem — patient data never leaves the clinic's
infrastructure (a real privacy differentiator), and there's no per-minute cloud
LLM cost.

## Target & beachhead
- Private practitioners / **chambers** and small diagnostic centers in **Dhaka &
  Chittagong** where one receptionist (or none after hours) is the booking
  bottleneck.
- Land **one friendly pilot clinic** via a warm intro, free or discounted, in
  exchange for a reference + metrics.

## Pricing (validate during pilot; build billing after)
| Tier | Monthly (BDT) | Includes |
|------|---------------|----------|
| Starter | ~3,000 | WhatsApp + web chat, 1 doctor, reminders |
| Voice  | ~8,000 | + inbound phone (voice), SMS reminders |
| Multi  | custom | multiple doctors/branches |

Payment rails for BD: **bKash / Nagad / SSLCommerz** (not Stripe). Self-serve
onboarding + billing is a post-pilot build; onboard the pilot white-glove.

## Pilot success metrics (instrumented in the product)
- Appointments booked by the agent / week
- After-hours bookings captured (would otherwise be lost)
- No-show rate before vs. after reminders
- Calls/messages handled without human intervention

These numbers are the pitch + ROI calculator for clinic #2+.

## Onboarding a clinic (operationally, today)
1. Create the clinic + admin: `POST /clinics` (or `make create-admin`).
2. Set the weekly schedule via the admin UI (`/schedule`).
3. Wire channels (rows in `channels`): WhatsApp number, inbound SMS number,
   and/or SIP DID → see `docs/WHATSAPP_AND_SMS.md`, `docs/TELEPHONY.md`.
4. Brand: clinic name, doctor name, doctor phone (clinic config).
5. Smoke test each channel end-to-end, then go live.

## Compliance / risk (Bangladesh)
- **Patient data privacy** — local hosting is the differentiator; add a consent
  line in the conversation and a written privacy policy. Track BD's emerging
  data-protection rules.
- **Telephony** — confirm BTRC rules for routing PSTN→SIP before launch.
- **SMS** — register a masking/sender ID with the gateway/operator.

## Sales collateral to produce
- 60-second **demo video of a real Bangla voice call** booking an appointment
  (do this *after* the neural TTS upgrade — espeak sounds robotic).
- One-page Bangla/English flyer + ROI calculator driven by pilot metrics.

## Sequencing to first revenue
1. Convincing demo: streaming + neural Bangla TTS + WhatsApp (done in code).
2. Land pilot clinic; onboard white-glove; instrument metrics.
3. Harden the channel the pilot actually uses (voice or WhatsApp).
4. After validation: billing (bKash/SSLCommerz) + self-serve onboarding.
