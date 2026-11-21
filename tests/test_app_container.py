"""0.9 app 容器：logger / metrics / db 按 app 隔离，并可通过 app.extensions 取用。"""

import logging

from flask_server.factory import create_app, create_core_app
from flask_server.module.container import (
    DB_EXT_KEY, LOGGER_EXT_KEY, METRICS_EXT_KEY, get_db, get_logger, get_metrics,
)


def _new_app():
    return create_app({'TESTING': True})


def test_logger_is_app_scoped_and_used_in_context():
    a, b = _new_app(), _new_app()
    la, lb = a.extensions[LOGGER_EXT_KEY], b.extensions[LOGGER_EXT_KEY]
    assert la is not lb
    assert la.name != lb.name

    from flask_server.util import Logger

    records = []

    class Capture(logging.Handler):
        def emit(self, record):
            records.append(record.getMessage())

    handler = Capture()
    la.addHandler(handler)
    try:
        with a.app_context():
            Logger.info('from-a')
    finally:
        la.removeHandler(handler)
    assert records == ['from-a'], records


def test_logger_falls_back_to_process_logger_outside_app():
    from flask_server.util import Logger

    records = []

    class Capture(logging.Handler):
        def emit(self, record):
            records.append(record.getMessage())

    parent = logging.getLogger('flask_server')
    parent.setLevel(logging.INFO)   # 确保 INFO 不被 root 的默认 WARNING 过滤
    handler = Capture()
    parent.addHandler(handler)
    try:
        Logger.info('outside-app')
    finally:
        parent.removeHandler(handler)
    assert records == ['outside-app']


def test_metrics_bundles_are_isolated_per_app():
    a, b = _new_app(), _new_app()
    ma, mb = a.extensions[METRICS_EXT_KEY], b.extensions[METRICS_EXT_KEY]
    assert ma is not mb
    assert ma.requests is not mb.requests
    assert ma.registry is not mb.registry

    client_a, client_b = a.test_client(), b.test_client()
    client_a.get('/hello')

    body_a = client_a.get('/metrics').get_data(as_text=True)
    body_b = client_b.get('/metrics').get_data(as_text=True)
    assert 'http_requests_total' in body_a
    assert 'route="/hello"' in body_a
    assert 'route="/hello"' not in body_b, 'B 的指标不应包含 A 的流量'
    assert get_metrics(app=a) is ma


def test_db_is_registered_in_app_container(monkeypatch, tmp_path):
    import importlib

    from flask_server import config
    from flask_server.module import sqlalchemy as sqlalchemy_fn

    sa_module = importlib.import_module('flask_server.module.sqlalchemy')

    db_file = tmp_path / 'container.db'
    monkeypatch.setattr(config, 'sqlalchemy_uri', f'sqlite:///{db_file.as_posix()}')
    monkeypatch.setattr(config, 'db_reflect_on_start', False)
    monkeypatch.setattr(sa_module, 'db', None)
    monkeypatch.setattr(sa_module, '_app', None)
    monkeypatch.setattr(sa_module, '_bound_uri', None)

    app = _new_app()
    db = sqlalchemy_fn()
    try:
        assert db is not None
        assert app.extensions[DB_EXT_KEY] is db
        with app.app_context():
            assert get_db() is db
        assert get_db() is None, '无 app 上下文时不应隐式返回某个 app 的 db'
    finally:
        with app.app_context():
            db.session.remove()
            db.engine.dispose()


def test_create_core_app_skips_http_stack():
    app = create_core_app()
    assert 'metrics' not in app.view_functions
    assert '/api/v1/auth/login' not in {str(r) for r in app.url_map.iter_rules()}
    assert app.extensions[LOGGER_EXT_KEY] is not None
    assert METRICS_EXT_KEY not in app.extensions, '核心 app 不装配指标组件'
    assert get_logger(app=app) is app.extensions[LOGGER_EXT_KEY]
