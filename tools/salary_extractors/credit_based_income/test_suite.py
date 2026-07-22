"""Credit-based income test suite.
Run: python -m tools.salary_extractors.credit_based_income.test_suite"""

from pathlib import Path

from tools.salary_extractors._eval import run
from tools.salary_extractors.credit_based_income import _calculate_credit_based_income

if __name__ == "__main__":
    run(Path(__file__).parent,
        lambda df: _calculate_credit_based_income(df=df), "cust_id", "final_income")
