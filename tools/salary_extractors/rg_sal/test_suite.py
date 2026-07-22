"""RG_SAL test suite. Run: python -m tools.salary_extractors.rg_sal.test_suite"""

from pathlib import Path

from tools.salary_extractors._eval import run
from tools.salary_extractors.rg_sal import rg_sal_calculate

if __name__ == "__main__":
    run(Path(__file__).parent, lambda df: rg_sal_calculate(df=df), "crn", "final_salary")
