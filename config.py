"""Application configuration loaded from environment / .env."""

import logging
import warnings
from functools import lru_cache

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

log = logging.getLogger(__name__)

_INSECURE_JWT_SECRET = "dev-insecure-change-me"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore"
    )

    # Postgres — used by both the appointment data layer and the LangGraph checkpointer.
    database_url: str = "postgresql://postgres:postgres@localhost:5432/appointments"

    # LLM provider for the agent — the single switch for local vs cloud:
    #   "ollama"     -> local Ollama (LOCAL LLM ON; default, no PII leaves the box)
    #   "openrouter" -> OpenRouter cloud (LOCAL LLM OFF; OpenAI-compatible API)
    #   "gemini"     -> Google Gemini cloud (LOCAL LLM OFF)
    # WARNING: any cloud provider sends the full conversation (patient PII)
    # off-box. Both cloud paths fall back to local Ollama if their key is unset.
    llm_provider: str = "ollama"

    # Ollama
    ollama_base_url: str = "http://localhost:11434"
    ollama_model: str = "gemma4:latest"
    ollama_temperature: float = 0.3
    # CPU threads for inference. 0 = let Ollama decide, but on hybrid Intel CPUs
    # Ollama counts only performance cores (2 on the pilot's Core Ultra 7 255U,
    # leaving 10 cores idle — 4.5x slower). Set to ~physical cores minus a couple
    # for the API/TTS/STT processes.
    ollama_num_thread: int = Field(0, ge=0)
    # Context window per request. Ollama's 4096 default silently truncates long
    # conversations (system prompt + tools ≈ 1.5k tokens already), which breaks
    # tool-calling; 8192 fits comfortably in RAM alongside the model.
    ollama_num_ctx: int = Field(8192, ge=2048)
    # Local embedding model for RAG (nomic-embed-text ships with Ollama pull).
    embedding_model: str = "nomic-embed-text"
    embedding_dims: int = 768  # nomic-embed-text output dimensions
    # RAG vector backend: "pgvector" (shared across workers; needs the pgvector
    # extension) | "chroma" (legacy local files; single-process only).
    rag_backend: str = "pgvector"

    # Gemini LLM (used when LLM_PROVIDER=gemini). Reuses gemini_api_key below.
    gemini_llm_model: str = "gemini-2.5-flash"
    gemini_temperature: float = 0.3

    # OpenRouter (used when LLM_PROVIDER=openrouter). OpenAI-compatible endpoint;
    # any OpenRouter model id works — swap OPENROUTER_MODEL to change it.
    openrouter_api_key: str = ""
    openrouter_base_url: str = "https://openrouter.ai/api/v1"
    openrouter_model: str = "google/gemma-4-31b-it"
    openrouter_temperature: float = 0.3

    # LiveKit
    livekit_url: str = "ws://localhost:7880"
    livekit_api_key: str = "devkey"
    livekit_api_secret: str = "devsecret"
    # Agent name used for explicit dispatch (must match @server.rtc_session in
    # main.py). The portal voice-token endpoint dispatches this agent into the
    # patient's browser room.
    livekit_agent_name: str = "appointment-setter"
    # Optional: apply LiveKit voice-isolation noise cancellation to the call input
    # (clearer STT in noisy clinics). Requires the livekit-plugins-noise-cancellation
    # package and is a LiveKit Cloud feature with possible usage cost — off by default.
    voice_noise_cancellation: bool = False
    # Voice worker load threshold (0-1): CPU utilisation above which this worker
    # stops accepting new calls (LiveKit dispatches them to another replica).
    # Default inf = never refuse: on a single-worker box there is no other
    # replica, and the LLM pegs the CPU during prefill — a finite threshold
    # makes calls hang at "connecting" whenever the box is busy. Set e.g. 0.7
    # via VOICE_LOAD_THRESHOLD only when running multiple worker replicas.
    voice_load_threshold: float = float("inf")
    # Scope for inbound voice calls that match no channels row (the platform
    # number, dispatched per-caller, never matches one):
    #   "platform"       -> the cross-hospital marketplace agent (search any
    #                       hospital, RAG over any uploaded knowledgebase,
    #                       book anywhere) — the unified-platform default.
    #   "default_clinic" -> legacy single-clinic behavior (slug='default').
    voice_fallback_scope: str = "platform"
    # Gate platform-number calls to premium/trial patients matched by SIP
    # caller-ID against their ONE-TIME OTP-verified phone number. Unknown /
    # unverified / free-tier / hidden-number callers get a polite spoken
    # decline + an SMS upgrade link, then the call ends. Set false for local
    # `main.py console` testing (console has no caller-ID and would be
    # declined). Only applies to the platform fallback — hospital/clinic
    # DIDs mapped in `channels` are never gated.
    voice_premium_gate: bool = True

    # LiveKit Inference voice service (used when STT_ENGINE/TTS_ENGINE = "livekit").
    # No separate provider keys needed — billed through your LiveKit Cloud account.
    # WARNING: patient audio/text leave the box (same PII trade-off as other cloud).
    livekit_stt_model: str = "speechmatics/enhanced"  # 61 langs incl. Bengali
    livekit_tts_model: str = "cartesia/sonic-3"       # supports bn
    livekit_tts_voice: str = ""                        # optional provider voice id

    # TTS engine for the voice worker:
    #   "gemini" -> Google Gemini cloud TTS      (natural; auto-falls back to MMS)
    #   "mms"    -> facebook/mms-tts-ben          (local neural VITS, fast on CPU)
    #   "espeak" -> espeak-ng                     (robotic, zero download)
    #   "parler" -> ai4bharat/indic-parler-tts    (very natural but unusable on CPU)
    tts_engine: str = "mms"
    tts_fast_engine: str = "mms"
    tts_quality_timeout_seconds: float = 6.0
    tts_voice_description: str = ""
    # Pitch shift in semitones applied to MMS-TTS output (negative = lower/deeper
    # voice, 0 = unchanged). Tempo is preserved (phase-vocoder via librosa).
    tts_pitch_semitones: float = 0.0

    # Gemini cloud TTS (used when TTS_ENGINE=gemini). Falls back to local MMS when
    # the key is missing or a call times out / errors.
    # WARNING: sends the agent's outgoing text (may contain patient PII) to Google.
    gemini_api_key: str = ""
    gemini_tts_model: str = "gemini-2.5-flash-preview-tts"
    gemini_tts_voice: str = "Kore"

    # STT engine for the voice worker: "whisper" (local) | "gemini" (cloud).
    # WARNING: gemini sends the patient's recorded audio (PII) to Google.
    stt_engine: str = "whisper"
    # STT (faster-whisper)
    whisper_model: str = "medium"
    stt_language: str = "bn"
    whisper_device: str = "cpu"
    # Gemini STT (used when STT_ENGINE=gemini). Reuses gemini_api_key.
    gemini_stt_model: str = "gemini-2.5-flash"

    # Clinic / doctor branding (global fallbacks for unscoped sessions)
    clinic_name: str = "Clinic"
    # Marketplace brand the platform-wide assistant speaks for (portal home
    # chat/voice with no hospital chosen). Set PLATFORM_NAME in .env — the
    # agent SAYS this name aloud on voice calls, so a Bangla spelling
    # (e.g. "আসা") may TTS better than the latin "ASA".
    platform_name: str = "ASA"
    doctor_name: str = "Doctor"
    doctor_phone: str = ""

    # Twilio SMS
    twilio_account_sid: str = ""
    twilio_auth_token: str = ""
    twilio_from_number: str = ""

    # Authentication (admin API)
    api_key: str = ""
    jwt_secret: str = _INSECURE_JWT_SECRET   # MUST override in production
    jwt_ttl_hours: int = 12

    # Platform admin key — required to create new tenants (POST /clinics).
    # Generate a strong random string; keep it out of source control.
    # Empty = clinic creation disabled until configured.
    platform_admin_key: str = ""

    # Payments (patient booking fees + patient/hospital subscriptions).
    # "manual" needs no gateway account — good for local dev / early pilots
    # (either auto-confirms, or shows a portal page with bKash/Nagad
    # instructions that a platform admin marks paid by hand). "sslcommerz"
    # is a free-sandbox BD gateway aggregating bKash/Nagad/cards.
    payment_provider: str = "manual"        # "manual" | "sslcommerz"
    payment_manual_autopay: bool = True     # manual provider: skip the pay step entirely
    booking_fee_default: int = 0            # ৳ platform default when a hospital sets none (0 = free)
    payment_ttl_minutes: int = 15           # how long a pending_payment slot hold survives
    patient_subscription_fee: int = 99      # ৳ per month
    patient_trial_days: int = 30            # full-access free trial a new patient gets on signup
    patient_subscription_days: int = 30     # premium horizon added per paid subscription period
    free_agent_bookings_per_month: int = 3  # AI chat/voice bookings before the free tier is capped
    free_history_limit: int = 3             # visible past appointments for free-tier patients
    hospital_subscription_fee: int = 999    # ৳ per month, default for a newly self-signed-up hospital
    hospital_trial_days: int = 30           # free period a hospital gets on signup before billing starts
    hospital_billing_grace_days: int = 7    # past_due window before a lapsed hospital is hidden ("suspended")

    # Hospital prepaid credit wallet (pass-through usage metering). Ships OFF:
    # when disabled every charge/credit/sweep call is a no-op, so nothing meters
    # until it is deliberately enabled per rollout.
    credits_enabled: bool = False
    credit_cost_booking: int = 5            # credits drawn per confirmed booking
    credit_cost_sms: int = 1               # credits per SMS sent
    credit_cost_voice_per_min: int = 2     # credits per voice minute (rounded up)
    credit_cost_whatsapp: int = 1          # credits per WhatsApp message sent
    default_credit_rate_bdt: float = 20.0  # ৳ per credit for a new wallet (per-hospital, overridable)
    wallet_debt_suspend_credits: int = 100  # hide the hospital once balance < -this
    wallet_low_balance_credits: int = 20    # warn threshold (UI banner / nudge)
    # Cost-estimate knobs — what each channel event actually costs US (telco /
    # Meta / gateway). Superadmin-only: drive the profit/margin dashboard,
    # never patient-facing. margin = revenue − channel cost − gateway fees.
    cost_sms_bdt: float = 0.35
    cost_voice_min_bdt: float = 1.5
    cost_whatsapp_bdt: float = 0.6
    gateway_fee_pct: float = 0.02          # SSLCommerz's cut of gross revenue

    sslcommerz_store_id: str = ""
    sslcommerz_store_passwd: str = ""
    sslcommerz_sandbox: bool = True
    # Public URLs the payment gateway redirects/IPNs back to — must be
    # reachable from the internet in production (a tunnel in dev).
    public_base_url: str = "http://localhost:8000"
    portal_base_url: str = "http://localhost:3000"

    # Booking behaviour
    availability_days_ahead: int = 7

    # Resilience / observability
    # Per-LLM-call budget. Must survive the worst realistic case on a CPU-only
    # box: cold model load + full prompt prefill on a long thread (live-observed
    # >360s on the first turn after a restart).
    ollama_timeout_seconds: float = 720.0
    # Budget for the last-resort recovery reply composed after a turn already
    # failed — fail fast here; the patient has waited long enough.
    recovery_reply_timeout_seconds: float = 90.0
    ollama_max_retries: int = Field(2, ge=0)
    ollama_max_concurrent: int = 4     # semaphore for concurrent LLM calls
    log_level: str = "INFO"
    log_json: bool = False
    sentry_dsn: str = ""

    # CORS (comma-separated origins for the admin UI)
    cors_origins: str = "http://localhost:3000,http://127.0.0.1:3000"

    # Rate limiting (per-process; back with Redis for multi-worker deployments).
    patient_rate_limit_per_min: int = 60
    admin_login_rate_limit_per_min: int = 10   # brute-force protection on /auth/login
    # Set True when the app runs behind a reverse proxy / load balancer (the
    # production norm — HTTPS termination). Then the client IP used for rate
    # limiting and the audit trail is taken from the X-Forwarded-For header the
    # proxy appends, instead of request.client.host (which would be the PROXY's
    # IP — collapsing every client into one rate-limit bucket and causing
    # false 429 lockouts for everyone). Keep False on direct-to-internet
    # deployments: an untrusted client can forge X-Forwarded-For to dodge limits.
    trust_proxy_headers: bool = False

    # WhatsApp (Meta Cloud API)
    whatsapp_token: str = ""
    whatsapp_phone_id: str = ""
    whatsapp_verify_token: str = ""
    # App secret for webhook HMAC-SHA256 (X-Hub-Signature-256).
    # Find it in Meta App Dashboard → App Settings → Basic → App Secret.
    whatsapp_app_secret: str = ""

    # SMS provider
    sms_provider: str = "twilio"          # "twilio" | "bdgateway" | "none"
    bd_sms_api_url: str = ""
    bd_sms_api_key: str = ""
    bd_sms_sender_id: str = ""

    # PII field-level encryption (Fernet symmetric key).
    # Generate: python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
    # Empty = no encryption (dev); MUST be set for hospital/bank deployments.
    patient_encryption_key: str = ""

    # HA / scalability
    redis_url: str = ""          # empty = in-process rate-limiter (single worker only)
    db_pool_min: int = 2
    db_pool_max: int = 20

    # Strict channel routing: reject inbound messages for unknown channel mappings
    # rather than silently falling back to the default clinic.
    # Recommended True for multi-tenant hospital/bank deployments.
    strict_channel_routing: bool = False

    # HIS / EMR integration
    his_webhook_secret: str = ""

    @model_validator(mode="after")
    def _warn_insecure_defaults(self) -> "Settings":
        if self.jwt_secret == _INSECURE_JWT_SECRET:
            warnings.warn(
                "JWT_SECRET is the insecure default — set a strong random value "
                "in .env before deploying to production.",
                stacklevel=2,
            )
        if self.livekit_api_key == "devkey" or self.livekit_api_secret == "devsecret":
            warnings.warn(
                "LIVEKIT_API_KEY / LIVEKIT_API_SECRET are insecure defaults — "
                "set production values in .env before deploying.",
                stacklevel=2,
            )
        if not self.patient_encryption_key:
            log.debug("PATIENT_ENCRYPTION_KEY not set — patient PII stored in plaintext.")
        return self

    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]


@lru_cache
def get_settings() -> Settings:
    """Cached singleton so .env is parsed once per process."""
    return Settings()


settings = get_settings()
