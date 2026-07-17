"""Configuration settings for the system."""

import os

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# =============================================================================
# MODEL CONFIGURATION
# =============================================================================

# LLM provider: "ollama" (local) or "kotak" (Model Gateway API)
LLM_PROVIDER = os.getenv("LLM_PROVIDER", "ollama").lower()

# Kotak Model Gateway — used only when LLM_PROVIDER == "kotak" (see utils/llm_factory.py)
KOTAK_TOKEN_URL = os.getenv("KOTAK_TOKEN_URL",
    "https://uat.api.idam.kotak.internal/oauth2/token")
KOTAK_API_URL = os.getenv("KOTAK_API_URL",
    "https://dev.ai.kotak.internal/model/anthropic/api/v1/chat/completions")
KOTAK_CLIENT_ID = os.getenv("KOTAK_CLIENT_ID", "")
KOTAK_CLIENT_SECRET = os.getenv("KOTAK_CLIENT_SECRET", "")
KOTAK_SCOPE = os.getenv("KOTAK_SCOPE", "openid profile email")
KOTAK_CA_BUNDLE = os.getenv("KOTAK_CA_BUNDLE", "")     # path to kotak-ca.pem ("" = system CAs)
# Set to "false" for the dev gateway (internal CA not trusted); ignored if CA bundle is set
KOTAK_VERIFY_SSL = os.getenv("KOTAK_VERIFY_SSL", "true").strip().lower() not in ("false", "0", "no")
KOTAK_MODEL = os.getenv("KOTAK_MODEL", "sonnet3.5")
KOTAK_MAX_TOKENS = int(os.getenv("KOTAK_MAX_TOKENS", "1024"))

# Pipeline models
SUMMARY_MODEL = "llama3.2"        # Report summaries (customer review narration)

# Models that support the Ollama `think` parameter (emit <think> reasoning traces)
THINKING_MODEL_PREFIXES = ("deepseek-r1", "qwq", "qwen3", "phi4-reasoning")


def is_thinking_model(model_name: str) -> bool:
    """Check if a model supports reasoning/thinking mode."""
    name = model_name.lower()
    return any(name.startswith(p) for p in THINKING_MODEL_PREFIXES)

# LLM inference parameters — change here to affect all LLM calls
LLM_TEMPERATURE: float = 0       # Deterministic output for all analytical calls
LLM_TEMPERATURE_CREATIVE: float = 0.1  # Slightly creative — used for persona generation
LLM_SEED: int = 42               # Reproducibility seed

# =============================================================================
# DATA PATHS — Change these when switching to new data files
# =============================================================================

# Delimiter for all input CSVs (tab-separated)
CSV_DELIMITER = "\t"

# Transaction data
TRANSACTIONS_FILE = os.path.join(_PROJECT_ROOT, "data/xn_d1.csv")

# Internal salary algorithm outputs
RG_SAL_FILE = os.path.join(_PROJECT_ROOT, "data/rg_sal_strings.csv")
RG_INCOME_FILE = os.path.join(_PROJECT_ROOT, "data/rg_income_strings.csv")

LOG_DIR = "logs"
