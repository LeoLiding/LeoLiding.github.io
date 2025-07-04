# http_server.py
import http.server
import socketserver
import logging
import ssl
import urllib.request
import urllib.error
import urllib.parse
import json
import socket
import time

# 配置日志
logging.basicConfig(level=logging.DEBUG,
                   format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

PORT = 8047
MODEL_API_URL = "http://127.0.0.1:8048"
MAX_RETRIES = 3
TIMEOUT = 30  # 增加超时时间到30秒

class ProxyHTTPRequestHandler(http.server.SimpleHTTPRequestHandler):
    def end_headers(self):
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Access-Control-Allow-Methods', 'GET, POST, OPTIONS')
        self.send_header('Access-Control-Allow-Headers', 'Content-Type, Authorization, Accept, Cache-Control, Connection')
        super().end_headers()

    def do_OPTIONS(self):
        self.send_response(200)
        self.end_headers()

    def do_POST(self):
        if self.path.startswith('/v1/'):
            try:
                # 读取请求体
                content_length = int(self.headers.get('Content-Length', 0))
                post_data = self.rfile.read(content_length)
                
                # 记录请求信息
                logger.debug(f"收到POST请求: {self.path}")
                logger.debug(f"请求头: {dict(self.headers)}")
                try:
                    logger.debug(f"请求体: {post_data.decode('utf-8')}")
                except UnicodeDecodeError:
                    logger.debug("请求体包含非UTF-8字符")
                
                # 构建转发请求
                target_url = f"{MODEL_API_URL}{self.path}"
                logger.info(f"转发请求到: {target_url}")
                
                # 创建请求
                req = urllib.request.Request(
                    target_url,
                    data=post_data,
                    headers={
                        'Content-Type': 'application/json; charset=utf-8',
                        'Authorization': 'Bearer token-abc123',
                        'User-Agent': 'Python/3.x',
                        'Accept': 'application/json',
                        'Accept-Charset': 'utf-8'
                    },
                    method='POST'
                )
                
                # 发送请求（带重试）
                retry_count = 0
                last_error = None
                
                while retry_count < MAX_RETRIES:
                    try:
                        logger.debug(f"尝试发送请求到模型API... (尝试 {retry_count + 1}/{MAX_RETRIES})")
                        
                        # 创建自定义的opener
                        opener = urllib.request.build_opener()
                        opener.addheaders = [
                            ('User-Agent', 'Python/3.x'),
                            ('Connection', 'keep-alive'),
                            ('Accept', 'application/json'),
                            ('Accept-Charset', 'utf-8')
                        ]
                        
                        # 设置socket超时
                        socket.setdefaulttimeout(TIMEOUT)
                        
                        with opener.open(req, timeout=TIMEOUT) as response:
                            logger.debug(f"收到模型API响应: {response.status} {response.reason}")
                            
                            # 设置响应头
                            self.send_response(response.status)
                            for header, value in response.getheaders():
                                if header.lower() not in ('transfer-encoding', 'connection'):
                                    self.send_header(header, value)
                            self.end_headers()
                            
                            # 转发响应数据
                            logger.debug("开始转发响应数据...")
                            while True:
                                chunk = response.read(8192)
                                if not chunk:
                                    break
                                try:
                                    self.wfile.write(chunk)
                                    self.wfile.flush()
                                except Exception as e:
                                    logger.error(f"写入响应数据时发生错误: {str(e)}")
                                    break
                            logger.debug("响应数据转发完成")
                            return  # 成功完成，退出函数
                            
                    except (urllib.error.URLError, socket.timeout) as e:
                        last_error = e
                        retry_count += 1
                        if retry_count < MAX_RETRIES:
                            wait_time = 2 ** retry_count  # 指数退避
                            logger.warning(f"请求失败，{wait_time}秒后重试: {str(e)}")
                            time.sleep(wait_time)
                        continue
                    except Exception as e:
                        logger.error(f"转发请求时发生错误: {str(e)}")
                        self.send_error(500, str(e))
                        return
                
                # 如果所有重试都失败了
                logger.error(f"所有重试都失败了: {str(last_error)}")
                self.send_error(500, f"请求超时: {str(last_error)}")
                    
            except Exception as e:
                logger.error(f"处理请求时发生错误: {str(e)}")
                try:
                    error_message = str(e).encode('utf-8')
                    self.send_error(500, error_message.decode('utf-8'))
                except:
                    self.send_error(500, "Internal Server Error")
        else:
            super().do_POST()

    def do_GET(self):
        try:
            super().do_GET()
        except Exception as e:
            logger.error(f"处理GET请求时发生错误: {str(e)}")
            self.send_error(500, str(e))

def run_server():
    # 修改监听地址为 '0.0.0.0'，允许所有网络接口访问
    httpd = http.server.HTTPServer(('0.0.0.0', PORT), ProxyHTTPRequestHandler)
    
    try:
        # 创建SSL上下文
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.minimum_version = ssl.TLSVersion.TLSv1_2
        context.load_cert_chain(certfile='cert.pem', keyfile='key.pem')
        
        # 包装socket
        httpd.socket = context.wrap_socket(httpd.socket, server_side=True)
        
        logger.info(f"HTTPS服务器启动在端口 {PORT}")
        logger.info(f"服务器监听地址: 0.0.0.0")
        logger.info(f"模型API地址: {MODEL_API_URL}")
        logger.info("SSL配置已加载，使用TLSv1.2或更高版本")
        httpd.serve_forever()
    except Exception as e:
        logger.error(f"服务器启动失败: {str(e)}")
        raise

if __name__ == '__main__':
    run_server() 