# WhatsApp & SMS channels

Both reuse the same LangGraph agent. Each is inert until configured, and each
maps an inbound identity to a clinic (multi-tenant).

## WhatsApp (Meta Cloud API)

1. Create a Meta app + WhatsApp Business account; get a **phone number ID** and a
   **permanent access token**. Choose a **verify token** (any secret string).
2. Set in `.env`:
   ```
   WHATSAPP_TOKEN=...
   WHATSAPP_PHONE_ID=...
   WHATSAPP_VERIFY_TOKEN=...
   ```
3. In the Meta dashboard, set the webhook callback URL to
   `https://<host>/whatsapp/webhook` and the verify token above. Subscribe to
   `messages`.
4. Map the business number to a clinic:
   ```sql
   INSERT INTO channels (clinic_id, kind, identifier)
   VALUES (<clinic_id>, 'whatsapp', '<display_phone_number from webhook>');
   ```

- **Text** messages are answered directly.
- **Voice notes** are downloaded and transcribed locally with faster-whisper
  (Bangla) — no cloud STT — then answered.
- Outbound confirmations/replies use `send_whatsapp_text`.

## SMS

Two providers, selected by `SMS_PROVIDER`:

- `twilio` (default): set `TWILIO_ACCOUNT_SID`, `TWILIO_AUTH_TOKEN`,
  `TWILIO_FROM_NUMBER`. Inbound webhook: `POST /twilio/sms`.
- `bdgateway`: a local BD HTTP SMS gateway (e.g. SSL Wireless) — cheaper and
  supports Bangla Unicode + masked sender IDs. Set:
  ```
  SMS_PROVIDER=bdgateway
  BD_SMS_API_URL=...
  BD_SMS_API_KEY=...
  BD_SMS_SENDER_ID=...
  ```
  Adjust the POST field names in `tools/sms.py::_bdgateway_send` to match your
  vendor's contract (they vary).

Map an inbound SMS number to a clinic with a `channels` row of kind `'sms'`
(identifier = the clinic's inbound number). Message bodies are in Bangla and
branded per clinic.
