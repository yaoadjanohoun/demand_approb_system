"""Backend email dont la configuration SMTP vient de la base de données
(EmailSettings) plutôt que de settings.py, pour que la changement ne nécessite
aucune modification de code ni redéploiement (retour client, cf. commentaire
sur EmailSettings dans approvals/models.py).

Sans configuration active, les emails partent sur la console — mode
dégradé sûr, cohérent avec le reste du projet (AD/SQL Server inactifs tant
que non configurés).
"""
from django.core.mail.backends.console import EmailBackend as ConsoleBackend
from django.core.mail.backends.smtp import EmailBackend as SMTPBackend


class DBEmailBackend(SMTPBackend):
    def __init__(self, *args, **kwargs):
        from .models import EmailSettings

        active = EmailSettings.get_active()
        self._console_fallback = None
        if active is None:
            self._console_fallback = ConsoleBackend(*args, **kwargs)
            # Toujours initialiser la classe parente (des attributs comme
            # .fail_silently sont lus par Django même en mode dégradé).
            kwargs.setdefault("host", "localhost")
            super().__init__(*args, **kwargs)
            return

        kwargs["host"] = active.host
        kwargs["port"] = active.port
        kwargs["username"] = active.username
        kwargs["password"] = active.password
        kwargs["use_tls"] = active.use_tls
        super().__init__(*args, **kwargs)
        self._from_email = active.from_email

    def send_messages(self, email_messages):
        if self._console_fallback is not None:
            return self._console_fallback.send_messages(email_messages)
        for message in email_messages:
            if not message.from_email:
                message.from_email = self._from_email
        return super().send_messages(email_messages)
