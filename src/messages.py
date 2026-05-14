DEFAULT_LOCALE = "ru"
SUPPORTED_LOCALES = ("ru", "en")

MESSAGES = {
    "ru": {
        "choose_language": "Выбери язык / Choose your language",
        "language_set": "Язык сохранён: русский.",
        "start_text": (
            "Отправь email в формате `example@outlook.com`, и я попробую "
            "получить код с этой почты."
        ),
        "help_text": (
            "Что умеет бот:\n"
            "- `/start` — выбрать язык\n"
            "- обычное сообщение `example@outlook.com` — запросить код"
        ),
        "admin_only": "",
        "add_usage": "Использование: `/add <ПОЧТА:ПАРОЛЬ:ПОЧТА:ПАРОЛЬ:ТОКЕН:ID>`",
        "add_invalid": (
            "Не удалось разобрать строку. Нужен формат "
            "`ПОЧТА:ПАРОЛЬ:ПОЧТА:ПАРОЛЬ:ТОКЕН:ID`."
        ),
        "add_success": "Почта `{email}` добавлена в `email.json`.",
        "add_updated": "Почта `{email}` уже существовала, запись обновлена.",
        "email_invalid": "Нужен email в формате `example@outlook.com`.",
        "email_missing": "Почта не найдена.",
        "email_taken": "Эта почта уже закреплена за другим пользователем.",
        "email_waiting": "Почта `{email}` принята. Жду письмо с кодом.",
        "code_found": "Код для `{email}`: `{code}`",
        "code_timeout": "Не дождался нового кода для `{email}` за отведённое время.",
        "code_failed": "Не удалось получить код для `{email}`.",
        "unknown_text": (
            "Я жду email в формате `example@outlook.com` или команду `/start`."
        ),
    },
    "en": {
        "choose_language": "Choose your language / Выбери язык",
        "language_set": "Language saved: English.",
        "start_text": (
            "Send an email in the `example@outlook.com` format and I will try "
            "to fetch the code for it."
        ),
        "help_text": (
            "What the bot can do:\n"
            "- `/start` — choose language\n"
            "- plain `example@outlook.com` message — request a code"
        ),
        "admin_only": "This command is available to administrators only.",
        "add_usage": "Usage: `/add <EMAIL:PASS:EMAIL:PASS:TOKEN:ID>`",
        "add_invalid": (
            "I could not parse that string. Expected `EMAIL:PASS:EMAIL:PASS:TOKEN:ID`."
        ),
        "add_success": "Mailbox `{email}` has been added to `email.json`.",
        "add_updated": "Mailbox `{email}` already existed, the record was updated.",
        "email_invalid": "Please send an email in the `example@outlook.com` format.",
        "email_missing": "Mailbox not found.",
        "email_taken": "This email is already assigned to another user.",
        "email_waiting": "Mailbox `{email}` accepted. Waiting for the code email.",
        "code_found": "Code for `{email}`: `{code}`",
        "code_timeout": "Timed out waiting for a new code for `{email}`.",
        "code_failed": "I could not fetch a code for `{email}`.",
        "unknown_text": (
            "Send an email in the `example@outlook.com` format or use `/start`."
        ),
    },
}


def translate(locale: str, key: str, **kwargs: str) -> str:
    bundle = MESSAGES.get(locale, MESSAGES[DEFAULT_LOCALE])
    template = bundle.get(key) or MESSAGES[DEFAULT_LOCALE][key]
    return template.format(**kwargs)
