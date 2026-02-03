from typing import Any, Dict, List, Optional


class LatticeError(Exception):

    def __init__(self, message: str, context: Optional[Dict[str, Any]] = None):
        super().__init__(message)
        self.context = context or {}


class ConfigurationError(LatticeError):
    pass


class ProviderError(LatticeError):

    def __init__(self, message: str, provider: Optional[str] = None, attempts: int = 0, context: Optional[Dict[str, Any]] = None):
        super().__init__(message, context)
        self.provider = provider
        self.attempts = attempts


class AgentError(LatticeError):

    def __init__(self, message: str, agent_name: Optional[str] = None, context: Optional[Dict[str, Any]] = None):
        super().__init__(message, context)
        self.agent_name = agent_name


class ContractError(LatticeError):

    def __init__(self, message: str, contract_id: Optional[str] = None, context: Optional[Dict[str, Any]] = None):
        super().__init__(message, context)
        self.contract_id = contract_id


class StageGateError(LatticeError):

    def __init__(self, message: str, gate_id: Optional[str] = None, context: Optional[Dict[str, Any]] = None):
        super().__init__(message, context)
        self.gate_id = gate_id




class RagError(LatticeError):

    def __init__(self, message: str, operation: Optional[str] = None, context: Optional[Dict[str, Any]] = None):
        super().__init__(message, context)
        self.operation = operation


class RouterError(LatticeError):

    def __init__(self, message: str, mode: Optional[str] = None, step: Optional[str] = None, context: Optional[Dict[str, Any]] = None):
        super().__init__(message, context)
        self.mode = mode
        self.step = step


def handle_provider_error(e: Exception, provider: str, attempts: int = 0) -> ProviderError:
    if isinstance(e, ProviderError):
        return e
    
    context = {
        "original_error": str(e),
        "error_type": type(e).__name__
    }
    
    status_code = getattr(e, "status_code", None)
    if status_code is not None:
        context["status_code"] = status_code

    response = getattr(e, "response", None)
    if response is not None:
        context["response"] = str(response)[:500]
        response_status_code = getattr(response, "status_code", None)
        if response_status_code is not None:
            context["status_code"] = response_status_code
     
    return ProviderError(str(e), provider, attempts, context)


def handle_agent_error(e: Exception, agent_name: str) -> AgentError:
    if isinstance(e, AgentError):
        return e
    
    context = {
        "original_error": str(e),
        "error_type": type(e).__name__
    }
    
    return AgentError(f"Agent '{agent_name}' failed: {e}", agent_name, context)




def handle_rag_error(e: Exception, operation: str) -> RagError:
    if isinstance(e, RagError):
        return e
    
    context = {
        "original_error": str(e),
        "error_type": type(e).__name__
    }
    
    return RagError(f"RAG operation '{operation}' failed: {e}", operation, context)
