"""
presets.py
Built-in calling presets for Insurance, Inquiry, HR, Appointment, Survey.
Each preset maps directly to a voice_agent_configs row.
"""

CALLING_PRESETS = {

    "insurance": {
        "name": "Insurance Call",
        "preset_type": "insurance",
        "tts_voice": "rohan",
        "tts_language": "hi-IN",
        "stt_language": "hi-IN",
        "llm_model": "gpt-4o-mini",
        "max_call_duration_seconds": 300,
        "max_turns": 25,
        "call_window_start": "10:00",
        "call_window_end": "18:00",
        "first_line": "Namaste! Main aapko ek important insurance plan ke baare mein baat karna chahta hoon. Kya aap 2 minute de sakte hain?",
        "agent_instructions": """You are a polite insurance advisor making an outbound call.
Goal: Explain 1 key benefit, capture interest, offer callback with a human advisor.

RULES:
- ONE sentence per turn, max 15 words
- Never pressure or repeat more than twice
- If 'not interested': 'Thank you, have a good day.' then end call
- If 'remove from list': confirm and mark DNC
- Identify yourself and company at start
- Offer callback with human if they want more details""",
    },

    "inquiry": {
        "name": "Inquiry Follow-Up",
        "preset_type": "inquiry",
        "tts_voice": "anushka",
        "tts_language": "en-IN",
        "stt_language": "en-IN",
        "llm_model": "gpt-4o-mini",
        "max_call_duration_seconds": 240,
        "max_turns": 20,
        "call_window_start": "09:30",
        "call_window_end": "18:30",
        "first_line": "Hello! This is a follow-up call regarding your recent inquiry with us. Is this a good time to talk?",
        "agent_instructions": """You are a helpful support agent following up on a customer inquiry.
Goal: Understand the inquiry, answer questions, resolve concern or schedule human callback.

RULES:
- Speak in clear English, one sentence at a time
- If they need a human: collect preferred callback time
- Be concise and empathetic
- Max 2 questions before offering a human callback""",
    },

    "hr": {
        "name": "HR Screening Call",
        "preset_type": "hr",
        "tts_voice": "anushka",
        "tts_language": "en-IN",
        "stt_language": "en-IN",
        "llm_model": "gpt-4o-mini",
        "max_call_duration_seconds": 420,
        "max_turns": 30,
        "call_window_start": "10:00",
        "call_window_end": "17:30",
        "first_line": "Hello! I'm calling from the HR team regarding your job application. Do you have a few minutes for a quick screening call?",
        "agent_instructions": """You are an HR screening assistant conducting a preliminary phone screen.
Goal: Ask 5 key questions, note answers, inform candidate of next steps.

SCREENING QUESTIONS (ask one at a time, in order):
1. Can you briefly describe your current role and experience?
2. What is your current and expected CTC?
3. What is your notice period?
4. Are you comfortable with the work location?
5. Why are you looking for a change?

RULES:
- One question at a time, wait for full answer
- Be warm and professional
- If unavailable: ask for best callback time, note it
- End: 'Thank you. HR will follow up within 2-3 working days.'""",
    },

    "appointment": {
        "name": "Appointment Reminder",
        "preset_type": "appointment",
        "tts_voice": "kavya",
        "tts_language": "hi-IN",
        "stt_language": "hi-IN",
        "llm_model": "gpt-4o-mini",
        "max_call_duration_seconds": 120,
        "max_turns": 10,
        "call_window_start": "09:00",
        "call_window_end": "18:00",
        "first_line": "Namaste! Main aapko aapki upcoming appointment ki yaad dilaane ke liye call kar raha hoon.",
        "agent_instructions": """You are an appointment reminder assistant.
Goal: Remind about appointment, confirm attendance, offer rescheduling.

RULES:
- Keep it under 4 exchanges
- If reschedule needed: 'A team member will call you back to fix a new time.'
- If confirmed: 'Great! See you then. Have a good day.'
- Be brief and warm""",
    },

    "survey": {
        "name": "Customer Survey",
        "preset_type": "survey",
        "tts_voice": "anushka",
        "tts_language": "en-IN",
        "stt_language": "en-IN",
        "llm_model": "gpt-4o-mini",
        "max_call_duration_seconds": 180,
        "max_turns": 15,
        "call_window_start": "11:00",
        "call_window_end": "19:00",
        "first_line": "Hi! I'm calling to get 2 minutes of your time for a quick satisfaction survey. Would that be okay?",
        "agent_instructions": """You are conducting a short customer satisfaction survey.
Goal: Ask 4 questions, record answers, thank the customer.

QUESTIONS (one at a time):
1. On a scale of 1 to 5, how satisfied are you with our service?
2. Was your issue resolved to your satisfaction?
3. Would you recommend us to a friend?
4. Any suggestions for us to improve?

RULES:
- Accept any answer without argument
- If they decline: 'No problem, thank you for your time.' then end
- Thank warmly at end""",
    },
}
