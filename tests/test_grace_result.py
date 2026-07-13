from flask_server.util.grace_result import GraceResult


def test_ok():
    r = GraceResult.ok()
    assert r.code == GraceResult.OK
    assert r.msg == '成功'
    assert r.data is None


def test_ok_with_data():
    r = GraceResult.ok({'x': 1})
    assert r.code == 0
    assert r.data == {'x': 1}


def test_param_error():
    r = GraceResult.param_error('bad')
    assert r.code == GraceResult.PARAM_ERROR
    assert r.msg == '参数错误'
    assert r.data == 'bad'


def test_error():
    r = GraceResult.error('boom')
    assert r.code == GraceResult.INNER_ERROR
    assert r.msg == '接口发生错误'
    assert r.data == 'boom'


def test_business_error():
    r = GraceResult.business_error(2001, '用户不存在')
    assert r.code == 2001
    assert r.msg == '用户不存在'
    assert r.data is None


def test_business_error_with_data():
    r = GraceResult.business_error(2002, '余额不足', {'balance': 10})
    assert r.code == 2002
    assert r.data == {'balance': 10}
