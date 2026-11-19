import os

from notification_auth import authorized_dispatch, valid_github_oidc

def test_rechaza_solicitudes_anonimas(monkeypatch):
    monkeypatch.delenv('NOTIFICATION_JOB_KEY', raising=False)
    assert not authorized_dispatch({})
    assert not authorized_dispatch({'authorization': 'Bearer invalid'})
    assert not valid_github_oidc('')

def test_cron_con_clave_privada(monkeypatch):
    key='una-clave-de-prueba-con-mas-de-veinticuatro-caracteres'
    monkeypatch.setenv('NOTIFICATION_JOB_KEY', key)
    assert authorized_dispatch({'x-notification-key': key})
    assert not authorized_dispatch({'x-notification-key': 'incorrecta'})

def test_oidc_y_audiencia_no_se_fian_del_cliente(monkeypatch):
    monkeypatch.delenv('NOTIFICATION_JOB_KEY', raising=False)
    assert not authorized_dispatch({'authorization': 'Bearer e30.e30.'})
    assert not authorized_dispatch({'authorization': 'Bearer '})
