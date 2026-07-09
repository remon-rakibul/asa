# Twilio SMS Webhook Setup

## 1. Create a Twilio account and buy a number

1. Sign up at [twilio.com](https://www.twilio.com) (free trial works fine).
2. In the Twilio Console go to **Phone Numbers → Manage → Buy a number**.
3. Search for a number with **SMS** capability and buy it.

---

## 2. Copy your credentials into `.env`

From the Twilio Console homepage copy the three values:

```
TWILIO_ACCOUNT_SID=ACxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx   # starts with AC
TWILIO_AUTH_TOKEN=xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
TWILIO_FROM_NUMBER=+1xxxxxxxxxx                         # your purchased number
```

---

## 3. Expose the backend over a public URL

Twilio needs to reach your `/twilio/sms` endpoint from the internet. During development, use **ngrok** to create a tunnel.

### Install ngrok

```bash
# macOS / Linux via snap
sudo snap install ngrok

# or download from https://ngrok.com/download
```

### Start your FastAPI backend

```bash
uvicorn api.app:app --reload --port 8000
```

### Open the tunnel (in a second terminal)

```bash
ngrok http 8000
```

ngrok prints a forwarding URL like:

```
Forwarding   https://a1b2-203-0-113-5.ngrok-free.app -> http://localhost:8000
```

Your webhook URL is:

```
https://a1b2-203-0-113-5.ngrok-free.app/twilio/sms
```

> **Note:** The free ngrok URL changes every time you restart ngrok. Use a paid ngrok account or a fixed deployment URL for a permanent address.

---

## 4. Configure the webhook in Twilio Console

1. In the Twilio Console go to **Phone Numbers → Manage → Active numbers**.
2. Click your number.
3. Scroll to the **Messaging Configuration** section.
4. Set **"A message comes in"** to:
   - Type: **Webhook**
   - URL: `https://<your-ngrok-subdomain>.ngrok-free.app/twilio/sms`
   - Method: **HTTP POST**
5. Click **Save configuration**.

---

## 5. Test it

Send an SMS from any phone to your Twilio number. The agent will reply with a Bangla greeting and begin the appointment booking flow. Each phone number gets its own persistent conversation thread.

---

## Production deployment

When you deploy the FastAPI backend to a server (e.g. a VPS, Railway, Render), replace the ngrok URL with your real domain:

```
https://your-domain.com/twilio/sms
```

Set the same webhook URL in the Twilio Console. No ngrok needed.

---

## Signature validation

The webhook validates every Twilio request using the `X-Twilio-Signature` header. This is enabled automatically as long as `TWILIO_AUTH_TOKEN` is set in `.env` — no extra steps needed. If you see `403 Invalid Twilio signature` errors, make sure the URL in the Twilio Console **exactly matches** the URL your server receives (scheme, host, path — no trailing slash differences).
