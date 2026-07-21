"""Données communes à la barre latérale (types de demande actifs, nombre de
demandes à approuver) — évite de recalculer ça dans chaque vue."""
from .models import Request, RequestType
from .services import WorkflowEngine


def sidebar(request):
    if not request.user.is_authenticated:
        return {}

    request_types = RequestType.objects.filter(is_active=True).order_by("name")

    pending_count = 0
    for req in Request.objects.filter(status=Request.Status.PENDING):
        if request.user.id in WorkflowEngine(req).get_effective_approvers():
            pending_count += 1

    return {
        "nav_request_types": request_types,
        "nav_pending_count": pending_count,
    }
