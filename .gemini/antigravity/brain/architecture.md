# Architecture Decision Records
Date: 2026-03-08
Owner: RapidXAI Agent
Status: Accepted

## Context
The project is a multilingual AI voice agent system built with LiveKit, FastAPI, and Postgres.

## Decisions
1. **Frontend**: Static HTML embedded in `ui_server.py` using Vanilla JS and Tailwind.
2. **Backend**: Python 3.10+, FastAPI for dashboard, LiveKit Workers for WebRTC.
3. **Database**: PostgreSQL (Supabase) via `psycopg2`.
4. **Security**: Hardened FastAPI middleware installed to meet AGENT_SOP.md Phase 6 standards. PII masked in agent logs.
5. **Skill Tandems Active**: `@senior-fullstack` + `@python-pro` + `@backend-security-coder`.
