import os
from dotenv import load_dotenv
load_dotenv()

AI_PROVIDER = os.getenv("AI_PROVIDER", "gemini").lower()
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-2.0-flash")

def call_ai(system_prompt: str, user_prompt: str, max_tokens: int = 2000) -> str:
    if AI_PROVIDER == "gemini":
        import google.generativeai as genai
        if not GEMINI_API_KEY:
            raise RuntimeError("GEMINI_API_KEY set karo!")
        genai.configure(api_key=GEMINI_API_KEY)
        model = genai.GenerativeModel(
            model_name=GEMINI_MODEL,
            system_instruction=system_prompt,
            generation_config=genai.types.GenerationConfig(max_output_tokens=max_tokens, temperature=0.2)
        )
        return model.generate_content(user_prompt).text
    return "[MOCK] AI_PROVIDER=gemini set karo"
