from langgraph.prebuilt import create_react_agent
from langchain_openai import ChatOpenAI
from src.config.settings import settings
from src.tool.tavily_search import tavily_search

SYSTEM_PROMPT = """你是一个智能问答助手，名字叫小豆豆，可以联网搜索实时信息来回答用户的问题。

回答规范：
- 使用中文回答
- 如果使用了搜索工具，在回答末尾列出参考来源（标题 + URL）
- 回答要清晰、准确、有条理
- 如果搜索结果不足以回答问题，诚实说明"""


def build_react_agent():
    llm = ChatOpenAI(
        model=settings.llm_model,
        api_key=settings.llm_api_key,
        base_url=settings.llm_base_url,
        streaming=True,
    )
    tools = [tavily_search]
    return create_react_agent(llm, tools, prompt=SYSTEM_PROMPT)
