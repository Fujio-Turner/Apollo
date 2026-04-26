"""
AI provider registry.

Each provider speaks the OpenAI Chat-Completions wire format (so the same
`openai` Python SDK works against all of them by overriding `base_url`).

To add a provider:
  1. Append an entry below.
  2. Make sure its API endpoint accepts OpenAI-style /chat/completions
     requests with `tools=[...]`. If it doesn't, tool-calling will degrade
     to plain text — the user just won't get graph-aware answers.
"""
from __future__ import annotations

import os


PROVIDERS: dict[str, dict] = {
    "xai": {
        "label": "xAI (Grok)",
        "base_url": "https://api.x.ai/v1",
        "env": "XAI_API_KEY",
        "key_url": "https://console.x.ai",
        "key_placeholder": "xai-...",
        "models": [
            "grok-4-1-fast-non-reasoning",
            "grok-4-1-fast-reasoning",
            "grok-4.20-reasoning",
            "grok-4.20-non-reasoning",
            "grok-4.20-multi-agent",
            "grok-4",
            "grok-4-fast-reasoning",
            "grok-4-fast-non-reasoning",
            "grok-3",
            "grok-3-mini",
        ],
        "default_model": "grok-4-1-fast-non-reasoning",
    },
    "openai": {
        "label": "OpenAI",
        "base_url": "https://api.openai.com/v1",
        "env": "OPENAI_API_KEY",
        "key_url": "https://platform.openai.com/api-keys",
        "key_placeholder": "sk-...",
        "models": [
            "gpt-5",
            "gpt-5-mini",
            "gpt-4.1",
            "gpt-4.1-mini",
            "gpt-4o",
            "gpt-4o-mini",
            "o3-mini",
        ],
        "default_model": "gpt-4o-mini",
    },
    "gemini": {
        "label": "Google Gemini",
        "base_url": "https://generativelanguage.googleapis.com/v1beta/openai/",
        "env": "GEMINI_API_KEY",
        "key_url": "https://aistudio.google.com/apikey",
        "key_placeholder": "AIza...",
        "models": [
            "gemini-2.5-pro",
            "gemini-2.5-flash",
            "gemini-2.0-flash",
            "gemini-1.5-pro",
            "gemini-1.5-flash",
        ],
        "default_model": "gemini-2.5-flash",
    },
    "anthropic": {
        "label": "Anthropic (Claude)",
        # Anthropic exposes an OpenAI-compatible endpoint at /v1/.
        # Note: tool schemas behave slightly differently than OpenAI; basic
        # function-calling works, but exotic features may not.
        "base_url": "https://api.anthropic.com/v1/",
        "env": "ANTHROPIC_API_KEY",
        "key_url": "https://console.anthropic.com/settings/keys",
        "key_placeholder": "sk-ant-...",
        "models": [
            "claude-sonnet-4-5",
            "claude-opus-4-1",
            "claude-3-5-sonnet-latest",
            "claude-3-5-haiku-latest",
        ],
        "default_model": "claude-3-5-sonnet-latest",
    },
    "llama": {
        # Llama models served via Groq's OpenAI-compatible endpoint.
        "label": "Llama (via Groq)",
        "base_url": "https://api.groq.com/openai/v1",
        "env": "GROQ_API_KEY",
        "key_url": "https://console.groq.com/keys",
        "key_placeholder": "gsk_...",
        "models": [
            "llama-3.3-70b-versatile",
            "llama-3.1-70b-versatile",
            "llama-3.1-8b-instant",
            "llama3-70b-8192",
        ],
        "default_model": "llama-3.3-70b-versatile",
    },
}

DEFAULT_PROVIDER = "xai"


def get_provider(provider_id: str) -> dict:
    return PROVIDERS.get(provider_id) or PROVIDERS[DEFAULT_PROVIDER]


def env_key(provider_id: str) -> str:
    return get_provider(provider_id)["env"]


def has_api_key(provider_id: str) -> bool:
    return bool(os.environ.get(env_key(provider_id)))


def public_registry() -> list[dict]:
    """Serializable list for the frontend (no secrets)."""
    return [
        {
            "id": pid,
            "label": p["label"],
            "env": p["env"],
            "key_url": p["key_url"],
            "key_placeholder": p["key_placeholder"],
            "models": list(p["models"]),
            "default_model": p["default_model"],
        }
        for pid, p in PROVIDERS.items()
    ]
