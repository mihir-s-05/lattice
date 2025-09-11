# LATTICE — LLM-Driven Multi-Agent Orchestrator

LATTICE is a CLI-only, single-process orchestration runtime that coordinates multiple AI agents via an intelligent Router LLM. It provides structured workflow management, contract testing, provenance tracking, web search capabilities, and comprehensive deliverables generation for software development projects.

## Table of Contents
- [Overview](#overview)
- [Key Features](#key-features)
- [Architecture](#architecture)
- [Requirements](#requirements)
- [Installation](#installation)
- [Quick Start](#quick-start)
- [Configuration](#configuration)
- [Web Search Setup](#web-search-setup)
- [CLI Commands](#cli-commands)
- [Execution Modes](#execution-modes)
- [Router Tools](#router-tools)
- [Stage Gates & Contract Tests](#stage-gates--contract-tests)
- [Huddles & Decision Making](#huddles--decision-making)
- [Artifacts & Logs](#artifacts--logs)
- [Provenance & Citations](#provenance--citations)
- [Troubleshooting](#troubleshooting)
- [Development Guide](#development-guide)

## Overview

LATTICE operates as a goal-oriented "agentic loop" where a Router LLM coordinates specialized agents (Backend, Frontend, LLM API, Tests) to accomplish software development tasks. The Router makes one action per turn via structured tool calls, with the host runtime enforcing safety constraints and logging all activities.

**Core workflow:**
1. Router LLM receives condensed run state and tool manifest
2. Issues exactly one tool call per turn
3. Host executes tool and returns structured observation
4. Loop continues until finalization or budget exhaustion
5. System generates comprehensive deliverables and reports

## Key Features

### LLM-Driven Orchestration
- **Router LLM**: Intelligent coordinator that plans, schedules, and manages agent workflows
- **Structured Tool Interface**: 13 core tools for mode switching, agent spawning, testing, and artifact management
- **Safety-First Design**: Host-enforced stage gates, artifact sandboxing, and immutable logs

### Multi-Agent Coordination
- **Backend Agent**: API contracts, server scaffolding, OpenAPI specifications
- **Frontend Agent**: UI wireframes, component schemas, web application scaffolding  
- **LLM API Agent**: Prompt engineering, tool adapters, integration patterns
- **Test Agent**: Contract testing, validation suites, quality assurance

### Advanced Capabilities
- **Web Search Integration**: Groq online search + local SearXNG adapter support
- **RAG (Retrieval-Augmented Generation)**: TF-IDF indexing of project documentation
- **Provenance Tracking**: Complete citation chains from sources to decisions
- **Decision Management**: Structured huddles with synthesis and consensus tracking

## Architecture

LATTICE uses a hub-and-spoke architecture with the Router LLM as the central coordinator:

```
    ┌─────────────────┐
    │   Router LLM    │ ← Central coordinator
    │   (Groq/Local)  │
    └─────────┬───────┘
              │
    ┌─────────┼───────┐
    │         │       │
┌───▼───┐ ┌──▼──–––┐ ┌──▼──┐ ┌───▼────┐
│Backend│ │Frontend│ │Tests│ │LLM API │
│Agent  │ │ Agent  │ │Agent│ │ Agent  │
└───────┘ └────────┘ └─────┘ └────────┘
```

### Execution Modes
- **Ladder**: Sequential, ordered execution path
- **Tracks**: Parallel agent execution with synchronization
- **Weave**: Hybrid critical path + documentation track (default)

## Requirements

- **Python**: 3.9+
- **Dependencies**: `requests`, `trafilatura`, `fastapi`, `PyYAML`, `ulid-py` (auto-installed via pyproject.toml)
- **Providers**: At least one of:
  - Groq API key (recommended for Router)
  - Gemini API key (recommended for Agents)  
  - Local LM Studio server at `http://localhost:1234/v1`

## Installation

### Option 1: Editable Install
```bash
cd lattice
pip install -e .
```

### Option 2: Module Execution
```bash
python -m src.lattice.cli <command>
```

## Quick Start

### 1. Set API Credentials
```bash
# Router (Groq - recommended)
export GROQ_API_KEY="gsk_..."

# Agents (Gemini - recommended)  
export GEMINI_API_KEY="AI..."

# Optional: Local fallback (LM Studio)
# Ensure LM Studio is running at http://localhost:1234/v1
```

### 2. Run Your First Project
```bash
lattice run "build a task management API with web frontend"
```

### 3. Monitor Progress
```bash
# View formatted model outputs
lattice logs <run_id> --output-only

# Follow logs in real-time
lattice logs <run_id> --follow
```

### 4. Inspect Results
```bash
# Project structure created under:
~/.lattice/runs/<run_id>/artifacts/
├── backend/        # FastAPI application
├── frontend/       # Web interface  
├── contracts/      # OpenAPI specs & tests
├── decisions/      # Decision summaries
└── deliverables/   # Final ZIP bundle
```

Note: By default, runs are stored under `~/.lattice/runs`. You can override this location by setting `LATTICE_RUNS_DIR` to a custom path.

## Configuration

LATTICE is configured via environment variables:

### Provider Configuration
```bash
# Provider precedence order
LATTICE_ROUTER_PROVIDER_ORDER="groq,lmstudio"  # Router fallback chain
LATTICE_AGENT_PROVIDER_ORDER="gemini-openai-compat,lmstudio"  # Agent fallback

# Force specific providers
LATTICE_ROUTER_PROVIDER="groq"      # Override router provider
LATTICE_AGENT_PROVIDER="gemini-openai-compat"   # Override agent provider

# Model selection
LATTICE_ROUTER_MODEL="openai/gpt-oss-120b"  # Router model
LATTICE_AGENT_MODEL="gemini-2.5-flash-lite" # Agent model
```

### Execution Configuration
```bash
# Execution mode (default: weave)
LATTICE_MODE="weave|ladder|tracks"

# Router behavior 
LATTICE_ROUTER_POLICY="llm"         # "llm" = agentic, "policy" = deterministic
LATTICE_ROUTER_MAX_STEPS="32"       # Safety budget (max actions)

# Huddle mode
LATTICE_HUDDLES="dialog"            # "dialog" or "synthesis"

# Model parameters
LATTICE_TEMPERATURE="0.2"           # Sampling temperature
LATTICE_MAX_TOKENS="4000"           # Max response tokens
```

### RAG & Search Configuration
```bash
# RAG settings
LATTICE_USE_RAG="1"                 # Enable/disable RAG
LATTICE_RAG_MIN_SCORE="0.15"        # Minimum relevance score
LATTICE_RAG_MAX_INGEST="20"         # Max files to index

# Web search
LATTICE_WEB_SEARCH="auto"           # "auto", "on", or "off"
```

## Web Search Setup

LATTICE supports two web search modes:

### 1. Groq Online Search (Recommended)
When using Groq as the Router provider with compatible models (`openai/gpt-oss-20b` or `openai/gpt-oss-120b`), web search is automatically enabled via Groq's built-in `browser_search` tool.

**Setup:**
```bash
export GROQ_API_KEY="gsk_..."
export LATTICE_ROUTER_PROVIDER="groq"
export LATTICE_ROUTER_MODEL="openai/gpt-oss-120b"
# Web search automatically available
```

### 2. Local SearXNG Adapter
For local/offline scenarios or when using LM Studio, configure a local SearXNG instance:

#### SearXNG Installation (Docker)
```bash
# Clone SearXNG
git clone https://github.com/searxng/searxng-docker.git
cd searxng-docker

# Configure settings (optional - edit .env file for customization)
# cp .env.template .env

# Start SearXNG
docker-compose up -d

# SearXNG will be available at http://localhost:8080
# Verify it's running: curl http://localhost:8080/search?format=json\&q=test
```

#### Configure LATTICE for Local Search
```bash
# Enable local adapter
export LATTICE_WEB_SEARCH_ADAPTER_SEARCH_BASE_URL="http://localhost:8080"

# Optional: Customize search behavior
export LATTICE_WEB_SEARCH_ADAPTER_DEFAULT_ENGINES="bing,brave,wikipedia"
export LATTICE_WEB_SEARCH_ADAPTER_LANGUAGE="en"
export LATTICE_WEB_SEARCH_ADAPTER_TIME_RANGE="month"
export LATTICE_WEB_SEARCH_ADAPTER_K="5"  # Pages to fetch per search

# Content extraction method
export LATTICE_WEB_SEARCH_ADAPTER_FETCH_TYPE="trafilatura"  # or "firecrawl"

# Optional: Firecrawl integration (requires separate service)
export LATTICE_WEB_SEARCH_ADAPTER_FIRECRAWL_BASE_URL="http://localhost:3002/v1"
export LATTICE_WEB_SEARCH_ADAPTER_FIRECRAWL_API_KEY="your_key"
```

#### Search Adapter Configuration Options
```bash
# Caching and performance
LATTICE_WEB_SEARCH_ADAPTER_CACHE_DIR="$HOME/.lattice/runs/<run_id>/cache"
LATTICE_WEB_SEARCH_ADAPTER_RESPECT_ROBOTS="on"

# Content filtering  
LATTICE_WEB_SEARCH_ADAPTER_DENYLIST_DOMAINS="example.com,bad.site"
```

## CLI Commands

### `lattice run`
Execute a goal-driven workflow:

```bash
lattice run "create a REST API for inventory management"

# Options:
--router-provider groq          # Override router provider
--router-model openai/gpt-oss-120b  # Override router model  
--huddles synthesis             # Huddle mode (dialog|synthesis)
--no-websearch                  # Disable web search
--no-rag                        # Disable RAG
```

### `lattice logs`
Inspect run logs and outputs:

```bash
lattice logs run-20241201-143022-abc123

# Options:
--output-only, -O               # Show only model outputs (formatted)
--follow, -f                    # Tail logs in real-time
```

### `lattice scrub`
Remove sensitive data from logs:

```bash
lattice scrub                   # Scrub all runs
lattice scrub run-20241201-143022-abc123  # Scrub specific run
```

## Execution Modes

### Weave Mode (Default)
Hybrid approach combining critical path execution with parallel documentation:
- **Critical Path**: Backend → Tests → Frontend (sequential)
- **Docs Track**: README/documentation generation (parallel)
- **Knowledge Integration**: Automatic replanning based on new information

### Ladder Mode  
Strictly sequential execution:
1. API contracts & tests
2. Backend scaffold
3. Frontend scaffold  
4. Smoke tests & validation

### Tracks Mode
Parallel execution with synchronization:
- All agents work simultaneously
- Periodic synchronization points
- Huddles for conflict resolution

## Router Tools

The Router LLM has access to 13 core tools for workflow orchestration:

### Planning & Mode Control
- **`set_mode`**: Switch execution mode (ladder|tracks|weave)
- **`open_huddle`**: Convene agents for interface alignment
- **`record_decision_summary`**: Log structured decisions with provenance

### Agent Management  
- **`spawn_agents`**: Create agent instances (frontend|backend|llmapi|tests)
- **`schedule_slice`**: Execute parallel agent workflows
- **`inject_summary`**: Share decision context between agents

### Information Retrieval
- **`rag_search`**: Query project documentation index
- **`web_search`**: Search web for relevant information (when enabled)

### Quality Assurance
- **`run_contract_tests`**: Execute validation suites
- **`propose_advance_step`**: Request stage gate progression

### Artifact Management
- **`write_artifact`**: Create project files
- **`read_artifact`**: Access existing artifacts
- **`finalize_run`**: Complete workflow and generate deliverables

## Stage Gates & Contract Tests

LATTICE enforces quality through progressive stage gates:

### Default Stage Gates
1. **sg_api_contract**: API specification validity
2. **sg_be_scaffold**: Backend implementation presence
3. **sg_fe_scaffold**: Frontend scaffold completion  
4. **sg_smoke**: Integration test passage

### Contract Test Types
- **Schema**: OpenAPI specification validation
- **Unit**: File existence and structure checks  
- **HTTP**: Endpoint functionality verification

### Gate Evaluation Process
```bash
# Gates are evaluated via boolean expressions:
tests.pass('api_contract') and artifact.exists('backend/**')

# Results include:
{
  "status": "passed|failed|blocked",
  "evidence": [...],      # Supporting artifacts/RAG docs
  "conditions": [...]     # Evaluated conditions
}
```

## Huddles & Decision Making

### Huddle Modes

#### Dialog Mode (Default)
Interactive multi-agent conversation:
- Agents propose interface changes
- Consensus tracking via `AGREE: yes|no`
- Blocker identification and resolution
- Router synthesis of final decisions

#### Synthesis Mode
LLM-driven decision generation:
- Direct decision synthesis without agent dialog
- Faster execution for clear-cut scenarios
- Automatic provenance linking

### DecisionSummary Structure
```json
{
  "id": "ds_01234567890abcdef",
  "topic": "API Authentication Strategy", 
  "options": ["JWT", "OAuth2", "API Keys"],
  "decision": "JWT with refresh tokens",
  "rationale": "Balances security with implementation simplicity",
  "risks": ["Token expiration handling", "Storage security"],
  "actions": [
    {"owner": "backend", "task": "Implement JWT middleware"}
  ],
  "contracts": [
    {"name": "auth_contract", "schema_hash": "sha256:..."}
  ],
  "sources": [
    {"type": "external", "url": "https://jwt.io/introduction/"},
    {"type": "artifact", "id": "artifacts/contracts/openapi.yaml"}
  ],
  "links": [
    {"title": "Huddle Transcript", "url": "artifacts/huddles/hud_..."}
  ]
}
```

## Artifacts & Logs

### Directory Structure
```
~/.lattice/runs/<run_id>/
├── artifacts/
│   ├── backend/           # FastAPI application
│   │   ├── app/main.py    # Server implementation
│   │   ├── requirements.txt
│   │   └── run.sh         # Startup script
│   ├── frontend/          # Web interface
│   │   ├── app/
│   │   │   ├── index.html
│   │   │   ├── script.js
│   │   │   └── styles.css
│   │   └── run.sh
│   ├── cli/               # Command-line interface
│   │   └── main.py        # CLI implementation
│   ├── contracts/         # API specifications
│   │   ├── openapi.yaml   # OpenAPI 3.1 spec
│   │   ├── tests/         # Contract tests
│   │   └── results/       # Test results
│   ├── decisions/         # Decision summaries
│   ├── huddles/           # Meeting transcripts
│   ├── plans/             # Execution plans
│   ├── finalization/      # Final reports
│   │   └── report.json
│   ├── deliverables/      # Packaged outputs
│   │   └── deliverables.zip
│   └── README.md          # Project documentation
├── run.jsonl              # Structured event log
└── config.json            # Run configuration
```

### Log Events
The `run.jsonl` file contains structured events:
- Model calls (provider, latency, tokens)
- Tool executions (parameters, observations)  
- Gate evaluations (conditions, evidence)
- Huddle activities (participants, decisions)
- Web search queries (results, performance)
- Provider fallbacks (reasons, timings)

## Provenance & Citations

LATTICE maintains complete traceability from sources to decisions:

### EvidenceRef Types
```json
// External web sources
{"type": "external", "url": "https://...", "title": "..."}

// Project artifacts  
{"type": "artifact", "id": "artifacts/...", "hash": "sha256:..."}

// RAG documents
{"type": "rag_doc", "id": "doc-id", "score": 0.85}
```

### Citation Pipeline
1. **Collection**: Sources gathered during web search, RAG queries
2. **Association**: Linked to decisions via `sources[]` field
3. **Validation**: Hash verification for artifact drift detection
4. **Consolidation**: Decision log with inline citations generated
5. **Indexing**: Machine-readable citation index for tooling

### Finalization Outputs
- **Decision Log**: `artifacts/decisions/decision_log.md` - Human-readable summary
- **Citation Index**: `artifacts/citations/index.json` - Machine-readable mappings
- **Drift Report**: Detection of changed artifacts since decision time

## Troubleshooting

### Common Issues

#### No Output Generated
```bash
# Check provider connectivity
lattice logs <run_id> | grep "model_call"

# Verify API keys
echo $GROQ_API_KEY
echo $GEMINI_API_KEY
```

#### Web Search Unavailable
```bash
# For Groq online search
export LATTICE_ROUTER_PROVIDER="groq"
export LATTICE_ROUTER_MODEL="openai/gpt-oss-120b"

# For local SearXNG
export LATTICE_WEB_SEARCH_ADAPTER_SEARCH_BASE_URL="http://localhost:8080"
```

#### Contract Tests Failing
```bash
# Ensure test definitions exist
ls ~/.lattice/runs/<run_id>/artifacts/contracts/tests/

# Check test results
cat ~/.lattice/runs/<run_id>/artifacts/contracts/results/*.json
```

#### Provider Fallbacks
```bash
# Monitor fallback chain in logs
lattice logs <run_id> | grep "provider_switch"
```

### Debug Mode
For detailed debugging, examine the structured logs:
```bash
# Raw JSONL events (all system events)
cat ~/.lattice/runs/<run_id>/run.jsonl

# Formatted model outputs only (clean, readable)
lattice logs <run_id> --output-only

# Follow real-time execution (tail -f behavior)
lattice logs <run_id> --follow

# Both follow and output-only can be combined
lattice logs <run_id> --follow --output-only
```

## Development Guide

### Project Structure
```
src/lattice/
├── cli.py              # Command-line interface
├── router.py           # Main orchestration logic
├── router_llm.py       # Router LLM integration
├── agents.py           # Specialized agent implementations
├── providers.py        # LLM provider abstraction
├── contracts.py        # Contract testing framework
├── stage_gates.py      # Progressive validation gates
├── huddle.py           # Decision-making coordination
├── finalize.py         # Deliverable generation
├── rag.py              # TF-IDF retrieval system
├── artifacts.py        # File management system
├── provenance.py       # Citation tracking
├── constants.py        # Configuration constants
└── templates/          # Code generation templates
    ├── backend/        # FastAPI templates
    ├── frontend/       # Web app templates  
    └── cli/            # CLI templates
```

### Key Extension Points

#### Custom Agents
```python
from lattice.agents import BaseAgent

class CustomAgent(BaseAgent):
    def plan(self, step_or_goal: str, context: Dict[str, Any]) -> AgentPlan:
        # Define agent planning logic
        pass
    
    def act(self, inputs: Dict[str, Any]) -> List[ArtifactRef]:
        # Implement agent actions
        pass
```

#### Custom Contract Tests
```python
# Add to artifacts/contracts/tests/contract_tests.json
{
  "id": "custom_test",
  "subject": "CustomValidation", 
  "type": "custom",
  "runner": "local",
  "custom_checks": [...]
}
```

#### Custom Router Tools
Tools are defined in `router.py:_build_tools_manifest()` and implemented in `router.py:_run_agentic()`.

### Architecture Principles

1. **Single Responsibility**: Each agent handles specific domain concerns
2. **Immutable Logs**: All actions logged to append-only JSONL
3. **Structured Observations**: Tool calls return normalized JSON responses
4. **Host Authority**: Safety constraints enforced by runtime, not LLM
5. **Provider Agnostic**: Abstract provider interface supports multiple LLMs
6. **Comprehensive Provenance**: Full citation chains from sources to deliverables

### Contributing

When modifying LATTICE:

1. **Preserve Tool Interface**: Router tools maintain backward compatibility
2. **Extend Constants**: Add new defaults to `constants.py`
3. **Log Everything**: New actions should emit structured events
4. **Test Contracts**: Validate changes don't break existing workflows
5. **Update Templates**: Ensure code generation templates stay current
