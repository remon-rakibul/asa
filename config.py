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

    # LLM provider for the agent: "ollama" (local) | "gemini" (cloud).
    # WARNING: gemini sends the full conversation (patient PII) to Google.
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
