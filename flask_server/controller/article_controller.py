from flask_server.app import app, json_response
from flask_server.util import Logger, GraceResult, CommonUtil
from flask_server.service import ArticleService
from flask import request, send_file

# 样例Controller，展示了通过MySQL进行增删改查


Logger.info("article_controller.py loaded")


@app.route('/api/article/admin/put', methods=['POST'])
@json_response
def add():
    ArticleService.add(
        request.payload['title'],
        request.payload['content'],
        request.payload['secret_content'],
        request.payload['money'],
        request.payload['state'],
    )
    return GraceResult.ok()


@app.route('/api/article/admin/modify', methods=['POST'])
@json_response
def modify():
    ArticleService.modify_by_aid(
        request.payload['aid'],
        request.payload['title'],
        request.payload['content'],
        request.payload['secret_content'],
        request.payload['money'],
        request.payload['state'],
    )
    return GraceResult.ok()


@app.route('/api/article/admin/delete', methods=['POST'])
@json_response
def delete():
    ArticleService.delete_by_aid(request.payload['aid'])
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
    article = ArticleService.get(request.params['aid'])
    if article is None:
        return GraceResult.param_error()
    article = CommonUtil.dict_map(article, mapper_list=[
        'aid', 'title', 'content', 'secret_content', 'money', 'state',
        'access_count', 'buy_count', 'update_time', 'create_time'
    ])
    return GraceResult.ok(article)


@app.route('/api/article/get', methods=['GET'])
@json_response
def get_frontend():
    is_buy = ArticleService.is_buy(request.info['user_key'], request.params['aid'])
    article = ArticleService.get(request.params['aid'], inc_access_count=True)
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




