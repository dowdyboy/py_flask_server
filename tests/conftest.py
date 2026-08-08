import os
import sys

import pytest

# 确保项目根目录在 sys.path 中，以便 import flask_server
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))


@pytest.fixture
def client():
    """Flask 测试客户端（HTTP 层集成测试用）"""
    from flask_server import app
    app.config['TESTING'] = True
    return app.test_client()

