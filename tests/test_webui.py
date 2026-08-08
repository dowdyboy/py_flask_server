"""webui controller 测试：生产缓存（LRU/淘汰）、SPA 回退、静态资源 404"""

import os
from collections import OrderedDict

from flask_server import config
import flask_server.controller.webui_controller as wc


def test_cache_get_set_basic(monkeypatch):
    cache = OrderedDict()
    monkeypatch.setattr(wc, 'path_exist_cache', cache)
    assert wc._cache_get('/x') is None
    wc._cache_set('/x', True)
    assert wc._cache_get('/x') is True
    wc._cache_set('/x', False)
    assert wc._cache_get('/x') is False


def test_cache_lru_order_refresh(monkeypatch):
    cache = OrderedDict()
    monkeypatch.setattr(wc, 'path_exist_cache', cache)
    wc._cache_set('/a', True)
    wc._cache_set('/b', True)
    wc._cache_get('/a')                 # 访问 /a → 移到末尾
    assert list(cache.keys()) == ['/b', '/a']


def test_cache_eviction(monkeypatch):
    monkeypatch.setattr(wc, '_PATH_CACHE_MAX', 2)
    cache = OrderedDict()
    monkeypatch.setattr(wc, 'path_exist_cache', cache)
    wc._cache_set('/a', True)
    wc._cache_set('/b', True)
    wc._cache_set('/c', True)           # 超限 → 淘汰最旧的 /a
    assert list(cache.keys()) == ['/b', '/c']


def test_prod_cache_flow(client, monkeypatch, tmp_path):
    """生产模式（debug=False + 缓存启用）：命中缓存返回文件"""
    fake_dir = tmp_path / 'webui'
    fake_dir.mkdir()
    (fake_dir / 'index.html').write_text('<html>idx</html>', encoding='utf-8')
    (fake_dir / 'app.js').write_text('console.log(1)', encoding='utf-8')
    monkeypatch.setattr(config, 'webui_dir', str(fake_dir))
    cache = OrderedDict()
    monkeypatch.setattr(wc, 'path_exist_cache', cache)

    resp1 = client.get('/app.js')
    assert resp1.status_code == 200
    assert resp1.get_data(as_text=True) == 'console.log(1)'
    # 首次请求后应写入缓存
    assert cache.get(os.path.join(str(fake_dir), 'app.js')) is True
    # 第二次请求命中缓存
    resp2 = client.get('/app.js')
    assert resp2.status_code == 200
    assert resp2.get_data(as_text=True) == 'console.log(1)'


def test_missing_index_returns_404(client, monkeypatch, tmp_path):
    """SPA 回退但 index.html 也不存在时返回 404"""
    fake_dir = tmp_path / 'webui_empty'
    fake_dir.mkdir()
    monkeypatch.setattr(config, 'webui_dir', str(fake_dir))
    monkeypatch.setattr(wc, 'path_exist_cache', OrderedDict())
    resp = client.get('/some/spa/route')
    assert resp.status_code == 404


def test_missing_static_returns_404(client, monkeypatch, tmp_path):
    """带静态扩展名且不存在的路径返回 404（不回退 index.html）"""
    fake_dir = tmp_path / 'webui_static'
    fake_dir.mkdir()
    (fake_dir / 'index.html').write_text('<html>idx</html>', encoding='utf-8')
    monkeypatch.setattr(config, 'webui_dir', str(fake_dir))
    monkeypatch.setattr(wc, 'path_exist_cache', OrderedDict())
    resp = client.get('/missing.js')
    assert resp.status_code == 404


def test_api_prefix_not_swallowed(client):
    """未匹配的 /api/ 路径不落入 SPA 回退"""
    resp = client.get('/api/some/unknown/path')
    assert resp.status_code == 404
