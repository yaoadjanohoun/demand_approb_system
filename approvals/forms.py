"""Construction d'un formulaire Django dynamique à partir d'un RequestType.form_schema."""
from django import forms

FIELD_BUILDERS = {
    "text": lambda: forms.CharField(widget=forms.Textarea(attrs={"rows": 3})),
    "number": lambda: forms.IntegerField(),
    "decimal": lambda: forms.DecimalField(max_digits=12, decimal_places=2),
    "date": lambda: forms.DateField(widget=forms.DateInput(attrs={"type": "date"})),
    "boolean": lambda: forms.BooleanField(),
}


def build_dynamic_form(request_type, data=None, initial=None):
    """Génère dynamiquement une classe Form à partir de request_type.form_schema.

    Le type "file" du schéma n'est pas encore supporté (nécessite un modèle
    de pièce jointe dédié) : ces champs sont ignorés pour l'instant.
    """
    field_defs = request_type.form_schema.get("fields", [])
    declared_fields = {}

    for field_def in field_defs:
        field_type = field_def["type"]
        builder = FIELD_BUILDERS.get(field_type)
        if builder is None:
            continue  # type "file" ou inconnu : non supporté pour l'instant
        field = builder()
        field.required = bool(field_def.get("required", False))
        field.label = field_def.get("label") or field_def["name"].replace("_", " ").capitalize()
        declared_fields[field_def["name"]] = field

    form_class = type("DynamicRequestForm", (forms.Form,), declared_fields)
    return form_class(data=data, initial=initial)
