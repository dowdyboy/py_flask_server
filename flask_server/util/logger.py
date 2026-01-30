import logging
import time
from ..config import config

# 日志工具类，用于输出日志


class Logger:

    @staticmethod
    def init(
                 filename=None,
                 level=logging.INFO,
                 format='%(asctime)s [%(levelname)s] %(message)s',
                 filemode='w',
                 **args
                 ):
        if filename is None:
            filename = time.strftime("%Y_%m_%d_%H_%M_%S", time.localtime())
            filename = f'log_{filename}.log'
        logging.basicConfig(filename=filename, level=level,
                            encoding='utf-8',
                            format=format, filemode=filemode, **args)

    @staticmethod
    def info(txt):
        print(txt)
        logging.info(txt)

    @staticmethod
    def warn(txt):
        print(txt)
        logging.warning(txt)

    @staticmethod
    def error(txt):
        print(txt)
        logging.error(txt)


Logger.init(filename=config.log_filename,
            level=config.log_level,
            filemode=config.log_filemode, )

