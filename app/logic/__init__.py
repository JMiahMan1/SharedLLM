# app/logic/__init__.py

from .pipeline import generate_rag_stream, try_handle_compound_command
from .utils import (
    contextualize_query,
    call_ollama_generate,
    call_openai_chat,
    get_ha_context,
    get_rag_context,
    update_history
)
from .web_search import tool_web_search
from .calendar_ops import (
    tool_calendar_list,
    tool_calendar_add,
    tool_calendar_delete,
    tool_calendar_update,
    tool_calendar_read
)
from .media_ops import handle_media_command
from .timer_ops import (
    tool_timer_add,
    tool_timer_list,
    tool_timer_delete,
    tool_timer_pause,
    tool_timer_resume
)
from .timer_storage import storage as timer_storage
# from .timer_scheduler import start_scheduler, stop_scheduler # Commented out as it wasn't in file list, or maybe I missed it.
# Wait, the read content showed: from .timer_scheduler import start_scheduler, stop_scheduler
# I need to check if timer_scheduler.py exists. The user didn't mention it but the import is there.
# I will include the import as seen in the remote file.
from .timer_scheduler import start_scheduler, stop_scheduler
