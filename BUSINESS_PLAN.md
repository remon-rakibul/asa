# Business Plan — Clinic Console AI Appointment System

## What You've Built

A multi-tenant SaaS platform that gives clinics and hospitals in Bangladesh an AI-powered receptionist. Patients book appointments in Bangla via web chat, WhatsApp, SMS, or phone call. Clinic staff manage everything from a polished admin console. The AI remembers patients across sessions, handles voice calls, and runs entirely on local hardware with no per-token API costs.

---

## The Problem Worth Solving

Bangladesh has ~3,500 private hospitals and tens of thousands of private clinics. Most of them:

- Take appointments by phone only, with a human receptionist who is unavailable nights and weekends
- Lose patients who hang up after waiting on hold
- Have no digital record of patient history — staff re-ask name, age, and condition every visit
- Cannot afford a custom software team

A missed appointment costs a clinic roughly ৳500–2,000 in lost revenue. A single busy specialist clinic misses 10–30 bookings a week.

---

## Revenue Models

### Model A — Monthly SaaS Subscription (Primary)

Charge each clinic a flat monthly fee for access to the platform.

| Tier | Price (BDT/month) | Price (USD/month) | What's included |
|---|---|---|---|
| **Starter** | ৳2,500 | ~$23 | 1 department, web chat only, up to 200 appointments/month |
| **Growth** | ৳6,000 | ~$55 | 3 departments, WhatsApp + SMS, 1,000 appointments/month |
| **Pro** | ৳12,000 | ~$110 | Unlimited departments, voice (SIP), unlimited appointments, audit log |
| **Enterprise** | Custom | Custom | Multi-hospital group, white-label, on-prem deployment, SLA |

**Unit economics at scale:**
- 100 Growth clinics = ৳600,000/month (~$5,500)
- 500 mixed clinics = ~$25,000–35,000/month
- Hosting cost per clinic on a shared server: ~$2–5/month (Ollama on a 32GB box handles ~50 concurrent clinics)

### Model B — Per-Appointment Commission (Upsell / Alternative)

Charge ৳10–25 per confirmed appointment instead of a subscription. Better fit for small clinics with irregular volume. Can be offered as a "pay-as-you-go" fallback for Starter clinics.

### Model C — Setup & Onboarding Fee (One-time)

Charge ৳5,000–15,000 per clinic for:
- Initial setup and configuration
- WhatsApp Business API number provisioning
- Custom greeting scripts in the doctor's name
- Staff training (1 hour remote session)

This covers your time and makes the relationship sticky from day one.

### Model D — Add-on Modules

| Add-on | Price |
|---|---|
| Voice SIP line (monthly) | ৳3,000/month per number |
| RAG knowledge base (upload hospital docs) | ৳1,500/month |
| SMS gateway credits (BD local) | Pass-through + 15% margin |
| White-label (clinic's own branding, custom domain) | ৳5,000 setup + ৳3,000/month |
| Priority support / dedicated Slack | ৳4,000/month |

### Model E — Referral / Agency Channel

Partner with healthcare IT consultants and medical equipment dealers in Dhaka. Offer 20–30% of the first year's subscription as referral commission. They already have relationships with clinic owners.

---

## Target Market

### Primary — Private specialist clinics in Bangladesh

- Cardiologists, orthopedics, gynecology, dermatology, ENT clinics in Dhaka, Chittagong, Sylhet
- 1–3 doctors per clinic, 1–2 receptionists
- Already using paper registers or basic WhatsApp groups
- Can pay ৳5,000–12,000/month if it saves them ৳20,000+ in missed appointments
- **Reachable through:** doctor associations (BMA), medical conferences, Facebook groups for clinic owners

### Secondary — Private hospital groups (3–20 departments)

- Larger ticket, longer sales cycle (2–4 months)
- Multi-department, need the Pro/Enterprise tier
- **Reachable through:** direct outreach to hospital admin/CEO, LinkedIn

### Tertiary — Diagnostic centers and pharmacy chains

- High volume, repetitive booking (blood tests, scans)
- Perfect for the voice + WhatsApp channel
- Can use the queue system heavily

---

## Competitive Advantage

| What others offer | What you offer |
|---|---|
| Generic booking widgets (Zocdoc-style) | Conversational AI in Bangla — patients just type naturally |
| Expensive custom software | Affordable SaaS, live in 1 day |
| Cloud-dependent (per-API-call cost) | Runs locally — your margin doesn't shrink as you scale |
| English-only AI | Native Bangla understanding, Bangla voice responses |
| No memory | Agent remembers each patient's name, age, history — no re-intake |
| Phone calls only | Web + WhatsApp + SMS + Voice from one platform |

**Moat:** The Bangla AI receptionist with memory is genuinely hard to replicate quickly. The combo of WhatsApp + voice + web in one product, tuned for Bangladeshi medical context, doesn't exist yet.

---

## Go-to-Market Plan

### Phase 1 — Validate (Month 1–3): Land 5 paying pilot clinics

1. **Pick 5 specialist clinics in Dhaka** personally known or via referral (cardiologist, gynecologist, dermatologist preferred — high appointment volume).
2. Offer a **free 30-day pilot**, then ৳3,000/month after. No contracts.
3. Sit with the receptionist for 1 hour to observe how they currently take bookings. Configure the AI with exact phrasing the doctor uses.
4. Measure: appointments booked via AI / total appointments. Target: 30%+ via AI within 2 weeks.
5. Collect testimonials and WhatsApp screenshots of patients praising the experience.

### Phase 2 — Grow (Month 3–12): Reach 50 clinics

- Use pilot testimonials in Facebook/LinkedIn ads targeted at `clinic owner`, `private doctor`, `hospital admin` in BD.
- Attend **CMSD** (Directorate General of Health Services) and private hospital association events.
- Partner with 2–3 healthcare IT consultants in Dhaka on the referral model.
- Publish case studies: "Dr. X's clinic increased appointments by 40% in 1 month."
- Pricing: lock in Growth tier at ৳6,000/month.

### Phase 3 — Scale (Year 2): 200+ clinics, enter hospital groups

- Hire 1 part-time support person (can be a medical student) to handle onboarding.
- Build a **self-serve onboarding flow** so clinics can sign up, configure, and go live without your involvement.
- Expand to Chittagong and Sylhet via regional resellers.
- Approach 3–4 mid-size private hospital groups for Enterprise deals.

---

## Pricing Psychology

- **Don't price per-seat or per-user.** Clinics find that confusing. Flat monthly is easy to budget.
- Anchor on the cost of a human receptionist: minimum wage in BD is ~৳12,500/month. Your Pro tier at ৳12,000 is cheaper than a part-time employee and works 24/7.
- Offer **annual prepay with 2 months free** to improve cash flow and reduce churn.
- Give the **first month free** on any paid tier — the switching cost of removing the WhatsApp number from a clinic's existing workflow is very high after 30 days (retention weapon).

---

## Cost Structure

| Cost | Estimate |
|---|---|
| VPS / dedicated server (32GB RAM, NVMe) | $60–120/month — handles 30–80 clinics |
| PostgreSQL managed DB (if not self-hosted) | $20–50/month |
| WhatsApp Business API (Meta) | Free up to 1,000 service convos/month/clinic on business tier |
| SMS gateway (BD local) | ৳0.30–0.50 per SMS (pass-through to clinic) |
| Your time (support + ops) at 50 clinics | ~10 hrs/week |
| Total fixed cost at 50 clinics | ~$200/month |

**Gross margin at 50 Growth clinics:** ৳300,000 revenue − ~৳22,000 costs = **~93% gross margin**.

---

## Revenue Projections

| Milestone | Clinics | MRR (BDT) | MRR (USD) |
|---|---|---|---|
| End of Month 3 | 5 pilots | ৳15,000 | ~$135 |
| End of Month 6 | 20 paying | ৳120,000 | ~$1,100 |
| End of Month 12 | 60 paying | ৳400,000 | ~$3,600 |
| End of Year 2 | 200 paying | ৳1,400,000 | ~$12,700 |
| End of Year 3 | 500 + 5 enterprise | ৳4,000,000 | ~$36,000 |

These assume a blended average of ৳7,000/clinic/month (mix of Growth + Pro).

---

## Risks and Mitigations

| Risk | Mitigation |
|---|---|
| Clinics unwilling to pay | Lead with free trial; show ROI in appointments recovered |
| WhatsApp API approval delays | Start with web chat; add WhatsApp as upgrade |
| Local Ollama too slow on cheap hardware | Offer cloud LLM fallback (Gemini) as optional tier |
| Doctor changes phone number / leaves clinic | Build doctor management UI (already done) |
| Competition from Indian/global SaaS | Bangla AI + local pricing + local support is the moat |
| Data privacy concerns (patient PII) | Self-hosted option; encryption at rest already implemented |

---

## What to Do This Week

1. **List 10 clinics** you or your family have visited. Call the receptionist and ask: "How do you handle appointments when you're not available?" That conversation IS your sales pitch.
2. Deploy the platform to a cheap VPS (even DigitalOcean $24/month droplet to start).
3. Create a simple landing page (can use your new homepage) with a "Request a free demo" WhatsApp button.
4. Set a price and say it out loud without flinching: "৳6,000 a month."

---

## Long-term Vision

The appointment system is the **entry point**. Once you have trust and data inside a clinic:

- **Prescription reminders** — automated WhatsApp follow-up after visit
- **Lab result notifications** — ping patients when reports are ready
- **Insurance pre-authorization** — AI fills standard forms
- **Telemedicine scheduling** — video call booking via the same interface
- **Analytics dashboard** — show clinic owners patient retention, peak hours, cancellation rates

Each of these is an additional ৳2,000–5,000/month upsell on top of the base subscription.

The real exit or scale play is becoming the **operating system for private healthcare in Bangladesh** — the layer that every clinic interaction flows through.
