"""Type de champ "Liste à boutons" (choice) — retour client : styliser les
champs du formulaire qui proposent un choix parmi plusieurs options, comme
sur les documents papier existants (ex: "Rapport créé pour : Plancher /
Ressources / Dubeau")."""
from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from django.test import TestCase

from .forms import build_dynamic_form
from .models import ApprovalRule, Request, RequestType, UserProfile

CHOICE_SCHEMA = {
    "fields": [
        {
            "name": "priorite", "type": "choice", "label": "Priorité", "required": True,
            "choices": ["Faible", "Normale", "Urgente"],
        },
    ]
}


class ChoiceFieldFormTests(TestCase):
    def setUp(self):
        self.request_type = RequestType.objects.create(
            name="Type avec choix", code="CHOICE", form_schema=CHOICE_SCHEMA
        )

    def test_dynamic_form_exposes_configured_choices(self):
        form = build_dynamic_form(self.request_type)
        field = form.fields["priorite"]
        self.assertEqual([c[0] for c in field.choices], ["Faible", "Normale", "Urgente"])

    def test_valid_choice_is_accepted(self):
        form = build_dynamic_form(self.request_type, data={"priorite": "Urgente"})
        self.assertTrue(form.is_valid())

    def test_value_outside_configured_choices_is_rejected(self):
        form = build_dynamic_form(self.request_type, data={"priorite": "Inexistant"})
        self.assertFalse(form.is_valid())

    def test_required_choice_field_cannot_be_left_empty(self):
        form = build_dynamic_form(self.request_type, data={})
        self.assertFalse(form.is_valid())


class ChoiceFieldRequestFlowTests(TestCase):
    def setUp(self):
        self.manager = User.objects.create_user("manager_choice", password="x")
        self.employee = User.objects.create_user("employee_choice", password="x")
        UserProfile.objects.create(user=self.employee, manager=self.manager)
        self.request_type = RequestType.objects.create(
            name="Type avec choix", code="CHOICE", form_schema=CHOICE_SCHEMA
        )
        ApprovalRule.objects.create(
            request_type=self.request_type, level=1, criteria={}, approvers_config={"type": "manager"}
        )
        self.client.login(username="employee_choice", password="x")

    def test_submitting_request_form_stores_the_selected_choice(self):
        response = self.client.post(
            f"/new/{self.request_type.id}/", {"action": "submit", "priorite": "Normale"}
        )
        self.assertEqual(response.status_code, 302)
        req = Request.objects.get(request_type=self.request_type)
        self.assertEqual(req.data["priorite"], "Normale")


class ChoiceFieldSchemaValidationTests(TestCase):
    def test_choice_type_with_choices_list_is_valid(self):
        rt = RequestType(name="Valide", code="VALIDE1", form_schema=CHOICE_SCHEMA)
        rt.full_clean()  # ne doit pas lever

    def test_unknown_field_type_is_rejected(self):
        rt = RequestType(
            name="Invalide", code="INVALIDE1",
            form_schema={"fields": [{"name": "x", "type": "not_a_real_type"}]},
        )
        with self.assertRaises(ValidationError):
            rt.full_clean()
