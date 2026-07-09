# User Manual

This manual covers every feature of the Clinic Console — the admin dashboard for the AI appointment-setter system. It is written for clinic owners, receptionists, doctors, and platform administrators.

---

## Table of Contents

1. [What this product does](#1-what-this-product-does)
2. [Roles and access](#2-roles-and-access)
3. [Logging in](#3-logging-in)
4. [Dashboard](#4-dashboard)
5. [Appointments](#5-appointments)
6. [Schedule](#6-schedule)
7. [Patients](#7-patients)
8. [Queue](#8-queue)
9. [Conversations](#9-conversations)
10. [Test Chat](#10-test-chat)
11. [Integrations & Channels](#11-integrations--channels)
12. [Settings](#12-settings)
13. [Hospitals (platform admin only)](#13-hospitals-platform-admin-only)
14. [Audit Log](#14-audit-log)
15. [How the AI agent works](#15-how-the-ai-agent-works)
16. [Platform admin operations](#16-platform-admin-operations)

---

## 1. What this product does

This system replaces a human receptionist for appointment booking. Patients contact the clinic by WhatsApp, SMS, or phone call. An AI agent — running entirely in Bangla — collects the patient's name, age, and mobile number, offers available time slots, and confirms the booking. No human is needed to handle the call.

The clinic admin uses this dashboard to:
- Configure the doctor's weekly schedule
- View and cancel appointments
- Review conversation transcripts
- Manage channels (WhatsApp numbers, SMS numbers, voice lines)
- Register patients and manage the OPD queue
- Monitor what the AI is doing in real time

---

## 2. Roles and access

| Role | Who it's for | What they can see/do |
|---|---|---|
| `platform_admin` | You (the SaaS operator) | Everything: all hospitals, all clinics, create new tenants |
| `hospital_admin` | The clinic owner / doctor | Their clinic's full dashboard: schedule, appointments, patients, queue, conversations, settings |
| `dept_head` | Department manager in a hospital | Department-level view: queue, appointments, patients |
| `receptionist` | Front-desk staff | Register patients, manage queue, view appointments |
| `doctor` | The treating physician | View their own schedule and appointments |

Every account is scoped to a hospital. A `hospital_admin` can only see their own clinic's data — they cannot see any other clinic.

---

## 3. Logging in

Go to `http://localhost:3000` (or your deployed URL). Enter your email and password on the login page. You are redirected to the Dashboard on success.

**If your login page just reloads** with no error, clear your browser's local storage for this domain (DevTools → Application → Local Storage → delete `clinic_token`) and try again.

**Forgot password:** There is no self-service reset yet. A platform admin can update your password directly in the database, or via the API.

---

## 4. Dashboard

The Dashboard is the first screen after login. It gives a live snapshot of the clinic's activity today.

### Stats row (four cards)

| Card | Meaning |
|---|---|
| Today's appointments | Total confirmed bookings for today |
| Remaining | Today's bookings that have not yet been completed |
| Completed | Visits marked done in the queue |
| Cancellations | Bookings cancelled today |

### Today's timeline

A visual hour-by-hour view of today's schedule. Each slot shows whether it is booked (confirmed), free, or in the past. Click any booked slot to see patient details.

### Upcoming appointments

The next five confirmed appointments across all upcoming days, with patient name, time, and a quick-cancel button.

### Voice numbers

If any voice channels (SIP/phone lines) are configured and have received calls, they appear here with a live counter showing total calls received and how many resulted in a booked appointment.

---

## 5. Appointments

**Sidebar → Appointments**

A full list of all bookings for this clinic, with filters.

### Filters

- **Date range** — from / to in YYYY-MM-DD format
- **Status** — All, Confirmed, or Cancelled
- **Search** — searches patient name, phone number, or MRN

### What each row shows

- Patient name and mobile number
- Appointment date and time (with Bangla slot label)
- Status badge (confirmed / cancelled)
- Which channel the patient used to book (WhatsApp, SMS, web, voice)

### Cancelling an appointment

Click the cancel button on any confirmed row. The status changes to cancelled immediately. The time slot is freed and becomes available for the agent to offer again.

> Cancelled appointments are not deleted — they remain in the list with a cancelled badge for your records.

### Availability preview

`GET /availability?days_ahead=7` (available in the API at `/docs`) returns the raw slot list that the AI is currently showing patients. Useful for debugging why certain times are or are not being offered.

---

## 6. Schedule

**Sidebar → Schedule**

Defines when the doctor is available each week. The AI uses this table to calculate which slots to offer patients.

### How it works

The schedule stores recurring weekly hours. For each active day, you set a start time, end time, and slot duration in minutes. The system divides that window into slots and subtracts already-confirmed appointments to produce the available list.

**Example:** Monday, 09:00–13:00, 20-minute slots → 12 slots per Monday. If 3 are already booked, the agent offers the remaining 9.

### Editing the schedule

Each row is a day of the week. Toggle the day on or off with the active switch. For active days, set:

- **Start time** — when the first slot begins
- **End time** — when the last slot must end (no slot starts after this)
- **Slot duration** — length of each appointment in minutes

Click **Save** to apply. The new schedule takes effect for the next patient who asks for slots — no restart needed.

### Days off

Deactivate a day by turning off its toggle. Patients will not be offered any slots on that day. To take a specific date off (e.g., a holiday), cancel the relevant appointments for that date manually from the Appointments page — the schedule editor is for recurring weekly patterns only.

### Booking window

Configured in Settings → Clinic → Booking window. Sets how many days ahead the agent can offer. Default is 7 days. Maximum is 60.

---

## 7. Patients

**Sidebar → Patients**

The patient registry — a hospital-level record of all patients who have been registered at this hospital, identified by MRN (Medical Record Number).

> **Important:** Patient records are separate from appointment bookings. The AI books appointments by phone number. The patient registry is for staff to maintain a formal medical identity record.

### Searching

Type in the search box to filter by name, phone number, or MRN. Results update as you type.

### Registering a patient

Click **Register Patient**. Fill in:
- Full name (required)
- Phone number (required — must be unique per hospital)
- Age (optional)
- Gender (optional)

An MRN is automatically assigned in the format `MRN-{hospital_id}-{sequence}`. If a patient with the same phone number already exists, the system returns their existing record rather than creating a duplicate.

### For platform_admin

Platform admins have no hospital scope by default. A hospital picker appears at the top of the page. Select a hospital first; the patient list for that hospital then loads.

---

## 8. Queue

**Sidebar → Queue**

The OPD token queue for today. Shows which patient is currently being seen, how many are waiting, and the full list of tokens issued today.

### How the queue works

When a patient arrives at the clinic for their appointment, a receptionist issues them a token. Tokens are numbered sequentially per day (A1, A2, A3…). The queue board shows the current token being served.

### Token states

| State | Meaning |
|---|---|
| Waiting | Token issued; patient is in the waiting area |
| Called | Receptionist has called this token number |
| In progress | Patient is currently with the doctor |
| Completed | Visit is done |

### Calling a token

Click **Call** next to a waiting token. The system marks it as called and sends an SMS notification to the patient's registered mobile number: "Your turn is ready — token A3."

### Completing a visit

Click **Complete** when the patient's consultation is finished. The token is marked done and the next waiting patient can be called.

---

## 9. Conversations

**Sidebar → Conversations**

A full log of every patient conversation the AI has handled for this clinic — WhatsApp, SMS, and web chat sessions.

### Session list (left panel)

Each row shows:
- The patient's name (once collected by the agent) or "New session" if the conversation did not complete
- The channel (WhatsApp / SMS / web)
- Date and time of the last message
- A phase indicator showing how far the conversation got (greeting → collecting info → showing slots → confirmed booking)

Click **Refresh** to reload the list without a full page reload.

### Transcript view (right panel)

Click any session to open the full turn-by-turn transcript. Patient messages and AI replies are shown in order, colour-coded by speaker. Useful for:
- Reviewing what the agent said if a patient complains
- Debugging unexpected booking failures
- Understanding how patients phrase their requests

---

## 10. Test Chat

**Sidebar → Test Chat**

A live chat interface that connects directly to the AI agent. Use this to:
- Test the greeting and conversation flow after changing settings
- Verify a new greeting message sounds correct
- Check that slot availability is being offered correctly
- Demonstrate the product to a third party

The test chat uses the `web` channel and your clinic's slug. Type a message and press Enter. The agent responds in Bangla exactly as it would to a real patient.

> Test chat sessions appear in the Conversations log with the session ID from your browser session.

---

## 11. Integrations & Channels

**Sidebar → Integrations**

Two sections: integration status and channel mappings.

### Integration status

A read-only panel showing which platform services are configured:

| Integration | What it means |
|---|---|
| WhatsApp (Meta Cloud API) | Whether `WHATSAPP_TOKEN` and `WHATSAPP_PHONE_ID` are set |
| SMS | Twilio or BD Gateway credentials present |
| Voice calls (LiveKit SIP) | LiveKit server URL and API keys are set |
| Language model | Which Ollama model is active (e.g. `gemma4`) |
| Text-to-speech | Which TTS engine is active (e.g. `mms` or `gemini`) |
| Speech-to-text | Which Whisper model and device (CPU/GPU) |

No secrets are shown here — only whether each service is connected.

### Channel mappings

Channels are the phone numbers and identifiers that route incoming patient messages to your clinic. Each channel has:
- **Kind** — `whatsapp`, `sms`, `web`, or `sip`
- **Identifier** — the phone number (e.g. `+8801700000000`) or slug for web
- **Label** — optional human-readable name (e.g. "Main WhatsApp line")

**Adding a channel:**
Click **Add channel**, select the kind, enter the identifier, and optionally add a label. Once saved, any patient message arriving on that number will be routed to your clinic.

**Removing a channel:**
Click the delete button on a channel row. Messages arriving on that number will no longer be handled by this clinic. If `strict_channel_routing` is disabled, they would fall back to the default clinic — so remove carefully.

**Channel stats:**
Below the channel list, call and appointment counts per voice number are shown. This updates in real time as calls come in.

> One number can only be mapped to one clinic at a time. Attempting to map a number already in use returns a 409 conflict error.

---

## 12. Settings

**Sidebar → Settings**

Three tabs: Clinic, Doctors, and Greetings.

### Clinic tab

**Clinic name**
The name the AI uses in every greeting and SMS. Set this to your clinic's actual name in Bangla or English. Example: `রেনেগেক্স ক্লিনিক`.

**Booking window**
How many days ahead patients can book. Set between 1 and 60. A setting of 7 means patients can only book within the next 7 days. If a patient asks for a slot 2 weeks away and your window is 7 days, the agent will tell them no slots are available that far out.

### Doctors tab

Manage the roster of doctors at this clinic. The **primary doctor** is the one the AI refers to in greetings and whose name appears in booking confirmation SMS messages.

**Adding a doctor:**
Click **Add doctor**. Fill in name, specialty, and phone number. Check "Primary" to make this doctor the one the agent speaks about.

**Editing a doctor:**
Click the edit icon on any doctor row to update their details.

**Deleting a doctor:**
Click the delete icon. If the deleted doctor was the primary, set another doctor as primary before the next patient call.

### Greetings tab

**Text / chat greeting**
What the AI says at the very start of a WhatsApp or web chat session. Write this in Bangla. Leave blank for the agent to auto-generate based on the clinic name and doctor name.

Example:
> আসসালামু আলাইকুম, আমি রেনেগেক্স ক্লিনিকের AI রিসেপশনিস্ট। ডাক্তার সাহেবের সাথে অ্যাপয়েন্টমেন্ট নিতে চাইলে দয়া করে আপনার পূর্ণ নাম বলুন।

**Voice / phone-call greeting**
What is spoken aloud at the start of a phone call. Write naturally — avoid lists, asterisks, or bullet points since these will be read out literally by the TTS engine. Leave blank for auto-generation.

Changes to greetings take effect on the next patient session — existing open sessions are not affected.

---

## 13. Hospitals (platform admin only)

**Sidebar → Hospitals** *(visible only to `platform_admin`)*

A platform-level view of all hospital tenants registered on this server.

### Hospital list

Each card shows:
- Hospital name and slug
- Address and license number (if provided)
- Timezone
- Status (active / suspended)
- A **Departments** section listing all clinics (departments) within the hospital

### Adding a hospital

Click **Add Hospital**. Fill in:
- Name (required)
- Slug — a unique URL-safe identifier (required)
- Address (optional)
- License number (optional)
- Timezone (defaults to `Asia/Dhaka`)

This creates the hospital record. To make it operational, add departments and create a `hospital_admin` user (see [§16](#16-platform-admin-operations)).

> In practice, use `POST /clinics` instead of creating hospitals and departments manually. That endpoint creates the hospital, clinic, default channel, and admin user in one shot.

### Adding a department

Click **Add Department** inside a hospital card. A department is a clinic (e.g. Cardiology, General OPD). Fill in:
- Clinic name
- Specialty code (optional, e.g. `CARD`)
- Floor (optional)
- Phone extension (optional)

---

## 14. Audit Log

**Sidebar → Audit Log**

An append-only record of every administrative write action — who did what, when, and on which record. Entries can never be deleted or modified.

### What is logged

- Appointment cancellations
- Schedule changes
- Doctor roster changes
- Channel additions and removals
- Clinic settings changes
- Patient registrations
- Queue token calls and completions

### Each entry shows

- Timestamp
- Actor (the user who performed the action) and their role
- Action type (e.g. `cancel_appointment`, `update_schedule`)
- Entity type and ID (which record was affected)
- Old value and new value (JSON diff, for change actions)
- IP address of the request

### Filtering

Select an entity type from the filter dropdown to narrow the log to a specific domain (appointments, schedule, etc.).

> Audit log access is restricted to `hospital_admin` and `platform_admin`.

---

## 15. How the AI agent works

Understanding this helps you configure the system correctly and diagnose unexpected behaviour.

### The conversation flow

Every patient conversation follows this fixed sequence:

```
1. Greeting       — AI introduces itself and asks for the patient's name
2. Collect info   — AI asks for name, age, and mobile number (one at a time)
3. Show slots     — AI fetches available slots and presents up to 5 options
4. Confirm slot   — Patient picks a slot; AI reads it back and asks to confirm
5. Book           — AI books the appointment in the database
6. Farewell       — AI confirms the booking and ends the conversation
```

If the patient is unclear about their slot choice, the AI offers the list again. If the slot was taken in a race condition (two patients booking simultaneously), the AI apologises and re-offers fresh slots.

### What the AI cannot do

- Handle anything outside appointment booking (medical questions, prescriptions, etc.)
- Cancel an existing appointment on behalf of a patient (they must contact the clinic directly)
- Book for a different day than is in the schedule
- Accept a slot that is already confirmed

### Language

The agent always responds in Bangla. All prompts, greetings, and slot labels are in Bangla. The system prompt forbids the agent from using markdown, emoji, or English in its replies.

### How the AI finds available slots

At the moment a patient asks to book, the system:
1. Reads the clinic's weekly schedule for the next N days (N = booking window)
2. Generates all slot datetimes within working hours
3. Subtracts all already-confirmed appointments
4. Returns the first 5 remaining slots, ordered by time

This means if you change the schedule, it takes effect immediately for the next patient — there is no caching of the slot list.

### SMS notifications

When an appointment is booked, two SMS messages are sent:
1. **Patient confirmation** — sent to the patient's mobile number with their appointment time
2. **Doctor notification** — sent to the primary doctor's mobile number with the patient's name and time

These are sent via Twilio (or BD Gateway, depending on your SMS configuration). If SMS credentials are not configured, the booking still succeeds but no messages are sent.

---

## 16. Platform admin operations

These operations are only available to `platform_admin` accounts.

### Creating a new clinic (tenant)

Use `POST /clinics` at `http://localhost:8000/docs#/auth/add_clinic_clinics_post`.

Required header: `X-Platform-Key: <your platform key from .env>`

Request body:
```json
{
  "slug": "my-clinic",
  "name": "My Clinic Name",
  "doctor_name": "Dr. Ahmed",
  "doctor_phone": "+8801700000000",
  "availability_days_ahead": 7,
  "admin_email": "admin@myclinic.com",
  "admin_password": "StrongPassword123"
}
```

This creates in one step:
1. A hospital record (the org container)
2. A clinic record linked to that hospital
3. A default `web` channel mapped to the slug
4. A `hospital_admin` user with the given email and password

The clinic owner can then log in at the dashboard with those credentials.

### Creating a platform admin account

Use `POST /platform-admins` at `http://localhost:8000/docs`.

Required header: `X-Platform-Key: <your platform key from .env>`

Request body:
```json
{
  "email": "newadmin@example.com",
  "password": "StrongPassword123"
}
```

The new account has `platform_admin` role with no hospital scope. They can log in immediately and see the Hospitals page.

### Finding your platform key

The `PLATFORM_ADMIN_KEY` is in your `.env` file:
```
grep PLATFORM_ADMIN_KEY .env
```

Keep this key secret. Anyone with it can create new tenants and platform admins on your server.

### Suspending a clinic

Set the clinic's status to `suspended` directly in the database. Suspended clinic users cannot log in.

```sql
UPDATE clinics SET status = 'suspended' WHERE slug = 'their-slug';
```

To restore access:
```sql
UPDATE clinics SET status = 'active' WHERE slug = 'their-slug';
```

### Viewing all clinics via API

`GET /clinics` with a valid `platform_admin` JWT returns all clinics across all hospitals.

---

*For technical details on how the system is built, see [ARCHITECTURE.md](ARCHITECTURE.md).*
