"""LLM provider factory.

Single entry point for constructing the chat model used across the pipeline.
Returns either a local Ollama model (default) or a Kotak Model Gateway model,
selected by ``config.settings.LLM_PROVIDER``.

The Kotak path wraps the ``requests``-based client in ``script.py``
(``KotakOAuth2Handler`` + ``KotakAIWrapper``) in a LangChain ``BaseChatModel`` so
it is a drop-in for ``ChatOllama`` — ``prompt | llm`` composition, ``.invoke``,
``.stream`` and ``AIMessage`` metadata all keep working unchanged.
"""

from functools import lru_cache
from typing import Any, List, Optional, Iterator

from langchain_ollama import ChatOllama
from langchain_core.callbacks import CallbackManagerForLLMRun
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, AIMessageChunk, BaseMessage, SystemMessage
from langchain_core.outputs import ChatGeneration, ChatGenerationChunk, ChatResult

from script import KotakOAuth2Handler, KotakAIWrapper
from config.settings import (
    LLM_PROVIDER, LLM_TEMPERATURE, LLM_SEED,
    KOTAK_TOKEN_URL, KOTAK_API_URL, KOTAK_CLIENT_ID, KOTAK_CLIENT_SECRET,
    KOTAK_CA_BUNDLE, KOTAK_MODEL, KOTAK_MAX_TOKENS,
)


@lru_cache(maxsize=1)
def _get_wrapper() -> KotakAIWrapper:
    """Build (once) the Kotak client with an OAuth2 handler.

    The handler caches the bearer token and refreshes it when expired, so a
    single shared wrapper reuses the token across all LLM calls.
    """
    oauth = KotakOAuth2Handler(
        token_url=KOTAK_TOKEN_URL,
        client_id=KOTAK_CLIENT_ID,
        client_secret=KOTAK_CLIENT_SECRET,
        ca_bundle=KOTAK_CA_BUNDLE or None,
    )
    return KotakAIWrapper(
        api_url=KOTAK_API_URL,
        oauth2_handler=oauth,
        ca_bundle=KOTAK_CA_BUNDLE or None,
    )


def _parse_response(resp: dict) -> tuple[str, dict]:
    """Extract text + token usage from the gateway response.

    Handles both OpenAI-style (``choices[0].message.content``) and
    Anthropic-style (``content`` as a list of text blocks or a string) shapes,
    since the exact gateway format cannot be verified off-network.
    """
    text = ""
    if isinstance(resp, dict):
        choices = resp.get("choices")
        if choices:
            text = (choices[0].get("message") or {}).get("content", "") or ""
        else:
            content = resp.get("content")
            if isinstance(content, list):
                text = "".join(
                    b.get("text", "") for b in content if isinstance(b, dict)
                )
            elif isinstance(content, str):
                text = content

        usage = resp.get("usage") or {}
        input_tokens = usage.get("input_tokens", usage.get("prompt_tokens", 0)) or 0
        output_tokens = usage.get("output_tokens", usage.get("completion_tokens", 0)) or 0
        total = usage.get("total_tokens", input_tokens + output_tokens) or 0
    else:
        input_tokens = output_tokens = total = 0

    usage_metadata = {
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "total_tokens": total,
    }
    return text, usage_metadata


class KotakChatModel(BaseChatModel):
    """LangChain chat model backed by the Kotak Model Gateway (via script.py)."""

    model: str = KOTAK_MODEL
    temperature: float = LLM_TEMPERATURE
    max_tokens: int = KOTAK_MAX_TOKENS
    json_mode: bool = False

    @property
    def _llm_type(self) -> str:
        return "kotak-model-gateway"

    def _build_message(self, messages: List[BaseMessage]) -> AIMessage:
        system_parts = [m.content for m in messages if isinstance(m, SystemMessage)]
        user_parts = [m.content for m in messages if not isinstance(m, SystemMessage)]
        system = "\n".join(p for p in system_parts if p)
        user = "\n".join(p for p in user_parts if p)

        if self.json_mode:
            system = (system + "\n" if system else "") + "Respond with only valid JSON."

        resp = _get_wrapper().send_message(
            message=user,
            model=self.model,
            max_tokens=self.max_tokens,
            temperature=self.temperature,
            system=system or None,
        )
        text, usage_metadata = _parse_response(resp)
        return AIMessage(
            content=text,
            usage_metadata=usage_metadata,
            response_metadata={"model": self.model},
        )

    def _generate(
        self,
        messages: List[BaseMessage],
        stop: Optional[List[str]] = None,
        run_manager: Optional[CallbackManagerForLLMRun] = None,
        **kwargs: Any,
    ) -> ChatResult:
        message = self._build_message(messages)
        return ChatResult(generations=[ChatGeneration(message=message)])

    def _stream(
        self,
        messages: List[BaseMessage],
        stop: Optional[List[str]] = None,
        run_manager: Optional[CallbackManagerForLLMRun] = None,
        **kwargs: Any,
    ) -> Iterator[ChatGenerationChunk]:
        # The gateway response is consumed non-incrementally; emit it as one chunk.
        message = self._build_message(messages)
        chunk = ChatGenerationChunk(message=AIMessageChunk(content=message.content))
        if run_manager:
            run_manager.on_llm_new_token(message.content, chunk=chunk)
        yield chunk


def create_chat_model(
    model_name: str,
    *,
    json_mode: bool = False,
    reasoning: Optional[bool] = None,
) -> BaseChatModel:
    """Return the configured chat model (Ollama by default, Kotak if selected).

    Args:
        model_name: Ollama model name (ignored for Kotak, which uses KOTAK_MODEL).
        json_mode: Request JSON output (Ollama ``format="json"``; Kotak system hint).
        reasoning: Ollama ``reasoning`` flag for <think>-capable models.
    """
    if LLM_PROVIDER == "kotak":
        return KotakChatModel(
            model=KOTAK_MODEL,
            temperature=LLM_TEMPERATURE,
            max_tokens=KOTAK_MAX_TOKENS,
            json_mode=json_mode,
        )

    kwargs: dict = {"model": model_name, "temperature": LLM_TEMPERATURE, "seed": LLM_SEED}
    if json_mode:
        kwargs["format"] = "json"
    if reasoning is not None:
        kwargs["reasoning"] = reasoning
    return ChatOllama(**kwargs)
