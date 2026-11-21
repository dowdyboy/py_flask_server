

from typing import Any

from .error_code import ErrorCode


# HTTP响应对象工具类


class GraceResult:

    OK = int(ErrorCode.OK)
    PARAM_ERROR = int(ErrorCode.PARAM_ERROR)
    INNER_ERROR = int(ErrorCode.INTERNAL_ERROR)

    def __init__(self, code: int, msg: str, data: Any) -> None:
        self.code = code
        self.msg = msg
        self.data = data

    @staticmethod
    def ok(data: Any = None) -> 'GraceResult':
        return GraceResult(GraceResult.OK, '成功', data)

    @staticmethod
    def param_error(data: Any = None) -> 'GraceResult':
        return GraceResult(GraceResult.PARAM_ERROR, '参数错误', data)

    @staticmethod
    def error(data: Any = None) -> 'GraceResult':
        return GraceResult(GraceResult.INNER_ERROR, '接口发生错误', data)

    @staticmethod
    def business_error(code: int, msg: str, data: Any = None) -> 'GraceResult':
        """自定义业务错误码"""
        return GraceResult(code, msg, data)
