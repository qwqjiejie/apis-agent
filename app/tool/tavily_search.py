from langchain.tools import tool
from tavily import TavilyClient
from app.config.settings import get_settings
from app.tool.registry import register_tool


@register_tool
@tool
def tavily_search(query: str) -> str:
    """通过 Tavily 搜索引擎搜索互联网信息，返回 JSON 格式的搜索结果，包含 title、url、content 字段。"""
    client = TavilyClient(api_key=get_settings().tavily_api_key)
    response = client.search(query, search_depth="basic", max_results=5)
    results = []
    for r in response.get("results", []):
        results.append({
            "title": r.get("title", ""),
            "url": r.get("url", ""),
            "content": r.get("content", ""),
        })
    sources = [{"title": r["title"], "url": r["url"]} for r in results]
    return f"SEARCH_RESULTS: {response.get('answer', '')}\n\nSOURCES: {sources}\n\nDETAILS: {results}"
