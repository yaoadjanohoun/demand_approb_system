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


_ALLOWED_NAME_EXTRA_CHARS = set(" -'’")


def validate_person_name(value):
    """Prénom/nom : lettres (accents compris), espaces, apostrophes et tirets
    uniquement — retour déploiement : un champ "Prénom" enregistré avec une
    suite de caractères type "Super////////" (aucune validation avant) casse
    la logique métier partout où le nom complet est affiché (listes, emails,
    historique d'audit)."""
    if not value or not any(ch.isalpha() for ch in value):
        raise ValidationError("Doit contenir au moins une lettre.")
    invalid = sorted({ch for ch in value if not (ch.isalpha() or ch in _ALLOWED_NAME_EXTRA_CHARS)})
    if invalid:
        raise ValidationError(
            "Ne peut contenir que des lettres, espaces, apostrophes et tirets "
            f"(caractère(s) non autorisé(s) : {' '.join(invalid)})."
        )


_ALLOWED_ENTITY_EXTRA_CHARS = set(" '&().,-")


def validate_entity_name(value):
    """Nom d'une entité (département, site, type de demande, configuration
    email...) : plus permissif que validate_person_name (chiffres, "&",
    parenthèses, virgule, point tous légitimes ici — ex: "R&D", "Site 12",
    "Gmail (test)") mais reste une liste blanche — retour déploiement :
    une première version en liste noire (ne bloquait que quelques caractères
    "évidemment" invalides) laissait passer des suites de "/", "*", "+", "-",
    "!" sans aucune lettre/chiffre entre eux (ex: "test////...!!!!")."""
    if not value or not any(ch.isalnum() for ch in value):
        raise ValidationError("Doit contenir au moins une lettre ou un chiffre.")
    invalid = sorted({ch for ch in value if not (ch.isalnum() or ch in _ALLOWED_ENTITY_EXTRA_CHARS)})
    if invalid:
        raise ValidationError(f"Caractère(s) non autorisé(s) : {' '.join(invalid)}.")
