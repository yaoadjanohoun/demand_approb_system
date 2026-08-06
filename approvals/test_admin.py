"""Actions personnalisées de l'admin (bouton "Prévisualiser le PDF" sur
RequestType) — retour client : impossible de juger le rendu/la typographie
d'un formulaire sans créer et soumettre une vraie demande."""
from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse

from .models import Request, RequestType


class RequestTypePreviewPdfTests(TestCase):
    def setUp(self):
        self.admin_user = User.objects.create_user(
            "admin_preview", password="x", is_staff=True, is_superuser=True,
        )
        self.request_type = RequestType.objects.create(
            name="Achat Fournisseur", code="PREVIEW_PURCHASE",
            form_schema={"fields": [
                {"name": "montant", "type": "decimal", "label": "Montant", "required": True},
                {"name": "fournisseur", "type": "text", "label": "Fournisseur", "required": True},
                {"name": "date_achat", "type": "date", "label": "Date d'achat", "required": False},
                {"name": "urgent", "type": "boolean", "label": "Urgent", "required": False},
                {"name": "categorie", "type": "choice", "label": "Catégorie",
                 "choices": ["Matériel", "Logiciel"], "required": False},
                {"name": "justificatif", "type": "file", "label": "Justificatif", "required": False},
            ]},
        )
        self.client.login(username="admin_preview", password="x")

    def _url(self):
        return reverse("admin:approvals_requesttype_preview_pdf", args=[self.request_type.id])

    def test_preview_returns_a_pdf(self):
        response = self.client.get(self._url())
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "application/pdf")
        self.assertTrue(response.content.startswith(b"%PDF"))

    def test_preview_does_not_create_a_real_request(self):
        self.client.get(self._url())
        self.assertFalse(Request.objects.filter(request_type=self.request_type).exists())

    def test_preview_includes_sample_values_for_each_field_type(self):
        response = self.client.get(self._url())
        self.assertIn(b"Exemple de texte", response.content)
        self.assertIn(b"123.45", response.content)
        self.assertIn(b"Mat", response.content)  # "Matériel" (accent en latin-1)

    def test_preview_forbidden_for_non_staff_user(self):
        User.objects.create_user("nobody", password="x")
        self.client.logout()
        self.client.login(username="nobody", password="x")
        response = self.client.get(self._url())
        self.assertIn(response.status_code, (302, 403))
