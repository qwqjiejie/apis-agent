/**
 * API 调用封装
 * 封装所有后端 API 调用
 */

// 测试后端连接
const testConnection = async (backendUrl) => {
    try {
        const response = await fetch(`${backendUrl}/file/list`, {
            method: 'GET',
            headers: {
                'Accept': 'application/json'
            }
        });
        return { success: true };
    } catch (error) {
        return {
            success: false,
            error: '无法连接到后端服务，请确保后端在 ' + backendUrl + ' 运行'
        };
    }
};

// 加载会话列表
const loadChats = async (backendUrl) => {
    try {
        const response = await fetch(`${backendUrl}/session/list?pageNum=1&pageSize=100`, {
            method: 'GET',
            headers: {
                'Accept': 'application/json'
            }
        });

        if (!response.ok) {
            throw new Error('获取会话列表失败');
        }

        const result = await response.json();
        if (result.code === 200 && result.data && result.data.records) {
            return result.data.records.map(item => ({
                id: item.conversationId,
                title: item.question
                    ? item.question.substring(0, 20) + (item.question.length > 20 ? '...' : '')
                    : '新对话',
                agentType: item.agentType,
                fileid: item.fileid,
                messages: []
            }));
        }
        return [];
    } catch (error) {
        console.error('加载会话列表失败:', error);
        return [];
    }
};

// 获取会话详情
const getChatDetail = async (backendUrl, chatId) => {
    try {
        const response = await fetch(`${backendUrl}/session/${chatId}`, {
            method: 'GET',
            headers: {
                'Accept': 'application/json'
            }
        });

        if (!response.ok) {
            throw new Error('获取会话详情失败');
        }

        const result = await response.json();
        if (result.code === 200 && result.data) {
            return result.data;
        }
        return null;
    } catch (error) {
        console.error('获取会话详情失败:', error);
        return null;
    }
};

// 删除会话
const deleteChat = async (backendUrl, chatId) => {
    try {
        const response = await fetch(`${backendUrl}/session/${chatId}`, {
            method: 'DELETE',
            headers: {
                'Accept': 'application/json'
            }
        });

        if (!response.ok) {
            throw new Error('删除会话失败');
        }

        const result = await response.json();
        return {
            success: result.code === 200 || result.code === 0,
            message: result.message
        };
    } catch (error) {
        console.error('删除会话失败:', error);
        return {
            success: false,
            error: error.message
        };
    }
};

// 上传文件
const uploadFile = async (backendUrl, file) => {
    const formData = new FormData();
    formData.append('file', file);

    const response = await fetch(`${backendUrl}/file/upload`, {
        method: 'POST',
        body: formData
    });

    if (!response.ok) {
        let errorMsg = '文件上传失败';
        try {
            const errorBody = await response.json();
            if (errorBody && errorBody.message) {
                errorMsg = errorBody.message;
            }
        } catch (e) {
            // 响应体不是 JSON，使用默认错误消息
        }
        throw new Error(errorMsg);
    }

    const result = await response.json();
    if (result.code === 200 && result.data) {
        return {
            success: true,
            fileId: result.data.fileId
        };
    }
    throw new Error(result.message || '文件上传失败');
};

// 获取流式聊天 API URL
const getStreamChatUrl = (backendUrl, selectedAgent, hasFile) => {
    if (hasFile) {
        return `${backendUrl}/agent/file/stream`;
    } else if (selectedAgent === 'ppt') {
        return `${backendUrl}/agent/pptx/stream`;
    } else if (selectedAgent === 'deep') {
        return `${backendUrl}/agent/deep/stream`;
    } else if (selectedAgent === 'skills') {
        return `${backendUrl}/agent/skills/stream`;
    }
    return `${backendUrl}/agent/chat/stream`;
};

// 停止流式请求
const stopStream = async (backendUrl, conversationId) => {
    try {
        const stopUrl = `${backendUrl}/agent/stop?conversationId=${conversationId}`;
        const response = await fetch(stopUrl, {
            method: 'GET'
        });
        const result = await response.json();
        return result;
    } catch (error) {
        console.warn('调用停止接口失败:', error);
        return null;
    }
};

// 导出 API 函数（用于非模块化环境）
window.APP_API = {
    testConnection,
    loadChats,
    getChatDetail,
    deleteChat,
    uploadFile,
    getStreamChatUrl,
    stopStream
};
