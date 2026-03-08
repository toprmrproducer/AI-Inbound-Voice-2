-- ══════════════════════════════════════════════════════════════════════════════
-- SUPABASE MIGRATION 4 — Run in Supabase SQL Editor
-- Dashboard → SQL Editor → paste and run this entire block
-- ══════════════════════════════════════════════════════════════════════════════

-- 9. SIP Trunks Table
CREATE TABLE IF NOT EXISTS public.sip_trunks (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    name TEXT NOT NULL,
    provider TEXT NOT NULL,
    sip_uri TEXT NOT NULL,
    username TEXT,
    password TEXT,
    caller_id_number TEXT,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

-- RLS for SIP Trunks (service role only)
ALTER TABLE public.sip_trunks ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS "Deny anon sip_trunks" ON public.sip_trunks;
CREATE POLICY "Deny anon sip_trunks" ON public.sip_trunks FOR ALL TO anon USING (false);
