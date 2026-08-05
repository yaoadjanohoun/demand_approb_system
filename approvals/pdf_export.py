"""Génère un PDF résumé d'une demande (retour client : en plus du document
de référence attaché au type de demande — voir RequestType.reference_form_pdf
— l'approbateur doit pouvoir télécharger un PDF reprenant les réponses
saisies par le demandeur). Ce PDF est généré à la volée à partir des mêmes
données que la page de détail (grouped_labeled_data), pas une copie du PDF
de référence : aucun champ de formulaire PDF (AcroForm) n'est rempli.

L'habillage (logos, en-tête, pied de page — voir DocumentBranding) est
propre à chaque type de demande et s'affiche automatiquement sur chaque
page, via header()/footer() (FPDF les rappelle à chaque saut de page)."""
import re

from fpdf import FPDF
from fpdf.enums import XPos, YPos

from .forms import _format_value, grouped_labeled_data
from .models import BrandingLogo, CustomFont, DocumentBranding, DocumentTemplate

# fpdf2's write_html() ne comprend que l'attribut HTML align="..." sur une
# balise, pas la CSS style="text-align: ...". Or RichTextWidget (bouton
# "centrer" du header_text/footer_text) produit exactement ce style CSS via
# document.execCommand du navigateur (<div style="text-align: center;">) —
# l'alignement était donc silencieusement ignoré (toujours à gauche).
# Converti ici en <p align="..."> avant de passer à write_html().
_ALIGN_STYLE_RE = re.compile(
    r'<div style="text-align:\s*(left|center|right|justify);?">(.*?)</div>',
    re.IGNORECASE | re.DOTALL,
)


def _fix_html_alignment(html):
    return _ALIGN_STYLE_RE.sub(lambda m: f'<p align="{m.group(1).lower()}">{m.group(2)}</p>', html)

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

# Rendu automatique (voir _generate_auto_layout) : bleu-gris sobre pour les
# titres de section et les filets, cohérent avec un document d'entreprise
# sans dépendre d'une couleur de marque non configurée.
_ACCENT_RGB = (31, 58, 95)
_COL_GUTTER_MM = 6
# Deux champs courts (ex: "Département" / "Date") tiennent côte à côte sur
# une même ligne, comme les formulaires papier existants — un seuil de
# longueur évite qu'une valeur longue ne déborde de sa colonne.
_SHORT_VALUE_MAX_CHARS = 30

# Mêmes couleurs que les pastilles de statut de l'interface web (voir
# .status-* dans app.css) — pour que le statut se reconnaisse d'un coup
# d'œil, PDF comme écran.
_STATUS_COLORS = {
    "DRAFT": (75, 81, 96),
    "PENDING": (179, 118, 10),
    "APPROVED": (15, 138, 95),
    "REJECTED": (193, 54, 43),
    "RETURNED": (29, 95, 176),
}

# Typographie du corps du document (voir DocumentBranding.body_font/
# body_font_size/line_spacing/underline_values) — les hauteurs de ligne
# ci-dessous ont été calées pour une taille de base de 11pt en espacement
# "normal" ; "scale" les ajuste proportionnellement pour toute autre taille/
# espacement choisi par l'admin.
_SPACING_MULTIPLIERS = {"compact": 0.8, "normal": 1.0, "spacious": 1.3}
_DEFAULT_STYLE = {"family": "Helvetica", "size": 11, "is_builtin": True, "scale": 1.0, "underline": False}


def _resolve_body_style(branding, registered_fonts):
    size = (branding.body_font_size if branding else None) or 11
    family = ((branding.body_font if branding else "") or "Helvetica").strip() or "Helvetica"
    if family not in _BUILTIN_FONT_FAMILIES and (family, "") not in registered_fonts:
        family = "Helvetica"  # police non enregistrée (fichier manquant/corrompu) : repli sûr
    spacing_key = branding.line_spacing if branding else "normal"
    scale = (size / 11.0) * _SPACING_MULTIPLIERS.get(spacing_key, 1.0)
    return {
        "family": family,
        "size": size,
        "is_builtin": family in _BUILTIN_FONT_FAMILIES,
        "scale": scale,
        "underline": bool(branding and branding.underline_values),
    }


def _pdf_safe(text):
    for original, replacement in _TYPOGRAPHIC_REPLACEMENTS.items():
        text = text.replace(original, replacement)
    return text.encode("latin-1", errors="replace").decode("latin-1")


def _write_line(pdf, text, height=6, safe=True):
    text = _pdf_safe(text) if safe else str(text)
    pdf.multi_cell(0, height, text, **_NEXT_LINE)


# new_x=LEFT (pas LMARGIN) : ramène le curseur au bord gauche de LA COLONNE
# (pas de la page) après chaque ligne, pour empiler label/valeur dans une
# colonne sans dériver vers la marge de page.
_COL_NEXT_LINE = {"new_x": XPos.LEFT, "new_y": YPos.NEXT}


def _write_col_line(pdf, text, width, height=6, safe=True):
    text = _pdf_safe(text) if safe else str(text)
    pdf.multi_cell(width, height, text, **_COL_NEXT_LINE)


def _display_value(row):
    return str(row["value"]) if row["value"] not in (None, "") else "-"


def _is_short_value(value_str):
    return "\n" not in value_str and len(value_str) <= _SHORT_VALUE_MAX_CHARS


def _row_color(row, accent_rgb):
    """Couleur de la valeur : "color" explicite (ex: Statut) prioritaire sur
    "highlight" (case "Mettre en évidence" du formulaire, voir form_schema),
    sinon noir."""
    if "color" in row:
        return row["color"]
    if row.get("highlight"):
        return accent_rgb
    return (0, 0, 0)


def _render_two_col_row(pdf, left_row, right_row, col_width, accent_rgb=_ACCENT_RGB, style=None):
    """Affiche un ou deux champs côte à côte (label en petit/gras au-dessus
    de la valeur) — retombe sur une seule colonne si right_row est None
    (dernier champ impair d'une section)."""
    style = style or _DEFAULT_STYLE
    family, is_builtin, scale = style["family"], style["is_builtin"], style["scale"]
    label_size = max(6, style["size"] - 2)
    label_h, value_h, row_h = 5 * scale, 6 * scale, 13 * scale

    y0 = pdf.get_y()
    x_left = pdf.l_margin
    x_right = x_left + col_width + _COL_GUTTER_MM

    def render_cell(row, x):
        pdf.set_xy(x, y0)
        pdf.set_font(family, "B", label_size)
        _write_col_line(pdf, str(row["label"]).upper(), col_width, height=label_h, safe=is_builtin)
        pdf.set_font(family, "", style["size"])
        pdf.set_text_color(*_row_color(row, accent_rgb))
        value_y = pdf.get_y()
        _write_col_line(pdf, _display_value(row), col_width, height=value_h, safe=is_builtin)
        pdf.set_text_color(0, 0, 0)
        if style["underline"]:
            underline_y = value_y + value_h - 0.5
            pdf.set_draw_color(150, 150, 150)
            pdf.set_line_width(0.2)
            pdf.line(x, underline_y, x + col_width, underline_y)
            pdf.set_draw_color(0, 0, 0)

    render_cell(left_row, x_left)
    if right_row is not None:
        render_cell(right_row, x_right)

    pdf.set_xy(x_left, y0 + row_h)


def _render_field_rows(pdf, rows, col_width, accent_rgb=_ACCENT_RGB, style=None):
    """Empile des paires de champs courts côte à côte, et les champs longs
    (zone de texte) sur toute la largeur — mélange les deux dans l'ordre où
    les champs apparaissent, comme sur les formulaires papier existants."""
    style = style or _DEFAULT_STYLE
    family, is_builtin, scale = style["family"], style["is_builtin"], style["scale"]
    label_size = max(6, style["size"] - 2)
    pending = None
    for row in rows:
        value_str = _display_value(row)
        if _is_short_value(value_str):
            if pending is None:
                pending = row
            else:
                _render_two_col_row(pdf, pending, row, col_width, accent_rgb, style)
                pending = None
        else:
            if pending is not None:
                _render_two_col_row(pdf, pending, None, col_width, accent_rgb, style)
                pending = None
            pdf.set_font(family, "B", label_size)
            _write_line(pdf, str(row["label"]).upper(), height=5 * scale, safe=is_builtin)
            pdf.set_font(family, "", style["size"])
            pdf.set_text_color(*_row_color(row, accent_rgb))
            _write_line(pdf, value_str, height=6 * scale, safe=is_builtin)
            pdf.set_text_color(0, 0, 0)
            pdf.ln(2 * scale)
    if pending is not None:
        _render_two_col_row(pdf, pending, None, col_width, accent_rgb, style)


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
            self._write_html_no_page_break(_pdf_safe(_fix_html_alignment(self._branding.header_text)))
        self.ln(2)

    def footer(self):
        if self._branding and self._branding.footer_text:
            self.set_y(-18)
            self.set_font("Helvetica", "", 8)
            self._write_html_no_page_break(_pdf_safe(_fix_html_alignment(self._branding.footer_text)))

        self.set_y(-10)
        self.set_font("Helvetica", "I", 7)
        self.set_text_color(120, 120, 120)
        self.cell(0, 5, f"Page {self.page_no()}/{{nb}}", align="C")
        self.set_text_color(0, 0, 0)

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
        # Fabric.js 6 sérialise le type en "Textbox"/"Image" (majuscule) —
        # comparaison insensible à la casse pour ne pas dépendre d'une
        # version précise de Fabric.js.
        obj_type = (obj.get("type") or "").lower()
        data = obj.get("data") or {}
        x_mm = (obj.get("left", 0) or 0) / _TEMPLATE_PX_PER_MM
        y_mm = (obj.get("top", 0) or 0) / _TEMPLATE_PX_PER_MM
        scale_x = obj.get("scaleX", 1) or 1
        scale_y = obj.get("scaleY", 1) or 1

        if obj_type == "textbox":
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

        elif obj_type == "image" and data.get("logoId") in logos_by_id:
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
    accent_rgb = _hex_to_rgb(branding.accent_color) if branding and branding.accent_color else _ACCENT_RGB

    pdf = _BrandedPDF(branding)
    pdf.set_compression(False)  # PDF lisible en clair (pratique pour les tests, coût négligeable ici)
    pdf.alias_nb_pages()  # {nb} dans footer() : nombre total de pages, résolu à la génération finale
    registered_fonts = _register_custom_fonts(pdf)
    style = _resolve_body_style(branding, registered_fonts)
    family, is_builtin, scale = style["family"], style["is_builtin"], style["scale"]
    pdf.set_auto_page_break(auto=True, margin=25)
    pdf.add_page()
    col_width = (pdf.w - pdf.l_margin - pdf.r_margin - _COL_GUTTER_MM) / 2

    pdf.set_font(family, "B", style["size"] + 5)
    _write_line(pdf, req.request_type.name, height=10 * scale, safe=is_builtin)
    pdf.ln(2 * scale)

    meta_rows = [
        {"label": "Reference", "value": req.reference},
        {"label": "Statut", "value": req.get_status_display(),
         "color": _STATUS_COLORS.get(req.status, (0, 0, 0))},
        {"label": "Demandeur", "value": req.requester.get_full_name() or req.requester.username},
        {"label": "Soumise le",
         "value": req.submitted_at.strftime("%d/%m/%Y %H:%M") if req.submitted_at else "-"},
    ]
    _render_two_col_row(pdf, meta_rows[0], meta_rows[1], col_width, accent_rgb, style)
    _render_two_col_row(pdf, meta_rows[2], meta_rows[3], col_width, accent_rgb, style)
    pdf.ln(2 * scale)

    pdf.set_draw_color(*accent_rgb)
    pdf.set_line_width(0.5)
    pdf.line(pdf.l_margin, pdf.get_y(), pdf.w - pdf.r_margin, pdf.get_y())
    pdf.ln(5 * scale)

    for group in grouped_labeled_data(req.request_type, req.data or {}):
        if group["section"]:
            pdf.set_font(family, "B", style["size"] + 2)
            pdf.set_text_color(*accent_rgb)
            _write_line(pdf, group["section"], height=8 * scale, safe=is_builtin)
            pdf.set_draw_color(*accent_rgb)
            pdf.set_line_width(0.3)
            pdf.line(pdf.l_margin, pdf.get_y(), pdf.w - pdf.r_margin, pdf.get_y())
            pdf.set_text_color(0, 0, 0)
            pdf.ln(3 * scale)

        _render_field_rows(pdf, group["rows"], col_width, accent_rgb, style)
        pdf.ln(2 * scale)

    return bytes(pdf.output())
