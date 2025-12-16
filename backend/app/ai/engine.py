from openai import OpenAI
from app.core.config import settings

# Initialize client only if key is present
client = OpenAI(api_key=settings.OPENAI_API_KEY) if settings.OPENAI_API_KEY else None

class AIEngine:
    def analyze_failure(self, error_log: str, dom_snapshot: str) -> str:
        if not client:
            return "AI Analysis unavailable (No API Key)"
            
        prompt = f"""
        Analyze the following test failure:
        Error: {error_log}
        DOM Snapshot (snippet): {dom_snapshot[:2000]}...
        
        Explain why the test failed in simple terms.
        """
        try:
            response = client.chat.completions.create(
                model="gpt-4o",
                messages=[{"role": "user", "content": prompt}]
            )
            return response.choices[0].message.content
        except Exception as e:
            return f"AI Analysis failed: {str(e)}"

    def heal_selector(self, broken_selector: str, dom_snapshot: str) -> str:
        if not client:
            return ""
            
        prompt = f"""
        The selector '{broken_selector}' failed to find an element.
        Here is the DOM snapshot:
        {dom_snapshot}
        
        Find the element that most likely corresponds to the broken selector.
        Return ONLY the new selector.
        """
        try:
            response = client.chat.completions.create(
                model="gpt-4o",
                messages=[{"role": "user", "content": prompt}]
            )
            return response.choices[0].message.content.strip()
        except Exception as e:
            print(f"AI Healing failed: {e}")
            return ""

ai_engine = AIEngine()
