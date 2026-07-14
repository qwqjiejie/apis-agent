import logging

from langchain.agents import create_agent
from langchain_openai import ChatOpenAI
from src.config.settings import settings
from src.tool.tavily_search import tavily_search
from src.tool.bash_tool import bash_tool
from src.tool.file_system_tools import read_file, write_file, edit_file, list_files, glob_files
from src.tool.grep_tool import grep_tool
from src.tool.skills_tool import load_skills

logger = logging.getLogger("dodo")

CHAT_SYSTEM_PROMPT = """你是一个智能问答助手，名字叫小豆豆，可以联网搜索实时信息来回答用户的问题。

回答规范：
- 使用中文回答
- 如果使用了搜索工具，在回答末尾列出参考来源（标题 + URL）
- 回答要清晰、准确、有条理
- 如果搜索结果不足以回答问题，诚实说明"""

SKILLS_SYSTEM_PROMPT = """你是一个通用技能助手，名字叫小豆豆，拥有完整的工具集来处理各种任务。

可用工具类型：
- 搜索工具：联网搜索实时信息
- 文件系统工具：读写编辑文件、列出目录、匹配文件
- 代码搜索工具：正则表达式搜索代码内容
- Shell 工具：执行系统命令
- 技能工具：执行特定领域的技能任务

工作原则：
1. 分析用户意图，选择最合适的工具组合
2. 执行前评估风险：写文件和执行命令操作需确认影响范围
3. 工具执行结果不足以完成任务时，尝试替代方案或诚实说明
4. 输出使用中文，代码和命令保持原样"""


def _build_llm():
    return ChatOpenAI(
        model=settings.llm_model,
        api_key=settings.llm_api_key,
        base_url=settings.llm_base_url,
        streaming=True,
    )


def build_react_agent():
    return create_agent(_build_llm(), [tavily_search], system_prompt=CHAT_SYSTEM_PROMPT)


def build_skills_agent():
    tools = [
        tavily_search,
        read_file,
        write_file,
        edit_file,
        list_files,
        glob_files,
        grep_tool,
        bash_tool,
    ]
    skill_tools = load_skills()
    if skill_tools:
        logger.info(f"已加载 {len(skill_tools)} 个技能")
    tools.extend(skill_tools)
    return create_agent(_build_llm(), tools, system_prompt=SKILLS_SYSTEM_PROMPT)
