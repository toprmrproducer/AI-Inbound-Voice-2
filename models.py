# models.py
from dataclasses import dataclass, field
from typing import Optional

@dataclass
class AgentConfig:
    # Identity
    id: Optional[str] = None
    name: str = 'Default Agent'
    subtitle: str = ''

    # Prompt & greeting
    system_prompt: str = ''
    opening_greeting: str = ''
    first_line: str = ''

    # LLM
    llm_provider: str = 'openai'      # 'openai' | 'groq' | 'openrouter' | 'anthropic'
    llm_model: str = 'gpt-4.1-mini'
    llm_base_url: Optional[str] = None  # for OpenRouter / LiteLLM proxy
    temperature: float = 0.4
    max_tokens: int = 400
    max_turns: int = 25

    # TTS
    tts_provider: str = 'sarvam'
    tts_model: str = 'bulbul:v3'
    tts_voice: str = 'rohan'
    tts_language: str = 'hi-IN'

    # STT
    stt_provider: str = 'sarvam'
    stt_model: str = 'saaras:v3'
    stt_language: str = 'unknown'
    stt_min_endpointing_delay: float = 0.5

    # Flags
    is_inbound_active: bool = False
    is_outbound_active: bool = False
