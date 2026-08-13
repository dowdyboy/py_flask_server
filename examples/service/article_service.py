from flask_server.module import sqlalchemy, sqlalchemy_trans
from flask_server.util import RandomGenerator, Logger, CommonUtil
from sqlalchemy import and_
from datetime import datetime


# 样例服务
# 展示了如何使用SQLAlchemy访问数据库
# 展示了如何使用本地文件存储
# 展示了如何使用内存缓存
# 展示了如何使用装饰器处理事务


def _get_article_po():
    """延迟导入 ArticlePO（声明式定义见 examples/model/article_declared.py）"""
    from examples.model.article_declared import ArticlePO
    return ArticlePO


def _get_buy_record_po():
    """延迟导入 BuyRecordPO（声明式定义见 examples/model/article_declared.py）"""
    from examples.model.article_declared import BuyRecordPO
    return BuyRecordPO


class ArticleService:

    @staticmethod
    @sqlalchemy_trans
    def add(title, content, secret_content, money, state):
        ArticlePO = _get_article_po()
        article = ArticlePO()
        article.aid = f'article_{RandomGenerator.secrets_token(32)}'
        article.title = title
        article.content = content
        article.secret_content = secret_content
        article.money = money
        article.state = state
        article.access_count = 0
        article.buy_count = 0
        article.update_time = datetime.now()
        article.create_time = datetime.now()
        Logger.info(f'ArticleService add : {article.__dict__}')
        sqlalchemy().session.add(article)

    @staticmethod
    @sqlalchemy_trans
    def modify_by_aid(aid, title, content, secret_content, money, state):
        ArticlePO = _get_article_po()
        article = ArticlePO.query.filter(ArticlePO.aid == aid).first()
        if article is None:
            return False
        Logger.info(f'ArticleService modify_by_aid : <before> {article.__dict__}')
        article.title = title
        article.content = content
        article.secret_content = secret_content
        article.money = money
        article.state = state
        article.update_time = datetime.now()
        Logger.info(f'ArticleService modify_by_aid : <after> {article.__dict__}')

    @staticmethod
    @sqlalchemy_trans
    def delete_by_aid(aid):
        ArticlePO = _get_article_po()
        article = ArticlePO.query.filter(ArticlePO.aid == aid).first()
        if article is not None:
            Logger.info(f'ArticleService delete_by_aid : {article.__dict__}')
            sqlalchemy().session.delete(article)

    @staticmethod
    @sqlalchemy_trans
    def increase_access_count():
        """示例：空实现，请按需补全"""
        pass

    @staticmethod
    def list():
        ArticlePO = _get_article_po()
        articles = ArticlePO.query.all()
        articles = list(map(lambda x: CommonUtil.obj_to_dict(x), articles))
        return articles

    @staticmethod
    @sqlalchemy_trans
    def get(aid, inc_access_count=False):
        ArticlePO = _get_article_po()
        article = ArticlePO.query.filter(ArticlePO.aid == aid).first()
        if article is None:
            return None
        if inc_access_count:
            article.access_count += 1
        return CommonUtil.obj_to_dict(article)

    @staticmethod
    def is_buy(user_key, aid):
        BuyRecordPO = _get_buy_record_po()
        record = BuyRecordPO.query.filter(and_(
            BuyRecordPO.user_key == user_key,
            BuyRecordPO.aid == aid
        )).first()
        return record is not None

    @staticmethod
    def get_buy_record(user_key):
        BuyRecordPO = _get_buy_record_po()
        records = BuyRecordPO.query.filter(
            BuyRecordPO.user_key == user_key
        ).all()
        return CommonUtil.obj_to_dict(records)
