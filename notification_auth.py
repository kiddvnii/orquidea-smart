"""Autenticación del programador de GitHub Actions mediante tokens OIDC firmados.

No requiere guardar una clave compartida en GitHub ni abrir el endpoint al público.
Se conserva NOTIFICATION_JOB_KEY como alternativa para cron externo.
"""
from __future__ import annotations

import os
import secrets

import jwt
from jwt import PyJWKClient
from jwt.exceptions import InvalidTokenError, PyJWKClientError

GITHUB_ISSUER = 'https://token.actions.githubusercontent.com'
GITHUB_JWKS = GITHUB_ISSUER + '/.well-known/jwks'
AUDIENCE = 'orquidea-smart-notifications'
ALLOWED_REPOSITORY = 'kiddvnii/orquidea-smart'
ALLOWED_REPOSITORY_ID = '1431729844'
WORKFLOW_REF = ALLOWED_REPOSITORY + '/.github/workflows/watering-reminders.yml@refs/heads/main'

# Con caché para no consultar las claves públicas en cada ejecución.
_jwks_client = PyJWKClient(GITHUB_JWKS, cache_jwk_set=True, lifespan=3600, timeout=8)


def valid_github_oidc(token: str) -> bool:
    if not token or len(token) > 10000:
        return False
    try:
        key = _jwks_client.get_signing_key_from_jwt(token)
        claims = jwt.decode(
            token,
            key.key,
            algorithms=['RS256'],
            issuer=GITHUB_ISSUER,
            audience=AUDIENCE,
            leeway=15,
            options={'require': [
                'iss', 'aud', 'exp', 'nbf', 'iat',
                'repository', 'repository_id', 'ref', 'workflow_ref', 'event_name',
            ]},
        )
    except (InvalidTokenError, PyJWKClientError, ValueError, TimeoutError, OSError):
        return False
    return (
        claims.get('repository') == ALLOWED_REPOSITORY
        and str(claims.get('repository_id')) == ALLOWED_REPOSITORY_ID
        and claims.get('ref') == 'refs/heads/main'
        and claims.get('workflow_ref') == WORKFLOW_REF
        and claims.get('event_name') in ('schedule', 'workflow_dispatch')
    )


def authorized_dispatch(headers) -> bool:
    expected = os.getenv('NOTIFICATION_JOB_KEY', '')
    provided = headers.get('x-notification-key', '')
    if expected and len(expected) >= 24 and secrets.compare_digest(expected, provided):
        return True
    authorization = headers.get('authorization', '')
    if not authorization.startswith('Bearer '):
        return False
    return valid_github_oidc(authorization[len('Bearer '):].strip())
