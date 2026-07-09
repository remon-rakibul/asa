Building an AI appointment agent for doctors' clinics — solo.

Patients call or message. The agent handles the entire booking flow. No receptionist required.

**What's built:**
- Multi-tenant architecture with isolated clinic workspaces
- WhatsApp, SMS, and voice channel support
- JWT auth, RBAC, and full audit logging
- Admin console for clinic and availability management

Currently integrating real-time voice — live STT/TTS in an active phone call is a different class of engineering problem. Latency, reliability, and natural conversation flow all have to work together.

Stack: Python · FastAPI · PostgreSQL · asyncpg · Alembic · LiveKit

Open to collaborators who want to work on voice AI or real-time systems in a real product context. Also open to conversations with engineering teams building in this space.

DM me if that's you.

#VoiceAI #HealthTech #Python #FastAPI #BuildInPublic
