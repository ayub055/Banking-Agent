"""Configuration settings for the system."""

import os

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# =============================================================================
# MODEL CONFIGURATION
# =============================================================================

MODEL_NAME = "llama3.2"
MAX_ITERATIONS = 10

# Pipeline models
PARSER_MODEL = "mistral"          # Intent parsing + report planner — needs fast JSON, no think tags
EXPLAINER_MODEL = "llama3.2"      # Real-time query chat explainer — fast response
SUMMARY_MODEL = "llama3.2" # Report summaries — reasoning model with <think> traces

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

# Streaming
STREAM_DELAY: float = 0.025      # Seconds between streamed chunks (0 = no delay)

# =============================================================================
# DATA PATHS — Change these when switching to new data files
# =============================================================================

# Transaction data
TRANSACTIONS_FILE = os.path.join(_PROJECT_ROOT, "data", "rgs.csv")
TRANSACTIONS_DELIMITER = "\t"

# Bureau DPD tradeline data
BUREAU_DPD_FILE = os.path.join(_PROJECT_ROOT, "dpd_data.csv")
BUREAU_DPD_DELIMITER = "\t"

# Pre-computed tradeline features
TL_FEATURES_FILE = os.path.join(_PROJECT_ROOT, "tl_features.csv")
TL_FEATURES_DELIMITER = "\t"

# Internal salary algorithm outputs
RG_SAL_FILE = os.path.join(_PROJECT_ROOT, "rg_sal_strings.csv")
RG_SAL_DELIMITER = "\t"
RG_INCOME_FILE = os.path.join(_PROJECT_ROOT, "rg_income_strings.csv")
RG_INCOME_DELIMITER = "\t"

LOG_DIR = "logs"

# =============================================================================
# SETTINGS
# =============================================================================

VERBOSE_MODE = True
STREAMING_ENABLED = True
USE_LLM_EXPLAINER = True
