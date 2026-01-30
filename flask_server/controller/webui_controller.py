import os
import copy
from flask import send_from_directory
from flask_server import app, config
from flask_server.util import Logger

# 对静态界面的Controller，一般不用修改


Logger.info("webui_controller.py loaded")

path_exist_cache = dict()

response_cache = dict()


# def send_from_directory_with_cache(webui_dir, filename):
#     if config.webui_cache:
#         filepath = os.path.join(webui_dir, filename)
#         if filepath in response_cache.keys():
#             return copy.copy(response_cache[filepath])
#         else:
#             resp = send_from_directory(webui_dir, filename)
#             response_cache[filepath] = copy.copy(resp)
#             return resp
#     else:
#         return send_from_directory(webui_dir, filename)


@app.route('/', methods=['GET'], defaults={'filename': 'index.html'})
@app.route('/<path:filename>', methods=['GET'])
def webui(filename):
    filepath = os.path.join(config.webui_dir, filename)
    if filepath in path_exist_cache.keys():
        if path_exist_cache[filepath]:
            return send_from_directory(config.webui_dir, filename)
        else:
            return send_from_directory(config.webui_dir, 'index.html')
    else:
        if os.path.exists(filepath):
            path_exist_cache[filepath] = True
            return send_from_directory(config.webui_dir, filename)
        else:
            path_exist_cache[filepath] = False
            return send_from_directory(config.webui_dir, 'index.html')

