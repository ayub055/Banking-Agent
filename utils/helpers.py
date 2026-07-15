"""
Utility functions used across the project.
"""


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


def format_inr_units(amount) -> str:
    """Format an INR amount as a compact human-readable string using Cr/L units.

    Examples:
        1,85,72,860  -> '1.86 Cr'
        54,30,546    -> '54.31 L'
        85,420       -> '85,420'
    """
    if amount is None:
        return "N/A"
    abs_val = abs(float(amount))
    if abs_val >= 1_00_00_000:          # 1 crore
        result = f"{abs_val / 1_00_00_000:.2f} Cr"
    elif abs_val >= 1_00_000:            # 1 lakh
        result = f"{abs_val / 1_00_000:.2f} L"
    else:
        result = format_inr(abs_val)
    return f"-{result}" if float(amount) < 0 else result


def strip_segment_prefix(value: str) -> str:
    """Strip leading sort-code prefixes from segment/node labels.

    Handles both letter-prefixed ('I.Super' → 'Super') and
    number-prefixed ('20. Others' → 'Others') values.
    Also converts underscores to spaces ('Super_Plus' → 'Super Plus').
    """
    import re
    if not value:
        return value
    cleaned = re.sub(r'^[A-Za-z0-9]+\.\s*', '', value).replace('_', ' ')
    return cleaned if cleaned else value


def print_header(title: str, char: str = "=", width: int = 60):
    """Print a formatted header."""
    print(char * width)
    print(title.center(width))
    print(char * width)


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
