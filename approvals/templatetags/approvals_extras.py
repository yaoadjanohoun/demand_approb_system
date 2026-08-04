from django import template

from ..forms import grouped_form_fields

register = template.Library()


@register.filter
def group_fields(form, request_type):
    """Regroupe les champs d'un formulaire dynamique par section, pour
    request_form.html — voir forms.grouped_form_fields."""
    return grouped_form_fields(form, request_type)
