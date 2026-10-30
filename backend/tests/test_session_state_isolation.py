"""Item 6: session ids are uuid4 and checkpoint state is never shared.

``str(id(self))`` could be reused after garbage collection, letting a later
call inherit an earlier caller's conversation via ``_CHECKPOINTS``. Session ids
are now uuid4 and ``release_thread`` frees the store on cleanup.
"""

from __future__ import annotations

import gc
import uuid
from unittest.mock import MagicMock

from modules.realtime.realtime_voice_session import RealtimeVoiceSession
from modules.workflow.conversation_workflow_graph import (
    ConversationWorkflow,
    _CHECKPOINTS,
    release_thread,
)
from tests.fake_llm import FakeLLM


def test_two_sessions_have_distinct_uuid_ids() -> None:
    first = RealtimeVoiceSession(MagicMock(), MagicMock())
    first_id = first.session_id
    del first
    gc.collect()
    second = RealtimeVoiceSession(MagicMock(), MagicMock())
    assert second.session_id != first_id
    uuid.UUID(second.session_id)


def test_release_thread_frees_checkpoint_store() -> None:
    workflow = ConversationWorkflow(llm=FakeLLM(reply="hi"))
    thread_id = workflow.thread_id
    assert thread_id in _CHECKPOINTS

    release_thread(thread_id)
    assert thread_id not in _CHECKPOINTS


def test_released_thread_does_not_inherit_state() -> None:
    first = ConversationWorkflow(llm=FakeLLM(reply="first"))
    first_id = first.thread_id
    release_thread(first_id)

    # A later workflow reusing the same id gets a fresh checkpoint, not the
    # earlier caller's conversation.
    second = ConversationWorkflow(llm=FakeLLM(reply="second"), thread_id=first_id)
    assert second._saver is not first._saver
    release_thread(first_id)
