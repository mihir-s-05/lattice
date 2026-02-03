import pytest


VALID_OPENAPI = """openapi: 3.0.0
info:
  title: Test
  version: "1.0"
paths: {}
"""

INVALID_OPENAPI = """openapi: 3.0.0
info:
  title: Test
  version: "1.0"
"""


def test_openapi_validation_falls_back_without_validator(monkeypatch):
    from lattice import contracts

    def _nope():
        raise ImportError("openapi-spec-validator not installed")

    monkeypatch.setattr(contracts, "_load_openapi_validator", _nope)
    out = contracts._validate_openapi(VALID_OPENAPI)
    assert out["method"] == "regex"
    assert out["score"] >= 1
    assert out.get("validation_errors")


def test_openapi_validation_regex_on_yaml_parse_error():
    from lattice import contracts

    out = contracts._validate_openapi("openapi: [")
    assert out["method"] == "regex"
    assert out.get("validation_errors")


def test_openapi_validation_uses_validator_when_available():
    pytest.importorskip("openapi_spec_validator")
    from lattice import contracts

    out = contracts._validate_openapi(VALID_OPENAPI)
    assert out["method"] == "openapi-spec-validator"
    assert out["schema_valid"] is True
    assert out.get("validation_errors") == []


def test_openapi_validation_reports_errors_for_invalid_spec():
    pytest.importorskip("openapi_spec_validator")
    from lattice import contracts

    out = contracts._validate_openapi(INVALID_OPENAPI)
    assert out["method"] == "openapi-spec-validator"
    assert out["schema_valid"] is False
    assert out.get("validation_errors")
