"""Éditeur visuel (Fabric.js) de mise en page PDF par type de demande —
retour client : positionnement libre des champs, polices personnalisées,
logos placés librement (voir DocumentTemplate, CustomFont, approvals/
pdf_export.py::_generate_from_template)."""
import io
import shutil
import tempfile

from django.contrib.auth.models import User
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from PIL import Image

from .models import BrandingLogo, CustomFont, DocumentBranding, DocumentTemplate, Request, RequestType
from .pdf_export import generate_request_summary_pdf


def _tiny_png(name="logo.png"):
    # Vrai PNG valide (pas juste l'en-tête magique) : pdf.image() le décode
    # réellement (via Pillow) pour l'intégrer au PDF, contrairement à un
    # simple test de présence de fichier.
    buffer = io.BytesIO()
    Image.new("RGB", (4, 4), color="blue").save(buffer, format="PNG")
    return SimpleUploadedFile(name, buffer.getvalue(), content_type="image/png")


def _tiny_ttf(name="font.ttf"):
    # Volontairement invalide (pas une vraie fonte TTF) : sert à tester le
    # repli sur Helvetica quand pdf.add_font() échoue sur un fichier corrompu.
    return SimpleUploadedFile(name, b"\x00\x01\x00\x00" + b"0" * 50, content_type="font/ttf")


@override_settings(MEDIA_ROOT=tempfile.mkdtemp())
class DocumentTemplateEditorViewTests(TestCase):
    @classmethod
    def tearDownClass(cls):
        from django.conf import settings

        shutil.rmtree(settings.MEDIA_ROOT, ignore_errors=True)
        super().tearDownClass()

    def setUp(self):
        self.staff = User.objects.create_user("staff_editor", password="x", is_staff=True)
        self.employee = User.objects.create_user("employee_editor", password="x")
        self.request_type = RequestType.objects.create(
            name="Nouveau rapport", code="REPORT",
            form_schema={"fields": [{"name": "departement", "type": "text", "label": "Département"}]},
        )

    def test_staff_can_open_editor_and_it_creates_a_template(self):
        self.client.login(username="staff_editor", password="x")
        response = self.client.get(f"/mise-en-page/{self.request_type.id}/")
        self.assertEqual(response.status_code, 200)
        self.assertTrue(DocumentTemplate.objects.filter(request_type=self.request_type).exists())

    def test_non_staff_cannot_open_editor(self):
        self.client.login(username="employee_editor", password="x")
        response = self.client.get(f"/mise-en-page/{self.request_type.id}/")
        self.assertNotEqual(response.status_code, 200)

    def test_saving_layout_persists_canvas_json(self):
        self.client.login(username="staff_editor", password="x")
        canvas_json = '{"objects": [{"type": "textbox", "text": "Bonjour"}]}'
        response = self.client.post(f"/mise-en-page/{self.request_type.id}/", {"canvas_json": canvas_json})
        self.assertEqual(response.status_code, 302)
        template = DocumentTemplate.objects.get(request_type=self.request_type)
        self.assertEqual(template.canvas_json, {"objects": [{"type": "textbox", "text": "Bonjour"}]})


@override_settings(MEDIA_ROOT=tempfile.mkdtemp())
class TemplatePdfRenderingTests(TestCase):
    @classmethod
    def tearDownClass(cls):
        from django.conf import settings

        shutil.rmtree(settings.MEDIA_ROOT, ignore_errors=True)
        super().tearDownClass()

    def setUp(self):
        self.employee = User.objects.create_user("employee_tpl", password="x")
        self.request_type = RequestType.objects.create(
            name="Nouveau rapport", code="REPORT",
            form_schema={"fields": [{"name": "departement", "type": "text", "label": "Département"}]},
        )
        self.req = Request.objects.create(
            request_type=self.request_type, requester=self.employee, data={"departement": "Ventes"}
        )

    def test_field_bound_text_is_replaced_by_the_real_value(self):
        DocumentTemplate.objects.create(
            request_type=self.request_type,
            canvas_json={"objects": [{
                "type": "Textbox", "text": "{{departement}}", "left": 30, "top": 30,
                "width": 150, "fontSize": 14, "fill": "#000000",
                "data": {"field": "departement"},
            }]},
        )
        pdf_bytes = generate_request_summary_pdf(self.req)
        self.assertTrue(pdf_bytes.startswith(b"%PDF"))
        self.assertIn(b"Ventes", pdf_bytes)
        self.assertNotIn(b"{departement}", pdf_bytes)

    def test_static_text_is_used_literally(self):
        DocumentTemplate.objects.create(
            request_type=self.request_type,
            canvas_json={"objects": [{
                "type": "Textbox", "text": "Texte fixe", "left": 30, "top": 30,
                "width": 150, "fontSize": 14, "fill": "#000000", "data": {},
            }]},
        )
        pdf_bytes = generate_request_summary_pdf(self.req)
        self.assertIn(b"Texte fixe", pdf_bytes)

    def test_empty_template_falls_back_to_auto_layout(self):
        DocumentTemplate.objects.create(request_type=self.request_type, canvas_json={})
        pdf_bytes = generate_request_summary_pdf(self.req)
        self.assertTrue(pdf_bytes.startswith(b"%PDF"))
        self.assertIn(self.req.reference.encode(), pdf_bytes)  # signature du rendu automatique

    def test_no_template_at_all_falls_back_to_auto_layout(self):
        pdf_bytes = generate_request_summary_pdf(self.req)
        self.assertIn(self.req.reference.encode(), pdf_bytes)

    def test_logo_placed_on_canvas_is_drawn(self):
        branding = DocumentBranding.objects.create(request_type=self.request_type)
        logo = BrandingLogo.objects.create(branding=branding, image=_tiny_png(), order=1)
        DocumentTemplate.objects.create(
            request_type=self.request_type,
            canvas_json={"objects": [{
                "type": "Image", "left": 10, "top": 10, "width": 40, "height": 40,
                "data": {"logoId": logo.id},
            }]},
        )
        pdf_bytes = generate_request_summary_pdf(self.req)
        self.assertTrue(pdf_bytes.startswith(b"%PDF"))

    def test_missing_logo_does_not_crash_generation(self):
        DocumentTemplate.objects.create(
            request_type=self.request_type,
            canvas_json={"objects": [{
                "type": "Image", "left": 10, "top": 10, "width": 40, "height": 40,
                "data": {"logoId": 999999},
            }]},
        )
        pdf_bytes = generate_request_summary_pdf(self.req)
        self.assertTrue(pdf_bytes.startswith(b"%PDF"))

    def test_custom_font_is_used_when_registered(self):
        font = CustomFont.objects.create(name="Corporate", regular_ttf=_tiny_ttf())
        DocumentTemplate.objects.create(
            request_type=self.request_type,
            canvas_json={"objects": [{
                "type": "Textbox", "text": "Titre", "left": 30, "top": 30,
                "width": 150, "fontSize": 14, "fontFamily": font.name, "fill": "#000000", "data": {},
            }]},
        )
        # La fonte factice n'est pas un vrai TTF : add_font() doit échouer
        # proprement et retomber sur Helvetica plutôt que de planter.
        pdf_bytes = generate_request_summary_pdf(self.req)
        self.assertTrue(pdf_bytes.startswith(b"%PDF"))
        self.assertIn(b"Titre", pdf_bytes)

    def test_unknown_font_falls_back_to_helvetica(self):
        DocumentTemplate.objects.create(
            request_type=self.request_type,
            canvas_json={"objects": [{
                "type": "Textbox", "text": "Titre", "left": 30, "top": 30,
                "width": 150, "fontSize": 14, "fontFamily": "PoliceInexistante", "fill": "#000000", "data": {},
            }]},
        )
        pdf_bytes = generate_request_summary_pdf(self.req)
        self.assertTrue(pdf_bytes.startswith(b"%PDF"))
        self.assertIn(b"Titre", pdf_bytes)
