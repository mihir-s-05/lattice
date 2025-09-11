import os
from typing import Dict, Any, Optional
from pathlib import Path
from .errors import TemplateError, handle_template_error


class TemplateLoader:
    
    def __init__(self, template_root: Optional[str] = None):
        if template_root is None:
            self.template_root = Path(__file__).parent / "templates"
        else:
            self.template_root = Path(template_root)
    
    def load_template(self, template_path: str) -> str:
        full_path = self.template_root / template_path
        try:
            with open(full_path, 'r', encoding='utf-8') as f:
                return f.read()
        except FileNotFoundError:
            raise TemplateError(f"Template not found: {template_path}", template_path)
        except Exception as e:
            raise handle_template_error(e, template_path)
    
    def render_template(self, template_path: str, context: Optional[Dict[str, Any]] = None) -> str:
        template_content = self.load_template(template_path)
        
        if context is None:
            return template_content
            
        rendered = template_content
        for key, value in context.items():
            placeholder = f"{{{{{key}}}}}"
            rendered = rendered.replace(placeholder, str(value))
            
        return rendered
    
    def get_frontend_templates(self, context: Optional[Dict[str, Any]] = None) -> Dict[str, str]:
        templates = {}
        frontend_path = self.template_root / "frontend"
        
        if not frontend_path.exists():
            return templates
            
        for template_file in frontend_path.glob("*"):
            if template_file.is_file():
                key = template_file.name
                try:
                    templates[key] = self.render_template(f"frontend/{key}", context)
                except Exception:
                    templates[key] = self.load_template(f"frontend/{key}")
                    
        return templates
    
    def get_backend_templates(self, context: Optional[Dict[str, Any]] = None) -> Dict[str, str]:
        templates = {}
        backend_path = self.template_root / "backend"
        
        if not backend_path.exists():
            return templates
            
        for template_file in backend_path.glob("*"):
            if template_file.is_file():
                key = template_file.name
                try:
                    templates[key] = self.render_template(f"backend/{key}", context)
                except Exception:
                    templates[key] = self.load_template(f"backend/{key}")
                    
        return templates
    
    def get_cli_templates(self, context: Optional[Dict[str, Any]] = None) -> Dict[str, str]:
        templates = {}
        cli_path = self.template_root / "cli"
        
        if not cli_path.exists():
            return templates
            
        for template_file in cli_path.glob("*"):
            if template_file.is_file():
                key = template_file.name
                try:
                    templates[key] = self.render_template(f"cli/{key}", context)
                except Exception:
                    templates[key] = self.load_template(f"cli/{key}")
                    
        return templates


_template_loader = None

def get_template_loader() -> TemplateLoader:
    global _template_loader
    if _template_loader is None:
        _template_loader = TemplateLoader()
    return _template_loader


def render_template(template_path: str, context: Optional[Dict[str, Any]] = None) -> str:
    return get_template_loader().render_template(template_path, context)


def get_frontend_templates(context: Optional[Dict[str, Any]] = None) -> Dict[str, str]:
    return get_template_loader().get_frontend_templates(context)


def get_backend_templates(context: Optional[Dict[str, Any]] = None) -> Dict[str, str]:
    return get_template_loader().get_backend_templates(context)


def get_cli_templates(context: Optional[Dict[str, Any]] = None) -> Dict[str, str]:
    return get_template_loader().get_cli_templates(context)