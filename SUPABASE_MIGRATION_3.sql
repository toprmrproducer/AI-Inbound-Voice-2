-- Demo Links Table
CREATE TABLE IF NOT EXISTS demo_links (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    slug TEXT NOT NULL UNIQUE,
    label TEXT,
    language TEXT DEFAULT 'auto',
    total_sessions INTEGER DEFAULT 0,
    is_active BOOLEAN DEFAULT TRUE,
    created_at TIMESTAMPTZ DEFAULT now()
);
