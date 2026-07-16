"""Narration normalization and extraction utilities."""

import re
from typing import Optional


def like_to_regex(kw: str) -> str:
    """Convert a SQL-LIKE keyword to a regex pattern.

    Supports the ``%`` wildcard (matches any sequence); every other character
    is ``re.escape``-d. Canonical home for the keyword→regex conversion used by
    the event-detector keyword rules and the ``tools/rules`` predicates.

    Examples:
        "ECS RETURN"   → "ECS\\ RETURN"   (exact substring)
        "NACH%BOUNCE"  → "NACH.*BOUNCE"   (wildcard)
        "%ATL/%"       → ".*ATL/.*"       (contains)
    """
    parts = kw.split("%")
    return ".*".join(re.escape(p) for p in parts)


def normalize_narration(text: str) -> str:
    """
    Normalize narration for fuzzy matching.

    - Convert to lowercase
    - Remove numbers
    - Remove special characters
    - Strip whitespace

    Args:
        text: Raw narration string

    Returns:
        Normalized string for comparison
    """
    if not text:
        return ""

    text = text.lower()
    text = re.sub(r'\d+', '', text)  # Remove numbers
    text = re.sub(r'[^a-z\s]', ' ', text)  # Remove special chars, keep letters and spaces
    text = re.sub(r'\s+', ' ', text)  # Collapse multiple spaces
    return text.strip()


_MIN_MERCHANT_LEN = 3  # extracted name must be > 3 chars after digit cleanup


def _strip_digits(text: str) -> str:
    """Remove digit runs from a candidate merchant name and collapse whitespace."""
    if not text:
        return ""
    text = re.sub(r'\d+', ' ', text)
    text = re.sub(r'\s+', ' ', text)
    return text.strip(" -_/.,")


def _finalize(name: Optional[str], original: str) -> Optional[str]:
    """Prefer the digit-stripped extracted name; if that's too short, keep the
    original extracted name (with digits) so alphanumeric merchant codes like
    ``PLA5150547202`` survive. Last resort: digit-stripped full narration.
    """
    cleaned = _strip_digits(name) if name else ""
    if len(cleaned) > _MIN_MERCHANT_LEN:
        return cleaned
    if name:
        raw = re.sub(r'\s+', ' ', name).strip(" -_/.,")
        if len(raw) > _MIN_MERCHANT_LEN:
            return raw
    fallback = _strip_digits(original)
    return fallback or (original.strip() or None)


def extract_recipient_name(narration: str) -> Optional[str]:
    """
    Extract remitter / recipient name from transaction narrations.

    Patterns handled (in priority order):
    - NACH (DR): "NACH-10-DR-HDFC-LIFE-12345-RTN" → "HDFC"  (first segment between 3rd and last dash)
    - IFT: "IFT RAJU KUMAR 12345" → "RAJU KUMAR"
    - UPI: "UPI/RAJU KUMAR/9876@ybl/..." → "RAJU KUMAR"
    - IMPS (Recd): "Recd:IMPS/123/RAJU KUMAR/..." → "RAJU KUMAR"
    - IMPS (Sent): "SentIMPS123456RAJU KUMAR/..." → "RAJU KUMAR"
    - IMPS (generic /): any "...IMPS/.../NAME/..." → 3rd /-segment
    - RTGS: "RTGS 12345 RAJU KUMAR HDFC" → "RAJU KUMAR"
    - MB:RECEIVED FROM: "MB:RECEIVED FROM RAJU KUMAR" → "RAJU KUMAR"
    - NEFT: "NEFT 12345 RAJU KUMAR" → "RAJU KUMAR"

    Digit runs are stripped from any extracted name. If the cleaned name is
    not longer than 3 chars, the full (digit-stripped) narration is returned
    so callers always have a non-empty handle to group on.

    Args:
        narration: Raw transaction narration

    Returns:
        Extracted name (digit-stripped) or None if narration was empty.
    """
    if not narration:
        return None

    narration = narration.strip()
    upper = narration.upper()

    # Pattern 0: NACH — "NACH-<digits>-DR-<merchant>-...-<ref>" (case insensitive).
    nach_match = re.match(r'^NACH-\d+-DR-(.+)$', narration, re.IGNORECASE)
    if nach_match:
        rest = nach_match.group(1)
        if '-' in rest:
            segs = rest.split('-')
            # Drop trailing segment (ref code / bounce reason); take first remaining.
            merchant = segs[0]
        else:
            merchant = rest
        return _finalize(merchant, narration)

    # Pattern 1: IFT — "IFT <name words> <last_token>"
    if narration.startswith("IFT"):
        parts = narration.split()
        if len(parts) >= 3:
            return _finalize(' '.join(parts[1:-1]), narration)
        return _finalize(None, narration)

    # Pattern 2: UPI — "UPI/NAME/ID/..."
    if upper.startswith("UPI/"):
        parts = narration.split('/')
        if len(parts) >= 2:
            return _finalize(parts[1], narration)
        return _finalize(None, narration)

    # Pattern 3: IMPS received — "Recd:IMPS/.../NAME/..."
    if "Recd:IMPS/" in narration:
        parts = narration.split('/')
        if len(parts) >= 3:
            return _finalize(parts[2], narration)
        return _finalize(None, narration)

    # Pattern 4: IMPS sent — "SentIMPS<digits>[<name>][/...]"
    if "SentIMPS" in narration or "sentimps" in narration.lower():
        if '/' in narration:
            head = narration.split('/', 1)[0]
            stripped = re.sub(r'^SentIMPS', '', head, flags=re.IGNORECASE).strip()
            if stripped.isdigit():
                merchant = stripped
            else:
                merchant = re.sub(r'^\d+', '', stripped).strip()
            return _finalize(merchant, narration)
        else:
            stripped = re.sub(r'SentIMPS\s*', '', narration, flags=re.IGNORECASE).strip()
            return _finalize(stripped, narration)

    # Pattern 4b: Generic IMPS-with-slash — "...IMPS/x/NAME/..." → 3rd /-segment.
    if "IMPS/" in upper:
        parts = narration.split('/')
        if len(parts) >= 3:
            return _finalize(parts[2], narration)
        # Fallback: strip "IMPS" prefix and use the rest.
        return _finalize(re.sub(r'IMPS\s*', '', narration, flags=re.IGNORECASE), narration)

    # Pattern 5: RTGS — "RTGS <code> <name words> <bank>"
    if narration.startswith("RTGS"):
        parts = narration.split()
        if len(parts) >= 3:
            name = ' '.join(parts[2:-1]) if len(parts) > 3 else parts[2]
            return _finalize(name, narration)
        return _finalize(None, narration)

    # Pattern 6: MB:RECEIVED FROM — "MB:RECEIVED FROM <name>"
    if narration.startswith("MB:RECEIVED FROM"):
        parts = narration.split("RECEIVED FROM")
        if len(parts) >= 2:
            return _finalize(parts[1], narration)
        return _finalize(None, narration)

    # Pattern 6b: MB:RECEIVED MONEY / MB:SENT MONEY — self-transfers.
    if narration.startswith("MB:SENT MONEY") or narration.startswith("MB:RECEIVED MONEY"):
        if '/' in narration:
            purpose = narration.split('/', 1)[1]
            return _finalize(purpose, narration)
        return _finalize(None, narration)

    # Pattern 7: NEFT — "NEFT <code> <name words>"
    if narration.startswith("NEFT"):
        parts = narration.split()
        if len(parts) >= 3:
            return _finalize(' '.join(parts[2:]), narration)
        return _finalize(None, narration)

    return None


def clean_narration(text: str) -> Optional[str]:
    """
    Lightweight cleanup of narration for display as a fallback merchant name.

    Unlike normalize_narration (which strips digits and lowercases for fuzzy
    matching), this preserves readability: keeps digits, title-cases, and
    only removes special characters.

    Args:
        text: Raw narration string

    Returns:
        Cleaned title-cased string, or None if empty after cleaning
    """
    if not text:
        return None

    text = re.sub(r'[^a-zA-Z0-9\s]', ' ', text)  # Remove special chars, keep letters+digits
    text = re.sub(r'\s+', ' ', text).strip()       # Collapse whitespace

    if not text:
        return None

    return text.title()


FUZZY_GROUP_THRESHOLD = 70  # token_set_ratio score (0-100) — shared default


def are_similar(s1: str, s2: str, threshold: int = FUZZY_GROUP_THRESHOLD) -> bool:
    """Canonical fuzzy similarity check for two narrations / merchant names.

    Compares ``normalize_narration``-d forms with ``fuzz.token_set_ratio``;
    falls back to case-insensitive equality when fuzzywuzzy is unavailable.
    """
    try:
        from fuzzywuzzy import fuzz
    except ImportError:
        return s1.lower() == s2.lower()
    n1 = normalize_narration(s1)
    n2 = normalize_narration(s2)
    if not n1 or not n2:
        return False
    return fuzz.token_set_ratio(n1, n2) >= threshold


def fuzzy_group_keys(keys, threshold: int = FUZZY_GROUP_THRESHOLD):
    """Greedy single-pass fuzzy grouping over a list of name strings.

    Uses ``fuzz.token_set_ratio`` on ``normalize_narration``-d versions of the
    names; returns a dict mapping each input key to its canonical
    representative (the first key that established the group). Deterministic:
    keys are sorted before grouping.

    Falls back to exact lower-cased match when ``fuzzywuzzy`` is unavailable.

    Args:
        keys: iterable of name strings to group.
        threshold: similarity score 0-100 (default 70).

    Returns:
        ``{original_key: representative_key}`` for every input.
    """
    try:
        from fuzzywuzzy import fuzz
        have_fuzz = True
    except ImportError:
        have_fuzz = False

    out: dict = {}
    reps: list = []  # list of (original_rep, normalized_rep)
    for k in sorted({str(x) for x in keys if x is not None}, key=str.lower):
        nk = normalize_narration(k)
        chosen = None
        for rep_orig, rep_norm in reps:
            if not nk or not rep_norm:
                if k.lower() == rep_orig.lower():
                    chosen = rep_orig
                    break
                continue
            if have_fuzz:
                if fuzz.token_set_ratio(nk, rep_norm) >= threshold:
                    chosen = rep_orig
                    break
            else:
                if nk == rep_norm:
                    chosen = rep_orig
                    break
        if chosen is None:
            reps.append((k, nk))
            out[k] = k
        else:
            out[k] = chosen
    return out


_GENERIC_MERCHANT_TOKENS = {
    "emi", "loan", "bank", "payment", "nach", "neft", "imps", "upi", "rtgs",
    "credit", "debit", "transfer", "fund", "txn", "ref",
}


def _normalize_for_bucket(name: str) -> str:
    """Lowercase, collapse non-alphanumerics to single space, keep digits.

    This preserves identifier-like tokens (e.g. ``HDFC0001``, ``BAJAJFINSERV12``)
    that would otherwise be lost by ``normalize_narration`` (which strips
    digits), so the exact bucket step distinguishes different lenders.
    """
    if not name:
        return ""
    t = name.lower()
    t = re.sub(r"[^a-z0-9]+", " ", t)
    t = re.sub(r"\s+", " ", t).strip()
    return t


def is_generic_merchant(name: str) -> bool:
    """True if ``name`` is empty, very short, or only generic tokens."""
    norm = _normalize_for_bucket(name)
    if not norm or len(norm) < 4:
        return True
    tokens = [t for t in norm.split() if t]
    if not tokens:
        return True
    return all(t in _GENERIC_MERCHANT_TOKENS for t in tokens)


def exact_then_fuzzy_group(keys, threshold: int = 88):
    """Two-stage grouping: exact bucket on digit-preserving normalized name,
    then a tight fuzzy pass across bucket leaders.

    Stage 1 collapses obvious dupes (case/punctuation noise) while keeping
    digit-bearing identifiers distinct (so two different lender accounts do
    not collide). Stage 2 then uses ``fuzz.token_set_ratio`` at a tighter
    threshold (default 88, vs. the legacy 70) on the digit-stripped form, so
    cross-bucket fuzzy merges only happen for genuine near-duplicates like
    ``BAJAJ FIN`` / ``BAJAJ FINSERV``.

    Args:
        keys: iterable of name strings to group.
        threshold: tight fuzzy threshold (0-100) for stage 2 (default 88).

    Returns:
        ``{original_key: representative_key}`` for every input.
    """
    try:
        from fuzzywuzzy import fuzz
        have_fuzz = True
    except ImportError:
        have_fuzz = False

    uniq = sorted({str(x) for x in keys if x is not None}, key=str.lower)

    # Stage 1: exact bucket on digit-preserving normalized form.
    bucket_to_rep: dict = {}
    key_to_rep: dict = {}
    for k in uniq:
        b = _normalize_for_bucket(k)
        if b not in bucket_to_rep:
            bucket_to_rep[b] = k
        key_to_rep[k] = bucket_to_rep[b]

    # Stage 2: tight fuzzy merge across bucket leaders only.
    leaders = list(bucket_to_rep.values())
    leader_norms = {ld: normalize_narration(ld) for ld in leaders}
    leader_to_canon: dict = {}
    canon_norms: list = []  # list of (canon_leader, normalized_canon)
    for ld in sorted(leaders, key=str.lower):
        ld_norm = leader_norms[ld]
        chosen = None
        for canon, canon_norm in canon_norms:
            if not ld_norm or not canon_norm:
                if ld.lower() == canon.lower():
                    chosen = canon
                    break
                continue
            if have_fuzz:
                if fuzz.token_set_ratio(ld_norm, canon_norm) >= threshold:
                    chosen = canon
                    break
            else:
                if ld_norm == canon_norm:
                    chosen = canon
                    break
        if chosen is None:
            canon_norms.append((ld, ld_norm))
            leader_to_canon[ld] = ld
        else:
            leader_to_canon[ld] = chosen

    return {k: leader_to_canon[rep] for k, rep in key_to_rep.items()}


def is_salary_narration(narration: str) -> bool:
    """
    Check if narration indicates a salary/income transaction.

    Args:
        narration: Transaction narration

    Returns:
        True if salary-related keywords found
    """
    if not narration:
        return False

    from config.keywords import SALARY_KEYWORDS
    narration_lower = narration.lower()

    return any(keyword in narration_lower for keyword in SALARY_KEYWORDS)
