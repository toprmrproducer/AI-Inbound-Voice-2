-- ══════════════════════════════════════════════════════════════════════════════
-- SUPABASE MIGRATION 2 — Run in Supabase SQL Editor to add missing columns
-- Dashboard → SQL Editor → paste and run this entire block
-- ══════════════════════════════════════════════════════════════════════════════

-- Agent text fields
ALTER TABLE agents ADD COLUMN IF NOT EXISTS openinggreeting         TEXT DEFAULT '';
ALTER TABLE agents ADD COLUMN IF NOT EXISTS first_line              TEXT DEFAULT '';
ALTER TABLE agents ADD COLUMN IF NOT EXISTS agent_instructions      TEXT DEFAULT '';
ALTER TABLE agents ADD COLUMN IF NOT EXISTS system_prompt           TEXT DEFAULT '';

-- LLM provider
ALTER TABLE agents ADD COLUMN IF NOT EXISTS llm_provider            TEXT DEFAULT 'openai';
ALTER TABLE agents ADD COLUMN IF NOT EXISTS llm_model               TEXT DEFAULT 'gpt-4.1-mini';

-- Provider API keys
ALTER TABLE agents ADD COLUMN IF NOT EXISTS openrouter_api_key      TEXT DEFAULT '';
ALTER TABLE agents ADD COLUMN IF NOT EXISTS anthropic_api_key       TEXT DEFAULT '';
ALTER TABLE agents ADD COLUMN IF NOT EXISTS groq_api_key            TEXT DEFAULT '';

-- STT settings
ALTER TABLE agents ADD COLUMN IF NOT EXISTS stt_provider            TEXT DEFAULT 'sarvam';
ALTER TABLE agents ADD COLUMN IF NOT EXISTS stt_language            TEXT DEFAULT 'hi-IN';
ALTER TABLE agents ADD COLUMN IF NOT EXISTS stt_min_endpointing_delay FLOAT DEFAULT 0.5;

-- TTS settings
ALTER TABLE agents ADD COLUMN IF NOT EXISTS tts_provider            TEXT DEFAULT 'sarvam';
ALTER TABLE agents ADD COLUMN IF NOT EXISTS tts_voice               TEXT DEFAULT 'rohan';
ALTER TABLE agents ADD COLUMN IF NOT EXISTS tts_language            TEXT DEFAULT 'hi-IN';

-- Tuning
ALTER TABLE agents ADD COLUMN IF NOT EXISTS temperature             FLOAT DEFAULT 0.3;
ALTER TABLE agents ADD COLUMN IF NOT EXISTS max_tokens              INTEGER DEFAULT 400;
ALTER TABLE agents ADD COLUMN IF NOT EXISTS max_turns               INTEGER DEFAULT 25;

-- Phone number routing (array of phone numbers this agent handles)
ALTER TABLE agents ADD COLUMN IF NOT EXISTS phone_numbers           TEXT[] DEFAULT '{}';

-- Subtitle
ALTER TABLE agents ADD COLUMN IF NOT EXISTS subtitle                TEXT DEFAULT 'AI Assistant';

-- Verify columns were added
SELECT column_name, data_type, column_default
FROM information_schema.columns
WHERE table_name = 'agents'
ORDER BY ordinal_position;
