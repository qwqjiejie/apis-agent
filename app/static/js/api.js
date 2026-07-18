/**
 * API 调用封装
 * 封装所有后端 API 调用，并统一处理登录 token / 匿名 ID 请求头。
 */

const AUTH_STORAGE_KEYS = {
    token: 'apis_agent_token',
    user: 'apis_agent_user',
    anonymousId: 'apis_agent_anonymous_id'
};

const normalizeBackendUrl = (backendUrl) => (backendUrl || '').replace(/\/$/, '');

const getAnonymousId = () => {
    let anonymousId = localStorage.getItem(AUTH_STORAGE_KEYS.anonymousId);
    if (!anonymousId) {
        if (window.crypto && crypto.randomUUID) {
            anonymousId = crypto.randomUUID();
        } else {
            anonymousId = Date.now().toString(36) + Math.random().toString(36).slice(2);
        }
        localStorage.setItem(AUTH_STORAGE_KEYS.anonymousId, anonymousId);
    }
    return anonymousId;
};

const getAuthToken = () => localStorage.getItem(AUTH_STORAGE_KEYS.token) || '';

const getAuthUser = () => {
    const raw = localStorage.getItem(AUTH_STORAGE_KEYS.user);
    if (!raw) return null;
    try {
        return JSON.parse(raw);
    } catch (error) {
        localStorage.removeItem(AUTH_STORAGE_KEYS.user);
        return null;
    }
};

const saveAuthSession = (payload) => {
    localStorage.setItem(AUTH_STORAGE_KEYS.token, payload.token);
    localStorage.setItem(AUTH_STORAGE_KEYS.user, JSON.stringify({
        userId: payload.userId,
        username: payload.username
    }));
};

const clearAuthSession = () => {
    localStorage.removeItem(AUTH_STORAGE_KEYS.token);
    localStorage.removeItem(AUTH_STORAGE_KEYS.user);
};

const getAuthState = () => ({
    token: getAuthToken(),
    user: getAuthUser(),
    anonymousId: getAnonymousId(),
    isAuthenticated: !!getAuthToken()
});

const authHeaders = (headers = {}) => {
    const token = getAuthToken();
    if (token) {
        return { ...headers, Authorization: `Bearer ${token}` };
    }
    return { ...headers, 'X-Anonymous-Id': getAnonymousId() };
};

const jsonHeaders = () => authHeaders({
    'Content-Type': 'application/json',
    'Accept': 'application/json'
});

const parseJsonResponse = async (response, fallbackMessage) => {
    let result = null;
    try {
        result = await response.json();
    } catch (error) {
        result = null;
    }

    if (!response.ok) {
        const message = result?.message || fallbackMessage || `请求失败 (${response.status})`;
        throw new Error(message);
    }

    if (result && result.code !== undefined && result.code !== 200 && result.code !== 0) {
        throw new Error(result.message || fallbackMessage || '请求失败');
    }

    return result;
};

// ===== 认证接口 =====

const login = async (backendUrl, username, password) => {
    const baseUrl = normalizeBackendUrl(backendUrl);
    const response = await fetch(`${baseUrl}/api/v1/auth/login`, {
        method: 'POST',
        headers: {
            'Content-Type': 'application/json',
            'Accept': 'application/json'
        },
        body: JSON.stringify({ username, password })
    });
    const result = await parseJsonResponse(response, '登录失败');
    saveAuthSession(result.data);
    return result.data;
};

const register = async (backendUrl, username, password) => {
    const baseUrl = normalizeBackendUrl(backendUrl);
    const response = await fetch(`${baseUrl}/api/v1/auth/register`, {
        method: 'POST',
        headers: {
            'Content-Type': 'application/json',
            'Accept': 'application/json'
        },
        body: JSON.stringify({ username, password })
    });
    const result = await parseJsonResponse(response, '注册失败');
    saveAuthSession(result.data);
    return result.data;
};

const syncAnonymousSessions = async (backendUrl, anonymousId = getAnonymousId()) => {
    const baseUrl = normalizeBackendUrl(backendUrl);
    const response = await fetch(`${baseUrl}/api/v1/auth/sync`, {
        method: 'POST',
        headers: jsonHeaders(),
        body: JSON.stringify({ anonymousId })
    });
    const result = await parseJsonResponse(response, '同步匿名会话失败');
    return result.data;
};

const getCurrentUser = async (backendUrl) => {
    const baseUrl = normalizeBackendUrl(backendUrl);
    const response = await fetch(`${baseUrl}/api/v1/auth/me`, {
        method: 'GET',
        headers: authHeaders({ 'Accept': 'application/json' })
    });
    const result = await parseJsonResponse(response, '获取登录状态失败');
    return result.data;
};

const logout = () => {
    clearAuthSession();
};

// ===== 通用业务接口 =====

// 测试后端连接
const testConnection = async (backendUrl) => {
    try {
        const baseUrl = normalizeBackendUrl(backendUrl);
        const response = await fetch(`${baseUrl}/health/live`, {
            method: 'GET',
            headers: { 'Accept': 'application/json' }
        });
        if (!response.ok) {
            throw new Error(`服务响应异常 (${response.status})`);
        }
        return { success: true };
    } catch (error) {
        return {
            success: false,
            error: '无法连接到后端服务，请确保后端在 ' + backendUrl + ' 运行'
        };
    }
};

// 创建会话
const createSession = async (backendUrl) => {
    const baseUrl = normalizeBackendUrl(backendUrl);
    const response = await fetch(`${baseUrl}/api/v1/session`, {
        method: 'POST',
        headers: authHeaders({ 'Accept': 'application/json' })
    });
    const result = await parseJsonResponse(response, '创建会话失败');
    return result.data;
};

// 加载会话列表
const loadChats = async (backendUrl) => {
    try {
        const baseUrl = normalizeBackendUrl(backendUrl);
        const response = await fetch(`${baseUrl}/api/v1/session/list`, {
            method: 'POST',
            headers: jsonHeaders(),
            body: JSON.stringify({ pageNum: 1, pageSize: 100 })
        });

        const result = await parseJsonResponse(response, '获取会话列表失败');
        if (result.data && result.data.records) {
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
        const baseUrl = normalizeBackendUrl(backendUrl);
        const response = await fetch(`${baseUrl}/api/v1/session/detail`, {
            method: 'POST',
            headers: jsonHeaders(),
            body: JSON.stringify({ conversationId: chatId })
        });

        const result = await parseJsonResponse(response, '获取会话详情失败');
        return result.data || null;
    } catch (error) {
        console.error('获取会话详情失败:', error);
        return null;
    }
};

// 删除会话
const deleteChat = async (backendUrl, chatId) => {
    try {
        const baseUrl = normalizeBackendUrl(backendUrl);
        const response = await fetch(`${baseUrl}/api/v1/session/delete`, {
            method: 'POST',
            headers: jsonHeaders(),
            body: JSON.stringify({ conversationId: chatId })
        });

        const result = await parseJsonResponse(response, '删除会话失败');
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
    const baseUrl = normalizeBackendUrl(backendUrl);
    const formData = new FormData();
    formData.append('file', file);

    const response = await fetch(`${baseUrl}/api/v1/file/upload`, {
        method: 'POST',
        headers: authHeaders(),
        body: formData
    });

    const result = await parseJsonResponse(response, '文件上传失败');
    if (result.data) {
        return {
            success: true,
            fileId: result.data.fileId
        };
    }
    throw new Error(result.message || '文件上传失败');
};

// 能力前缀映射（前端根据选中Agent拼接前缀到用户消息前）
const CAPABILITY_PREFIX_MAP = {
    'chat': '',
    'ppt': '生成ppt: ',
    'deep': '深度研究: ',
    'skills': '',
    'file': '分析文档: ',
};

// 获取流式聊天 API URL（统一入口）
const getStreamChatUrl = (backendUrl) => {
    const baseUrl = normalizeBackendUrl(backendUrl);
    return `${baseUrl}/api/v1/chat`;
};

// 构建能力前缀消息
const buildPrefixedMessage = (message, agentType, hasFile) => {
    if (hasFile && !message) return '分析文档: 请帮我分析上传的文件';
    if (!message) return message;
    const prefix = CAPABILITY_PREFIX_MAP[agentType] || '';
    return prefix + message;
};

// 停止流式请求
const stopStream = async (backendUrl, conversationId) => {
    try {
        const baseUrl = normalizeBackendUrl(backendUrl);
        const response = await fetch(`${baseUrl}/api/v1/agent/stop`, {
            method: 'POST',
            headers: jsonHeaders(),
            body: JSON.stringify({ conversationId: conversationId })
        });
        return await parseJsonResponse(response, '停止请求失败');
    } catch (error) {
        console.warn('调用停止接口失败:', error);
        return null;
    }
};

// 导出 API 函数（用于非模块化环境）
window.APP_API = {
    getAnonymousId,
    getAuthState,
    getAuthToken,
    getAuthUser,
    authHeaders,
    login,
    register,
    logout,
    syncAnonymousSessions,
    getCurrentUser,
    testConnection,
    createSession,
    loadChats,
    getChatDetail,
    deleteChat,
    uploadFile,
    getStreamChatUrl,
    buildPrefixedMessage,
    stopStream
};
