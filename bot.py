"""Compatibility entry point for the modular bot application.

New code should import from ``app`` modules directly. Existing deployment can keep
running ``python bot.py``.
"""

import asyncio

from app.config import *  # noqa: F401,F403
from app.database import *  # noqa: F401,F403
from app.diagnostics import *  # noqa: F401,F403
from app.questions import *  # noqa: F401,F403
from app.services import *  # noqa: F401,F403
from app.states import *  # noqa: F401,F403
from app.ui import *  # noqa: F401,F403
from app.runtime import router

from app.database import _application_answers, _next_application_id, _remove_staff_messages
from app.diagnostics import _build_error_payload, _error_update_context
from app.states import _is_application_state, _state_matches
from app.ui import _form_callback, _validate_form_session

from app.handlers.admin import *  # noqa: F401,F403
from app.handlers.customization import *  # noqa: F401,F403
from app.handlers.decisions import *  # noqa: F401,F403
from app.handlers.form import *  # noqa: F401,F403
from app.handlers.user import *  # noqa: F401,F403

from app.main import main

if __name__ == "__main__":
    asyncio.run(main())
