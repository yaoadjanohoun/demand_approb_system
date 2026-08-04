"""Génère un PDF résumé d'une demande (retour client : en plus du document
de référence attaché au type de demande — voir RequestType.reference_form_pdf
— l'approbateur doit pouvoir télécharger un PDF reprenant les réponses
saisies par le demandeur). Ce PDF est généré à la volée à partir des mêmes
données que la page de détail (grouped_labeled_data), pas une copie du PDF
de référence : aucun champ de formulaire PDF (AcroForm) n'est rempli.

L'habillage (logos, en-tête, pied de page — voir DocumentBranding) est
propre à chaque type de demande et s'affiche automatiquement sur chaque
page, via header()/footer() (FPDF les rappelle à chaque saut de page)."""
from fpdf import FPDF
from fpdf.enums import XPos, YPos

from .forms import _format_value, grouped_labeled_data
from .models import BrandingLogo, CustomFont, DocumentBranding, DocumentTemplate

# Doit correspondre à PX_PER_MM dans document_template_editor.html : l'éditeur
# visuel travaille en pixels d'écran, le PDF en millimètres — même échelle
# des deux côtés, sinon la mise en page dessinée ne correspond plus à ce qui
# est imprimé.
_TEMPLATE_PX_PER_MM = 3
_PT_PER_PX = 2.83465 / _TEMPLATE_PX_PER_MM

_BUILTIN_FONT_FAMILIES = {"Helvetica", "Times", "Courier"}

# new_x=LMARGIN, new_y=NEXT : fpdf2 ne ramène plus le curseur à la marge de
# gauche après un multi_cell par défaut (il reste à droite du texte écrit) —
# sans ça, le multi_cell suivant tente de s'écrire dans le vide restant à
# droite de la page et plante ("Not enough horizontal space").
_NEXT_LINE = {"new_x": XPos.LMARGIN, "new_y": YPos.NEXT}

# La police de base (Helvetica) ne supporte que Latin-1 — les accents français
# passent, mais pas les caractères typographiques "intelligents" (tiret
# cadratin utilisé par labeled_data() pour une valeur vide, guillemets
# courbes, points de suspension...) qu'un utilisateur peut aussi taper
# librement dans un champ texte. Remplacés par leur équivalent ASCII ; tout
# ce qui reste hors Latin-1 est neutralisé plutôt que de faire planter la
# génération du PDF.
_TYPOGRAPHIC_REPLACEMENTS = {
    "—": "-",  # tiret cadratin —
    "–": "-",  # tiret demi-cadratin –
    "‘": "'", "’": "'",  # guillemets simples courbes
    "“": '"', "”": '"',  # guillemets doubles courbes
    "…": "...",  # points de suspension …
    " ": " ",  # espace insécable
}

_LOGO_HEIGHT_MM = 14
_LOGO_GAP_MM = 6


def _pdf_safe(text):
    for original, replacement in _TYPOGRAPHIC_REPLACEMENTS.items():
        text = text.replace(original, replacement)
    return text.encode("latin-1", errors="replace").decode("latin-1")


def _write_line(pdf, text, height=6):
    pdf.multi_cell(0, height, _pdf_safe(text), **_NEXT_LINE)


class _BrandedPDF(FPDF):
    """FPDF rappelle header()/footer() à chaque add_page() — c'est ce qui
    permet à l'habillage de se répéter automatiquement sur chaque page,
    sans que generate_request_summary_pdf() ait à y penser."""

    def __init__(self, branding):
        super().__init__()
        self._branding = branding

    def header(self):
        if not self._branding:
            return
        logos = list(self._branding.logos.all())
        if logos:
            x = self.l_margin
            for logo in logos:
                try:
                    self.image(logo.image.path, x=x, y=self.t_margin, h=_LOGO_HEIGHT_MM)
                except Exception:
                    continue  # logo supprimé du disque ou illisible : on n'interrompt pas la génération
                x += _LOGO_HEIGHT_MM + _LOGO_GAP_MM
            self.set_y(self.t_margin + _LOGO_HEIGHT_MM + 2)
        if self._branding.header_text:
            self.set_font("Helvetica", "", 9)
            self._write_html_no_page_break(_pdf_safe(self._branding.header_text))
        self.ln(2)

    def footer(self):
        if not self._branding or not self._branding.footer_text:
            return
        self.set_y(-18)
        self.set_font("Helvetica", "", 8)
        self._write_html_no_page_break(_pdf_safe(self._branding.footer_text))

    def _write_html_no_page_break(self, html):
        """write_html() déclenche le saut de page automatique dès que la
        position d'écriture tombe dans la marge de bas de page — exactement
        où se trouve le pied de page (placé volontairement à 18mm du bas,
        marge de saut à 25mm) : le texte partait sur une page fantôme au lieu
        de s'afficher. Un en-tête/pied de page ne doit jamais créer de
        nouvelle page, donc désactivé le temps de cet appel."""
        auto, margin = self.auto_page_break, self.b_margin
        self.set_auto_page_break(False)
        self.write_html(html)
        self.set_auto_page_break(auto, margin)


def generate_request_summary_pdf(req):
    template = DocumentTemplate.objects.filter(request_type=req.request_type).first()
    if template and template.canvas_json.get("objects"):
        return _generate_from_template(req, template)
    return _generate_auto_layout(req)


def _register_custom_fonts(pdf):
    """Enregistre chaque police personnalisée pour chaque variante fournie
    (gras/italique) et renvoie l'ensemble des (nom, style) effectivement
    enregistrés avec succès — un fichier corrompu, mal formé ou supprimé du
    disque ne doit pas empêcher la génération du PDF, juste faire retomber
    cette police sur les polices de base (voir _set_template_font). Attrapé
    largement : un TTF invalide peut échouer de multiples façons selon la
    bibliothèque de parsing (fontTools) — struct.error, KeyError, etc., pas
    seulement FileNotFoundError."""
    registered = set()
    for font in CustomFont.objects.all():
        variants = [("", font.regular_ttf), ("B", font.bold_ttf), ("I", font.italic_ttf), ("BI", font.bold_italic_ttf)]
        for style, file_field in variants:
            if not file_field:
                continue
            try:
                pdf.add_font(font.name, style, file_field.path)
            except Exception:
                continue
            registered.add((font.name, style))
    return registered


def _set_template_font(pdf, family, style, size_pt, registered_fonts):
    """Bascule sur Helvetica si la police/variante demandée n'a pas pu être
    enregistrée (fichier manquant, ou variante gras/italique non fournie par
    l'admin) — mieux vaut un rendu approché que de faire planter tout le PDF."""
    if family not in _BUILTIN_FONT_FAMILIES and (family, style) not in registered_fonts:
        style = "B" if "B" in style else ""  # tente au moins le style de base de cette police
        if (family, style) not in registered_fonts:
            family, style = "Helvetica", style
    pdf.set_font(family, style, size_pt)
    return family in _BUILTIN_FONT_FAMILIES


#fonction de génération du template

def _generate_from_template(req, template):
    pdf = FPDF(format="A4")
    pdf.set_compression(False)
    pdf.set_auto_page_break(False)
    pdf.add_page()

    registered_fonts = _register_custom_fonts(pdf)

    values_by_field_name = _values_by_field_name(req)
    logos_by_id = {logo.id: logo for logo in BrandingLogo.objects.filter(id__in=_logo_ids(template))}

    for obj in template.canvas_json.get("objects", []):
        data = obj.get("data") or {}
        x_mm = (obj.get("left", 0) or 0) / _TEMPLATE_PX_PER_MM
        y_mm = (obj.get("top", 0) or 0) / _TEMPLATE_PX_PER_MM
        scale_x = obj.get("scaleX", 1) or 1
        scale_y = obj.get("scaleY", 1) or 1

        if obj.get("type") == "textbox":
            field_name = data.get("field")
            text = values_by_field_name.get(field_name, "") if field_name else obj.get("text", "")

            width_mm = (obj.get("width", 100) * scale_x) / _TEMPLATE_PX_PER_MM
            size_pt = round((obj.get("fontSize", 14) or 14) * scale_y * _PT_PER_PX)
            style = ("B" if obj.get("fontWeight") == "bold" else "") + ("I" if obj.get("fontStyle") == "italic" else "")
            family = obj.get("fontFamily") or "Helvetica"
            is_builtin = _set_template_font(pdf, family, style, size_pt, registered_fonts)
            text = _pdf_safe(text) if is_builtin else str(text)

            r, g, b = _hex_to_rgb(obj.get("fill") or "#000000")
            pdf.set_text_color(r, g, b)

            align_map = {"left": "L", "center": "C", "right": "R", "justify": "J"}
            align = align_map.get(obj.get("textAlign"), "L")

            pdf.set_xy(x_mm, y_mm)
            pdf.multi_cell(width_mm, size_pt / 2.5, text, align=align)

        elif obj.get("type") == "image" and data.get("logoId") in logos_by_id:
            logo = logos_by_id[data["logoId"]]
            width_mm = (obj.get("width", 0) * scale_x) / _TEMPLATE_PX_PER_MM
            height_mm = (obj.get("height", 0) * scale_y) / _TEMPLATE_PX_PER_MM
            try:
                pdf.image(logo.image.path, x=x_mm, y=y_mm, w=width_mm, h=height_mm)
            except Exception:
                continue  # fichier supprimé/corrompu : ne doit pas faire échouer tout le PDF

    return bytes(pdf.output())


def _hex_to_rgb(hex_color):
    try:
        hex_color = hex_color.lstrip("#")
        return tuple(int(hex_color[i:i + 2], 16) for i in (0, 2, 4))
    except (ValueError, IndexError):
        return (0, 0, 0)


def _values_by_field_name(req):
    """Valeur formatée (voir forms._format_value) de chaque champ soumis,
    indexée par nom technique — sert à remplacer les placeholders {{champ}}
    de l'éditeur visuel par la vraie valeur au moment de générer le PDF."""
    field_defs = req.request_type.form_schema.get("fields", [])
    decimal_fields = {f["name"] for f in field_defs if f["type"] == "decimal"}
    currency = req.request_type.default_currency
    return {
        name: str(_format_value(value, name in decimal_fields, currency))
        for name, value in (req.data or {}).items()
    }


def _logo_ids(template):
    ids = []
    for obj in template.canvas_json.get("objects", []):
        logo_id = (obj.get("data") or {}).get("logoId")
        if logo_id is not None:
            ids.append(logo_id)
    return ids


def _generate_auto_layout(req):
    branding = DocumentBranding.objects.filter(request_type=req.request_type).prefetch_related("logos").first()

    pdf = _BrandedPDF(branding)
    pdf.set_compression(False)  # PDF lisible en clair (pratique pour les tests, coût négligeable ici)
    pdf.set_auto_page_break(auto=True, margin=25)
    pdf.add_page()

    pdf.set_font("Helvetica", "B", 16)
    _write_line(pdf, req.request_type.name, height=10)

    pdf.set_font("Helvetica", "", 11)
    meta_lines = [
        f"Reference : {req.reference}",
        f"Statut : {req.get_status_display()}",
        f"Demandeur : {req.requester.get_full_name() or req.requester.username}",
        f"Soumise le : {req.submitted_at.strftime('%d/%m/%Y %H:%M') if req.submitted_at else '-'}",
    ]
    for line in meta_lines:
        _write_line(pdf, line)
    pdf.ln(4)

    for group in grouped_labeled_data(req.request_type, req.data or {}):
        if group["section"]:
            pdf.set_font("Helvetica", "B", 13)
            _write_line(pdf, group["section"], height=8)
            pdf.ln(1)

        for row in group["rows"]:
            pdf.set_font("Helvetica", "B", 10)
            _write_line(pdf, str(row["label"]))
            pdf.set_font("Helvetica", "", 11)
            _write_line(pdf, str(row["value"]) if row["value"] not in (None, "") else "-")
            pdf.ln(2)

    return bytes(pdf.output())
