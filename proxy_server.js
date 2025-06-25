const express = require('express');
const cors = require('cors');
const { createProxyMiddleware } = require('http-proxy-middleware');

const app = express();

// 启用 CORS
app.use(cors({
    origin: ['https://leoliding.github.io', 'http://localhost:3000'],
    methods: ['GET', 'POST', 'OPTIONS'],
    allowedHeaders: ['Content-Type', 'Authorization']
}));

// 代理配置
const proxyOptions = {
    target: 'http://127.0.0.1:8048',
    changeOrigin: true,
    pathRewrite: {
        '^/api': '/v1/chat/completions'
    },
    onProxyRes: function(proxyRes, req, res) {
        proxyRes.headers['Access-Control-Allow-Origin'] = '*';
    }
};

// 使用代理中间件
app.use('/api', createProxyMiddleware(proxyOptions));

// 健康检查端点
app.get('/health', (req, res) => {
    res.status(200).send('OK');
});

const PORT = process.env.PORT || 3000;
app.listen(PORT, () => {
    console.log(`Proxy server is running on port ${PORT}`);
}); 