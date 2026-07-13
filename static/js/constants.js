/**
 * 常量定义文件
 * 存放应用中的固定常量
 */

// 智能体列表
const AGENTS = [
    { id: 'chat', name: '对话助手', icon: '💬' },
    { id: 'file', name: '文件问答', icon: '📁' },
    { id: 'ppt', name: 'PPT生成', icon: '📊' },
    { id: 'deep', name: '深度研究', icon: '🔬' },
    { id: 'skills', name: '技能助手', icon: '🛠' }
];

// 支持的文件类型
const SUPPORTED_FILE_TYPES = {
    mime: [
        'application/pdf',
        'application/msword',
        'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
        'text/plain',
        'image/png',
        'image/jpeg',
        'image/jpg'
    ],
    extensions: ['pdf', 'doc', 'docx', 'txt', 'png', 'jpg', 'jpeg']
};

// 流式消息类型
const STREAM_TYPES = {
    TEXT: 'text',           // 文本内容
    THINKING: 'thinking',   // 思考过程
    TOOL_START: 'tool_start', // 工具开始执行
    TOOL_END: 'tool_end',   // 工具执行完成
    REFERENCE: 'reference', // 参考来源
    RECOMMEND: 'recommend', // 推荐问题
    ERROR: 'error',         // 错误事件（含重试通知）
    COMPLETE: 'complete',   // 完成
    DONE: '[DONE]'          // 结束标记
};

// 导出常量（用于非模块化环境）
window.APP_CONSTANTS = {
    AGENTS,
    SUPPORTED_FILE_TYPES,
    STREAM_TYPES
};
