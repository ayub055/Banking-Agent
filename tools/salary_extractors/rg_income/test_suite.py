"""RG income test suite. Run: python -m tools.salary_extractors.rg_income.test_suite"""

from pathlib import Path

from tools.salary_extractors._eval import run
from tools.salary_extractors.rg_income import RGIncomeExtractor

if __name__ == "__main__":
    run(Path(__file__).parent,
        lambda df: RGIncomeExtractor().extract(df=df), "cust_id", "total_income")
