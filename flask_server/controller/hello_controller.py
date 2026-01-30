from flask_server.app import app
from flask_server.util import Logger

Logger.info("hello_controller.py loaded")


@app.route('/hello', methods=['GET'])
def hello():
    return 'Hello, World!'

