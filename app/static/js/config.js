/**
 * 全局配置文件
 * 可以在这里修改后端地址等配置
 */

// 后端 API 地址
// 默认使用当前页面同源服务；直接打开 HTML 文件时回退到 README 中的本地端口。
const DEFAULT_BACKEND_URL = window.location.protocol.startsWith('http')
    ? window.location.origin
    : 'http://localhost:8080';

// 修改这里可以切换到不同的后端服务器
const CONFIG = {
    backendUrl: DEFAULT_BACKEND_URL
};

// 导出配置（用于非模块化环境）
window.APP_CONFIG = CONFIG;
