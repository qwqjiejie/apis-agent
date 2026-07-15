"""统一异常类层次结构。

将所有异常归为三类：
- DodoAgentError（基类）：所有业务异常的根
- InfrastructureError：基础设施不可用（Redis/MySQL/Milvus/MinIO），可降级恢复
- ValidationError：用户输入不合法（文件过大、类型不支持等），需提示用户
- AgentExecutionError：Agent 运行时异常，需记录日志并通知调用方
"""


class DodoAgentError(Exception):
    """所有业务异常的基类。"""

    def __init__(self, message: str, code: int = 500):
        super().__init__(message)
        self.message = message
        self.code = code


# =============================================================================
# 基础设施异常 — 外部服务不可用，可降级恢复
# =============================================================================

class InfrastructureError(DodoAgentError):
    """基础设施不可用。调用方可捕获此类异常做降级处理。"""

    def __init__(self, message: str, service: str = ""):
        super().__init__(message, code=503)
        self.service = service


class RedisError(InfrastructureError):
    def __init__(self, message: str):
        super().__init__(message, service="Redis")


class DatabaseError(InfrastructureError):
    def __init__(self, message: str):
        super().__init__(message, service="MySQL")


class MilvusError(InfrastructureError):
    def __init__(self, message: str):
        super().__init__(message, service="Milvus")


class MinIOError(InfrastructureError):
    def __init__(self, message: str):
        super().__init__(message, service="MinIO")


# =============================================================================
# 输入验证异常 — 用户输入不合法，需提示用户修正
# =============================================================================

class ValidationError(DodoAgentError):
    """用户输入不合法。message 会直接展示给用户。"""

    def __init__(self, message: str):
        super().__init__(message, code=400)


class FileTooLargeError(ValidationError):
    def __init__(self, max_mb: int):
        super().__init__(f"文件不能超过{max_mb}MB大小")


class QueryTooLongError(ValidationError):
    def __init__(self, max_length: int):
        super().__init__(f"输入内容不能超过{max_length}字符")


class UnsupportedFileTypeError(ValidationError):
    def __init__(self, file_type: str):
        super().__init__(f"不支持的文件类型: {file_type}")


class InvalidMimeTypeError(ValidationError):
    def __init__(self, expected: str, actual: str = ""):
        msg = f"文件 MIME 类型不匹配，期望: {expected}"
        if actual:
            msg += f"，实际: {actual}"
        super().__init__(msg)


class AgentBusyError(ValidationError):
    def __init__(self):
        super().__init__("当前会话有任务正在执行中，请稍后再试")


# =============================================================================
# Agent 执行异常 — 运行时错误，需记录日志
# =============================================================================

class AgentExecutionError(DodoAgentError):
    """Agent 运行时异常。"""

    def __init__(self, message: str):
        super().__init__(message, code=500)
