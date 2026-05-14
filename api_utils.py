"""API認証・LLM呼び出しユーティリティ.

複数のLLMプロバイダー（Gemini, Claude）を統一インターフェースで利用する。
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# API Key resolution
# ---------------------------------------------------------------------------

def resolve_anthropic_api_key(env_var: str = "ANTHROPIC_API_KEY") -> str:
    """Resolve Anthropic API key from multiple sources."""
    key = os.environ.get(env_var, "")
    if key:
        return key

    for candidate in [Path(".api_key"), Path(__file__).parent / ".api_key"]:
        if candidate.exists():
            key = candidate.read_text(encoding="utf-8").strip()
            if key:
                return key

    cred_path = Path.home() / ".claude" / ".credentials.json"
    if cred_path.exists():
        try:
            creds = json.loads(cred_path.read_text(encoding="utf-8"))
            token = creds.get("claudeAiOauth", {}).get("accessToken", "")
            if token:
                return token
        except (json.JSONDecodeError, KeyError):
            pass

    raise RuntimeError("Anthropic API key not found.")


def resolve_gemini_api_key(config: dict | None = None) -> str:
    """Resolve Gemini API key from file or environment variable."""
    # 1. Environment variable
    key = os.environ.get("GEMINI_API_KEY", "")
    if key:
        return key

    # 2. Key file (from config or default)
    key_file = ".gemini_api_key"
    if config:
        key_file = config.get("gemini", {}).get("api_key_file", key_file)

    for candidate in [Path(key_file), Path(__file__).parent / key_file]:
        if candidate.exists():
            key = candidate.read_text(encoding="utf-8").strip()
            if key:
                return key

    raise RuntimeError(
        "Gemini API key not found. Set GEMINI_API_KEY env var or create .gemini_api_key file."
    )


# ---------------------------------------------------------------------------
# Unified LLM client
# ---------------------------------------------------------------------------

class LLMClient:
    """Unified LLM client supporting Gemini and Claude."""

    def __init__(self, config: dict):
        """Initialize from the 'llm' section of config.json."""
        self.provider = config.get("provider", "gemini")
        self.config = config
        self._client = None

    def generate(
        self,
        system_prompt: str,
        user_prompt: str,
        max_tokens: int = 500,
    ) -> str:
        """Generate text using the configured LLM provider."""
        if self.provider == "gemini":
            return self._generate_gemini(system_prompt, user_prompt, max_tokens)
        elif self.provider == "claude":
            return self._generate_claude(system_prompt, user_prompt, max_tokens)
        else:
            raise ValueError(f"Unknown LLM provider: {self.provider}")

    def _generate_gemini(
        self,
        system_prompt: str,
        user_prompt: str,
        max_tokens: int,
    ) -> str:
        from google import genai

        gemini_cfg = self.config.get("gemini", {})
        api_key = resolve_gemini_api_key(self.config)
        model = gemini_cfg.get("model", "gemini-2.0-flash-lite")

        client = genai.Client(api_key=api_key)

        config_kwargs = {
            "system_instruction": system_prompt,
            "max_output_tokens": max_tokens,
            "temperature": 0.7,
            "response_mime_type": "application/json",
        }
        # Disable thinking for 2.5+ models to save tokens
        if "2.5" in model or "3" in model:
            config_kwargs["thinking_config"] = genai.types.ThinkingConfig(
                thinking_budget=0,
            )

        response = client.models.generate_content(
            model=model,
            contents=user_prompt,
            config=genai.types.GenerateContentConfig(**config_kwargs),
        )
        return response.text

    def _generate_claude(
        self,
        system_prompt: str,
        user_prompt: str,
        max_tokens: int,
    ) -> str:
        import anthropic

        claude_cfg = self.config.get("claude", {})
        api_key = resolve_anthropic_api_key(claude_cfg.get("api_key_env", "ANTHROPIC_API_KEY"))
        model = claude_cfg.get("model", "claude-sonnet-4-20250514")

        client = anthropic.Anthropic(api_key=api_key)
        response = client.messages.create(
            model=model,
            max_tokens=max_tokens,
            system=[
                {
                    "type": "text",
                    "text": system_prompt,
                    "cache_control": {"type": "ephemeral"},
                }
            ],
            messages=[{"role": "user", "content": user_prompt}],
        )
        return response.content[0].text
