#!/usr/bin/env python
"""Point d'entrée de production, lancé par IIS via HttpPlatformHandler (web.config).

`manage.py runserver` est un serveur de développement (mono-thread, rechargement
automatique, warnings de sécurité) : ce script sert l'application avec waitress,
un serveur WSGI pur Python (donc installable sans compilation, cf. contrainte
"Zéro Droit Admin Local" des Spécifications Techniques §1.2).

IIS/HttpPlatformHandler impose le port d'écoute via la variable d'environnement
HTTP_PLATFORM_PORT (voir web.config) ; HTTP_PLATFORM_PORT=8080 par défaut permet
aussi de lancer ce script manuellement pour un test local.

Ne lance PAS les migrations : conformément aux Spécifications Techniques §2.4,
elles sont exécutées une fois par le script de déploiement de la DSI avant le
basculement du trafic, pas à chaque démarrage du processus applicatif.
"""
import os
import sys

import waitress

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'demand_approb_system_main.settings')


def main():
    import django

    django.setup()
    from django.core.wsgi import get_wsgi_application

    application = get_wsgi_application()

    port = int(os.environ.get('HTTP_PLATFORM_PORT', '8080'))
    host = os.environ.get('APP_LISTEN_HOST', '127.0.0.1')
    threads = int(os.environ.get('APP_WAITRESS_THREADS', '8'))

    print(f"Démarrage du serveur de production sur {host}:{port} ({threads} threads)", file=sys.stderr)
    waitress.serve(application, host=host, port=port, threads=threads)


if __name__ == '__main__':
    main()
