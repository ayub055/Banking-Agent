"""
Utility functions used across the project.
"""

import logging

_logger = logging.getLogger(__name__)


def safe_call(fn, *args, default=None, **kwargs):
    """Run ``fn(*args, **kwargs)``; on any exception log a warning and return
    ``default``.

    Central fail-soft wrapper so each report section degrades independently
    instead of aborting the whole build. The failing callable is identified by
    its ``__name__``, so no per-module log prefix is needed.
    """
    try:
        return fn(*args, **kwargs)
    except Exception as exc:  # noqa: BLE001
        _logger.warning("%s failed: %s", getattr(fn, "__name__", fn), exc)
        return default


def format_inr(amount: float) -> str:
    """Format a number with Indian comma placement (lakhs/crores).

    Indian system: 1,00,00,000 (1 crore), 10,00,000 (10 lakh), 1,00,000 (1 lakh).
    The last three digits are grouped, then every two digits thereafter.

    Args:
        amount: Numeric value to format.

    Returns:
        String with Indian-style commas, no decimals. E.g. '1,85,72,860'.
    """
    is_negative = amount < 0
    num = abs(int(round(amount)))
    s = str(num)

    if len(s) <= 3:
        result = s
    else:
        # Last 3 digits
        last3 = s[-3:]
        rest = s[:-3]
        # Group remaining digits in pairs from right
        parts = []
        while len(rest) > 2:
            parts.append(rest[-2:])
            rest = rest[:-2]
        if rest:
            parts.append(rest)
        parts.reverse()
        result = ",".join(parts) + "," + last3

    return f"-{result}" if is_negative else result


def mask_customer_id(customer_id: int | str) -> str:
    """
    Mask customer ID to show only last 4 digits.

    Args:
        customer_id: Customer identifier (int or str)

    Returns:
        Masked string showing only last 4 digits (e.g., "###4898")

    Examples:
        >>> mask_customer_id(9449274898)
        '###4898'
        >>> mask_customer_id("1234567890")
        '###7890'
        >>> mask_customer_id(123)
        '###123'
    """
    id_str = str(customer_id)
    if len(id_str) <= 4:
        return f"###{id_str}"
    return f"###{id_str[-4:]}"
