"""
AI LLM Service — Handles communication with Azure OpenAI / OpenAI API.

Configuration:
    LLM_BASE_URL  — Azure endpoint or OpenAI base URL
    LLM_API_KEY   — API key
    LLM_MODEL     — Deployment/model name (e.g., "gpt-4o", "gpt-4o-mini")

Works with both Azure OpenAI and standard OpenAI API (same chat completions format).
"""
import logging
import json
from typing import Optional

import httpx

from app.config import settings

logger = logging.getLogger(__name__)


def _get_llm_config() -> dict:
    """Get LLM configuration from settings."""
    return {
        "base_url": getattr(settings, "LLM_BASE_URL", ""),
        "api_key": getattr(settings, "LLM_API_KEY", ""),
        "model": getattr(settings, "LLM_MODEL", "gpt-4o-mini"),
    }


def chat_completion(
    messages: list[dict],
    temperature: float = 0.7,
    max_tokens: int = 1500,
    response_format: Optional[dict] = None,
) -> Optional[str]:
    """
    Send a chat completion request to Azure OpenAI / OpenAI.

    Args:
        messages: List of {"role": "system"|"user"|"assistant", "content": "..."}
        temperature: Creativity (0.0 = deterministic, 1.0 = creative)
        max_tokens: Max response length
        response_format: Optional {"type": "json_object"} for JSON mode

    Returns:
        Assistant's reply content string, or None on failure.
    """
    config = _get_llm_config()

    if not config["base_url"] or not config["api_key"]:
        logger.error("LLM_BASE_URL or LLM_API_KEY not configured.")
        return None

    # Build the request URL
    base_url = config["base_url"].rstrip("/")

    # Azure OpenAI uses: {base_url}/openai/deployments/{model}/chat/completions?api-version=...
    # Standard OpenAI uses: {base_url}/chat/completions
    # Detect Azure by checking common Azure domain patterns
    is_azure = any(domain in base_url for domain in [
        "openai.azure.com",
        "cognitiveservices.azure.com",
        "services.ai.azure.com",
        ".azure.com",
    ])

    if is_azure:
        # Azure OpenAI / Azure AI format
        api_version = getattr(settings, "LLM_API_VERSION", "2024-12-01-preview")
        # If URL already contains /openai/, don't add it again
        if "/openai/" in base_url:
            url = f"{base_url}/deployments/{config['model']}/chat/completions?api-version={api_version}"
        else:
            url = f"{base_url}/openai/deployments/{config['model']}/chat/completions?api-version={api_version}"
        headers = {
            "Content-Type": "application/json",
            "api-key": config["api_key"],
        }
    else:
        # Standard OpenAI-compatible format
        url = f"{base_url}/chat/completions"
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {config['api_key']}",
        }

    logger.info(f"LLM request: is_azure={is_azure}, url={url[:80]}...")

    payload = {
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
    }

    if not is_azure:
        payload["model"] = config["model"]

    if response_format:
        payload["response_format"] = response_format

    try:
        with httpx.Client(timeout=30.0) as client:
            response = client.post(url, json=payload, headers=headers)
            response.raise_for_status()

        data = response.json()
        return data["choices"][0]["message"]["content"]

    except httpx.HTTPStatusError as e:
        logger.error(f"LLM API error {e.response.status_code}: {e.response.text[:500]}")
        return None
    except Exception as e:
        logger.error(f"LLM request failed: {e}")
        return None


def chat_completion_json(
    messages: list[dict],
    temperature: float = 0.5,
    max_tokens: int = 2000,
) -> Optional[dict]:
    """
    Same as chat_completion but expects and parses a JSON response.
    Uses JSON mode if available, otherwise parses from text.
    """
    result = chat_completion(
        messages=messages,
        temperature=temperature,
        max_tokens=max_tokens,
        response_format={"type": "json_object"},
    )

    if not result:
        return None

    try:
        return json.loads(result)
    except json.JSONDecodeError:
        # Try to extract JSON from markdown code blocks
        if "```json" in result:
            json_str = result.split("```json")[1].split("```")[0].strip()
            return json.loads(json_str)
        elif "```" in result:
            json_str = result.split("```")[1].split("```")[0].strip()
            return json.loads(json_str)
        logger.error(f"Failed to parse LLM JSON response: {result[:200]}")
        return None
