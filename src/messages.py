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
            "- обычное сообщение `example@outlook.com` — запросить код\n"
            "- `/refresh` — админская команда для получения refresh_token"
        ),
        "admin_only": "Эта команда доступна только администраторам.",
        "add_usage": "Использование: `/add <ПОЧТА:ПАРОЛЬ:ПОЧТА:ПАРОЛЬ:ТОКЕН:ID>`",
        "add_invalid": (
            "Не удалось разобрать строку. Нужен формат "
            "`ПОЧТА:ПАРОЛЬ:ПОЧТА:ПАРОЛЬ:ТОКЕН:ID`."
        ),
        "add_success": "Почта `{email}` добавлена в `email.json`.",
        "add_updated": "Почта `{email}` уже существовала, запись обновлена.",
        "refresh_prompt": "Пришли client_id следующим сообщением.",
        "refresh_started": "Принял client_id `{client_id}`. Запрашиваю device_code.",
        "refresh_running": (
            "Для тебя уже выполняется запрос refresh_token. Дождись результата."
        ),
        "refresh_device_code_ready": "Device code для client_id `{client_id}` получен.",
        "refresh_waiting_for_confirmation": (
            "Открой ссылку ниже, авторизуйся в Microsoft и потом нажми кнопку "
            "`Я зашел`. Polling я начинаю сразу и пришлю refresh_token, как только "
            "он появится."
        ),
        "refresh_open_login_button": "Открыть вход",
        "refresh_logged_in_button": "Я зашел",
        "refresh_acknowledged": "Принял. Продолжаю ждать refresh_token.",
        "refresh_ack_denied": "Эта кнопка не для тебя.",
        "refresh_success": "Refresh token для client_id `{client_id}`:\n{refresh_token}",
        "refresh_failed": (
            "Не удалось получить refresh_token для client_id `{client_id}`."
        ),
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
            "- plain `example@outlook.com` message — request a code\n"
            "- `/refresh` — admin-only refresh_token flow"
        ),
        "admin_only": "This command is available to administrators only.",
        "add_usage": "Usage: `/add <EMAIL:PASS:EMAIL:PASS:TOKEN:ID>`",
        "add_invalid": (
            "I could not parse that string. Expected `EMAIL:PASS:EMAIL:PASS:TOKEN:ID`."
        ),
        "add_success": "Mailbox `{email}` has been added to `email.json`.",
        "add_updated": "Mailbox `{email}` already existed, the record was updated.",
        "refresh_prompt": "Send the client_id in your next message.",
        "refresh_started": "Accepted client_id `{client_id}`. Requesting device_code now.",
        "refresh_running": (
            "A refresh_token request is already running for you. Please wait for it to finish."
        ),
        "refresh_device_code_ready": "Device code for client_id `{client_id}` has been received.",
        "refresh_waiting_for_confirmation": (
            "Open the link below, sign in to Microsoft, then tap `I logged in`. "
            "Polling starts immediately and I will send the refresh_token as soon "
            "as it appears."
        ),
        "refresh_open_login_button": "Open login",
        "refresh_logged_in_button": "I logged in",
        "refresh_acknowledged": "Accepted. I am still waiting for the refresh_token.",
        "refresh_ack_denied": "This button is not for you.",
        "refresh_success": "Refresh token for client_id `{client_id}`:\n{refresh_token}",
        "refresh_failed": "I could not fetch a refresh_token for client_id `{client_id}`.",
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
