"""
RG_SAL keyword mappings, thresholds and per-method config.

Pure data consumed by extractor.py — the salary-detection keywords, the three
method exclusion lists, the Company whitelist (all verbatim from RG_SAL.ipynb
cells 8/9/14/15/20/21/22) and the DEFAULT_PARAMS thresholds. Kept out of the
extractor so the notebook keyword sets can be edited in one place.
"""

# ─── Parameters (thresholds from the notebook) ──────────────────────────────

DEFAULT_PARAMS = {
    "min_amount": 10000,          # Tran_GTE_10K gate
    "lookback_months": 6,         # trailing window
    "px_threshold": 0.4,          # Px_GTE_40_PER (Percent method)
    "min_txn": 3,                 # per-customer credit-txn count gate (>=3)
    "max_txn": 100,               # per-customer credit-txn count gate (<=100)
    "min_months": 3,              # per-customer distinct-month gate (>=3)
    "cluster_fuzzy": 70,          # name_match_ratio threshold (was DIFFERENCE > 3)
    "self_transfer_fuzzy": 60,    # fuzzy_score threshold (self-transfer flag)
    "refine_min_txn": 3,          # refine validity gate: 3..7 max-per-month rows
    "refine_max_txn": 7,
    "median_tol": 0.2,            # ±20% median band (range_status)
    "median_tol_50": 0.5,         # ±50% median band (range_status_50_per)
    "day_diff": 5,                # day-of-month proximity window
}


# ─── Merchant salary-keyword blocks (cells 9 / 15 / 22) ─────────────────────
# Salary/Company use the base set; Percent adds '%compensation%' (the ONLY
# merchant-CASE difference across the three methods — §4 "similar, not identical").

SAL_KEYWORDS_BASE = [
    "%SALARY%", "% SALARY %", "%WAGE%", "salary%", "sal %", "%/salary%", "%/salary",
    "%/ salary%", "%/ sal %", "%/sal %", "%/ salar%", "%/salar", "%-sal", "%-sal-",
    "%cognizantsal%", "%salar%", "%payroll%", "%WAGES%", "%BULK DEPOSIT%", "% INCOME %",
    "PROFESSIONAL FEE%", "STIPEND%", "%PROFESSIONALFEE%", "%PROFESSIONAL FEE%", "%STIPEND%",
]
SAL_KEYWORDS_PERCENT = SAL_KEYWORDS_BASE + ["%compensation%"]


# ─── Method-specific exclusion lists (cells 8 / 14 / 20) ────────────────────
# Kept as three separate verbatim lists — they genuinely differ.

EXCL_PERCENT = [
    r"%PYT LOAN A\C SPLN%", "%MB PL%", "%NB PL%", "%LOS PL%", "%LOM PL%", "%WAC PL%",
    "%KW PL%", "PLCC%", "%SHARE KHAN%", "%INSTALLMENT%", "%SECURITIES%", "%ADVANTAGE%",
    "% RD", "RD %", "%MOBILE BANKING PL%", "%HDFC BANK LTD RA OP%", "%HDFC BANK LTD%",
    "%HDFC DISB FUNDED HDFC%", "%BAJAJ FINANCE LTD S%", "%BAJAJ FINANCE LTD PAY HDFC%",
    "%ICICI BANK LTD RAOG%", "%ICICI BANK LTD RAOG NEFT DISB%", "%FULLERTON INDIA CREDIT COMP%",
    "%RA-LOAN DISBURSEMENT A/C%", "%MUTHOOT FINANCE LIM%", "%LOAN FROM CLIX%",
    "%PL DISBURSEMENT SUSP%", "%RA DISBURSEMENT A/C%", "%MYLOANCARE VENTURES%", "%LOP %",
    "%PPR%", "%DISB%", "%DIS%", "%TATA CAPITAL%", "%ADITYABIRLAFINANCEL%", "%STANDARD CHARTERED%",
    "%INDUSND BANK CHENNA%", "%INCRED FINANCIAL SERVICES%", "%MUTHOOT FINANCE LIM%", "UPI%",
    "UPI/%", "% NAV %", "UPIR%", "SXFR%", "CDEP%", "SWEEP %", "%CRE001%", "CCPMT%", "DEPBK%",
    "%RENT", "%IMPS%", "%INTEREST%", "%FUNDING%", "%CASH%", "%LOAN%", "%REFUND%", "%PROVIDENT%",
    "% OWN %", "%/OWN %", "%/ OWN %", "%/ OWN%", "% SELF %", "%/SELF%", "% NRE %", "%MATURITY%",
    "FD %", "%PRINCIPAL%", "MB:RECEIVED%", "CASH DEPOSIT%", "% TAX%", "TD %", "%TRF FROM KS%",
    "% P2P %", "% ZERODHA %", "% ANGEL ONE %", "% UPSTOX %", "% 5PAISA %", "% FYERS %",
    "% PAYTM MONEY %", "% CLG %", "% CHQ %", "% CHARGESWAGES %", "% CHARGES %", "% ADVANCES %",
    "% PAYU %", "%MUTHOOT%",
]

EXCL_SALARY = [
    r"%PYT LOAN A\C SPLN%", "%MB PL%", "%NB PL%", "%LOS PL%", "%LOM PL%", "%WAC PL%",
    "%KW PL%", "PLCC%", "%SHARE KHAN%", "%INSTALLMENT%", "%SECURITIES%", "%ADVANTAGE%",
    "% RD", "RD %", "%MOBILE BANKING PL%", "%RA-LOAN DISBURSEMENT A/C%", "%LOAN FROM CLIX%",
    "%PL DISBURSEMENT SUSP%", "%RA DISBURSEMENT A/C%", "%MYLOANCARE VENTURES%", "%LOP %",
    "%PPR%", "%DISB%", "%DIS%", "UPI%", "% NAV %", "UPIR%", "SXFR%", "CDEP%", "SWEEP %",
    "%CRE001%", "CCPMT%", "DEPBK%", "% RENT", "%INTEREST%", "%FUNDING%", "%CASH%", "%LOAN%",
    "%REFUND%", "%PROVIDENT%", "% OWN %", "%/OWN %", "% SELF %", "%/SELF%", "% NRE %",
    "%MATURITY%", "FD %", "%PRINCIPAL%", "MB:RECEIVED%", "CASH DEPOSIT%", "% TAX%", "TD %",
    "%TRF FROM KS%", "% P2P %", "% ZERODHA %", "% ANGEL ONE %", "% UPSTOX %", "% 5PAISA %",
    "% FYERS %", "% PAYTM MONEY %", "% CLG %", "% CHQ %", "% CHARGESWAGES %", "% CHARGES %",
    "% ADVANCES %", "% PAYU %", "%MUTHOOT%",
]

EXCL_COMPANY = [
    r"%PYT LOAN A\C SPLN%", "%MB PL%", "%NB PL%", "%LOS PL%", "%LOM PL%", "%WAC PL%",
    "%KW PL%", "PLCC%", "%SHARE KHAN%", "%INSTALLMENT%", "%SECURITIES%", "%ADVANTAGE%",
    "% RD", "RD %", "%MOBILE BANKING PL%", "%HDFC BANK LTD RA OP%", "%HDFC BANK LTD%",
    "%HDFC DISB FUNDED HDFC%", "%BAJAJ FINANCE LTD S%", "%BAJAJ FINANCE LTD PAY HDFC%",
    "%ICICI BANK LTD RAOG%", "%FULLERTON INDIA CREDIT COMP%", "%RA-LOAN DISBURSEMENT A/C%",
    "%MUTHOOT FINANCE LIM", "%LOAN FROM CLIX%", "%PL DISBURSEMENT SUSP%", "%RA DISBURSEMENT A/C%",
    "%MYLOANCARE VENTURES%", "%PF SETTLEMENT%", "%SETTLEMENT%", "%ENCASHMENT%", "%LOP %",
    "%PPR%", "%DISB%", "%DIS%", "%TATA CAPITAL%", "%ADITYABIRLAFINANCEL%", "%STANDARD CHARTERED%",
    "%INDUSND BANK CHENNA%", "%INCRED FINANCIAL SERVICES%", "%MUTHOOT FINANCE LIM%", "UPI%",
    "% NAV %", "UPIR%", "SXFR%", "CDEP%", "SWEEP %", "%CRE001%", "CCPMT%", "DEPBK%", "% RENT",
    "%/RENT", "%INTEREST%", "%FUNDING%", "%CASH%", "%LOAN%", "%REFUND%", "%PROVIDENT%", "% OWN %",
    "%/OWN %", "%/ OWN %", "%/ OWN%", "% SELF %", "%/SELF%", "% NRE %", "%MATURITY%", "FD %",
    "%PRINCIPAL%", "MB:RECEIVED%", "CASH DEPOSIT%", "% TAX%", "TD %", "%TRF FROM KS%", "% PMSI %",
    "% P2P %", "% ZERODHA %", "% ANGEL ONE %", "% UPSTOX %", "% 5PAISA %", "% FYERS %",
    "% PAYTM MONEY %", "% CLG %", "% CHQ %", "%TRF CHQ%", "% CHARGESWAGES %", "% CHARGES %",
    "% ADVANCES %", "% PAYU %", "%Int.Pd%", "%/emi", "%MUTHOOT%",
]

# Company whitelist (cell 21) — a row must ALSO match one of these to be Company income.
COMPANY_WHITELIST = [
    "% TCS %", "% INFOSYS %", "% WIPRO %", "% ONGC %", "% HCLTECH %", "% HCL TECH %",
    "% TECH MAHINDRA %", "% IOC %", "% SAIL %", "% QUESS CORP %", "% QUESS %", "%RELIANCE%",
    "%L&T INFOTECH%", "% L&T %", "% LARSEN %", "% BHEL %", "%MINDTREE%", "%TATA STEEL%",
    "% NTPC %", "% HINDUSTAN AERON %", "% TEAMLEASE %", "% INTERGLOBE AVI %", "% TML %",
    "% TATA MOTORS %", "% BPCL %", "% MARUTI SUZUKI %", "% HPCL %", "% L&T TECHNOLOGY %",
    "% NLC INDIA %", "% ITC %", "% ULTRATECHCEMENT %", "% ULTRATECH %", "% MTNL %",
    "% BHARAT ELEC %", "% BHARAT ELECTRIC %", "% DR.-REDDYSLABS %", "% NALCO %",
    "% VODAPHONE IDEA %", "% POWER GRID CORP %", "% HINDALCO %", "% CIPLA %", "% OIL INDIA %",
    "% HERO MOTOCORP %", "% LUPIN %", "% SUN PHARMA %", "% HUL %", "% MPHASIS %", "% GRASIM %",
    "% ASHOK LEYLAND %", "% AUROBINDO PHARM %", "% SIEMENS %", "% SPICEJET %", "% BHARTI AIRTEL %",
    "% GAIL %", "% APOLLO HOSPITAL %", "% NHPC %", "% NESTLE %", "% JSW STEEL %", "% COFORGE LTD %",
    "% BAJAJ AUTO %", "% HINDUJA GLOBAL %", "% BANDHAN BANK %", "% MRF %", "% BOSCH %",
    "% MOTHERSON SUMI %", "% PUNJAB & SIND %", "% ORACLE FIN SERV %", "% M&M FINANCIAL %",
    "% PERSISTENT %", "% CADILA HEALTH %", "% GLENMARK %", "% ALKEM LAB %", "% TORRENT PHARMA %",
    "% ADITYA BIRLA %", "% NMDC %", "% TITAN COMPANY %", "% MUTHOOT FINANCE %", "% SHRIRAM TRANS %",
    "% TATA COMM %", "% ASIAN PAINTS %", "% CESC %", "% MUTHOOT %", "% ORACLE %", "% MOTHERSON %",
    "% TITAN %", "%PVT%", "%LTD%", "%PRIVATE%", "%CORP%", "%ENTERPRI%", "% LTD%", "% LIMITED%",
    "% INDUSTR%", "% IND%", "% INDIA%", "%TECHNOL%", "%GROUP%", "%SERVICE%", "% POWER%", "%INFRAS%",
    "% LAB%", "%LOGISTIC%", "%CEMENT%", "%AMUL%", "%HEALTH%", "%TYRES%", "%HOSPITAL%", "%ELECTR%",
    "%MOTOR%", "%PAINTS%", "% AUTO%", "%SYSTEM%", "% COMPAN%", "%PHARMA%", "%AGENC%", "%SHIPP%",
    "%CHEMICAL%", "%AGRO%", "%CYCLE%", "% TECH%", "%ENERGY%", "%INFRATECH%", "%FIELD%",
    "%MANAGEMENT%", "%HOTEL%", "%SOLUTION%", "%DEVELOP%", "%RETAIL%", "%INTERNAT%", "%PRODUCT%",
    "% DELOITTE %", "% IBM %", "%PRIVA DIAM%",
]

# Per-method config consumed by RGSalExtractor._run_method.
METHODS = {
    "salary": {  # priority 1
        "exclusions": EXCL_SALARY,
        "pre_gate": "Tran_GTE_10K >= 1",
        "whitelist": None,
        "salary_only": True,             # cell 16: keep only merchant='salary' rows
        "sal_keywords": SAL_KEYWORDS_BASE,
    },
    "percent": {  # priority 2
        "exclusions": EXCL_PERCENT,
        "pre_gate": "Px_GTE_40_PER >= 1 AND Tran_GTE_10K >= 1 AND DATE_BET_20_10 >= 1",
        "whitelist": None,
        "salary_only": False,
        "sal_keywords": SAL_KEYWORDS_PERCENT,
    },
    "company": {  # priority 3
        "exclusions": EXCL_COMPANY,
        "pre_gate": "Tran_GTE_10K >= 1",
        "whitelist": COMPANY_WHITELIST,
        "salary_only": False,
        "sal_keywords": SAL_KEYWORDS_BASE,
    },
}
