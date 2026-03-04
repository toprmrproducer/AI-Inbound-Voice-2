-- =====================================================
-- RapidXAI Voice Agent — Full Schema Migration
-- Run this ONCE on your Postgres VPS before deploying
-- =====================================================
-- Connect: psql "postgres://postgres:PASSWORD@HOST:5432/postgres"
-- =====================================================

-- Drop old tables cleanly (use CASCADE to handle FK deps)
DROP TABLE IF EXISTS campaign_leads   CASCADE;
DROP TABLE IF EXISTS campaigns        CASCADE;
DROP TABLE IF EXISTS voice_agent_configs CASCADE;
DROP TABLE IF EXISTS sip_trunks       CASCADE;
DROP TABLE IF EXISTS dnc_list         CASCADE;
DROP TABLE IF EXISTS transcript_lines CASCADE;
DROP TABLE IF EXISTS call_transcripts CASCADE;
DROP TABLE IF EXISTS call_logs        CASCADE;
DROP TABLE IF EXISTS crm_contacts     CASCADE;
DROP TABLE IF EXISTS demo_links       CASCADE;
DROP TABLE IF EXISTS agents           CASCADE;

-- ── CALL LOGS ────────────────────────────────────────
CREATE TABLE call_logs (
    id                 UUID         PRIMARY KEY DEFAULT gen_random_uuid(),
    created_at         TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    phone              TEXT         NOT NULL DEFAULT 'unknown',
    caller_name        TEXT         DEFAULT '',
    duration           INTEGER      DEFAULT 0,
    call_type          TEXT         DEFAULT 'inbound',
    call_date          DATE         DEFAULT CURRENT_DATE,
    call_hour          INTEGER      DEFAULT 0,
    call_day_of_week   TEXT         DEFAULT '',
    was_booked         BOOLEAN      DEFAULT false,
    sentiment          TEXT         DEFAULT 'unknown',
    summary            TEXT         DEFAULT '',
    transcript         TEXT         DEFAULT '',
    recording_url      TEXT         DEFAULT '',
    estimated_cost_usd FLOAT        DEFAULT 0.0,
    stt_provider       TEXT         DEFAULT 'sarvam',
    tts_provider       TEXT         DEFAULT 'sarvam',
    llm_model          TEXT         DEFAULT 'gpt-4o-mini',
    cli_used           TEXT         DEFAULT '',
    interrupt_count    INTEGER      DEFAULT 0,
    campaign_id        UUID         DEFAULT NULL,
    agent_config_id    UUID         DEFAULT NULL
);

-- ── TRANSCRIPT LINES ─────────────────────────────────
CREATE TABLE transcript_lines (
    id         UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    room_id    TEXT        NOT NULL,
    phone      TEXT        NOT NULL DEFAULT 'unknown',
    role       TEXT        NOT NULL DEFAULT 'agent',
    content    TEXT        NOT NULL DEFAULT ''
);

-- ── DEMO LINKS ────────────────────────────────────────
CREATE TABLE demo_links (
    id             SERIAL      PRIMARY KEY,
    slug           TEXT        UNIQUE NOT NULL,
    label          TEXT,
    language       TEXT        DEFAULT 'auto',
    created_at     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    is_active      BOOLEAN     DEFAULT TRUE,
    total_sessions INTEGER     DEFAULT 0
);

-- ── CRM CONTACTS ──────────────────────────────────────
CREATE TABLE crm_contacts (
    id          UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    phone       TEXT        UNIQUE NOT NULL,
    name        TEXT        DEFAULT '',
    email       TEXT        DEFAULT '',
    last_call   TIMESTAMPTZ,
    total_calls INTEGER     DEFAULT 0,
    was_booked  BOOLEAN     DEFAULT false,
    notes       TEXT        DEFAULT ''
);

-- ── MASS CALLING: SIP TRUNKS ─────────────────────────
CREATE TABLE sip_trunks (
    id                         UUID    PRIMARY KEY DEFAULT gen_random_uuid(),
    created_at                 TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    name                       TEXT    NOT NULL,
    provider                   TEXT    NOT NULL DEFAULT 'vobiz',
    trunk_type                 TEXT    NOT NULL DEFAULT 'outbound',
    sip_address                TEXT    NOT NULL DEFAULT '',
    auth_username              TEXT    DEFAULT '',
    auth_password              TEXT    DEFAULT '',
    number_pool                JSONB   DEFAULT '[]'::JSONB,
    livekit_trunk_id           TEXT    DEFAULT '',
    max_concurrent_calls       INT     DEFAULT 10,
    max_calls_per_number_per_day INT   DEFAULT 150,
    is_active                  BOOLEAN DEFAULT TRUE,
    notes                      TEXT    DEFAULT ''
);

-- ── MASS CALLING: AGENT CONFIGS ─────────────────────
CREATE TABLE voice_agent_configs (
    id                     UUID    PRIMARY KEY DEFAULT gen_random_uuid(),
    created_at             TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    name                   TEXT    NOT NULL,
    preset_type            TEXT    NOT NULL DEFAULT 'custom',
    sip_trunk_id           UUID    REFERENCES sip_trunks(id) ON DELETE SET NULL,
    cli_override           TEXT    DEFAULT '',
    llm_model              TEXT    DEFAULT 'gpt-4o-mini',
    llm_provider           TEXT    DEFAULT 'openai',
    tts_provider           TEXT    DEFAULT 'sarvam',
    tts_voice              TEXT    DEFAULT 'rohan',
    tts_language           TEXT    DEFAULT 'hi-IN',
    stt_provider           TEXT    DEFAULT 'sarvam',
    stt_language           TEXT    DEFAULT 'hi-IN',
    agent_instructions     TEXT    DEFAULT '',
    first_line             TEXT    DEFAULT '',
    max_call_duration_seconds INT  DEFAULT 300,
    max_turns              INT     DEFAULT 25,
    call_window_start      TIME    DEFAULT '09:30',
    call_window_end        TIME    DEFAULT '19:30',
    timezone               TEXT    DEFAULT 'Asia/Kolkata',
    is_active              BOOLEAN DEFAULT TRUE
);

-- ── MASS CALLING: CAMPAIGNS ──────────────────────────
CREATE TABLE campaigns (
    id                  UUID    PRIMARY KEY DEFAULT gen_random_uuid(),
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    name                TEXT    NOT NULL,
    status              TEXT    DEFAULT 'draft',
    agent_config_id     UUID    REFERENCES voice_agent_configs(id) ON DELETE SET NULL,
    sip_trunk_id        UUID    REFERENCES sip_trunks(id) ON DELETE SET NULL,
    max_calls_per_minute INT    DEFAULT 5,
    max_retries_per_lead INT    DEFAULT 2,
    retry_delay_hours   INT     DEFAULT 4,
    daily_start_time    TIME    DEFAULT '09:30',
    daily_end_time      TIME    DEFAULT '19:30',
    timezone            TEXT    DEFAULT 'Asia/Kolkata',
    total_leads         INT     DEFAULT 0,
    called_count        INT     DEFAULT 0,
    answered_count      INT     DEFAULT 0,
    booked_count        INT     DEFAULT 0,
    notes               TEXT    DEFAULT '',
    completed_at        TIMESTAMPTZ
);

-- ── MASS CALLING: LEADS ──────────────────────────────
CREATE TABLE campaign_leads (
    id               UUID    PRIMARY KEY DEFAULT gen_random_uuid(),
    created_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    campaign_id      UUID    REFERENCES campaigns(id) ON DELETE CASCADE,
    phone            TEXT    NOT NULL,
    name             TEXT    DEFAULT '',
    email            TEXT    DEFAULT '',
    custom_data      JSONB   DEFAULT '{}'::JSONB,
    status           TEXT    DEFAULT 'pending',
    attempts         INT     DEFAULT 0,
    last_attempt_at  TIMESTAMPTZ,
    last_result      TEXT    DEFAULT '',
    livekit_room_id  TEXT    DEFAULT '',
    call_duration_seconds INT DEFAULT 0,
    booked           BOOLEAN DEFAULT FALSE,
    notes            TEXT    DEFAULT ''
);

-- ── MASS CALLING: DNC LIST ───────────────────────────
CREATE TABLE dnc_list (
    id         UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    phone      TEXT        UNIQUE NOT NULL,
    reason     TEXT        DEFAULT 'manual',
    source     TEXT        DEFAULT 'dashboard'
);

-- ── INDEXES ──────────────────────────────────────────
CREATE INDEX idx_call_logs_created_at  ON call_logs(created_at DESC);
CREATE INDEX idx_call_logs_phone       ON call_logs(phone);
CREATE INDEX idx_call_logs_call_date   ON call_logs(call_date);
CREATE INDEX idx_call_logs_campaign    ON call_logs(campaign_id);
CREATE INDEX idx_transcript_room       ON transcript_lines(room_id, created_at);
CREATE INDEX idx_crm_phone             ON crm_contacts(phone);
CREATE INDEX idx_demo_slug             ON demo_links(slug);
CREATE INDEX idx_dnc_phone             ON dnc_list(phone);
CREATE INDEX idx_leads_campaign_status ON campaign_leads(campaign_id, status);

-- ── VERIFY ───────────────────────────────────────────
SELECT table_name
FROM information_schema.tables
WHERE table_schema = 'public'
ORDER BY table_name;
