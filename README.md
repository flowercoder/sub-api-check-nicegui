# sub-api-check-nicegui
For check sub api health

这是一个跨平台、纯本地运行的大模型 API 测速与监控工具。全面适配 OpenAI 和 Claude 模型，支持批量检查与健康度监控。数据零上云，绝对保障隐私安全。（支持环境：Python 3.8.5+）

下载本项目,并解压到同一目录下
- bash start.sh 启 动
- bash start.sh stop 停 止 所 有 匹 配 到 的  app.py 进 程

正常启动后，浏览器打开http://本机ip:80/

Note:默认运行端口是80端口，要改端口的话查找
run_port = int(os.environ.get('API_KEY_TESTER_PORT', os.environ.get('PORT', '80')))
把端口改掉即可。

