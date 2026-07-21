"""Construction d'un formulaire Django dynamique à partir d'un RequestType.form_schema."""
import datetime

from django import forms

from .models import UserProfile


class ProfilePhotoForm(forms.ModelForm):
    class Meta:
        model = UserProfile
        fields = ["photo"]


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
        if field_type == "date":
            # Pré-rempli avec la date du jour, modifiable par le demandeur (retour client) ;
            # ignoré automatiquement si un `initial` explicite est fourni pour ce champ
            # (ex: correction d'une demande retournée, cf. `initial` du Form Django).
            field.initial = datetime.date.today
        declared_fields[field_def["name"]] = field

    form_class = type("DynamicRequestForm", (forms.Form,), declared_fields)
    return form_class(data=data, initial=initial)


def labeled_data(request_type, data):
    """Associe chaque valeur de Request.data à son label configuré dans le
    form_schema (au lieu du nom technique, ex: "date_debut" -> "Date de début"),
    dans l'ordre du formulaire. Les clés obsolètes non présentes dans le schéma
    (champ supprimé depuis) gardent leur nom technique en repli."""
    field_defs = request_type.form_schema.get("fields", [])
    labels = {f["name"]: f.get("label") or f["name"].replace("_", " ").capitalize() for f in field_defs}
    decimal_fields = {f["name"] for f in field_defs if f["type"] == "decimal"}
    currency = request_type.default_currency

    rows = []
    seen = set()
    for field_def in field_defs:
        name = field_def["name"]
        if name not in data:
            continue
        rows.append({"label": labels[name], "value": _format_value(data[name], name in decimal_fields, currency)})
        seen.add(name)
    for name, value in data.items():
        if name not in seen:
            rows.append({"label": labels.get(name, name), "value": _format_value(value, name in decimal_fields, currency)})
    return rows


def _format_value(value, is_decimal=False, currency=""):
    if isinstance(value, bool):
        return "Oui" if value else "Non"
    if value is None or value == "":
        return "—"
    if is_decimal and currency:
        return f"{value} {currency}"
    return value
