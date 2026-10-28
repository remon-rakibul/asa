.PHONY: up down logs migrate migrate-local create-admin shell-backend shell-db test test-frontend verify tunnel tunnel-down tunnel-logs tunnel-status

up:
	docker compose up --build -d

down:
	docker compose down

logs:
	docker compose logs -f

# Apply DB migrations (Alembic is the single source of truth) inside the backend container.
migrate:
	docker compose exec backend alembic upgrade head

# Apply migrations against your local DATABASE_URL (no docker).
migrate-local:
	.venv/bin/alembic upgrade head

# Create an admin login. Example: make create-admin EMAIL=a@b.bd PASS=secret123
create-admin:
	.venv/bin/python -m scripts.create_admin --email $(EMAIL) --password $(PASS)

# Live-check every integration (LLM, DB, LiveKit/SIP, SMS, WhatsApp, payments).
# Exits non-zero if a configured integration is failing.
verify:
	docker compose exec backend python -m scripts.verify_integrations

shell-backend:
	docker compose exec backend bash

shell-db:
	docker compose exec postgres psql -U postgres -d appointments

test:
	.venv/bin/pytest tests/ -v

test-frontend:
	cd appointment-ui && npx vitest run

# ── Webhook tunnel (ngrok in Docker) — see docs/NGROK_TUNNEL.md ─────────────
# Exposes the backend at https://$NGROK_DOMAIN so Meta can deliver WhatsApp
# webhooks. Stop any host `ngrok http` first: one domain = one live tunnel.
tunnel:
	docker compose --profile tunnel up -d ngrok
	@$(MAKE) --no-print-directory tunnel-status

tunnel-down:
	docker compose --profile tunnel stop ngrok
	docker compose --profile tunnel rm -f ngrok

tunnel-logs:
	docker compose --profile tunnel logs -f ngrok

# Prints the public URL and checks the WhatsApp webhook answers through it.
tunnel-status:
	@NGROK_DOMAIN=$$(grep -E '^NGROK_DOMAIN=' .env | cut -d= -f2- | sed 's/[[:space:]]*#.*//;s/[[:space:]]*$$//'); \
	NGROK_INSPECT_PORT=$$(grep -E '^NGROK_INSPECT_PORT=' .env | cut -d= -f2- | sed 's/[[:space:]]*#.*//;s/[[:space:]]*$$//'); \
	echo "Public URL:  https://$$NGROK_DOMAIN"; \
	echo "Webhook:     https://$$NGROK_DOMAIN/whatsapp/webhook"; \
	echo "Inspector:   http://127.0.0.1:$${NGROK_INSPECT_PORT:-4040}"; \
	for i in 1 2 3 4 5 6 7 8 9 10; do \
	  code=$$(curl -s -o /dev/null -w '%{http_code}' -H 'ngrok-skip-browser-warning: 1' "https://$$NGROK_DOMAIN/health/live"); \
	  [ "$$code" = 200 ] && { echo "Tunnel:      UP (backend reachable)"; exit 0; }; sleep 2; \
	done; echo "Tunnel:      DOWN (last HTTP $$code) — run: make tunnel-logs"; exit 1
