-- ══════════════════════════════════════════════════════════════════════════════
-- SUPABASE MIGRATION — 40-Improvement Plan
-- Run each block in Supabase SQL Editor (Dashboard → SQL Editor)
-- ══════════════════════════════════════════════════════════════════════════════

-- #4 — Audio codec logging
ALTER TABLE call_logs ADD COLUMN IF NOT EXISTS audio_codec TEXT DEFAULT NULL;

-- #14 — Post-call sentiment
ALTER TABLE call_logs ADD COLUMN IF NOT EXISTS sentiment TEXT DEFAULT NULL;

-- #19 — Call duration analytics
ALTER TABLE call_logs ADD COLUMN IF NOT EXISTS call_date DATE DEFAULT NULL;
ALTER TABLE call_logs ADD COLUMN IF NOT EXISTS call_hour INTEGER DEFAULT NULL;
ALTER TABLE call_logs ADD COLUMN IF NOT EXISTS call_day_of_week TEXT DEFAULT NULL;
ALTER TABLE call_logs ADD COLUMN IF NOT EXISTS was_booked BOOLEAN DEFAULT FALSE;

-- #30 — Interrupt count
ALTER TABLE call_logs ADD COLUMN IF NOT EXISTS interrupt_count INTEGER DEFAULT 0;

-- #34 — Cost estimation
ALTER TABLE call_logs ADD COLUMN IF NOT EXISTS estimated_cost_usd NUMERIC(10,5) DEFAULT NULL;

-- #33 — Real-time transcript streaming (new table)
CREATE TABLE IF NOT EXISTS call_transcripts (
    id UUID DEFAULT gen_random_uuid() PRIMARY KEY,
    call_room_id TEXT NOT NULL,
    phone TEXT,
    role TEXT CHECK (role IN ('user', 'assistant')),
    content TEXT,
    created_at TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_call_transcripts_room ON call_transcripts (call_room_id);
CREATE INDEX IF NOT EXISTS idx_call_transcripts_phone ON call_transcripts (phone);

-- #38 — Active call monitoring (new table)
CREATE TABLE IF NOT EXISTS active_calls (
    room_id TEXT PRIMARY KEY,
    phone TEXT,
    caller_name TEXT,
    status TEXT DEFAULT 'ringing',
    started_at TIMESTAMPTZ DEFAULT NOW(),
    last_updated TIMESTAMPTZ DEFAULT NOW()
);

-- Useful index for dashboard queries
CREATE INDEX IF NOT EXISTS idx_call_logs_date ON call_logs (call_date);
CREATE INDEX IF NOT EXISTS idx_call_logs_booked ON call_logs (was_booked);
CREATE INDEX IF NOT EXISTS idx_call_logs_sentiment ON call_logs (sentiment);
