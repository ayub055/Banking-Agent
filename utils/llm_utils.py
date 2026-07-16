"""LLM output utilities.

Handles post-processing of LLM responses — primarily stripping and
logging the <think>...</think> reasoning block produced by DeepSeek-R1
and other chain-of-thought models — plus per-call token usage logging.
"""

import re
import logging
from datetime import datetime
from typing import Dict, Any

logger = logging.getLogger(__name__)


def extract_reasoning(message, label: str = "LLM", customer_id=None) -> str:
    """Extract reasoning from AIMessage.additional_kwargs and log it.

    When ChatOllama is used with reasoning=True, the thinking content is
    placed in additional_kwargs["reasoning_content"] rather than in the
    message content.  This function captures that reasoning, logs it at
    DEBUG level, and returns only the clean content.

    Also handles the legacy case where <think> tags are inline in content
    (reasoning=None on older Ollama versions).

    Args:
        message:     AIMessage from ChatOllama (or plain str for backwards compat).
        label:       Descriptive label for the log entry.
        customer_id: Optional CRN (retained for call-site compatibility).

    Returns:
        Clean answer text (str).
    """
    # Backwards compat: if someone passes a plain string, fall through to strip_think
    if isinstance(message, str):
        return strip_think(message, label=label)

    content = message.content or ""
    reasoning = (message.additional_kwargs or {}).get("reasoning_content", "")

    if reasoning:
        reasoning = reasoning.strip()
        logger.debug(
            "\n============================================================\n"
            "[%s — REASONING]\n"
            "------------------------------------------------------------\n"
            "%s\n"
            "============================================================",
            label,
            reasoning,
        )

    # Also handle any inline <think> tags (belt-and-suspenders)
    return strip_think(content, label=label)


def strip_think(text: str, label: str = "LLM") -> str:
    """Strip <think>...</think> block from DeepSeek-R1 / CoT model output.

    The thinking content is logged at DEBUG level so it is visible in logs
    for debugging and learning, but never reaches the final report output.

    Args:
        text:  Raw LLM response, possibly containing a <think> block.
        label: Descriptive label shown in the log line (e.g. "CustomerReview").

    Returns:
        Clean answer text with the think block removed.
    """
    if not text:
        return text

    think_match = re.search(r"<think>(.*?)</think>", text, flags=re.DOTALL)
    if think_match:
        think_content = think_match.group(1).strip()
        if think_content:
            logger.debug(
                "\n============================================================\n"
                "[%s — THINK BLOCK]\n"
                "------------------------------------------------------------\n"
                "%s\n"
                "============================================================",
                label,
                think_content,
            )
        text = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL).strip()

    return text


def log_token_usage(
    message,
    label: str,
    customer_id=None,
    wall_time_s: float = 0.0,
) -> Dict[str, Any]:
    """Extract and log token usage from an AIMessage.

    Call this right after chain.invoke() to capture token counts.
    Works with any LangChain model that populates usage_metadata
    (Ollama, OpenAI, vLLM).  If the model does not report token
    counts the record is still created with zeroes so that wall-time
    is always captured.

    Args:
        message:      AIMessage returned by chain.invoke().
        label:        Chain label (e.g. "CustomerReview").
        customer_id:  Optional CRN for correlation.
        wall_time_s:  Elapsed wall-clock seconds for this call.

    Returns:
        Dict with the recorded usage fields.
    """
    input_tokens = 0
    output_tokens = 0
    total_tokens = 0
    reasoning_tokens = 0
    model_name = ""

    # Extract from AIMessage.usage_metadata (LangChain standard)
    if hasattr(message, "usage_metadata") and message.usage_metadata:
        um = message.usage_metadata
        input_tokens = um.get("input_tokens", 0) or 0
        output_tokens = um.get("output_tokens", 0) or 0
        total_tokens = um.get("total_tokens", 0) or (input_tokens + output_tokens)
        # Some models report reasoning tokens separately
        out_details = um.get("output_token_details", {}) or {}
        reasoning_tokens = out_details.get("reasoning", 0) or 0

    # Extract from response_metadata (Ollama / OpenAI)
    if hasattr(message, "response_metadata") and message.response_metadata:
        rm = message.response_metadata
        model_name = rm.get("model", "")
        # Ollama puts eval_count / prompt_eval_count here
        if not input_tokens:
            input_tokens = rm.get("prompt_eval_count", 0) or 0
        if not output_tokens:
            output_tokens = rm.get("eval_count", 0) or 0
        if not total_tokens:
            total_tokens = input_tokens + output_tokens

    # Compute effective output speed
    tokens_per_sec = output_tokens / wall_time_s if wall_time_s > 0 and output_tokens else 0.0

    record = {
        "timestamp": datetime.now().isoformat(),
        "label": label,
        "customer_id": customer_id,
        "model": model_name,
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "reasoning_tokens": reasoning_tokens,
        "total_tokens": total_tokens,
        "wall_time_s": round(wall_time_s, 2),
        "tokens_per_sec": round(tokens_per_sec, 1),
    }

    logger.info(
        "[%s] tokens in=%d out=%d (reasoning=%d) total=%d | %.1fs (%.1f tok/s)%s",
        label, input_tokens, output_tokens, reasoning_tokens, total_tokens,
        wall_time_s, tokens_per_sec,
        f" | model={model_name}" if model_name else "",
    )

    return record
