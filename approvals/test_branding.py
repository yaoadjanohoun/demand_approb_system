"""Sections dans form_schema (regroupement des champs sous des sous-titres,
ex: "Informations sur le demandeur") et habillage de document par type de
demande (DocumentBranding : logos, en-tête, pied de page) — retour client :
coller à la structure des formulaires papier existants."""
import re
import shutil
import tempfile

from django.contrib.auth.models import User
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client, TestCase, override_settings

from .forms import grouped_form_fields, grouped_labeled_data, build_dynamic_form
from .models import BrandingLogo, DocumentBranding, Request, RequestType
from .pdf_export import generate_request_summary_pdf


SCHEMA_WITH_SECTIONS = {
    "fields": [
        {"name": "departement", "type": "text", "label": "Département", "required": True, "section": "Demandeur"},
        {"name": "titre_poste", "type": "text", "label": "Titre", "required": False, "section": "Demandeur"},
        {"name": "titre_rapport", "type": "text", "label": "Titre du rapport", "required": True, "section": "Rapport"},
        {"name": "commentaire", "type": "text", "label": "Commentaire libre", "required": False},
    ]
}


class GroupedLabeledDataTests(TestCase):
    def setUp(self):
        self.request_type = RequestType.objects.create(
            name="Nouveau rapport", code="REPORT", form_schema=SCHEMA_WITH_SECTIONS
        )

    def test_fields_are_grouped_by_section_in_schema_order(self):
        data = {
            "departement": "Ventes",
            "titre_poste": "Coordonnateur",
            "titre_rapport": "Suivi hebdomadaire",
            "commentaire": "RAS",
        }
        groups = grouped_labeled_data(self.request_type, data)
        sections = [g["section"] for g in groups]
        self.assertEqual(sections, ["", "Demandeur", "Rapport"])
        self.assertEqual([r["label"] for r in groups[1]["rows"]], ["Département", "Titre"])
        self.assertEqual([r["label"] for r in groups[2]["rows"]], ["Titre du rapport"])

    def test_group_without_section_omitted_when_no_ungrouped_field(self):
        request_type = RequestType.objects.create(
            name="Congé", code="LEAVE",
            form_schema={"fields": [{"name": "motif", "type": "text", "label": "Motif", "section": "Détails"}]},
        )
        groups = grouped_labeled_data(request_type, {"motif": "Vacances"})
        self.assertEqual([g["section"] for g in groups], ["Détails"])

    def test_form_fields_grouped_the_same_way(self):
        form = build_dynamic_form(self.request_type)
        groups = grouped_form_fields(form, self.request_type)
        sections = [g["section"] for g in groups]
        self.assertEqual(sections, ["", "Demandeur", "Rapport"])
        self.assertEqual([f.name for f in groups[1]["fields"]], ["departement", "titre_poste"])


class RequestFormSectionRenderingTests(TestCase):
    def setUp(self):
        self.employee = User.objects.create_user("employee_sec", password="x")
        self.request_type = RequestType.objects.create(
            name="Nouveau rapport", code="REPORT", form_schema=SCHEMA_WITH_SECTIONS
        )
        self.client.login(username="employee_sec", password="x")

    def test_section_titles_shown_on_request_form(self):
        response = self.client.get(f"/new/{self.request_type.id}/")
        self.assertContains(response, "Demandeur")
        self.assertContains(response, "Rapport")

    def test_section_titles_shown_on_request_detail(self):
        req = Request.objects.create(
            request_type=self.request_type, requester=self.employee,
            data={"departement": "Ventes", "titre_rapport": "Suivi"},
        )
        response = self.client.get(f"/{req.pk}/")
        self.assertContains(response, "Demandeur")
        self.assertContains(response, "Rapport")


@override_settings(MEDIA_ROOT=tempfile.mkdtemp())
class DocumentBrandingModelTests(TestCase):
    @classmethod
    def tearDownClass(cls):
        from django.conf import settings

        shutil.rmtree(settings.MEDIA_ROOT, ignore_errors=True)
        super().tearDownClass()

    def _tiny_png(self, name="logo.png"):
        return SimpleUploadedFile(
            name, b"\x89PNG\r\n\x1a\n" + b"0" * 20, content_type="image/png"
        )

    def test_branding_is_specific_to_a_request_type(self):
        rt1 = RequestType.objects.create(name="Type A", code="A", form_schema={"fields": []})
        rt2 = RequestType.objects.create(name="Type B", code="B", form_schema={"fields": []})
        DocumentBranding.objects.create(request_type=rt1, header_text="En-tête A")

        self.assertEqual(rt1.branding.header_text, "En-tête A")
        with self.assertRaises(DocumentBranding.DoesNotExist):
            rt2.branding

    def test_logos_are_ordered(self):
        rt = RequestType.objects.create(name="Type A", code="A", form_schema={"fields": []})
        branding = DocumentBranding.objects.create(request_type=rt)
        second = BrandingLogo.objects.create(branding=branding, image=self._tiny_png("b.png"), order=2)
        first = BrandingLogo.objects.create(branding=branding, image=self._tiny_png("a.png"), order=1)
        self.assertEqual(list(branding.logos.all()), [first, second])


@override_settings(MEDIA_ROOT=tempfile.mkdtemp())
class PdfExportBrandingTests(TestCase):
    """PDF résumé : vérifie que l'habillage (en-tête/pied de page) est bien
    inclus, et que l'absence d'habillage ne fait pas planter la génération."""

    @classmethod
    def tearDownClass(cls):
        from django.conf import settings

        shutil.rmtree(settings.MEDIA_ROOT, ignore_errors=True)
        super().tearDownClass()

    def setUp(self):
        self.employee = User.objects.create_user("employee_pdf_branding", password="x")
        self.request_type = RequestType.objects.create(
            name="Nouveau rapport", code="REPORT", form_schema=SCHEMA_WITH_SECTIONS
        )
        self.req = Request.objects.create(
            request_type=self.request_type, requester=self.employee,
            data={"departement": "Ventes", "titre_rapport": "Suivi"},
        )

    def test_pdf_includes_branding_header_and_footer_text(self):
        DocumentBranding.objects.create(
            request_type=self.request_type,
            header_text="Lauzon Ltd - 123 rue Principale",
            footer_text="Document confidentiel",
        )
        pdf_bytes = generate_request_summary_pdf(self.req)
        self.assertIn(b"Lauzon Ltd", pdf_bytes)
        self.assertIn(b"Document confidentiel", pdf_bytes)

    def test_pdf_generation_works_without_any_branding_configured(self):
        pdf_bytes = generate_request_summary_pdf(self.req)
        self.assertTrue(pdf_bytes.startswith(b"%PDF"))

    def test_pdf_includes_section_titles(self):
        pdf_bytes = generate_request_summary_pdf(self.req)
        self.assertIn(b"Demandeur", pdf_bytes)
        self.assertIn(b"Rapport", pdf_bytes)

    def test_footer_style_center_alignment_is_respected(self):
        """RichTextWidget produit <div style="text-align: center;"> via
        execCommand du navigateur — fpdf2.write_html() ignore silencieusement
        cette CSS (il ne comprend que l'attribut HTML align=), le texte
        restait à gauche malgré le réglage "centrer" choisi par l'admin."""
        DocumentBranding.objects.create(
            request_type=self.request_type,
            footer_text='<div style="text-align: center;">Centre</div>',
        )
        pdf_bytes = generate_request_summary_pdf(self.req)
        match = re.search(rb"BT ([\d.]+) [\d.]+ Td \(Centre\) Tj ET", pdf_bytes)
        self.assertIsNotNone(match, "texte 'Centre' introuvable dans le flux PDF")
        x_position = float(match.group(1))
        self.assertGreater(x_position, 100, "le texte est resté aligné à gauche (marge ~28pt)")

    def test_custom_accent_color_used_for_section_title(self):
        DocumentBranding.objects.create(request_type=self.request_type, accent_color="#FF0000")
        pdf_bytes = generate_request_summary_pdf(self.req)
        self.assertIn(b"1 0 0 rg", pdf_bytes)

    def test_default_accent_color_used_when_not_configured(self):
        pdf_bytes = generate_request_summary_pdf(self.req)
        self.assertIn(b"0.1216 0.2275 0.3725 rg", pdf_bytes)

    def test_highlighted_field_uses_accent_color(self):
        request_type = RequestType.objects.create(
            name="Type surbrillance", code="HILITE",
            form_schema={"fields": [
                {"name": "champ_important", "type": "text", "label": "Champ important", "highlight": True},
                {"name": "champ_normal", "type": "text", "label": "Champ normal"},
            ]},
        )
        DocumentBranding.objects.create(request_type=request_type, accent_color="#FF0000")
        req = Request.objects.create(
            request_type=request_type, requester=self.employee,
            data={"champ_important": "Urgent", "champ_normal": "Normal"},
        )
        pdf_bytes = generate_request_summary_pdf(req)
        self.assertIn(b"1 0 0 rg", pdf_bytes)

    def test_custom_font_size_changes_title_size(self):
        DocumentBranding.objects.create(request_type=self.request_type, body_font_size=14)
        pdf_bytes = generate_request_summary_pdf(self.req)
        # Titre = taille de base + 5 (voir _generate_auto_layout) : 14 + 5 = 19.
        self.assertIn(b"19.00 Tf", pdf_bytes)

    def test_underline_values_draws_lines_under_short_values(self):
        DocumentBranding.objects.create(request_type=self.request_type, underline_values=True)
        pdf_bytes = generate_request_summary_pdf(self.req)
        self.assertIn(b" G\n", pdf_bytes)  # trait gris (set_draw_color) tracé sous une valeur

    def test_no_underline_by_default(self):
        pdf_bytes = generate_request_summary_pdf(self.req)
        self.assertNotIn(b"0.5882 0.5882 0.5882 RG", pdf_bytes)

    def test_invalid_custom_font_name_falls_back_to_helvetica(self):
        DocumentBranding.objects.create(request_type=self.request_type, body_font="PoliceInexistante")
        pdf_bytes = generate_request_summary_pdf(self.req)
        self.assertTrue(pdf_bytes.startswith(b"%PDF"))
        self.assertIn(b"Nouveau rapport", pdf_bytes)
