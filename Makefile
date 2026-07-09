.PHONY: up down logs migrate migrate-local create-admin shell-backend shell-db test test-frontend

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

shell-backend:
	docker compose exec backend bash

shell-db:
	docker compose exec postgres psql -U postgres -d appointments

test:
	.venv/bin/pytest tests/ -v

test-frontend:
	cd appointment-ui && npx vitest run
