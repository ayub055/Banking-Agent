"""Key findings helpers.

A small generic ``KeyFinding`` dataclass and a ``findings_to_dicts``
serialiser, retained for reuse. The bureau key-findings engine that
previously consumed these was removed with the bureau pipeline, so
nothing in the banking-only flow currently wires them in.
"""

from dataclasses import dataclass, asdict
from typing import Dict, List


@dataclass
class KeyFinding:
    category: str
    finding: str
    inference: str
    severity: str


def findings_to_dicts(findings: List[KeyFinding]) -> List[Dict]:
    return [asdict(f) for f in findings]
