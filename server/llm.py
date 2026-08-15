"""
OpenAI-compatible LLM client for the Voice PE server.

Points at the configured base URL (default: Spark abliterated pair on port
8888) and supports multi-turn conversation history.
"""

from __future__ import annotations

import logging
from typing import Optional

from openai import AsyncOpenAI

from config import config

logger = logging.getLogger(__name__)


class LLMClient:
    """Async client for an OpenAI-compatible LLM endpoint."""

    def __init__(self) -> None:
        self._client = AsyncOpenAI(
            base_url=config.llm_base_url,
            api_key=config.llm_api_key,
        )
        self._model = config.llm_model
        self._system_prompt = config.llm_system_prompt
        self._max_tokens = config.llm_max_tokens
        self._temperature = config.llm_temperature

    async def process(
        self,
        text: str,
        system_prompt: Optional[str] = None,
        conversation_history: Optional[list[dict[str, str]]] = None,
    ) -> str:
        """Send a user utterance to the LLM and return the assistant reply.

        Args:
            text: The transcribed user speech.
            system_prompt: Optional override for the system prompt.  Uses
                           the config default if not provided.
            conversation_history: Optional list of previous messages in
                                  OpenAI message format:
                                  ``[{"role": "user"|"assistant", "content": "..."}]``

        Returns:
            The assistant's text response.

        Raises:
            openai.APIError: On API or network failures.
        """
        system = system_prompt or self._system_prompt

        # We use dict[str, str] for internal handling; cast to the broader
        # openai type at the call site to keep the type checker happy.
        messages: list[dict[str, str]] = [
            {"role": "system", "content": system},
        ]

        if conversation_history:
            messages.extend(conversation_history)

        messages.append({"role": "user", "content": text})

        logger.debug(
            "LLM call (%s): %d messages, %d max_tokens, temp=%0.2f",
            self._model,
            len(messages),
            self._max_tokens,
            self._temperature,
        )

        response = await self._client.chat.completions.create(
            model=self._model,
            messages=messages,
            max_tokens=self._max_tokens,
            temperature=self._temperature,
        )

        reply = response.choices[0].message.content or ""
        logger.info("LLM response (%d chars): %s", len(reply), reply[:200])
        return reply

    @property
    def model_name(self) -> str:
        return self._model
