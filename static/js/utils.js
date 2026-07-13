/**
 * 工具函数文件
 * 存放通用工具函数
 */

// 生成唯一ID
const generateId = () => {
    return 'chat_' + Date.now() + '_' + Math.random().toString(36).substr(2, 9);
};

// 格式化文件大小
const formatFileSize = (bytes) => {
    if (bytes < 1024) return bytes + ' B';
    if (bytes < 1024 * 1024) return (bytes / 1024).toFixed(1) + ' KB';
    return (bytes / (1024 * 1024)).toFixed(1) + ' MB';
};

// 配置 Markdown
const setupMarkdown = () => {
    if (typeof marked !== 'undefined' && typeof hljs !== 'undefined') {
        marked.setOptions({
            highlight: function(code, lang) {
                if (lang && hljs.getLanguage(lang)) {
                    try {
                        return hljs.highlight(code, { language: lang }).value;
                    } catch (err) {}
                }
                return hljs.highlightAuto(code).value;
            },
            breaks: true,
            gfm: true,
            sanitize: false
        });
    }
};

// 渲染 Markdown
const renderMarkdown = (content) => {
    if (!content) return '';
    if (typeof marked === 'undefined') return content;

    // 处理各种换行符
    let processedContent = content
        .replace(/\\n/g, '\n')
        .replace(/\\r\\n/g, '\n')
        .replace(/\\r/g, '\n');

    const html = marked.parse(processedContent);

    // 如果 DOMPurify 可用，则进行 XSS 防护
    if (typeof DOMPurify !== 'undefined') {
        return DOMPurify.sanitize(html);
    }
    return html;
};

// 处理参考来源数据（统一处理多种格式）
const processReferences = (refsData) => {
    if (!refsData) return [];

    let references = refsData;

    // 如果是字符串，尝试解析
    if (typeof references === 'string') {
        try {
            references = JSON.parse(references);
        } catch (e) {
            return [];
        }
    }

    // 检查嵌套格式 {data: {content: "..."}}
    if (references && references.data && references.data.content) {
        const contentData = references.data.content;
        if (typeof contentData === 'string') {
            try {
                references = JSON.parse(contentData);
            } catch (e) {
                return [];
            }
        } else {
            references = contentData;
        }
    }

    // 检查后端返回的格式 {type: 'reference', content: "[...]"}
    if (references && references.type === 'reference' && typeof references.content === 'string') {
        try {
            references = JSON.parse(references.content);
        } catch (e) {
            return [];
        }
    }

    // 确保是数组
    if (!Array.isArray(references)) {
        return [];
    }

    // 转换为统一格式
    return references
        .filter(ref => ref != null)
        .map(ref => {
            let linkUrl, displayTitle, content;

            if (typeof ref === 'string') {
                try {
                    const parsed = JSON.parse(ref);
                    linkUrl = parsed.url || parsed.link;
                    displayTitle = parsed.title || parsed.url || parsed.link || '无标题';
                    content = parsed.content || '';
                } catch {
                    linkUrl = ref;
                    displayTitle = ref;
                    content = '';
                }
            } else if (typeof ref === 'object' && ref !== null) {
                linkUrl = ref.url || ref.link;
                displayTitle = ref.title || ref.url || ref.link || '无标题';
                content = ref.content || '';
            }

            // 确保 URL 是绝对路径
            if (linkUrl && !linkUrl.startsWith('http://') && !linkUrl.startsWith('https://')) {
                linkUrl = 'https://' + linkUrl;
            }

            return { url: linkUrl, title: displayTitle, content };
        })
        .filter(ref => ref.url);
};

// 处理推荐问题数据
const processRecommendations = (recommendData) => {
    if (!recommendData) return [];

    let recommendations = recommendData;

    // 如果是字符串，尝试解析
    if (typeof recommendations === 'string') {
        try {
            recommendations = JSON.parse(recommendations);
        } catch (e) {
            return [];
        }
    }

    // 确保是数组
    if (!Array.isArray(recommendations)) {
        return [];
    }

    return recommendations;
};

// 导出工具函数（用于非模块化环境）
window.APP_UTILS = {
    generateId,
    formatFileSize,
    setupMarkdown,
    renderMarkdown,
    processReferences,
    processRecommendations
};
