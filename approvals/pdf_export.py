"""Génère un PDF résumé d'une demande (retour client : en plus du document
de référence attaché au type de demande — voir RequestType.reference_form_pdf
— l'approbateur doit pouvoir télécharger un PDF reprenant les réponses
saisies par le demandeur). Ce PDF est généré à la volée à partir des mêmes
données que la page de détail (labeled_data), pas une copie du PDF de
référence : aucun champ de formulaire PDF (AcroForm) n'est rempli."""
from fpdf import FPDF
from fpdf.enums import XPos, YPos

from .forms import labeled_data

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
    " ": " ",  # espace insécable
}


def _pdf_safe(text):
    for original, replacement in _TYPOGRAPHIC_REPLACEMENTS.items():
        text = text.replace(original, replacement)
    return text.encode("latin-1", errors="replace").decode("latin-1")


def _write_line(pdf, text, height=6):
    pdf.multi_cell(0, height, _pdf_safe(text), **_NEXT_LINE)


def generate_request_summary_pdf(req):
    pdf = FPDF()
    pdf.set_compression(False)  # PDF lisible en clair (pratique pour les tests, coût négligeable ici)
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

    pdf.set_font("Helvetica", "B", 13)
    _write_line(pdf, "Details de la demande", height=8)
    pdf.ln(1)

    for row in labeled_data(req.request_type, req.data or {}):
        pdf.set_font("Helvetica", "B", 10)
        _write_line(pdf, str(row["label"]))
        pdf.set_font("Helvetica", "", 11)
        _write_line(pdf, str(row["value"]) if row["value"] not in (None, "") else "-")
        pdf.ln(2)

    return bytes(pdf.output())
