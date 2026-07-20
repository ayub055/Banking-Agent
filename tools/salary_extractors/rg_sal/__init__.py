"""RG_SAL salary extractor — see extractor.py for the pipeline and CONVERSION_NOTES.md §5."""

from tools.salary_extractors.rg_sal.extractor import (
    RGSalExtractor,
    rg_sal_calculate,
)

__all__ = ["RGSalExtractor", "rg_sal_calculate"]
