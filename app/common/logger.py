import logging

from app.common.trace_context import trace_id_var, session_id_var

_original_factory = logging.getLogRecordFactory()


def _record_factory(*args, **kwargs):
    record = _original_factory(*args, **kwargs)
    record.trace_id = trace_id_var.get() or "-"
    record.session_id = session_id_var.get() or "-"
    return record


logging.setLogRecordFactory(_record_factory)

logger = logging.getLogger("apis")
