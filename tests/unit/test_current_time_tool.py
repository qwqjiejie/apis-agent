from datetime import datetime, timedelta

from app.tool import TOOL_REGISTRY
from app.tool.current_time import get_current_time


def test_get_current_time_is_registered_and_returns_local_time():
    before = datetime.now().astimezone()

    result = get_current_time.invoke({})

    after = datetime.now().astimezone()
    returned = datetime.fromisoformat(result)

    assert TOOL_REGISTRY["get_current_time"] is get_current_time
    assert returned.utcoffset() is not None
    assert before - timedelta(seconds=1) <= returned <= after
