import sys

errors = []
warnings = []

print("=== livekit-agents preflight check ===\n")

# 1. Check version
try:
    import livekit.agents as la
    ver = getattr(la, '__version__', 'unknown')
    print(f"✅ livekit.agents version: {ver}")
except Exception as e:
    errors.append(f"❌ livekit.agents import failed: {e}")

# 2. Check all imports used in agent.py
checks = [
    ("livekit.agents", "Agent"),
    ("livekit.agents", "AgentSession"),
    ("livekit.agents", "JobContext"),
    ("livekit.agents", "WorkerOptions"),
    ("livekit.agents", "RoomOptions"),          # 1.5+
    ("livekit.agents", "RoomInputOptions"),     # 1.4.x
    ("livekit.agents", "RoomOutputOptions"),    # 1.4.x
    ("livekit.agents.cli", "run"),              # does NOT exist
    ("livekit.agents.cli", "run_app"),          # correct in 1.4.x
    ("livekit.agents", "llm"),
]

for module, attr in checks:
    try:
        import importlib
        mod = importlib.import_module(module)
        val = getattr(mod, attr, None)
        if val:
            print(f"✅  {module}.{attr} — EXISTS")
        else:
            warnings.append(f"⚠️   {module}.{attr} — MISSING")
            print(f"⚠️   {module}.{attr} — MISSING")
    except Exception as e:
        errors.append(f"❌  {module}.{attr} — ERROR: {e}")
        print(f"❌  {module}.{attr} — ERROR: {e}")

# 3. Check AgentSession.start() signature
try:
    import inspect
    from livekit.agents import AgentSession
    sig = str(inspect.signature(AgentSession.start))
    print(f"\n✅  AgentSession.start signature: {sig}")
    if 'room_options' in sig:
        print("    → Use: room_options=")
    elif 'room_input_options' in sig:
        print("    → Use: room_input_options=")
    else:
        warnings.append("⚠️   AgentSession.start: unknown room options param name")
except Exception as e:
    errors.append(f"❌  AgentSession.start inspect failed: {e}")

# 4. Check JobContext for wait_for_disconnect
try:
    from livekit.agents import JobContext
    has_wait = hasattr(JobContext, 'wait_for_disconnect')
    if has_wait:
        print(f"✅  JobContext.wait_for_disconnect — EXISTS")
    else:
        warnings.append("⚠️   JobContext.wait_for_disconnect — MISSING, use event-based wait")
        print(f"⚠️   JobContext.wait_for_disconnect — MISSING")
except Exception as e:
    errors.append(f"❌  JobContext check failed: {e}")

# 5. Check Agent hooks
try:
    from livekit.agents import Agent
    has_on_enter           = hasattr(Agent, 'on_enter')
    has_on_user_turn       = hasattr(Agent, 'on_user_turn_completed')
    print(f"\n{'✅' if has_on_enter else '⚠️ '} Agent.on_enter — {'EXISTS' if has_on_enter else 'MISSING'}")
    print(f"{'✅' if has_on_user_turn else '⚠️ '} Agent.on_user_turn_completed — {'EXISTS' if has_on_user_turn else 'MISSING'}")
except Exception as e:
    errors.append(f"❌  Agent hooks check failed: {e}")

# 6. Check plugins
for plugin in ['livekit.plugins.openai', 'livekit.plugins.sarvam', 'livekit.plugins.silero']:
    try:
        import importlib
        importlib.import_module(plugin)
        print(f"✅  {plugin} — OK")
    except Exception as e:
        errors.append(f"❌  {plugin} — {e}")

# 7. Check calendar_tools for async_create_booking
try:
    import calendar_tools
    has_async = hasattr(calendar_tools, 'async_create_booking')
    print(f"\n{'✅' if has_async else '⚠️ '} calendar_tools.async_create_booking — {'EXISTS' if has_async else 'MISSING — add wrapper'}")
except Exception as e:
    errors.append(f"❌  calendar_tools import failed: {e}")

# 8. Check tiktoken
try:
    import tiktoken
    print(f"✅  tiktoken — OK")
except ImportError:
    warnings.append("⚠️   tiktoken not installed — token counting will be skipped (non-critical)")
    print(f"⚠️   tiktoken — not installed (non-critical)")

print("\n=== SUMMARY ===")
if errors:
    print(f"\n🔴 {len(errors)} ERRORS (will crash agent):")
    for e in errors: print(f"   {e}")
if warnings:
    print(f"\n🟡 {len(warnings)} WARNINGS (may cause issues):")
    for w in warnings: print(f"   {w}")
if not errors and not warnings:
    print("🟢 All checks passed — safe to deploy")
elif not errors:
    print("🟡 No blocking errors — warnings are non-critical")

sys.exit(1 if errors else 0)
