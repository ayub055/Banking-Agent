"""RG income extractor subpackage.

Re-exports the public extractor so callers can keep using
``from tools.salary_extractors.rg_income import RGIncomeExtractor``.
"""

from .extractor import RGIncomeExtractor

__all__ = ["RGIncomeExtractor"]
