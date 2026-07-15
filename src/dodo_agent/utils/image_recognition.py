import base64
import logging
import os

from openai import OpenAI

from src.dodo_agent.config.settings import get_settings

logger = logging.getLogger("dodo")

IMAGE_TYPES = {"png", "jpg", "jpeg", "gif", "bmp", "webp"}

RECOGNITION_PROMPT = "请详细描述这张图片中的内容，包括其中出现的文字、物体、场景、人物、图表数据等所有信息。尽可能详尽。"


def recognize_image(file_path: str) -> str | None:
    if not get_settings().vision_model:
        logger.debug("未配置 vision_model，跳过图片识别")
        return None

    ext = os.path.splitext(file_path)[1].lower().lstrip(".")
    if ext not in IMAGE_TYPES:
        return None

    try:
        with open(file_path, "rb") as f:
            image_data = base64.b64encode(f.read()).decode("utf-8")

        mime = _mime_type(ext)
        data_uri = f"data:{mime};base64,{image_data}"

        client = OpenAI(
            api_key=get_settings().llm_api_key,
            base_url=get_settings().llm_base_url,
        )

        response = client.chat.completions.create(
            model=get_settings().vision_model,
            messages=[
                {
                    "role": "user",
                    "content": [
                        {"type": "image_url", "image_url": {"url": data_uri}},
                        {"type": "text", "text": RECOGNITION_PROMPT},
                    ],
                }
            ],
            max_tokens=2000,
        )

        result = response.choices[0].message.content
        if result:
            logger.info(f"图片识别成功: {os.path.basename(file_path)} -> {len(result)} chars")
        return result

    except Exception as e:
        logger.error(f"图片识别失败 {file_path}: {e}")
        return None


def _mime_type(ext: str) -> str:
    mapping = {
        "png": "image/png",
        "jpg": "image/jpeg",
        "jpeg": "image/jpeg",
        "gif": "image/gif",
        "bmp": "image/bmp",
        "webp": "image/webp",
    }
    return mapping.get(ext, "image/jpeg")
