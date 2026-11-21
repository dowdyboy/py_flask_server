from flask_server.util.grace_result import GraceResult
from flask_server.util.error_code import ErrorCode


def test_error_code_enum_is_stable():
    """对外契约：错误码数值不能变（客户端/文档依赖这些数字）"""
    assert int(ErrorCode.OK) == 0
    assert int(ErrorCode.PARAM_ERROR) == 1001
    assert int(ErrorCode.INTERNAL_ERROR) == -1
    assert int(ErrorCode.AUTH_INVALID) == 4001
    assert int(ErrorCode.UNAUTHORIZED) == 4002
    assert int(ErrorCode.LOGIN_LOCKED) == 4003
    assert int(ErrorCode.DEPENDENCY_NOT_READY) == 5030
    assert GraceResult.OK == ErrorCode.OK
    assert GraceResult.PARAM_ERROR == ErrorCode.PARAM_ERROR
    assert GraceResult.INNER_ERROR == ErrorCode.INTERNAL_ERROR


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
