"""Notifications email sur les événements du workflow (retour client) :

"qu'une notification soit envoyée aux approbateurs et aux administrateurs...
via envoi de mail" à la soumission, et "quand la demande est retournée au
demandeur pour info, il n'y a pas de notifications... il faudra gérer cela
avec email" pour les décisions.

Appelé depuis WorkflowEngine (services.py) à chaque transition. Utilise le
DBEmailBackend déjà en place (approvals/email_backend.py) : sans
configuration SMTP active, les emails partent sur la console — aucune
notification ne doit jamais faire planter une action métier, donc chaque
envoi est protégé par un try/except journalisé.
"""
import logging

from django.contrib.auth import get_user_model
from django.core.mail import send_mail

logger = logging.getLogger(__name__)
User = get_user_model()


def _emails_for_ids(user_ids):
    if not user_ids:
        return []
    return list(
        User.objects.filter(id__in=user_ids, is_active=True)
        .exclude(email="")
        .values_list("email", flat=True)
    )


def _staff_emails():
    return list(
        User.objects.filter(is_staff=True, is_active=True)
        .exclude(email="")
        .values_list("email", flat=True)
    )


def _send(subject, message, recipients):
    # dédoublonne en gardant l'ordre, retire les entrées vides
    recipients = list(dict.fromkeys(r for r in recipients if r))
    if not recipients:
        return
    try:
        send_mail(subject, message, None, recipients, fail_silently=False)
    except Exception:
        logger.exception("Échec d'envoi de notification email : %s", subject)


def _request_path(req):
    return f"/{req.pk}/"


def _requester_label(req):
    return req.requester.get_full_name() or req.requester.username


def notify_submission(req):
    """Nouvelle demande active : confirmation au demandeur + alerte aux
    approbateurs du niveau 1 et aux admins fonctionnels."""
    if req.requester.email:
        _send(
            "[Demandes] Votre demande a été soumise",
            f"Votre demande « {req.request_type.name} » a bien été soumise et est "
            f"en attente d'approbation.\n\nConsultez-la : {_request_path(req)}",
            [req.requester.email],
        )

    from .services import WorkflowEngine

    approver_emails = _emails_for_ids(WorkflowEngine(req).get_effective_approvers())
    recipients = approver_emails + _staff_emails()
    _send(
        f"[Demandes] Nouvelle demande « {req.request_type.name} » de {_requester_label(req)}",
        f"{_requester_label(req)} a soumis une demande « {req.request_type.name} ».\n\n"
        f"Consultez-la : {_request_path(req)}",
        recipients,
    )


def notify_auto_approved(req):
    if not req.requester.email:
        return
    _send(
        "[Demandes] Votre demande a été approuvée automatiquement",
        f"Votre demande « {req.request_type.name} » ne correspondait à aucune règle "
        f"d'approbation configurée et a donc été approuvée automatiquement.\n\n"
        f"Consultez-la : {_request_path(req)}",
        [req.requester.email],
    )


def notify_level_activated(req):
    """Un niveau devient courant (après une approbation intermédiaire, une
    resoumission, ou une intervention admin) : alerte ses approbateurs."""
    from .services import WorkflowEngine

    recipients = _emails_for_ids(WorkflowEngine(req).get_effective_approvers())
    _send(
        f"[Demandes] Demande « {req.request_type.name} » en attente de votre approbation",
        f"Une demande de {_requester_label(req)} attend votre validation "
        f"(niveau {req.current_level}).\n\nConsultez-la : {_request_path(req)}",
        recipients,
    )


def notify_decision(req, action_label, comment=None):
    """Décision finale ou intermédiaire notifiée au demandeur (approuvée,
    refusée, retournée pour information)."""
    if not req.requester.email:
        return
    body = f"Votre demande « {req.request_type.name} » a été {action_label}."
    if comment:
        body += f"\n\nCommentaire : {comment}"
    body += f"\n\nConsultez-la : {_request_path(req)}"
    _send(f"[Demandes] Demande {action_label}", body, [req.requester.email])
