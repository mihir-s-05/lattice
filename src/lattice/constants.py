import os
from typing import Dict, List

DEFAULT_ARTIFACT_DIR = "artifacts"
DEFAULT_DECISION_DIR = "artifacts/decisions"
DEFAULT_HUDDLE_DIR = "artifacts/huddles"
DEFAULT_BACKEND_DIR = "artifacts/backend"
DEFAULT_FRONTEND_DIR = "artifacts/frontend"
DEFAULT_CONTRACTS_DIR = "artifacts/contracts"
DEFAULT_RESULTS_DIR = "artifacts/contracts/results"
DEFAULT_KNOWLEDGE_DIR = "artifacts/knowledge"
DEFAULT_PLANS_DIR = "artifacts/plans"
DEFAULT_FINALIZATION_DIR = "artifacts/finalization"
DEFAULT_CITATIONS_DIR = "artifacts/citations"

DEFAULT_CONFIG_FILE = "config.json"
DEFAULT_RUN_LOG_FILE = "run.jsonl"
DEFAULT_RAG_INDEX_FILE = "rag_index.json"
DEFAULT_ARTIFACT_INDEX_FILE = "index.json"
DEFAULT_SIGNALS_FILE = "signals.jsonl"
DEFAULT_PLAN_GRAPH_FILE = "plan_graph.json"
DEFAULT_SNAPSHOT_FILE = "snapshot.json"
DEFAULT_DELIVERABLES_FILE = "deliverables.zip"
DEFAULT_DECISION_LOG_FILE = "decision_log.md"
DEFAULT_CITATIONS_INDEX_FILE = "index.json"

DEFAULT_RAG_TOKEN_LIMIT = 50000
DEFAULT_RAG_SNIPPET_LENGTH = 500
DEFAULT_RAG_TOP_K = 5
DEFAULT_RAG_MIN_SCORE = 0.15
DEFAULT_RAG_MAX_INGEST_FILES = 20
DEFAULT_RAG_MAX_FILE_SIZE = 1024 * 1024

DEFAULT_ROUTER_PROVIDER_ORDER = ["groq", "lmstudio"]
DEFAULT_AGENT_PROVIDER_ORDER = ["gemini-openai-compat", "lmstudio"]

DEFAULT_MODEL_BY_PROVIDER: Dict[str, str] = {
    "groq": "openai/gpt-oss-120b",
    "gemini-openai-compat": "gemini-2.5-flash-lite",
    "lmstudio": "gpt-oss-20b",
}

DEFAULT_HTTP_TIMEOUT = 60
DEFAULT_ROUTER_MAX_STEPS = 32
DEFAULT_TEMPERATURE = 0.2
DEFAULT_MAX_TOKENS = None
DEFAULT_RETRY_COUNT = 2
DEFAULT_MAX_RETRY_DELAY = 8

DEFAULT_ARTIFACT_ID_LENGTH = 16
DEFAULT_HASH_ALGORITHM = "sha256"

DEFAULT_MESSAGE_MAX_LENGTH = 800
DEFAULT_CONTENT_MAX_LENGTH = 1200
DEFAULT_TRANSCRIPT_NOTES_MAX_LENGTH = 2000
DEFAULT_OUTPUT_PREVIEW_LENGTH = 500

DEFAULT_STAGE_GATES = [
    {
        "id": "sg_api_contract",
        "name": "API contract passes",
        "conditions": ["tests.pass('api_contract')"]
    },
    {
        "id": "sg_be_scaffold",
        "name": "Backend scaffold present",
        "conditions": ["tests.pass('api_contract') and artifact.exists('backend/**')"]
    },
    {
        "id": "sg_fe_scaffold",
        "name": "Frontend scaffold present",
        "conditions": ["artifact.exists('frontend/**')"]
    },
    {
        "id": "sg_smoke",
        "name": "Smoke tests pass",
        "conditions": ["tests.pass('smoke_suite')"]
    }
]

DEFAULT_EXECUTION_MODE = "weave"
SUPPORTED_EXECUTION_MODES = ["ladder", "tracks", "weave"]

DEFAULT_HUDDLE_MODE = "dialog"
SUPPORTED_HUDDLE_MODES = ["dialog", "synthesis"]

DEFAULT_ROUTER_POLICY = "llm"
SUPPORTED_ROUTER_POLICIES = ["llm", "policy"]

DEFAULT_WEB_SEARCH_MODE = "auto"
SUPPORTED_WEB_SEARCH_MODES = ["auto", "on", "off"]

ENV_VAR_KEYS = [
    "LATTICE_PROVIDER_ORDER",
    "LATTICE_ROUTER_PROVIDER_ORDER",
    "LATTICE_AGENT_PROVIDER_ORDER",
    "LATTICE_PROVIDER",
    "LATTICE_ROUTER_PROVIDER",
    "LATTICE_AGENT_PROVIDER",
    "LATTICE_MODEL",
    "LATTICE_ROUTER_MODEL",
    "LATTICE_AGENT_MODEL",
    "LATTICE_BASE_URL",
    "LATTICE_USE_RAG",
    "LATTICE_TEMPERATURE",
    "LATTICE_MAX_TOKENS",
    "LATTICE_HUDDLES",
    "LATTICE_MODE",
    "LATTICE_ROUTER_POLICY",
    "LATTICE_WEB_SEARCH",
    "LATTICE_WEB_SEARCH_ADAPTER_URL",
    "LATTICE_WEB_SEARCH_ADAPTER_ENABLED",
    "LATTICE_WEB_SEARCH_ADAPTER_SEARCH_BASE_URL",
    "LATTICE_WEB_SEARCH_ADAPTER_DEFAULT_ENGINES",
    "LATTICE_WEB_SEARCH_ADAPTER_LANGUAGE",
    "LATTICE_WEB_SEARCH_ADAPTER_TIME_RANGE",
    "LATTICE_WEB_SEARCH_ADAPTER_FETCH_TYPE",
    "LATTICE_WEB_SEARCH_ADAPTER_FIRECRAWL_BASE_URL",
    "LATTICE_WEB_SEARCH_ADAPTER_FIRECRAWL_API_KEY",
    "LATTICE_WEB_SEARCH_ADAPTER_K",
    "LATTICE_WEB_SEARCH_ADAPTER_CACHE_DIR",
    "LATTICE_WEB_SEARCH_ADAPTER_RESPECT_ROBOTS",
    "LATTICE_WEB_SEARCH_ADAPTER_DENYLIST_DOMAINS",
    "LATTICE_ROUTER_MAX_STEPS",
    "LATTICE_RAG_MIN_SCORE",
    "LATTICE_RAG_MAX_INGEST",
    "LATTICE_GPTOSS_TEMPERATURE",
    "LATTICE_GPTOSS_TOP_K",
    "LATTICE_GPTOSS_MIN_P",
    "LATTICE_GPTOSS_TOP_P",
    "GROQ_BASE_URL",
    "GROQ_API_KEY",
    "GROQ_MODEL",
    "GEMINI_BASE_URL",
    "GEMINI_API_KEY",
    "GEMINI_MODEL",
    "LMSTUDIO_BASE_URL",
    "LMSTUDIO_API_KEY",
    "LMSTUDIO_MODEL",
]

SENSITIVE_ENV_PATTERNS = ["KEY", "TOKEN", "SECRET", "PASSWORD"]

RAG_INGEST_PATTERNS = [
    "README*",
    "readme*",
    "docs/**/*.md",
    "docs/**/*.txt",
    "documentation.txt",
]

TEMPLATE_FILE_EXTENSIONS = [".html", ".js", ".css", ".py", ".sh", ".md", ".json", ".yaml", ".yml"]


def get_runs_base_dir() -> str:
    """Return the centralized base directory for runs.

    Defaults to ~/.lattice/runs, but can be overridden via the LATTICE_RUNS_DIR
    environment variable. Ensures the directory exists.
    """
    base = os.environ.get("LATTICE_RUNS_DIR")
    if not base or not str(base).strip():
        base = os.path.join(os.path.expanduser("~"), ".lattice", "runs")
    try:
        os.makedirs(base, exist_ok=True)
    except Exception:
        fallback = os.path.join(os.getcwd(), "runs")
        os.makedirs(fallback, exist_ok=True)
        return fallback
    return base