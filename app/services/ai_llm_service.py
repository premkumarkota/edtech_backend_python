"""
AI LLM Service — talks to Anthropic (Claude) via the official SDK.

Used by the AI Study Planner and the AI chat bot. The public functions
`chat_completion` and `chat_completion_json` keep the same signatures they had
under the old Azure/OpenAI implementation, so all callers work unchanged.

Configuration (app/config.py / .env.dev):
    ANTHROPIC_API_KEY  — Claude API key (sk-ant-...)
    ANTHROPIC_MODEL    — model id (default "claude-sonnet-5")
"""
import logging
import json
from typing import Optional

from app.config import settings

logger = logging.getLogger(__name__)


def _split_system_and_conversation(messages: list[dict]) -> tuple[str, list[dict]]:
    """
    Anthropic keeps the system prompt separate from the message list, and the
    message list must start with a 'user' turn. Split OpenAI-style messages
    (which put system inside the list) into (system_text, conversation).
    """
    system_parts: list[str] = []
    conversation: list[dict] = []

    for m in messages:
        role = m.get("role")
        content = m.get("content", "")
        if content is None:
            content = ""
        if role == "system":
            if content:
                system_parts.append(str(content))
        else:
            conversation.append({"role": role, "content": str(content)})

    # Drop any leading assistant turns — Anthropic requires the first message
    # to be from the user.
    while conversation and conversation[0]["role"] != "user":
        conversation.pop(0)

    return "\n\n".join(system_parts).strip(), conversation


def chat_completion(
    messages: list[dict],
    temperature: float = 0.7,
    max_tokens: int = 1500,
    response_format: Optional[dict] = None,
) -> Optional[str]:
    """
    Send a chat completion request to Claude.

    Args:
        messages: List of {"role": "system"|"user"|"assistant", "content": "..."}
        temperature: Ignored (current Claude models reject sampling params); kept
                     for signature compatibility with existing callers.
        max_tokens: Max response length.
        response_format: If {"type": "json_object"}, Claude is instructed to
                         return JSON only.

    Returns:
        Assistant reply text, or None on failure.
    """
    api_key = (getattr(settings, "ANTHROPIC_API_KEY", "") or "").strip()
    if not api_key:
        logger.error("ANTHROPIC_API_KEY not configured.")
        return None

    try:
        import anthropic
    except ImportError:
        logger.error("The 'anthropic' package is not installed. Run: pip install anthropic")
        return None

    model = (getattr(settings, "ANTHROPIC_MODEL", "") or "claude-sonnet-5").strip()

    system_text, conversation = _split_system_and_conversation(messages)
    if not conversation:
        logger.error("chat_completion called with no user message.")
        return None

    if response_format and response_format.get("type") == "json_object":
        json_instruction = (
            "Respond with ONLY valid JSON. Do not include any prose, explanations, "
            "or markdown code fences."
        )
        system_text = f"{system_text}\n\n{json_instruction}".strip()

    kwargs = {
        "model": model,
        "max_tokens": max_tokens,
        # Disabled: these are short, well-scoped utility calls — keeps the full
        # token budget for the answer and responses fast. (Sampling params like
        # temperature are not supported on current Claude models.)
        "thinking": {"type": "disabled"},
        "messages": conversation,
    }
    if system_text:
        kwargs["system"] = system_text

    try:
        client = anthropic.Anthropic(api_key=api_key)
        response = client.messages.create(**kwargs)
    except Exception as e:
        logger.error(f"Claude request failed: {e}")
        return None

    text = "".join(
        block.text for block in response.content if getattr(block, "type", None) == "text"
    ).strip()
    return text or None


def chat_completion_json(
    messages: list[dict],
    temperature: float = 0.5,
    max_tokens: int = 2000,
) -> Optional[dict]:
    """
    Same as chat_completion but expects and parses a JSON response.
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
        logger.error(f"Failed to parse Claude JSON response: {result[:200]}")
        return None
