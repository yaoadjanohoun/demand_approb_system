"""JSON Schema validators for the approvals app's JSONFields.

Schemas match "Dictionnaire de Données" section 2 (Schémas A, B, C).
"""
import jsonschema
from django.core.exceptions import ValidationError

FORM_SCHEMA = {  # Schéma A — RequestType.form_schema
    "type": "object",
    "required": ["fields"],
    "properties": {
        "fields": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["name", "type"],
                "properties": {
                    "name": {"type": "string", "pattern": "^[a-z_]+$"},
                    "type": {
                        "type": "string",
                        "enum": ["text", "number", "decimal", "date", "boolean", "file"],
                    },
                    "label": {"type": "string"},
                    "required": {"type": "boolean"},
                },
            },
        },
    },
}

CRITERIA_SCHEMA = {  # Schéma B — ApprovalRule.criteria
    "type": "object",
    "properties": {
        "min_amount": {"type": "number"},
        "max_amount": {"type": "number"},
        "department_ids": {"type": "array", "items": {"type": "integer"}},
        "site_id": {"type": "integer"},
        "country_code": {"type": "string"},
    },
    "additionalProperties": False,
}

APPROVERS_CONFIG_SCHEMA = {  # Schéma C — ApprovalRule.approvers_config
    "type": "object",
    "required": ["type"],
    "properties": {
        "type": {"type": "string", "enum": ["user", "group", "role", "manager", "custom"]},
        "user_id": {"type": "integer"},
        "group_id": {"type": "integer"},
        "role_id": {"type": "integer"},
        "fallback_user_id": {"type": "integer"},
    },
}


def _validate(value, schema, label):
    try:
        jsonschema.validate(instance=value, schema=schema)
    except jsonschema.ValidationError as exc:
        raise ValidationError(f"{label} invalide : {exc.message}") from exc


def validate_form_schema(value):
    _validate(value, FORM_SCHEMA, "form_schema")


def validate_criteria(value):
    _validate(value, CRITERIA_SCHEMA, "criteria")


def validate_approvers_config(value):
    _validate(value, APPROVERS_CONFIG_SCHEMA, "approvers_config")
