from typing import Any, Dict, List, Optional


class LatticeError(Exception):
    """Base exception for all Lattice-related errors."""
    
    def __init__(self, message: str, context: Optional[Dict[str, Any]] = None):
        super().__init__(message)
        self.context = context or {}


class ConfigurationError(LatticeError):
    """Raised when there are configuration issues."""
    pass


class ProviderError(LatticeError):
    """Raised when there are LLM provider issues."""
    
    def __init__(self, message: str, provider: Optional[str] = None, attempts: int = 0, context: Optional[Dict[str, Any]] = None):
        super().__init__(message, context)
        self.provider = provider
        self.attempts = attempts


class AgentError(LatticeError):
    """Raised when agent execution fails."""
    
    def __init__(self, message: str, agent_name: Optional[str] = None, context: Optional[Dict[str, Any]] = None):
        super().__init__(message, context)
        self.agent_name = agent_name


class ContractError(LatticeError):
    """Raised when contract testing fails."""
    
    def __init__(self, message: str, contract_id: Optional[str] = None, context: Optional[Dict[str, Any]] = None):
        super().__init__(message, context)
        self.contract_id = contract_id


class StageGateError(LatticeError):
    """Raised when stage gate evaluation fails."""
    
    def __init__(self, message: str, gate_id: Optional[str] = None, context: Optional[Dict[str, Any]] = None):
        super().__init__(message, context)
        self.gate_id = gate_id


class TemplateError(LatticeError):
    """Raised when template processing fails."""
    
    def __init__(self, message: str, template_path: Optional[str] = None, context: Optional[Dict[str, Any]] = None):
        super().__init__(message, context)
        self.template_path = template_path


class RagError(LatticeError):
    """Raised when RAG operations fail."""
    
    def __init__(self, message: str, operation: Optional[str] = None, context: Optional[Dict[str, Any]] = None):
        super().__init__(message, context)
        self.operation = operation


class RouterError(LatticeError):
    """Raised when router execution fails."""
    
    def __init__(self, message: str, mode: Optional[str] = None, step: Optional[str] = None, context: Optional[Dict[str, Any]] = None):
        super().__init__(message, context)
        self.mode = mode
        self.step = step


def handle_provider_error(e: Exception, provider: str, attempts: int = 0) -> ProviderError:
    """Convert generic exceptions to ProviderError with context."""
    if isinstance(e, ProviderError):
        return e
    
    context = {
        "original_error": str(e),
        "error_type": type(e).__name__
    }
    
    if hasattr(e, "status_code"):
        context["status_code"] = e.status_code
    if hasattr(e, "response"):
        context["response"] = str(e.response)[:500]  # Truncate long responses
    
    return ProviderError(str(e), provider, attempts, context)


def handle_agent_error(e: Exception, agent_name: str) -> AgentError:
    """Convert generic exceptions to AgentError with context."""
    if isinstance(e, AgentError):
        return e
    
    context = {
        "original_error": str(e),
        "error_type": type(e).__name__
    }
    
    return AgentError(f"Agent '{agent_name}' failed: {e}", agent_name, context)


def handle_template_error(e: Exception, template_path: str) -> TemplateError:
    """Convert generic exceptions to TemplateError with context."""
    if isinstance(e, TemplateError):
        return e
    
    context = {
        "original_error": str(e),
        "error_type": type(e).__name__
    }
    
    return TemplateError(f"Template '{template_path}' failed: {e}", template_path, context)


def handle_rag_error(e: Exception, operation: str) -> RagError:
    """Convert generic exceptions to RagError with context."""
    if isinstance(e, RagError):
        return e
    
    context = {
        "original_error": str(e),
        "error_type": type(e).__name__
    }
    
    return RagError(f"RAG operation '{operation}' failed: {e}", operation, context)