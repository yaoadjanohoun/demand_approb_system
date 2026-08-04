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

from .forms import grouped_labeled_data
from .models import DocumentBranding

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
                except (FileNotFoundError, RuntimeError):
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
