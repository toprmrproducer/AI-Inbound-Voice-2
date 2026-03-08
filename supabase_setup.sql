-- Supabase Database Complete Setup (OG Architecture + UI/Campaigns)

-- 1. Agents Table
CREATE TABLE IF NOT EXISTS public.agents (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name TEXT NOT NULL DEFAULT 'AI Assistant',
    subtitle TEXT DEFAULT 'Voice Agent',
    agentinstructions TEXT DEFAULT '',
    openinggreeting TEXT DEFAULT '',
    firstline TEXT DEFAULT 'Namaste! How can I help you today?',
    llmprovider TEXT DEFAULT 'openai',
    llmmodel TEXT DEFAULT 'gpt-4.1-mini',
    ttsprovider TEXT DEFAULT 'sarvam',
    ttsvoice TEXT DEFAULT 'rohan',
    ttslanguage TEXT DEFAULT 'hi-IN',
    sttprovider TEXT DEFAULT 'sarvam',
    sttlanguage TEXT DEFAULT 'unknown',
    sttminendpointingdelay DOUBLE PRECISION DEFAULT 0.5,
    temperature DOUBLE PRECISION DEFAULT 0.4,
    max_tokens INTEGER DEFAULT 400,
    maxturns INTEGER DEFAULT 25,
    openrouterapikey TEXT DEFAULT '',
    anthropicapikey TEXT DEFAULT '',
    groqapikey TEXT DEFAULT '',
    is_inbound_active BOOLEAN DEFAULT FALSE,
    is_outbound_active BOOLEAN DEFAULT FALSE,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

-- Partial indexes to ensure only ONE active inbound and ONE active outbound agent at a time
CREATE UNIQUE INDEX IF NOT EXISTS ensure_one_active_inbound_agent ON public.agents (is_inbound_active) WHERE is_inbound_active = true;
CREATE UNIQUE INDEX IF NOT EXISTS ensure_one_active_outbound_agent ON public.agents (is_outbound_active) WHERE is_outbound_active = true;

-- 2. Call Logs Table (OG Schema)
CREATE TABLE IF NOT EXISTS public.call_logs (
    id                  UUID        DEFAULT gen_random_uuid() PRIMARY KEY,
    phone_number        TEXT,
    duration_seconds    INTEGER     DEFAULT 0,
    transcript          TEXT,
    summary             TEXT,
    recording_url       TEXT,
    caller_name         TEXT,
    sentiment           TEXT,
    estimated_cost_usd  NUMERIC(10,5),
    call_date           DATE,
    call_hour           INTEGER,
    call_day_of_week    TEXT,
    was_booked          BOOLEAN DEFAULT FALSE,
    interrupt_count     INTEGER DEFAULT 0,
    audio_codec         TEXT,
    created_at          TIMESTAMPTZ DEFAULT NOW()
);

-- Note: We check if the existing columns are there because some might have been added via ALTER in migrations
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_schema='public' AND table_name='call_logs' AND column_name='sentiment') THEN
        ALTER TABLE public.call_logs ADD COLUMN sentiment TEXT;
    END IF;
    IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_schema='public' AND table_name='call_logs' AND column_name='estimated_cost_usd') THEN
        ALTER TABLE public.call_logs ADD COLUMN estimated_cost_usd NUMERIC(10,5);
    END IF;
    IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_schema='public' AND table_name='call_logs' AND column_name='call_date') THEN
        ALTER TABLE public.call_logs ADD COLUMN call_date DATE;
    END IF;
    IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_schema='public' AND table_name='call_logs' AND column_name='call_hour') THEN
        ALTER TABLE public.call_logs ADD COLUMN call_hour INTEGER;
    END IF;
    IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_schema='public' AND table_name='call_logs' AND column_name='call_day_of_week') THEN
        ALTER TABLE public.call_logs ADD COLUMN call_day_of_week TEXT;
    END IF;
    IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_schema='public' AND table_name='call_logs' AND column_name='was_booked') THEN
        ALTER TABLE public.call_logs ADD COLUMN was_booked BOOLEAN DEFAULT FALSE;
    END IF;
    IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_schema='public' AND table_name='call_logs' AND column_name='interrupt_count') THEN
        ALTER TABLE public.call_logs ADD COLUMN interrupt_count INTEGER DEFAULT 0;
    END IF;
    IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_schema='public' AND table_name='call_logs' AND column_name='audio_codec') THEN
        ALTER TABLE public.call_logs ADD COLUMN audio_codec TEXT;
    END IF;
END $$;

CREATE INDEX IF NOT EXISTS idx_call_logs_phone_number ON public.call_logs (phone_number);
CREATE INDEX IF NOT EXISTS idx_call_logs_created_at ON public.call_logs (created_at DESC);


-- 3. Transcripts Table
CREATE TABLE IF NOT EXISTS public.call_transcripts (
    id           UUID        DEFAULT gen_random_uuid() PRIMARY KEY,
    call_room_id TEXT        NOT NULL,
    phone        TEXT,
    role         TEXT        CHECK (role IN ('user', 'assistant')),
    content      TEXT,
    created_at   TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_call_transcripts_room  ON public.call_transcripts (call_room_id);
CREATE INDEX IF NOT EXISTS idx_call_transcripts_phone ON public.call_transcripts (phone);


-- 4. Active Calls Tracking Table
CREATE TABLE IF NOT EXISTS public.active_calls (
    room_id      TEXT        PRIMARY KEY,
    phone        TEXT,
    caller_name  TEXT,
    status       TEXT        DEFAULT 'ringing',
    started_at   TIMESTAMPTZ DEFAULT NOW(),
    last_updated TIMESTAMPTZ DEFAULT NOW()
);


-- 5. Campaigns Table
CREATE TABLE IF NOT EXISTS public.campaigns (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    name TEXT NOT NULL,
    phone_numbers TEXT,
    sip_trunk_id TEXT,
    max_concurrent_calls INTEGER DEFAULT 5,
    notes TEXT,
    agent_id UUID REFERENCES public.agents(id) ON DELETE SET NULL,
    calls_per_minute INTEGER DEFAULT 5,
    retry_failed BOOLEAN DEFAULT true,
    max_retries INTEGER DEFAULT 2,
    status TEXT DEFAULT 'draft',
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);


-- 6. Campaign Numbers (Leads) Table 
CREATE TABLE IF NOT EXISTS public.campaign_numbers (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    campaign_id BIGINT REFERENCES public.campaigns(id) ON DELETE CASCADE,
    phone TEXT NOT NULL,
    name TEXT,
    email TEXT,
    status TEXT DEFAULT 'pending',
    custom_data JSONB DEFAULT '{}'::jsonb,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_campaign_numbers_campaign_id ON public.campaign_numbers (campaign_id);
CREATE INDEX IF NOT EXISTS idx_campaign_numbers_status_phone ON public.campaign_numbers (status, phone);


-- 7. Bookings Table
CREATE TABLE IF NOT EXISTS public.bookings (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    start_time TIMESTAMP WITH TIME ZONE NOT NULL,
    caller_name TEXT,
    caller_phone TEXT,
    caller_email TEXT,
    notes TEXT,
    status TEXT DEFAULT 'confirmed',
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);


-- 8. DNC (Do Not Call) List
CREATE TABLE IF NOT EXISTS public.dnc_list (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    phone TEXT UNIQUE NOT NULL,
    reason TEXT DEFAULT '',
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);


-- RLS Configuration
ALTER TABLE public.call_transcripts ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS "Allow anon insert transcripts" ON public.call_transcripts;
CREATE POLICY "Allow anon insert transcripts"
    ON public.call_transcripts FOR INSERT TO anon WITH CHECK (true);
DROP POLICY IF EXISTS "Allow anon select transcripts" ON public.call_transcripts;
CREATE POLICY "Allow anon select transcripts"
    ON public.call_transcripts FOR SELECT TO anon USING (true);

ALTER TABLE public.active_calls ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS "Allow anon all active_calls" ON public.active_calls;
CREATE POLICY "Allow anon all active_calls"
    ON public.active_calls FOR ALL TO anon USING (true) WITH CHECK (true);

-- Other tables RLS (deny anon, allow service role)
ALTER TABLE public.agents ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.campaigns ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.campaign_numbers ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.call_logs ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.bookings ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.dnc_list ENABLE ROW LEVEL SECURITY;
