import os
from langchain_groq import ChatGroq

def get_llm(model="llama-3.3-70b-versatile", temperature=0.3):
    """
    Get a ChatGroq instance with fallback API keys if rate-limited.
    Reads a comma-separated list of keys from GROQ_API_KEY.
    """
    keys_str = os.getenv("GROQ_API_KEY", "")
    # Split and strip keys
    keys = [k.strip() for k in keys_str.split(",") if k.strip()]
    
    if not keys:
        # Will fail gracefully downstream if no key is provided
        return ChatGroq(model=model, api_key="", temperature=temperature)
        
    primary_llm = ChatGroq(
        model=model,
        api_key=keys[0],
        temperature=temperature,
    )
    
    # If multiple keys are provided, set up fallbacks for rate limits
    if len(keys) > 1:
        fallbacks = [
            ChatGroq(model=model, api_key=k, temperature=temperature)
            for k in keys[1:]
        ]
        return primary_llm.with_fallbacks(fallbacks)
        
    return primary_llm
