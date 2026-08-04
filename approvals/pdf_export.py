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


def generate_request_summary_pdf(req):
    pdf = FPDF()
    pdf.set_compression(False)  # PDF lisible en clair (pratique pour les tests, coût négligeable ici)
    pdf.add_page()

    pdf.set_font("Helvetica", "B", 16)
    pdf.multi_cell(0, 10, req.request_type.name, **_NEXT_LINE)

    pdf.set_font("Helvetica", "", 11)
    meta_lines = [
        f"Reference : {req.reference}",
        f"Statut : {req.get_status_display()}",
        f"Demandeur : {req.requester.get_full_name() or req.requester.username}",
        f"Soumise le : {req.submitted_at.strftime('%d/%m/%Y %H:%M') if req.submitted_at else '-'}",
    ]
    for line in meta_lines:
        pdf.multi_cell(0, 6, line, **_NEXT_LINE)
    pdf.ln(4)

    pdf.set_font("Helvetica", "B", 13)
    pdf.multi_cell(0, 8, "Details de la demande", **_NEXT_LINE)
    pdf.ln(1)

    for row in labeled_data(req.request_type, req.data or {}):
        pdf.set_font("Helvetica", "B", 10)
        pdf.multi_cell(0, 6, str(row["label"]), **_NEXT_LINE)
        pdf.set_font("Helvetica", "", 11)
        pdf.multi_cell(0, 6, str(row["value"]) if row["value"] not in (None, "") else "-", **_NEXT_LINE)
        pdf.ln(2)

    return bytes(pdf.output())
