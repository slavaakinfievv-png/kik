"""Статические данные анкеты, лимиты и справочники."""

QUESTIONS = [
    {
        "key": "name",
        "title": "Как к тебе обращаться?",
        "icon": "👤",
        "hint": "Напиши своё имя или удобное обращение.",
        "type": "text",
    },
    {
        "key": "roblox",
        "title": "Никнейм в Roblox",
        "icon": "🎮",
        "hint": "Укажи точный Roblox-ник.",
        "type": "text",
    },
    {
        "key": "level",
        "title": "Уровень в Evade",
        "icon": "📊",
        "hint": "Выбери готовый вариант или введи свой уровень.",
        "type": "level",
        "options": ["10", "25", "50", "100", "150"],
    },
    {
        "key": "experience",
        "title": "Как давно играешь в Evade?",
        "icon": "⏱",
        "hint": "Выбери вариант.",
        "type": "choice",
        "options": [
            "Меньше месяца",
            "1–6 месяцев",
            "6–12 месяцев",
            "1–2 года",
            "Более 2 лет",
        ],
    },
    {
        "key": "timezone",
        "title": "Твой часовой пояс",
        "icon": "🕐",
        "hint": "Выбери вариант.",
        "type": "choice",
        "options": ["UTC+0", "UTC+1", "UTC+2", "UTC+3", "UTC+4", "UTC+5", "UTC+6"],
    },
    {
        "key": "activity",
        "title": "Как часто ты играешь?",
        "icon": "📅",
        "hint": "Выбери вариант.",
        "type": "choice",
        "options": [
            "Каждый день",
            "3–5 раз в неделю",
            "1–2 раза в неделю",
            "Реже раза в неделю",
        ],
    },
    {
        "key": "skill",
        "title": "Оцени свой уровень игры от 1 до 10",
        "icon": "🎯",
        "hint": "Выбери оценку.",
        "type": "rating",
        "options": [str(i) for i in range(1, 11)],
    },
    {
        "key": "teamwork",
        "title": "Насколько хорошо ты играешь в команде?",
        "icon": "🤝",
        "hint": "Выбери оценку от 1 до 10.",
        "type": "rating",
        "options": [str(i) for i in range(1, 11)],
    },
    {
        "key": "clans",
        "title": "Состоял ли раньше в других кланах?",
        "icon": "🏆",
        "hint": "Выбери ответ.",
        "type": "yes_no",
    },
    {
        "key": "reason",
        "title": "Почему хочешь вступить именно в наш клан?",
        "icon": "💬",
        "hint": "Выбери готовый вариант или напиши свой.",
        "type": "choice",
        "options": [
            "Хочу играть с командой",
            "Хочу развиваться в Evade",
            "Хочу участвовать в клановых играх",
        ],
    },
    {
        "key": "rules",
        "title": "Готов ли соблюдать правила клана?",
        "icon": "📜",
        "hint": "Выбери ответ.",
        "type": "yes_no",
    },
]


ANSWER_MAX_LENGTH = {
    "name": 80,
    "roblox": 50,
    "level": 10,
    "experience": 150,
    "timezone": 50,
    "activity": 150,
    "skill": 2,
    "teamwork": 2,
    "clans": 100,
    "reason": 700,
    "rules": 100,
}


DEFAULT_TEXT_STYLE = "standard"


TEXT_STYLES = {
    "standard": "Обычный",
    "bold": "Жирный",
    "italic": "Курсив",
    "underline": "Подчёркнутый",
    "mono": "Моноширинный",
    "spoiler": "Спойлер",
}


REJECTION_REASONS = {
    "low_level": "Недостаточный уровень",
    "low_activity": "Недостаточная активность",
    "not_suitable": "Не подходит по требованиям",
    "rules": "Проблемы с соблюдением правил",
    "other": "Другая причина / решение администрации",
}
