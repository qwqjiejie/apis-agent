"""OnlineEval — 在线评估结果采集与 A/B 对比。

采集真实用户交互数据（评分、反馈），按 agent 配置分组统计，
支持不同 prompt/模型/工具的 A/B 对比评估。

评估指标：
- 用户满意度（点赞率）
- 首响应时间
- 工具调用次数
- 回答完成率
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger("apis")


@dataclass
class EvalRecord:
    """单次交互的评估记录。"""
    session_id: str
    agent_version: str = "default"      # Agent 配置版本标识（A/B 分组）
    rating: int = 0                      # 用户评分：1=点赞, -1=点踩, 0=未评
    comment: str = ""
    first_response_ms: int = 0           # 首响应时间
    total_response_ms: int = 0           # 总响应时间
    tool_count: int = 0                  # 工具调用次数
    query_length: int = 0                # 问题长度
    answer_length: int = 0               # 回答长度
    completed: bool = True               # 是否正常完成
    model_name: str = ""                 # 使用的模型名
    timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


class OnlineEvalCollector:
    """在线评估采集器。

    使用方式:
        collector = OnlineEvalCollector()
        collector.record(record)
        summary = collector.summary(agent_version="v2")
    """

    def __init__(self, max_records: int = 10000):
        self._records: list[EvalRecord] = []
        self._max = max_records

    def record(self, record: EvalRecord):
        self._records.append(record)
        if len(self._records) > self._max:
            self._records = self._records[-self._max:]

    def summary(self, agent_version: str = "") -> dict[str, Any]:
        """按 agent_version 统计汇总指标。"""
        records = self._records
        if agent_version:
            records = [r for r in records if r.agent_version == agent_version]

        if not records:
            return {"count": 0}

        rated = [r for r in records if r.rating != 0]
        thumbs_up = [r for r in rated if r.rating == 1]
        completed_list = [r for r in records if r.completed]

        return {
            "count": len(records),
            "rated_count": len(rated),
            "thumbs_up_rate": round(len(thumbs_up) / len(rated), 3) if rated else 0,
            "completion_rate": round(len(completed_list) / len(records), 3) if records else 0,
            "avg_first_response_ms": round(
                sum(r.first_response_ms for r in records) / len(records)
            ) if records else 0,
            "avg_tool_count": round(
                sum(r.tool_count for r in records) / len(records), 1
            ) if records else 0,
            "avg_query_length": round(
                sum(r.query_length for r in records) / len(records)
            ) if records else 0,
        }

    def compare(self, version_a: str, version_b: str) -> dict[str, Any]:
        """对比两个 Agent 版本的指标。"""
        return {
            version_a: self.summary(version_a),
            version_b: self.summary(version_b),
        }

    def get_records(self, limit: int = 100) -> list[dict]:
        """获取最近的评估记录。"""
        return [
            {
                "sessionId": r.session_id,
                "agentVersion": r.agent_version,
                "rating": r.rating,
                "comment": r.comment,
                "firstResponseMs": r.first_response_ms,
                "toolCount": r.tool_count,
                "completed": r.completed,
                "modelName": r.model_name,
                "timestamp": r.timestamp,
            }
            for r in self._records[-limit:]
        ]


online_eval = OnlineEvalCollector()
