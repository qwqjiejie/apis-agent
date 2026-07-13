/**
 * 全局配置文件
 * 可以在这里修改后端地址等配置
 */

// 后端 API 地址
// 修改这里可以切换到不同的后端服务器
const CONFIG = {
    backendUrl: 'http://localhost:8888'
};

// 导出配置（用于非模块化环境）
window.APP_CONFIG = CONFIG;
