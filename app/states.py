"""FSM-состояния анкеты и вспомогательные проверки состояний."""

from aiogram.fsm.state import State
from aiogram.fsm.state import StatesGroup

class ApplicationForm(StatesGroup):
    filling = State()
    confirming = State()


class AdminCustomization(StatesGroup):
    waiting_emoji = State()
    waiting_text = State()


def _state_matches(current_state: str | None, target: State) -> bool:
    return current_state == target or current_state == target.state


def _is_application_state(current_state: str | None) -> bool:
    return (
        _state_matches(current_state, ApplicationForm.filling)
        or _state_matches(current_state, ApplicationForm.confirming)
    )
