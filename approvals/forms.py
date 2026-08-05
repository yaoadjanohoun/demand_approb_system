"""Construction d'un formulaire Django dynamique à partir d'un RequestType.form_schema."""
import datetime

from django import forms
from django.contrib.auth import get_user_model

from .models import Department, Site, UserProfile
from .validators import validate_person_name

User = get_user_model()


class ProfilePhotoForm(forms.ModelForm):
    class Meta:
        model = UserProfile
        fields = ["photo"]


class RequestContextForm(forms.Form):
    """Département/site concerné par la demande, distinct du profil du
    demandeur — affiché uniquement quand RequestType.requires_context_selection
    est actif (voir WorkflowEngine._routing_department_id/_routing_site_id)."""

    context_department = forms.ModelChoiceField(
        queryset=Department.objects.order_by("name"), label="Département concerné",
        empty_label="— Choisir —",
    )
    context_site = forms.ModelChoiceField(
        queryset=Site.objects.order_by("name"), label="Site concerné",
        required=False, empty_label="— Non applicable —",
    )


class PersonalInfoForm(forms.ModelForm):
    """Champs qu'un utilisateur peut modifier lui-même (retour client) — le
    reste (manager, département, site, rôle) reste réservé à un admin."""

    # Déclarés explicitement (plutôt que laissés à ModelForm) pour y ajouter
    # validate_person_name — le User.first_name/last_name de Django n'a par
    # défaut aucune restriction de caractères (retour déploiement : un champ
    # "Prénom" enregistré avec une suite de "/" cassait l'affichage du nom
    # complet partout dans l'app).
    first_name = forms.CharField(max_length=150, required=False, label="Prénom", validators=[validate_person_name])
    last_name = forms.CharField(max_length=150, required=False, label="Nom", validators=[validate_person_name])

    class Meta:
        model = User
        fields = ["username", "first_name", "last_name", "email"]


FIELD_BUILDERS = {
    "text": lambda field_def: forms.CharField(widget=forms.Textarea(attrs={"rows": 3})),
    "number": lambda field_def: forms.IntegerField(),
    "decimal": lambda field_def: forms.DecimalField(max_digits=12, decimal_places=2),
    "date": lambda field_def: forms.DateField(widget=forms.DateInput(attrs={"type": "date"})),
    "boolean": lambda field_def: forms.BooleanField(),
    # "Liste à boutons" (retour client) : options définies par l'admin dans le
    # constructeur de formulaire (FormSchemaBuilderWidget), affichées en radio.
    "choice": lambda field_def: forms.ChoiceField(
        choices=[(c, c) for c in field_def.get("choices", [])],
        widget=forms.RadioSelect,
    ),
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
        field = builder(field_def)
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


def grouped_form_fields(form, request_type):
    """Regroupe les champs liés (BoundField) d'un formulaire dynamique par
    section (voir grouped_labeled_data) — sert à afficher request_form.html
    avec les mêmes sous-titres que le détail de la demande et le PDF."""
    field_defs = request_type.form_schema.get("fields", [])
    sections = {f["name"]: f.get("section", "") for f in field_defs}

    groups = []
    index_by_section = {}
    for bound_field in form:
        section = sections.get(bound_field.name, "")
        if section not in index_by_section:
            index_by_section[section] = len(groups)
            groups.append({"section": section, "fields": []})
        groups[index_by_section[section]]["fields"].append(bound_field)

    groups.sort(key=lambda g: g["section"] != "")
    return groups


def labeled_data(request_type, data):
    """Associe chaque valeur de Request.data à son label configuré dans le
    form_schema (au lieu du nom technique, ex: "date_debut" -> "Date de début"),
    dans l'ordre du formulaire. Les clés obsolètes non présentes dans le schéma
    (champ supprimé depuis) gardent leur nom technique en repli."""
    field_defs = request_type.form_schema.get("fields", [])
    labels = {f["name"]: f.get("label") or f["name"].replace("_", " ").capitalize() for f in field_defs}
    sections = {f["name"]: f.get("section", "") for f in field_defs}
    highlights = {f["name"]: f.get("highlight", False) for f in field_defs}
    decimal_fields = {f["name"] for f in field_defs if f["type"] == "decimal"}
    currency = request_type.default_currency

    rows = []
    seen = set()
    for field_def in field_defs:
        name = field_def["name"]
        if name not in data:
            continue
        rows.append({
            "label": labels[name],
            "value": _format_value(data[name], name in decimal_fields, currency),
            "section": sections[name],
            "highlight": highlights[name],
        })
        seen.add(name)
    for name, value in data.items():
        if name not in seen:
            rows.append({
                "label": labels.get(name, name),
                "value": _format_value(value, name in decimal_fields, currency),
                "section": sections.get(name, ""),
                "highlight": highlights.get(name, False),
            })
    return rows


def grouped_labeled_data(request_type, data):
    """Comme labeled_data, mais regroupé par section (retour client : un PDF
    papier existant présente ses champs sous des sous-titres comme
    "Informations sur le demandeur" — voir RequestType.form_schema, clé
    "section" par champ). Les champs sans section vont dans un groupe sans
    titre, en premier ; l'ordre des groupes suit leur première apparition
    dans form_schema."""
    groups = []
    index_by_section = {}
    for row in labeled_data(request_type, data):
        section = row["section"]
        if section not in index_by_section:
            index_by_section[section] = len(groups)
            groups.append({"section": section, "rows": []})
        groups[index_by_section[section]]["rows"].append(row)

    groups.sort(key=lambda g: g["section"] != "")  # groupe sans titre toujours en premier
    return groups


def _format_value(value, is_decimal=False, currency=""):
    if isinstance(value, bool):
        return "Oui" if value else "Non"
    if value is None or value == "":
        return "—"
    if is_decimal and currency:
        return f"{value} {currency}"
    return value
