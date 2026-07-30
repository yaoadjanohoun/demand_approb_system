"""Suivi de la dernière navigation d'un utilisateur connecté (retour client :
choisir un approbateur de secours "actif aujourd'hui" — voir
services.py:WorkflowEngine.missing_manager_candidate et
views.py:_approver_candidates).

Throttlé (UPDATE_INTERVAL) pour ne pas écrire en base à chaque requête —
seule la fraîcheur "aujourd'hui" compte pour cet usage, pas la précision
à la seconde.
"""
import datetime

from django.utils import timezone

from .models import UserProfile

UPDATE_INTERVAL = datetime.timedelta(minutes=5)


class TrackLastSeenMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)
        user = getattr(request, "user", None)
        if user is not None and user.is_authenticated:
            self._touch(user)
        return response

    @staticmethod
    def _touch(user):
        now = timezone.now()
        profile = getattr(user, "profile", None)
        if profile is None:
            UserProfile.objects.get_or_create(user=user, defaults={"last_seen_at": now})
            return
        if profile.last_seen_at is None or now - profile.last_seen_at > UPDATE_INTERVAL:
            UserProfile.objects.filter(pk=profile.pk).update(last_seen_at=now)
