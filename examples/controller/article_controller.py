from flask_server.app import app, json_response
from flask_server.util import Logger, GraceResult, CommonUtil
from flask import request

# 样例Controller，展示了通过MySQL进行增删改查
# 注意：接入工程前需将 ArticleService 放入 flask_server/service/ 并导出
#       并将 ArticlePO/BuyRecordPO（examples/model/article_declared.py）放入 model/po/ 并导出

from examples.service.article_service import ArticleService


Logger.info("article_controller.py loaded")


def _require_fields(*names):
    """校验请求体字段是否齐全；缺失时返回 (None, 400 响应元组)。

    示例刻意不依赖 KeyError → 400：未捕获的 KeyError 属于服务端缺陷，框架按 500 处理。
    """
    payload = request.payload if isinstance(request.payload, dict) else {}
    missing = [name for name in names if name not in payload]
    if missing:
        return None, (GraceResult.param_error(data={'missing': missing}), 400)
    return {name: payload[name] for name in names}, None


def _require_param(name):
    """校验 query 参数是否存在；缺失时返回 (None, 400 响应元组)"""
    value = request.params.get(name)
    if value is None:
        return None, (GraceResult.param_error(data={'missing': [name]}), 400)
    return value, None


@app.route('/api/article/admin/put', methods=['POST'])
@json_response
def add():
    data, error = _require_fields('title', 'content', 'secret_content', 'money', 'state')
    if error is not None:
        return error
    ArticleService.add(
        data['title'],
        data['content'],
        data['secret_content'],
        data['money'],
        data['state'],
    )
    return GraceResult.ok()


@app.route('/api/article/admin/modify', methods=['POST'])
@json_response
def modify():
    data, error = _require_fields('aid', 'title', 'content', 'secret_content', 'money', 'state')
    if error is not None:
        return error
    ArticleService.modify_by_aid(
        data['aid'],
        data['title'],
        data['content'],
        data['secret_content'],
        data['money'],
        data['state'],
    )
    return GraceResult.ok()


@app.route('/api/article/admin/delete', methods=['POST'])
@json_response
def delete():
    data, error = _require_fields('aid')
    if error is not None:
        return error
    ArticleService.delete_by_aid(data['aid'])
    return GraceResult.ok()


@app.route('/api/article/admin/list', methods=['GET'])
@json_response
def list_backend():
    articles = ArticleService.list()
    articles = list(map(lambda x: CommonUtil.dict_map(x,
                                      mapper_list=['aid', 'title', 'money', 'state', 'access_count',
                                                   'buy_count', 'update_time', 'create_time']), articles))
    return GraceResult.ok(articles)


@app.route('/api/article/admin/get', methods=['GET'])
@json_response
def get_backend():
    aid, error = _require_param('aid')
    if error is not None:
        return error
    article = ArticleService.get(aid)
    if article is None:
        return GraceResult.business_error(4004, '文章不存在'), 404
    article = CommonUtil.dict_map(article, mapper_list=[
        'aid', 'title', 'content', 'secret_content', 'money', 'state',
        'access_count', 'buy_count', 'update_time', 'create_time'
    ])
    return GraceResult.ok(article)


@app.route('/api/article/get', methods=['GET'])
@json_response
def get_frontend():
    aid, error = _require_param('aid')
    if error is not None:
        return error
    is_buy = ArticleService.is_buy(request.info.get('user_key'), aid)
    article = ArticleService.get(aid, inc_access_count=True)
    if article is None or article['state'] == 0:
        return GraceResult.param_error()
    article = CommonUtil.dict_map(article, mapper_list=[
        'aid', 'title', 'content', 'secret_content', 'money', 'access_count',
        'buy_count', 'update_time', 'create_time'
    ])
    if is_buy:
        article['money'] = None
    else:
        article['secret_content'] = None
    return GraceResult.ok(article)


@app.route('/api/article/list', methods=['GET'])
@json_response
def list_frontend():
    articles = ArticleService.list()
    articles = list(filter(lambda x: x['state'] == 1, articles))
    records = ArticleService.get_buy_record(request.info['user_key'])
    articles = list(map(lambda x: CommonUtil.dict_map(x, mapper_list=[
        'aid', 'title', 'money', 'access_count', 'buy_count',
        'update_time', 'create_time',
    ]), articles))
    records = list(map(lambda x: x['aid'], records))
    for art in articles:
        if art['aid'] in records:
            art['have'] = True
        else:
            art['have'] = False
    return GraceResult.ok(articles)




