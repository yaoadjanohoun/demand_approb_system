"""Données communes à la barre latérale (types de demande actifs, nombre de
demandes à approuver) — évite de recalculer ça dans chaque vue."""
from .models import Request, RequestType
from .services import WorkflowEngine



# fonction pour défnir les infos dans le sidebar pour un utilisateur.
def sidebar(request):
    if not request.user.is_authenticated:
        return {}

    request_types = RequestType.objects.filter(is_active=True).order_by("name")

    pending_count = 0
    for req in Request.objects.filter(status=Request.Status.PENDING):
        if request.user.id in WorkflowEngine(req).get_effective_approvers():
            pending_count += 1

    profile = getattr(request.user, "profile", None)
    photo_url = profile.photo.url if profile and profile.photo else None

    # Rôle métier assigné (UserProfile.role, ex: "Comptable") — pas le rôle
    # système calculé (Super admin/Admin fonctionnel/Manager/Demandeur,
    # voir models.system_role_label, resté visible côté admin uniquement).
    # Retour client : laisser vide tant qu'aucun rôle n'est assigné, pour
    # qu'il s'affiche automatiquement dès qu'un admin fonctionnel le
    # renseigne — pas de texte de repli à corriger après coup.
    role_label = profile.role.name if profile and profile.role_id else ""

    return {
        "nav_request_types": request_types,
        "nav_pending_count": pending_count,
        "nav_photo_url": photo_url,
        "nav_role_label": role_label,
    }
