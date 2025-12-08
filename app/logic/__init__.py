# app/logic/__init__.py

from .pipeline import generate_rag_stream, try_handle_compound_command, contextualize_query
from .utils import (
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
from .note_ops import (
    tool_note_add,
    tool_note_append,
    tool_note_read,
    tool_note_delete,
)
from .timer_storage import storage as timer_storage
from .timer_scheduler import start_scheduler, stop_scheduler
