"""LLM output utilities.

Handles post-processing of LLM responses — primarily stripping and
logging the <think>...</think> reasoning block produced by DeepSeek-R1
and other chain-of-thought models.

Also provides token usage tracking for all LLM calls in the pipeline.
"""

import re
import json
import logging
import time
import threading
from datetime import datetime
from typing import Iterator, Optional, Dict, Any, List

logger = logging.getLogger(__name__)

# ── Reasoning file logger (deepseek only) ────────────────────────────
_reasoning_log_file: Optional[str] = None
_LOGGABLE_LABELS = {"CustomerReview", "BureauReview"}


def set_reasoning_log_file(path: str) -> None:
    """Enable writing think-block reasoning to a text file."""
    global _reasoning_log_file
    _reasoning_log_file = path


def _write_reasoning_to_file(label: str, content: str, customer_id=None) -> None:
    """Append reasoning content to the log file if enabled and label is loggable."""
    if not _reasoning_log_file or label not in _LOGGABLE_LABELS or not content:
        return
    try:
        crn_str = f" | CRN {customer_id}" if customer_id else ""
        with open(_reasoning_log_file, "a", encoding="utf-8") as f:
            f.write(f"\n## {label}{crn_str}\n")
            f.write(f"*{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}*\n\n")
            f.write(f"```\n{content}\n```\n\n---\n")
    except Exception as e:
        logger.warning("Failed to write reasoning log: %s", e)


def extract_reasoning(message, label: str = "LLM", customer_id=None) -> str:
    """Extract reasoning from AIMessage.additional_kwargs and log it.

    When ChatOllama is used with reasoning=True, the thinking content is
    placed in additional_kwargs["reasoning_content"] rather than in the
    message content.  This function captures that reasoning, logs it to
    the reasoning file (if enabled), and returns only the clean content.

    Also handles the legacy case where <think> tags are inline in content
    (reasoning=None on older Ollama versions).

    Args:
        message:     AIMessage from ChatOllama (or plain str for backwards compat).
        label:       Descriptive label for the log entry.
        customer_id: Optional CRN to include in the log entry.

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
        _write_reasoning_to_file(label, reasoning, customer_id=customer_id)

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
            _write_reasoning_to_file(label, think_content)
        text = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL).strip()

    return text


def stream_strip_think(chunks: Iterator[str], label: str = "LLM") -> Iterator[str]:
    """Strip <think> block from a streaming LLM response.

    Buffers the stream until </think> is found (or until it is clear there
    is no think block), logs the thinking content at DEBUG level, then yields
    all subsequent answer chunks transparently.

    Args:
        chunks: Iterator of string chunks from the LLM stream.
        label:  Descriptive label shown in the log line.

    Yields:
        Answer chunks with the think block removed.
    """
    buffer = ""
    think_done = False   # once True we stop buffering and yield directly
    in_think = False

    for chunk in chunks:
        if think_done:
            yield chunk
            continue

        buffer += chunk

        # Detect opening tag
        if not in_think and "<think>" in buffer:
            in_think = True

        # Detect closing tag
        if in_think and "</think>" in buffer:
            think_end = buffer.index("</think>") + len("</think>")
            think_block = buffer[:think_end]
            remainder = buffer[think_end:]

            # Extract and log the think content
            think_match = re.search(r"<think>(.*?)</think>", think_block, flags=re.DOTALL)
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

            buffer = ""
            think_done = True
            if remainder:
                yield remainder
            continue

        # No think block at all — if buffer grows large enough, just yield it
        if not in_think and len(buffer) > 200:
            yield buffer
            buffer = ""
            think_done = True

    # Flush any remaining buffer (e.g. model had no think block at all)
    if buffer and not think_done:
        yield buffer


# ── Token usage tracking ──────────────────────────────────────────────
_token_log_file: Optional[str] = None
_token_log_lock = threading.Lock()
_token_records: List[Dict[str, Any]] = []   # in-memory accumulator


def set_token_log_file(path: str) -> None:
    """Enable writing per-call token usage to a JSONL file."""
    global _token_log_file
    _token_log_file = path


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
        label:        Chain label (e.g. "CustomerReview", "BureauReview").
        customer_id:  Optional CRN for correlation.
        wall_time_s:  Elapsed wall-clock seconds for this call.

    Returns:
        Dict with the recorded usage fields (also appended to _token_records).
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

    with _token_log_lock:
        _token_records.append(record)

    # Log to console
    logger.info(
        "[%s] tokens in=%d out=%d (reasoning=%d) total=%d | %.1fs (%.1f tok/s)%s",
        label, input_tokens, output_tokens, reasoning_tokens, total_tokens,
        wall_time_s, tokens_per_sec,
        f" | model={model_name}" if model_name else "",
    )

    # Append to JSONL file (if enabled)
    if _token_log_file:
        try:
            with _token_log_lock:
                with open(_token_log_file, "a", encoding="utf-8") as f:
                    f.write(json.dumps(record) + "\n")
        except Exception as e:
            logger.warning("Failed to write token log: %s", e)

    return record


def get_token_records() -> List[Dict[str, Any]]:
    """Return a copy of all token usage records collected this session."""
    with _token_log_lock:
        return list(_token_records)


def get_token_summary() -> Dict[str, Any]:
    """Aggregate token usage across all recorded calls.

    Returns dict with per-label breakdown and totals.
    """
    with _token_log_lock:
        records = list(_token_records)

    if not records:
        return {"total_calls": 0}

    by_label: Dict[str, Dict[str, Any]] = {}
    for r in records:
        lbl = r["label"]
        if lbl not in by_label:
            by_label[lbl] = {
                "calls": 0, "input_tokens": 0, "output_tokens": 0,
                "reasoning_tokens": 0, "total_tokens": 0, "wall_time_s": 0.0,
            }
        s = by_label[lbl]
        s["calls"] += 1
        s["input_tokens"] += r["input_tokens"]
        s["output_tokens"] += r["output_tokens"]
        s["reasoning_tokens"] += r["reasoning_tokens"]
        s["total_tokens"] += r["total_tokens"]
        s["wall_time_s"] += r["wall_time_s"]

    total_input = sum(s["input_tokens"] for s in by_label.values())
    total_output = sum(s["output_tokens"] for s in by_label.values())
    total_reasoning = sum(s["reasoning_tokens"] for s in by_label.values())
    total_wall = sum(s["wall_time_s"] for s in by_label.values())

    return {
        "total_calls": len(records),
        "total_input_tokens": total_input,
        "total_output_tokens": total_output,
        "total_reasoning_tokens": total_reasoning,
        "total_tokens": total_input + total_output,
        "total_wall_time_s": round(total_wall, 2),
        "avg_tokens_per_sec": round(total_output / total_wall, 1) if total_wall > 0 else 0,
        "by_label": by_label,
    }


def clear_token_records() -> None:
    """Clear all accumulated token records."""
    with _token_log_lock:
        _token_records.clear()


def print_token_summary() -> str:
    """Return a human-readable token usage summary string."""
    s = get_token_summary()
    if s["total_calls"] == 0:
        return "No LLM calls recorded."

    lines = [
        "=" * 60,
        "LLM TOKEN USAGE SUMMARY",
        "=" * 60,
        "",
    ]

    for label, data in s["by_label"].items():
        avg_speed = data["output_tokens"] / data["wall_time_s"] if data["wall_time_s"] > 0 else 0
        lines.append(f"  {label}:")
        lines.append(f"    Calls: {data['calls']}")
        lines.append(f"    Input tokens:     {data['input_tokens']:,}")
        lines.append(f"    Output tokens:    {data['output_tokens']:,}")
        if data["reasoning_tokens"]:
            lines.append(f"    Reasoning tokens: {data['reasoning_tokens']:,}")
        lines.append(f"    Wall time:        {data['wall_time_s']:.1f}s")
        lines.append(f"    Speed:            {avg_speed:.1f} tok/s")
        lines.append("")

    lines.append("-" * 60)
    lines.append(f"  TOTAL: {s['total_input_tokens']:,} in + {s['total_output_tokens']:,} out "
                 f"= {s['total_tokens']:,} tokens")
    if s["total_reasoning_tokens"]:
        lines.append(f"  Reasoning: {s['total_reasoning_tokens']:,} tokens (included in output)")
    lines.append(f"  Wall time: {s['total_wall_time_s']:.1f}s | "
                 f"Avg speed: {s['avg_tokens_per_sec']:.1f} tok/s")
    lines.append("=" * 60)

    return "\n".join(lines)
