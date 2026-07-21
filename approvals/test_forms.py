"""Devise des montants configurable par l'admin (retour client) : la devise
ne doit jamais être écrite en dur dans le code/l'interface, mais choisie par
l'admin sur le RequestType (RequestType.default_currency)."""
from django.test import TestCase

from .forms import labeled_data
from .models import RequestType


class LabeledDataCurrencyTests(TestCase):
    def test_decimal_field_shows_configured_currency(self):
        request_type = RequestType.objects.create(
            name="Note de frais", code="EXPENSE", default_currency="CAD",
            form_schema={"fields": [{"name": "montant", "type": "decimal", "label": "Montant"}]},
        )
        rows = labeled_data(request_type, {"montant": "1500.00"})
        self.assertEqual(rows[0]["value"], "1500.00 CAD")

    def test_decimal_field_without_configured_currency_shows_raw_value(self):
        request_type = RequestType.objects.create(
            name="Sans devise", code="NOCUR",
            form_schema={"fields": [{"name": "montant", "type": "decimal", "label": "Montant"}]},
        )
        rows = labeled_data(request_type, {"montant": "1500.00"})
        self.assertEqual(rows[0]["value"], "1500.00")

    def test_non_decimal_field_unaffected_by_currency(self):
        request_type = RequestType.objects.create(
            name="Congés", code="LEAVE", default_currency="EUR",
            form_schema={"fields": [{"name": "motif", "type": "text", "label": "Motif"}]},
        )
        rows = labeled_data(request_type, {"motif": "Congé annuel"})
        self.assertEqual(rows[0]["value"], "Congé annuel")
