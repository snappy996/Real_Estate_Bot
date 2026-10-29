"""Settings in one place. Values come from environment variables or a .env file.

Streamlit's st.secrets is read by the app and exported to the environment,
so core/ never imports Streamlit.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

try:  # optional: python-dotenv
    from dotenv import load_dotenv

    load_dotenv(ROOT / ".env")
except ImportError:
    pass


@dataclass(frozen=True)
class Settings:
    database_url: str | None = os.getenv("DATABASE_URL")
    gemini_api_key: str | None = os.getenv("GEMINI_API_KEY")
    groq_api_key: str | None = os.getenv("GROQ_API_KEY")

    # Chat models. Change here, not in code.
    default_provider: str = os.getenv("LLM_PROVIDER", "gemini")
    gemini_model: str = os.getenv("GEMINI_MODEL", "gemini-3.8-flash")
    gemini_fallback_model: str = os.getenv("GEMINI_FALLBACK_MODEL", "gemini-3.5-flash-lite")
    groq_model: str = os.getenv("GROQ_MODEL", "openai/gpt-oss-120b")
    groq_fallback_model: str = os.getenv("GROQ_FALLBACK_MODEL", "openai/gpt-oss-20b")

    # Embeddings always stay on one model; vectors from different models can't mix.
    embedding_model: str = os.getenv("EMBEDDING_MODEL", "gemini-embedding-2")
    embedding_dim: int = int(os.getenv("EMBEDDING_DIM", "768"))

    # Scraper politeness.
    scraper_user_agent: str = os.getenv(
        "SCRAPER_USER_AGENT",
        "RealEstateResearchBot/0.1 (personal research project; contact via .env)",
    )
    scraper_delay_seconds: float = float(os.getenv("SCRAPER_DELAY_SECONDS", "3"))


settings = Settings()
