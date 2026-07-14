const { createApp, ref, computed, nextTick, onMounted, watch } = Vue;

createApp({
    setup() {
        // ===== 配置和常量 =====
        const backendUrl = ref(APP_CONFIG.backendUrl);
        const connectionError = ref(null);
        const agents = ref(APP_CONSTANTS.AGENTS);
        const { STREAM_TYPES, SUPPORTED_FILE_TYPES } = APP_CONSTANTS;

        // ===== 状态 =====
        const selectedAgent = ref('chat');
        const chatList = ref([]);
        const currentChatId = ref(null);
        const inputMessage = ref('');
        const selectedFile = ref(null);
        const uploadedFileId = ref(null);
        const isUploading = ref(false);
        const isDragOver = ref(false);
        const isSending = ref(false);
        const currentRecommendMsgId = ref(null);

        // 确认对话框状态
        const showConfirmDialog = ref(false);
        const confirmTitle = ref('确认操作');
        const confirmMessage = ref('');
        let confirmCallback = null;

        // 引用
        const messagesContainer = ref(null);
        const textareaInput = ref(null);

        // 当前流式输出容器的 DOM 引用
        let currentStreamContentDiv = null;
        let currentThinkingContentDiv = null;
        let currentThinkingSectionDiv = null;
        let currentThinkingContentWrapper = null;

        // 用于中断流式请求的 AbortController
        let abortController = null;

        // ===== 初始化 =====
        onMounted(async () => {
            await loadChatsFromStorage();
            createNewChat();
            APP_UTILS.setupMarkdown();
        });

        // ===== 工具函数（使用 utils.js） =====
        const { generateId, formatFileSize, renderMarkdown, processReferences, processRecommendations } = APP_UTILS;

        // ===== 直接更新流式输出的 DOM =====
        const updateStreamContent = (content, isThinking = false) => {
            if (isThinking && currentThinkingContentDiv) {
                const html = renderMarkdown(content);
                currentThinkingContentDiv.innerHTML = html;
                if (typeof hljs !== 'undefined') {
                    currentThinkingContentDiv.querySelectorAll('pre code').forEach((block) => {
                        hljs.highlightElement(block);
                    });
                }
            } else if (currentStreamContentDiv) {
                const html = renderMarkdown(content);
                currentStreamContentDiv.innerHTML = html;
                if (typeof hljs !== 'undefined') {
                    currentStreamContentDiv.querySelectorAll('pre code').forEach((block) => {
                        hljs.highlightElement(block);
                    });
                }
            }
            if (messagesContainer.value) {
                messagesContainer.value.scrollTop = messagesContainer.value.scrollHeight;
            }
        };

        // ===== API 相关 =====
        const testConnection = async () => {
            const result = await APP_API.testConnection(backendUrl.value);
            if (result.success) {
                connectionError.value = null;
            } else {
                connectionError.value = result.error;
            }
        };

        const loadChatsFromStorage = async () => {
            chatList.value = await APP_API.loadChats(backendUrl.value);
        };

        const selectChat = async (chatId) => {
            currentChatId.value = chatId;

            const chat = chatList.value.find(c => c.id === chatId);
            if (chat && chat.isNew) {
                return;
            }

            const sessionData = await APP_API.getChatDetail(backendUrl.value, chatId);
            if (sessionData) {
                const chat = chatList.value.find(c => c.id === chatId);
                if (chat) {
                    chat.agentType = sessionData.agentType;
                    chat.fileid = sessionData.fileid;
                    chat.messages = [];

                    if (sessionData.messages && Array.isArray(sessionData.messages)) {
                        sessionData.messages.forEach(msg => {
                            if (msg.question) {
                                chat.messages.push({
                                    id: 'user_' + msg.id,
                                    role: 'user',
                                    content: msg.question,
                                    file: !!msg.fileid,
                                    fileName: msg.fileid ? '已上传文件' : null,
                                    timestamp: msg.createTime ? new Date(msg.createTime).getTime() : Date.now()
                                });
                            }

                            if (msg.answer || msg.thinking) {
                                const reference = processReferences(msg.reference);
                                const showRef = false;
                                const thinkingContent = msg.thinking || '';
                                chat.messages.push({
                                    id: 'assistant_' + msg.id,
                                    role: 'assistant',
                                    content: msg.answer || '',
                                    thinking: thinkingContent ? [thinkingContent] : [],
                                    timeline: thinkingContent ? [{ type: 'thinking', content: thinkingContent }] : [],
                                    reference: reference,
                                    recommend: [],
                                    showTimeline: false,
                                    showReference: showRef,
                                    hasThinking: !!thinkingContent,
                                    timestamp: msg.createTime ? new Date(msg.createTime).getTime() : Date.now()
                                });
                            }
                        });
                    }

                    const firstUserMessage = chat.messages.find(m => m.role === 'user');
                    if (firstUserMessage && firstUserMessage.content) {
                        chat.title = firstUserMessage.content.substring(0, 20) + (firstUserMessage.content.length > 20 ? '...' : '');
                    }
                }
            }

            nextTick(() => {
                // Vue 模板会自动渲染参考来源，不需要手动操作 DOM
                scrollToBottom();
            });
        };

        const deleteChat = async (chatId) => {
            confirmTitle.value = '确认删除';
            confirmMessage.value = '删除该会话后将无法恢复，是否继续？';
            confirmCallback = async () => {
                const result = await APP_API.deleteChat(backendUrl.value, chatId);
                if (result.success) {
                    const index = chatList.value.findIndex(c => c.id === chatId);
                    if (index !== -1) {
                        chatList.value.splice(index, 1);
                        if (currentChatId.value === chatId) {
                            if (chatList.value.length > 0) {
                                selectChat(chatList.value[0].id);
                            } else {
                                createNewChat();
                            }
                        }
                    }
                } else {
                    alert('删除失败: ' + (result.message || result.error || '未知错误'));
                }
                showConfirmDialog.value = false;
            };
            showConfirmDialog.value = true;
        };

        // ===== 文件处理 =====
        const handleFileSelect = async (event) => {
            const files = event.target.files;
            if (files.length > 0) {
                if (selectedFile.value) {
                    alert('已上传文件，请先删除当前文件再上传新文件（限1个）');
                    return;
                }
                await handleFile(files[0]);
            }
            event.target.value = '';
        };

        const handleFile = async (file) => {
            selectedFile.value = file;
            isUploading.value = true;
            uploadedFileId.value = null;

            try {
                const validTypes = SUPPORTED_FILE_TYPES.mime;
                const validExts = SUPPORTED_FILE_TYPES.extensions;
                const fileExt = file.name.split('.').pop().toLowerCase();

                if (!validTypes.includes(file.type) && !validExts.includes(fileExt)) {
                    alert('不支持的文件类型，仅支持 PDF、Word、TXT、PNG、JPG 格式');
                    removeFile();
                    return;
                }

                const result = await APP_API.uploadFile(backendUrl.value, file);
                uploadedFileId.value = result.fileId;
            } catch (error) {
                console.error('文件上传错误:', error);
                alert('文件上传失败: ' + error.message);
                removeFile();
            } finally {
                isUploading.value = false;
            }
        };

        const removeFile = () => {
            selectedFile.value = null;
            uploadedFileId.value = null;
        };

        // ===== 消息发送和流式处理 =====
        const sendMessage = async (presetMessage = null) => {
            if (isSending.value || isUploading.value) return;

            const message = presetMessage || inputMessage.value.trim();
            if (!message && !selectedFile.value) return;
            const hasFile = !!selectedFile.value;
            currentRecommendMsgId.value = null;
            isSending.value = true;

            if (!presetMessage) {
                inputMessage.value = '';
            }
            const fileToSend = selectedFile.value;
            const fileIdToSend = uploadedFileId.value;
            if (textareaInput.value) {
                textareaInput.value.style.height = 'auto';
            }

            const chat = currentChat.value;
            if (chat.isNew) {
                chat.isNew = false;
            }

            const userMsg = {
                id: generateId(),
                role: 'user',
                content: message,
                file: hasFile,
                fileName: fileToSend ? fileToSend.name : null,
                timestamp: Date.now()
            };
            chat.messages.push(userMsg);

            if (chat.messages.filter(m => m.role === 'user').length === 1 && message) {
                chat.title = message.substring(0, 20) + (message.length > 20 ? '...' : '');
            }

            let aiMsg = {
                id: generateId(),
                role: 'assistant',
                content: '',
                thinking: [],
                timeline: [],
                reference: [],
                recommend: [],
                showTimeline: true,
                showReference: false,
                hasThinking: false,
                timestamp: Date.now()
            };
            chat.messages.push(aiMsg);
            // 重新赋值为 Vue proxy 版本，后续修改才能触发响应式更新
            aiMsg = chat.messages[chat.messages.length - 1];

            await nextTick();

            const messageElements = messagesContainer.value.querySelectorAll('.message.assistant');
            const aiMessageElement = messageElements[messageElements.length - 1];

            if (aiMessageElement) {
                const textContentDiv = aiMessageElement.querySelector('.text-content');
                if (textContentDiv) {
                    currentStreamContentDiv = textContentDiv;
                }
            }

            if (messagesContainer.value) {
                messagesContainer.value.scrollTop = messagesContainer.value.scrollHeight;
            }

            const apiUrl = APP_API.getStreamChatUrl(backendUrl.value, selectedAgent.value, hasFile && fileIdToSend);
            const url = new URL(apiUrl);
            url.searchParams.append('query', message || (hasFile ? '请分析这个文件' : ''));
            url.searchParams.append('conversationId', currentChatId.value);
            if (hasFile && fileIdToSend) {
                url.searchParams.append('fileId', fileIdToSend);
            }

            try {
                abortController = new AbortController();
                const signal = abortController.signal;

                const response = await fetch(url.toString(), {
                    method: 'GET',
                    headers: {
                        'Accept': 'text/event-stream',
                        'Cache-Control': 'no-cache',
                        'Connection': 'keep-alive'
                    },
                    signal: signal
                });

                if (!response.ok) {
                    throw new Error(`HTTP error! status: ${response.status}`);
                }

                const reader = response.body.getReader();
                const decoder = new TextDecoder('utf-8');
                let buffer = '';
                let thinkingContent = '';

                while (true) {
                    const { done, value } = await reader.read();
                    if (done) break;

                    const chunk = decoder.decode(value, { stream: true });
                    buffer += chunk;

                    let lineEndIndex;
                    while ((lineEndIndex = buffer.indexOf('\n')) !== -1) {
                        const line = buffer.substring(0, lineEndIndex);
                        buffer = buffer.substring(lineEndIndex + 1);

                        if (line.startsWith('data: ')) {
                            let dataStr = line.slice(6);

                            if (dataStr.trim() === STREAM_TYPES.DONE) {
                                isSending.value = false;
                                abortController = null;
                                currentStreamContentDiv = null;
                                currentThinkingContentDiv = null;
                                currentThinkingSectionDiv = null;
                                currentThinkingContentWrapper = null;
                                return;
                            }

                            if (dataStr.trim() === '') continue;

                            try {
                                if (dataStr.includes(STREAM_TYPES.DONE)) {
                                    const parts = dataStr.split(STREAM_TYPES.DONE);
                                    dataStr = parts[0];
                                }

                                const data = JSON.parse(dataStr);
                                processStreamData(data, aiMsg, thinkingContent);
                            } catch (e) {
                                console.warn('解析数据失败:', dataStr, e);
                            }
                        } else if (line.trim() !== '') {
                            let cleanLine = line.replace(/^data:\s*/, '').trim();

                            if (cleanLine === STREAM_TYPES.DONE) {
                                isSending.value = false;
                                abortController = null;
                                currentStreamContentDiv = null;
                                currentThinkingContentDiv = null;
                                currentThinkingSectionDiv = null;
                                currentThinkingContentWrapper = null;
                                return;
                            }

                            if (cleanLine && cleanLine.startsWith('{') && cleanLine.endsWith('}')) {
                                try {
                                    const data = JSON.parse(cleanLine);
                                    processStreamData(data, aiMsg, thinkingContent);
                                } catch (e) {
                                    console.warn('解析JSON行失败:', cleanLine, e);
                                }
                            }
                        }
                    }
                }

                if (buffer.trim() !== '') {
                    let cleanBuffer = buffer.replace(/^data:\s*/, '').trim();

                    if (cleanBuffer === STREAM_TYPES.DONE) {
                        isSending.value = false;
                        currentStreamContentDiv = null;
                        currentThinkingContentDiv = null;
                        currentThinkingSectionDiv = null;
                        currentThinkingContentWrapper = null;
                        return;
                    }

                    if (cleanBuffer && cleanBuffer.startsWith('{') && cleanBuffer.endsWith('}')) {
                        try {
                            const data = JSON.parse(cleanBuffer);
                            if (data.type === STREAM_TYPES.TEXT && data.content) {
                                aiMsg.content += data.content;
                                updateStreamContent(aiMsg.content);
                            } else if (data.type === STREAM_TYPES.COMPLETE) {
                                if (aiMsg.hasThinking) {
                                    aiMsg.showThinking = false;
                                }
                                isSending.value = false;
                            }
                        } catch (e) {
                            console.warn('解析剩余数据失败:', cleanBuffer, e);
                        }
                    }
                }

                isSending.value = false;
                currentStreamContentDiv = null;
                currentThinkingContentDiv = null;
                currentThinkingSectionDiv = null;
                currentThinkingContentWrapper = null;

            } catch (error) {
                console.error('请求错误:', error);
                if (error.name !== 'AbortError') {
                    aiMsg.content += '\n\n⚠️ 请求出错: ' + error.message;
                    updateStreamContent(aiMsg.content);
                }
                isSending.value = false;
                abortController = null;
                currentStreamContentDiv = null;
                currentThinkingContentDiv = null;
                currentThinkingSectionDiv = null;
                currentThinkingContentWrapper = null;
            }
        };

        const processStreamData = (data, aiMsg, thinkingContent) => {
            if (data.type === STREAM_TYPES.TEXT && data.content) {
                aiMsg.content += data.content;
                updateStreamContent(aiMsg.content);
            } else if (data.type === STREAM_TYPES.THINKING && data.content) {
                aiMsg.hasThinking = true;
                aiMsg.thinking.push(data.content);
                // 追加到时间线：如果最后一条是 thinking，合并内容；否则新建
                const last = aiMsg.timeline[aiMsg.timeline.length - 1];
                if (last && last.type === 'thinking') {
                    last.content += data.content;
                } else {
                    aiMsg.timeline.push({ type: 'thinking', content: data.content });
                }
                if (messagesContainer.value) {
                    messagesContainer.value.scrollTop = messagesContainer.value.scrollHeight;
                }
            } else if (data.type === STREAM_TYPES.TOOL_START) {
                aiMsg.timeline.push({
                    type: 'tool',
                    toolName: data.toolName || 'unknown',
                    toolCallId: data.toolCallId || '',
                    status: 'running'
                });
                if (messagesContainer.value) {
                    messagesContainer.value.scrollTop = messagesContainer.value.scrollHeight;
                }
            } else if (data.type === STREAM_TYPES.TOOL_END) {
                const toolCallId = data.toolCallId || '';
                const entry = aiMsg.timeline.find(t => t.type === 'tool' && t.toolCallId === toolCallId && t.status === 'running');
                if (entry) {
                    entry.status = 'completed';
                } else {
                    aiMsg.timeline.push({
                        type: 'tool',
                        toolName: data.toolName || 'unknown',
                        toolCallId,
                        status: 'completed'
                    });
                }
            } else if (data.type === STREAM_TYPES.REFERENCE && data.content) {
                try {
                    let refsData = data.content;
                    if (typeof refsData === 'string') {
                        refsData = JSON.parse(refsData);
                    }
                    if (refsData.data && refsData.data.content) {
                        refsData = refsData.data.content;
                    }
                    if (Array.isArray(refsData)) {
                        aiMsg.reference = processReferences(refsData);
                        if (aiMsg.reference.length > 0) {
                            aiMsg.showReference = true;
                        }
                    }
                } catch (e) {
                    console.warn('解析reference失败:', e, '原始数据:', data.content);
                }
            } else if (data.type === STREAM_TYPES.RECOMMEND && data.content) {
                try {
                    let recommendData = data.content;
                    if (typeof recommendData === 'string') {
                        recommendData = JSON.parse(recommendData);
                    }
                    if (Array.isArray(recommendData)) {
                        aiMsg.recommend = processRecommendations(recommendData);
                    }
                } catch (e) {
                    console.warn('解析recommend失败:', e, '原始数据:', data.content);
                }
            } else if (data.type === STREAM_TYPES.ERROR) {
                aiMsg.timeline.push({
                    type: 'error',
                    message: data.message || '未知错误',
                    detail: data.detail || '',
                    code: data.code || ''
                });
                if (messagesContainer.value) {
                    messagesContainer.value.scrollTop = messagesContainer.value.scrollHeight;
                }
            } else if (data.type === STREAM_TYPES.COMPLETE) {
                const hasError = aiMsg.timeline.some(t => t.type === 'error');
                aiMsg.showTimeline = hasError;
                isSending.value = false;
                currentRecommendMsgId.value = aiMsg.id;
                currentStreamContentDiv = null;
                currentThinkingContentDiv = null;
                currentThinkingSectionDiv = null;
                currentThinkingContentWrapper = null;
            }
        };

        const stopMessage = async () => {
            if (!isSending.value) return;

            // if (abortController) {
            //     abortController.abort();
            //     abortController = null;
            // }

            await APP_API.stopStream(backendUrl.value, currentChatId.value);

            isSending.value = false;
            currentStreamContentDiv = null;
            currentThinkingContentDiv = null;
            currentThinkingSectionDiv = null;
            currentThinkingContentWrapper = null;
        };

        // ===== UI 交互 =====
        const selectAgent = (agentId) => {
            if (selectedFile.value) {
                selectedFile.value = null;
                uploadedFileId.value = null;
            }
            selectedAgent.value = agentId;
        };

        const quickPrompt = (prompt) => {
            inputMessage.value = prompt;
            nextTick(() => {
                textareaInput.value?.focus();
            });
        };

        const clearAllRecommendQuestions = () => {
            if (currentChat.value && currentChat.value.messages) {
                currentChat.value.messages.forEach(msg => {
                    msg.recommend = [];
                });
            }
        };

        const sendRecommendQuestion = (question) => {
            if (isSending.value || isUploading.value) return;
            sendMessage(question);
        };

        const createNewChat = async () => {
            const existingNewChat = chatList.value.find(c => c.isNew);
            if (existingNewChat) {
                currentChatId.value = existingNewChat.id;
                return;
            }

            try {
                const response = await fetch(`${backendUrl.value}/session`, { method: 'POST' });
                const result = await response.json();
                const cid = result.data.conversationId;
                const newChat = { id: cid, title: '新对话', messages: [], isNew: true };
                chatList.value.unshift(newChat);
                currentChatId.value = newChat.id;
            } catch (e) {
                const newChat = { id: generateId(), title: '新对话', messages: [], isNew: true };
                chatList.value.unshift(newChat);
                currentChatId.value = newChat.id;
            }
        };

        const toggleThinking = (msgId) => {
            // 兼容旧调用，实际操作 timeline 的展开/折叠
            const chat = currentChat.value;
            if (!chat) return;
            const msg = chat.messages.find(m => m.id === msgId);
            if (msg) {
                msg.showTimeline = !msg.showTimeline;
            }
        };

        const toggleTimeline = (msgId) => {
            const chat = currentChat.value;
            if (!chat) return;
            const msg = chat.messages.find(m => m.id === msgId);
            if (msg) {
                msg.showTimeline = !msg.showTimeline;
            }
        };

        const toggleReference = (msgId) => {
            const chat = currentChat.value;
            if (!chat) return;
            const msg = chat.messages.find(m => m.id === msgId);
            if (msg) {
                msg.showReference = !msg.showReference;
            }
        };

        const currentChat = computed(() => {
            return chatList.value.find(c => c.id === currentChatId.value);
        });

        const isLastMessage = (msg) => {
            const chat = currentChat.value;
            if (!chat || chat.messages.length === 0) return false;
            const lastMsg = chat.messages[chat.messages.length - 1];
            return lastMsg.id === msg.id;
        };

        const copyMessage = async (msg) => {
            const textToCopy = msg.content || '';
            if (!textToCopy) return;

            try {
                if (navigator.clipboard && window.isSecureContext) {
                    await navigator.clipboard.writeText(textToCopy);
                } else {
                    const textarea = document.createElement('textarea');
                    textarea.value = textToCopy;
                    textarea.style.position = 'fixed';
                    textarea.style.opacity = '0';
                    document.body.appendChild(textarea);
                    textarea.select();
                    document.execCommand('copy');
                    document.body.removeChild(textarea);
                }
                msg.copied = true;
                setTimeout(() => { msg.copied = false; }, 2000);
            } catch (error) {
                console.error('复制失败:', error);
            }
        };

        const canSend = computed(() => {
            if (isSending.value) return false;
            if (isUploading.value) return false;
            return inputMessage.value.trim().length > 0 || selectedFile.value;
        });

        const scrollToBottom = () => {
            return nextTick(() => {
                if (messagesContainer.value) {
                    messagesContainer.value.scrollTop = messagesContainer.value.scrollHeight;
                }
            });
        };

        const confirmOk = () => {
            if (confirmCallback) {
                confirmCallback();
            }
        };

        const confirmCancel = () => {
            showConfirmDialog.value = false;
            confirmCallback = null;
        };

        watch(inputMessage, () => {
            nextTick(() => {
                if (textareaInput.value) {
                    textareaInput.value.style.height = 'auto';
                    textareaInput.value.style.height = textareaInput.value.scrollHeight + 'px';
                }
            });
        });

        return {
            backendUrl,
            connectionError,
            agents,
            selectedAgent,
            chatList,
            currentChatId,
            currentChat,
            inputMessage,
            selectedFile,
            isUploading,
            isDragOver,
            isSending,
            currentRecommendMsgId,
            canSend,
            messagesContainer,
            textareaInput,
            selectAgent,
            quickPrompt,
            sendRecommendQuestion,
            isLastMessage,
            createNewChat,
            selectChat,
            deleteChat,
            removeFile,
            handleFileSelect,
            sendMessage,
            stopMessage,
            toggleThinking,
            toggleTimeline,
            toggleReference,
            copyMessage,
            renderMarkdown,
            formatFileSize,
            testConnection,
            showConfirmDialog,
            confirmTitle,
            confirmMessage,
            confirmOk,
            confirmCancel
        };
    }
}).mount('#app');
