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
from .models import (
    ApprovalLog, BrandingLogo, CustomFont, Department, DocumentBranding, Request, RequestType, Role,
    Site, UserProfile,
)
from .pdf_export import generate_request_summary_pdf


def _tiny_png(name="logo.png"):
    # Vrai PNG valide (pas juste l'en-tête magique) : pdf.image() le décode
    # réellement (via Pillow) pour l'intégrer au PDF.
    import io

    from PIL import Image

    buffer = io.BytesIO()
    Image.new("RGB", (4, 4), color="blue").save(buffer, format="PNG")
    return SimpleUploadedFile(name, buffer.getvalue(), content_type="image/png")


def _valid_ttf_bytes():
    """Génère un TTF minimal mais réellement valide (fontTools, déjà une
    dépendance de fpdf2) — un fichier bidon ne suffit pas ici : le but est
    de tester ce qui se passe quand pdf.add_font() RÉUSSIT pour le style
    normal mais qu'aucune variante gras n'a été fournie."""
    import io

    from fontTools.fontBuilder import FontBuilder
    from fontTools.pens.ttGlyphPen import TTGlyphPen

    fb = FontBuilder(1000, isTTF=True)
    fb.setupGlyphOrder([".notdef", "A"])
    fb.setupCharacterMap({65: "A"})
    pen = TTGlyphPen(None)
    pen.moveTo((0, 0))
    pen.lineTo((0, 500))
    pen.lineTo((500, 500))
    pen.lineTo((500, 0))
    pen.closePath()
    glyph = pen.glyph()
    fb.setupGlyf({".notdef": glyph, "A": glyph})
    fb.setupHorizontalMetrics({".notdef": (500, 0), "A": (500, 0)})
    fb.setupHorizontalHeader(ascent=800, descent=-200)
    fb.setupNameTable({"familyName": "TestFont", "styleName": "Regular"})
    fb.setupOS2()
    fb.setupPost()
    buffer = io.BytesIO()
    fb.save(buffer)
    return buffer.getvalue()


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

    def test_pdf_shows_requester_department_site_and_role(self):
        department = Department.objects.create(name="Ventes")
        site = Site.objects.create(name="Lyon")
        role = Role.objects.create(name="Chargé de compte")
        UserProfile.objects.create(user=self.employee, department=department, site=site, role=role)
        pdf_bytes = generate_request_summary_pdf(self.req)
        self.assertIn(b"Ventes", pdf_bytes)
        self.assertIn(b"Lyon", pdf_bytes)
        self.assertIn(b"Charg", pdf_bytes)

    def test_pdf_shows_dash_for_department_site_role_when_profile_missing(self):
        pdf_bytes = generate_request_summary_pdf(self.req)
        self.assertTrue(pdf_bytes.startswith(b"%PDF"))

    def test_pdf_shows_placeholder_when_requester_has_no_signature(self):
        pdf_bytes = generate_request_summary_pdf(self.req)
        self.assertIn(b"SIGNATURE DU DEMANDEUR", pdf_bytes)
        self.assertIn(rb"\(non fournie\)", pdf_bytes)

    def test_pdf_includes_requester_signature_image_when_profile_has_one(self):
        profile, _ = UserProfile.objects.get_or_create(user=self.employee)
        profile.signature.save("signature.png", _tiny_png(), save=True)
        pdf_bytes = generate_request_summary_pdf(self.req)
        self.assertIn(b"SIGNATURE DU DEMANDEUR", pdf_bytes)
        self.assertNotIn(rb"\(non fournie\)", pdf_bytes)
        self.assertIn(b"/Image", pdf_bytes)

    def test_missing_requester_signature_file_does_not_crash_generation(self):
        profile, _ = UserProfile.objects.get_or_create(user=self.employee)
        profile.signature.save("signature.png", _tiny_png(), save=True)
        profile.signature.delete(save=False)
        profile.signature.name = "signatures/disparu.png"
        profile.save()
        pdf_bytes = generate_request_summary_pdf(self.req)
        self.assertTrue(pdf_bytes.startswith(b"%PDF"))

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
        match = re.search(rb"BT ([\d.]+) [\d.]+ Td.*?\(Centre\) Tj ET", pdf_bytes)
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

    def test_custom_status_color_overrides_default_blue(self):
        """Retour client : la couleur du statut "Retournée" (bleu par défaut)
        doit être modifiable, pas figée."""
        self.req.status = Request.Status.RETURNED
        self.req.save()
        DocumentBranding.objects.create(request_type=self.request_type, returned_color="#00FF00")
        pdf_bytes = generate_request_summary_pdf(self.req)
        self.assertIn(b"0 1 0 rg", pdf_bytes)
        self.assertNotIn(b"0.1137 0.3725 0.6902 rg", pdf_bytes)  # bleu par défaut absent

    def test_default_status_color_used_when_not_configured(self):
        self.req.status = Request.Status.RETURNED
        self.req.save()
        pdf_bytes = generate_request_summary_pdf(self.req)
        self.assertIn(b"0.1137 0.3725 0.6902 rg", pdf_bytes)

    def test_footer_custom_size_and_color(self):
        DocumentBranding.objects.create(
            request_type=self.request_type, footer_text="Mentions légales",
            footer_font_size=12, footer_color="#FF0000",
        )
        pdf_bytes = generate_request_summary_pdf(self.req)
        self.assertIn(b"12.00 Tf", pdf_bytes)
        self.assertIn(b"1 0 0 rg", pdf_bytes)

    def test_page_number_stays_above_a_long_footer_text(self):
        """Retour client : un pied de page sur plusieurs lignes recouvrait le
        numéro de page — le numéro doit rester à part, quelle que soit la
        longueur du pied de page."""
        long_footer = "<br>".join([f"Ligne {i} des mentions légales" for i in range(8)])
        DocumentBranding.objects.create(request_type=self.request_type, footer_text=long_footer)
        pdf_bytes = generate_request_summary_pdf(self.req)
        page_match = re.search(rb"BT ([\d.]+) ([\d.]+) Td.*?\(Page 1/\) Tj", pdf_bytes)
        self.assertIsNotNone(page_match, "numéro de page introuvable dans le flux PDF")
        page_y = float(page_match.group(2))
        # Coordonnées PDF : 0 = bas de page. Un numéro de page situé haut sur
        # la page (donc au-dessus de tout pied de page, aussi long soit-il)
        # doit avoir un y bien supérieur à la zone de pied de page (~40pt).
        self.assertGreater(page_y, 700)

    def test_footer_image_does_not_crash_generation(self):
        branding = DocumentBranding.objects.create(request_type=self.request_type)
        branding.footer_image.save("icon.png", _tiny_png(), save=True)
        pdf_bytes = generate_request_summary_pdf(self.req)
        self.assertTrue(pdf_bytes.startswith(b"%PDF"))

    def test_missing_footer_image_file_does_not_crash_generation(self):
        branding = DocumentBranding.objects.create(request_type=self.request_type)
        branding.footer_image.save("icon.png", _tiny_png(), save=True)
        branding.footer_image.delete(save=False)  # le champ pointe vers un fichier disparu
        branding.footer_image.name = "branding_footer/2026/01/disparu.png"
        branding.save()
        pdf_bytes = generate_request_summary_pdf(self.req)
        self.assertTrue(pdf_bytes.startswith(b"%PDF"))

    def test_no_underline_by_default(self):
        pdf_bytes = generate_request_summary_pdf(self.req)
        self.assertNotIn(b"0.5882 0.5882 0.5882 RG", pdf_bytes)

    def test_invalid_custom_font_name_falls_back_to_helvetica(self):
        DocumentBranding.objects.create(request_type=self.request_type, body_font="PoliceInexistante")
        pdf_bytes = generate_request_summary_pdf(self.req)
        self.assertTrue(pdf_bytes.startswith(b"%PDF"))
        self.assertIn(b"Nouveau rapport", pdf_bytes)


@override_settings(MEDIA_ROOT=tempfile.mkdtemp())
class ApprovalSectionPdfTests(TestCase):
    """Section "Approbations" en fin de PDF : nom de l'approbateur, date, et
    sa signature dessinée une fois dans son profil (retour client : reproduire
    la section signature du formulaire papier).

    @override_settings(MEDIA_ROOT=...) est CRITIQUE ici, pas juste une bonne
    pratique : tearDownClass supprime tout settings.MEDIA_ROOT — sans cet
    override, c'était le dossier media/ RÉEL du poste de dev qui partait à
    chaque exécution de la suite de tests (bug identifié en prod locale :
    signatures/photos/logos disparaissaient après chaque nouvelle feature)."""

    @classmethod
    def tearDownClass(cls):
        from django.conf import settings

        shutil.rmtree(settings.MEDIA_ROOT, ignore_errors=True)
        super().tearDownClass()

    def setUp(self):
        self.employee = User.objects.create_user("employee_approval_pdf", password="x")
        self.approver = User.objects.create_user(
            "approver_pdf", password="x", first_name="Jeanne", last_name="Tremblay",
        )
        self.request_type = RequestType.objects.create(
            name="Nouveau rapport", code="REPORT2", form_schema=SCHEMA_WITH_SECTIONS
        )
        self.req = Request.objects.create(
            request_type=self.request_type, requester=self.employee,
            data={"departement": "Ventes", "titre_rapport": "Suivi"},
        )

    def _approve(self, level=1):
        ApprovalLog.objects.create(
            request=self.req, actor=self.approver, action_type=ApprovalLog.ActionType.APPROVE,
            context={"level": level},
        )

    def test_no_approval_section_when_never_approved(self):
        pdf_bytes = generate_request_summary_pdf(self.req)
        self.assertNotIn(b"Approbations", pdf_bytes)

    def test_approval_section_shows_approver_department_site_and_role(self):
        department = Department.objects.create(name="Ventes")
        site = Site.objects.create(name="Lyon")
        role = Role.objects.create(name="Comptable")
        UserProfile.objects.create(user=self.approver, department=department, site=site, role=role)
        self._approve(level=1)
        pdf_bytes = generate_request_summary_pdf(self.req)
        self.assertIn(b"Ventes", pdf_bytes)
        self.assertIn(b"Lyon", pdf_bytes)
        self.assertIn(b"Comptable", pdf_bytes)

    def test_approval_section_shows_dash_when_approver_has_no_profile(self):
        self._approve(level=1)
        pdf_bytes = generate_request_summary_pdf(self.req)
        self.assertTrue(pdf_bytes.startswith(b"%PDF"))

    def test_approval_section_shows_approver_name_and_level(self):
        self._approve(level=1)
        pdf_bytes = generate_request_summary_pdf(self.req)
        self.assertIn(b"Approbations", pdf_bytes)
        self.assertIn(b"Niveau 1", pdf_bytes)
        self.assertIn(b"Jeanne Tremblay", pdf_bytes)

    def test_multiple_approval_levels_all_listed(self):
        self._approve(level=1)
        ApprovalLog.objects.create(
            request=self.req, actor=self.employee, action_type=ApprovalLog.ActionType.APPROVE,
            context={"level": 2},
        )
        pdf_bytes = generate_request_summary_pdf(self.req)
        self.assertIn(b"Niveau 1", pdf_bytes)
        self.assertIn(b"Niveau 2", pdf_bytes)

    def test_approval_section_includes_signature_image_when_profile_has_one(self):
        profile, _ = UserProfile.objects.get_or_create(user=self.approver)
        profile.signature.save("signature.png", _tiny_png(), save=True)
        self._approve(level=1)
        pdf_bytes = generate_request_summary_pdf(self.req)
        self.assertTrue(pdf_bytes.startswith(b"%PDF"))
        self.assertIn(b"/Image", pdf_bytes)

    def test_missing_signature_file_does_not_crash_generation(self):
        profile, _ = UserProfile.objects.get_or_create(user=self.approver)
        profile.signature.save("signature.png", _tiny_png(), save=True)
        profile.signature.delete(save=False)
        profile.signature.name = "signatures/disparu.png"
        profile.save()
        self._approve(level=1)
        pdf_bytes = generate_request_summary_pdf(self.req)
        self.assertTrue(pdf_bytes.startswith(b"%PDF"))
        self.assertIn(b"Jeanne Tremblay", pdf_bytes)

    def test_approval_without_actor_shows_placeholder(self):
        ApprovalLog.objects.create(
            request=self.req, actor=None, action_type=ApprovalLog.ActionType.APPROVE,
            context={"level": 1},
        )
        pdf_bytes = generate_request_summary_pdf(self.req)
        self.assertTrue(pdf_bytes.startswith(b"%PDF"))
        self.assertIn(b"Approbations", pdf_bytes)

    def test_custom_font_without_bold_variant_does_not_crash_generation(self):
        """Bug réel observé : une police personnalisée enregistrée avec
        succès pour son style normal (regular_ttf) mais sans variante gras
        faisait planter la génération — le titre/les labels/les titres de
        section demandent tous "B" (gras) sans vérifier que CE style précis
        avait été enregistré pour cette police."""
        font = CustomFont.objects.create(
            name="SansGras",
            regular_ttf=SimpleUploadedFile("sansgras.ttf", _valid_ttf_bytes(), content_type="font/ttf"),
        )
        DocumentBranding.objects.create(request_type=self.request_type, body_font=font.name)
        pdf_bytes = generate_request_summary_pdf(self.req)
        self.assertTrue(pdf_bytes.startswith(b"%PDF"))
