"""Authentification Active Directory (voir "Les Spécifications Techniques") :
"Aucun mot de passe interne n'est stocké dans la base de données Django."

Implémentation via `ldap3` (pur Python) plutôt que `django-auth-ldap`/`python-ldap`
(extension C nécessitant une compilation, généralement indisponible sous Windows
sans droits admin — cf. contrainte "Zéro Droit Admin Local" des spécifications).

Principe ("bind authentication") :
1. Connexion au serveur AD avec un compte de service pour retrouver le DN de
   l'utilisateur à partir de son identifiant (sAMAccountName).
2. Nouvelle connexion avec ce DN et le mot de passe fourni : si le bind
   réussit, le mot de passe est valide (AD fait toute la vérification, aucun
   mot de passe ne transite jamais vers la base de données Django).
3. Création/mise à jour de l'utilisateur Django (mot de passe local désactivé
   via set_unusable_password) et synchronisation des informations de profil
   (manager, département, site) à titre informatif.

Non couvert par ce backend (hors périmètre de l'application Django) :
Kerberos/SSO transparent, qui se configure au niveau du serveur d'hébergement
(IIS + HttpPlatformHandler), pas dans le code applicatif.
"""
import logging

import ldap3
from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.auth.backends import BaseBackend
from django.utils import timezone

from .models import UserProfile

logger = logging.getLogger(__name__)
User = get_user_model()


class ActiveDirectoryBackend(BaseBackend):
    def authenticate(self, request, username=None, password=None, **kwargs):
        if not username or not password:
            return None
        if not getattr(settings, "AUTH_LDAP_SERVER_URI", None):
            return None  # LDAP non configuré (ex: poste de développement local)

        server = ldap3.Server(settings.AUTH_LDAP_SERVER_URI, get_info=ldap3.NONE)

        try:
            service_conn = ldap3.Connection(
                server,
                user=settings.AUTH_LDAP_BIND_DN,
                password=settings.AUTH_LDAP_BIND_PASSWORD,
                auto_bind=True,
            )
        except ldap3.core.exceptions.LDAPException:
            logger.error("AD/LDAP : connexion du compte de service impossible.")
            return None

        try:
            entry = self._find_user_entry(service_conn, username)
            if entry is None:
                return None

            if not self._verify_password(server, entry.entry_dn, password):
                return None

            return self._get_or_create_user(service_conn, username, entry)
        finally:
            service_conn.unbind()

    def get_user(self, user_id):
        try:
            return User.objects.get(pk=user_id)
        except User.DoesNotExist:
            return None

    # ------------------------------------------------------------------
    def _find_user_entry(self, service_conn, username):
        safe_username = ldap3.utils.conv.escape_filter_chars(username)
        search_filter = settings.AUTH_LDAP_USER_SEARCH_FILTER.format(username=safe_username)
        attrs = list(settings.AUTH_LDAP_ATTR_MAP.values())
        service_conn.search(
            search_base=settings.AUTH_LDAP_USER_SEARCH_BASE,
            search_filter=search_filter,
            attributes=attrs,
        )
        if not service_conn.entries:
            return None
        return service_conn.entries[0]

    def _verify_password(self, server, user_dn, password):
        """Le bind réussit si et seulement si le mot de passe est correct."""
        try:
            user_conn = ldap3.Connection(server, user=user_dn, password=password, auto_bind=True)
        except ldap3.core.exceptions.LDAPBindError:
            return False
        except ldap3.core.exceptions.LDAPException:
            logger.error("AD/LDAP : erreur lors de la vérification du mot de passe.")
            return False
        user_conn.unbind()
        return True

    def _get_or_create_user(self, service_conn, username, entry):
        attr_map = settings.AUTH_LDAP_ATTR_MAP
        user, created = User.objects.get_or_create(username=username.lower())

        user.first_name = self._attr(entry, attr_map.get("first_name")) or user.first_name
        user.last_name = self._attr(entry, attr_map.get("last_name")) or user.last_name
        user.email = self._attr(entry, attr_map.get("email")) or user.email
        if created:
            user.set_unusable_password()  # aucun mot de passe interne stocké (cf. spec)
        user.save()

        self._sync_profile(service_conn, user, entry, attr_map)
        return user

    def _sync_profile(self, service_conn, user, entry, attr_map):
        profile, _ = UserProfile.objects.get_or_create(user=user)

        department_name = self._attr(entry, attr_map.get("department"))
        if department_name:
            profile.department_name = department_name

        site_name = self._attr(entry, attr_map.get("site"))
        if site_name:
            profile.site_name = site_name

        manager_dn = self._attr(entry, attr_map.get("manager"))
        if manager_dn:
            manager_username = self._resolve_username_from_dn(service_conn, manager_dn)
            if manager_username:
                manager = User.objects.filter(username=manager_username.lower()).first()
                if manager:
                    profile.manager = manager

        profile.last_ad_sync = timezone.now()
        profile.save()

    def _resolve_username_from_dn(self, service_conn, dn):
        """Retrouve le sAMAccountName (identifiant de connexion) d'un DN AD.

        Le CN d'un DN (ex: "CN=Jean Dupont,OU=...") est un nom d'affichage,
        pas forcément l'identifiant de connexion : on interroge l'annuaire
        plutôt que de parser le DN.
        """
        username_attr = settings.AUTH_LDAP_USERNAME_ATTR
        service_conn.search(
            search_base=dn,
            search_filter="(objectClass=*)",
            search_scope=ldap3.BASE,
            attributes=[username_attr],
        )
        if not service_conn.entries:
            return None
        return self._attr(service_conn.entries[0], username_attr)

    @staticmethod
    def _attr(entry, attr_name):
        if not attr_name or attr_name not in entry:
            return None
        value = entry[attr_name].value
        return str(value) if value else None
