# Patient data encryption (PII at rest)

ASA encrypts patient personal data in Postgres with a single symmetric key,
`PATIENT_ENCRYPTION_KEY` (Fernet: AES-128-CBC + HMAC-SHA256, via the
`cryptography` package). Code: `tools/crypto.py`.

Anyone who gets a copy of the database (a stolen backup, a leaked dump, a
compromised DB host) sees `gAAAAA…` ciphertext instead of names and phone
numbers. The app decrypts transparently, so staff and patients see normal data.

## What is encrypted

| Table | Columns |
|---|---|
| `appointments` | `patient_name`, `patient_mobile` |
| `sms_log` | `to_number`, `body` |
| `outbound_messages` (WhatsApp/SMS outbox) | `to_number`, `body` |

**Not** encrypted (plaintext even with the key set): `patient_accounts`
(name/phone/email — the phone is used for login and caller-ID lookups),
`patients`, conversation history in the LangGraph checkpoint tables, and
`inbound_events`. Protect the database itself (disk encryption, access
control) for those.

Fernet is randomised — the same phone encrypts differently each time — so
encrypted columns can't be searched with SQL `WHERE`. Code that matches on them
(e.g. reminder replies) decrypts in Python. Keep it that way when adding queries.

## Turning it on (new or existing install)

1. **Back up the database** first:
   ```bash
   mkdir -p backups
   docker compose exec -T postgres pg_dump -U postgres appointments | gzip > backups/before-encryption.sql.gz
   ```
   (`backups/` is git-ignored. This dump is **plaintext** — store it safely and
   delete it once you're confident.)
2. **Generate a key** and add it to `.env`:
   ```bash
   docker compose exec backend python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
   ```
   ```
   PATIENT_ENCRYPTION_KEY=<the 44-character key>
   ```
3. **Restart the services that write PII** so they load the key —
   backend and voice worker:
   ```bash
   docker compose up -d backend voice
   ```
   From now on every new booking, SMS log row and outbox message is encrypted.
4. **Encrypt rows stored before the key existed:**
   ```bash
   docker compose exec backend python -m scripts.encrypt_existing_pii --dry-run   # preview
   make encrypt-pii                                                               # do it
   ```
   Output looks like `appointments: 14/14 row(s) encrypted`. It is idempotent —
   running it again reports `0/N` and changes nothing.
5. **Check:**
   ```bash
   make verify          # the "PATIENT_ENCRYPTION_KEY is unset" warning is gone
   docker compose exec postgres psql -U postgres -d appointments \
     -c "select left(patient_mobile,12) from appointments limit 3"   # → gAAAAA…
   ```
   The admin console and patient portal should still show real names/numbers.

Until step 4 runs, old rows stay readable: `decrypt_field()` returns any value
that isn't ciphertext as-is, so a mixed table never breaks the app.

## Keeping the key safe

- **Back it up** outside the server (password manager / secrets vault).
  If the key is lost, encrypted data **cannot be recovered**.
- **Never change it** on a running install. Data encrypted with the old key
  would become unreadable (it would be shown as raw `gAAAAA…` text). There is
  no rotation tool yet; rotating means decrypting everything with the old key
  and re-encrypting with the new one in a maintenance window.
- `.env` is git-ignored — never commit the key, and don't paste it in chat or
  tickets.
- Every process that reads or writes PII needs the **same** key: the backend
  and the voice worker (both use `env_file: .env` in `docker-compose.yml`).
- Database backups contain ciphertext but are useless without the key, so
  store the key **separately** from the backups.

## Restoring a backup

Restore the dump as usual; the same `PATIENT_ENCRYPTION_KEY` must be in `.env`.
A pre-encryption (plaintext) dump can be restored and then re-encrypted with
`make encrypt-pii`.

## Troubleshooting

| Symptom | Cause / fix |
|---|---|
| Admin shows `gAAAAA…` instead of names | The running service has a different (or no) key from the one that encrypted the data. Restore the original key in `.env`, restart backend + voice. |
| `make encrypt-pii` says "PATIENT_ENCRYPTION_KEY is not set" | The backend container was started before the key was added — `docker compose up -d backend`. |
| Startup error `Fernet key must be 32 url-safe base64-encoded bytes` | The key was mistyped/truncated — it must be exactly the 44-character value generated above. |
