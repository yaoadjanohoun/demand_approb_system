"""Chiffrement du mot de passe SMTP stocké en base (EmailSettings.password).

Utilise Fernet (AES-128-CBC + HMAC) avec une clé dédiée fournie par variable
d'environnement — jamais en dur dans le code (même principe que les autres
secrets de configuration : AD_LDAP_BIND_PASSWORD, SQLSERVER_PASSWORD).
En développement local, une clé est dérivée de SECRET_KEY pour ne pas bloquer
le poste de dev ; en production, EMAIL_CONFIG_ENCRYPTION_KEY doit être définie
explicitement (générée une fois via Fernet.generate_key()) sans quoi la
donnée chiffrée devient illisible à chaque changement de SECRET_KEY.
"""
import base64
import hashlib
import os

from cryptography.fernet import Fernet, InvalidToken
from django.conf import settings


def _get_fernet():
    key = os.environ.get("EMAIL_CONFIG_ENCRYPTION_KEY")
    if not key:
        derived = hashlib.sha256(settings.SECRET_KEY.encode("utf-8")).digest()
        key = base64.urlsafe_b64encode(derived)
    return Fernet(key)


def encrypt(plaintext):
    if not plaintext:
        return ""
    return _get_fernet().encrypt(plaintext.encode("utf-8")).decode("ascii")


def decrypt(ciphertext):
    if not ciphertext:
        return ""
    try:
        return _get_fernet().decrypt(ciphertext.encode("ascii")).decode("utf-8")
    except InvalidToken:
        return ""
