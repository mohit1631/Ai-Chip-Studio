from __future__ import annotations
from app.config import settings

# True when call_ai() would just return the "[MOCK] ..." placeholder
# string rather than hitting a real model -- i.e. ai_provider isn't one of
# the supported providers, or it is but the matching API key isn't set.
# app/services/ai_lint.py (and anything else that wants to skip a doomed
# network call and go straight to a deterministic fallback) checks this
# instead of duplicating the same provider/key logic as call_ai() below.
MOCK_MODE = not (
    (settings.ai_provider == "gemini" and settings.gemini_api_key)
    or (settings.ai_provider == "anthropic" and settings.anthropic_api_key)
)


def call_ai(system_prompt: str, user_prompt: str, max_tokens: int = 2000) -> str:
    provider = getattr(settings, 'ai_provider', 'mock').lower()
    if provider == "gemini":
        return _call_gemini(system_prompt, user_prompt, max_tokens)
    elif provider == "anthropic":
        return _call_anthropic(system_prompt, user_prompt, max_tokens)
    return "[MOCK] AICHIP_AI_PROVIDER=gemini set karo"

def _call_gemini(system_prompt, user_prompt, max_tokens):
    import google.generativeai as genai
    api_key = getattr(settings, 'gemini_api_key', None)
    if not api_key:
        raise RuntimeError("AICHIP_GEMINI_API_KEY set karo!")
    genai.configure(api_key=api_key)
    model = genai.GenerativeModel(
        model_name=getattr(settings, 'gemini_model', 'gemini-2.0-flash'),
        system_instruction=system_prompt,
        generation_config=genai.types.GenerationConfig(max_output_tokens=max_tokens, temperature=0.2)
    )
    return model.generate_content(user_prompt).text

def _call_anthropic(system_prompt, user_prompt, max_tokens):
    import anthropic
    client = anthropic.Anthropic(api_key=settings.anthropic_api_key)
    response = client.messages.create(model=settings.ai_model, max_tokens=max_tokens,
        system=system_prompt, messages=[{"role": "user", "content": user_prompt}])
    return "".join(b.text for b in response.content if b.type == "text")
