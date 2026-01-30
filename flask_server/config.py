import os
import logging
from pathlib import Path


class Config:
    def __init__(self, ):
        # 系统配置
        self.port = 5000   # 服务端api的端口号
        self.project_dir = Path(__file__).parent.parent   # 项目根目录绝对路径
        self.thread_num = 10   # 线程池线程数量
        self.debug = True   # 是否开启debug

        # logger配置
        self.log_filename = os.path.join(self.project_dir, 'server.log')
        self.log_level = logging.DEBUG
        self.log_filemode = 'w'

        # sqlite相关配置
        self.db_file_path = None
        self.db_init_sql_list = []

        # sqlalchemy相关配置
        self.sqlalchemy_uri = None
        # self.sqlalchemy_uri = 'mysql+pymysql://username:passwd@xxx.xxx.xxx.xxx:3306/database_name'
        self.sqlalchemy_track_modify = False

        # 静态文件存储配置
        self.file_saved_path = os.path.join(self.project_dir, 'storage')

        # Webui配置
        self.webui_dir = os.path.join(self.project_dir, 'webui')
        # self.webui_cache = False   # 是否开启静态页面缓存，尚未实现

        # 自定义配置
        


config = Config()

