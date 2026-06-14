DEFAULT_LOCALE = "ru"
SUPPORTED_LOCALES = ("ru", "en")

MESSAGES = {
    "ru": {
        "choose_language": "Выбери язык / Choose your language",
        "language_set": "Язык сохранён: русский.",
        "start_text": (
            "➡️ Введите полученный код от продавца:\n"
            "(например: XHASHDAUSHFAFS)"
        ),
        "legacy_start_text": (
            "Отправь email в формате `example@outlook.com`, и я попробую "
            "получить код с этой почты."
        ),
        "help_text": (
            "Что умеет бот:\n"
            "- `/start` — ввести код от продавца или открыть активированную подписку\n"
            "- кнопка `Запросить код` — получить код входа для активированного аккаунта\n"
            "- кнопка `Сменить аккаунт` — отвязать текущий код и ввести новый\n"
            "- `/start en` — переключить язык на английский"
        ),
        "legacy_help_text": (
            "Что умеет бот:\n"
            "- `/start` — показать инструкцию\n"
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
        "addkey_usage": "Использование: `/addkey <количество> <срок_в_днях> <почта>`",
        "addkey_invalid": "Количество и срок должны быть положительными числами.",
        "addkey_email_missing": "Почта `{email}` не найдена в `email.json`.",
        "addkey_success": (
            "Создано `{count}` код(ов) для `{email}` на `{duration_days}` дней.\n"
            "Каждый код многоразовый: без лимита по пользователям и количеству использований до `{end_date}`.\n\n"
            "{codes}"
        ),
        "delkey_usage": "Использование: `/delkey <КОД>`",
        "delkey_missing": "Код `{code}` не найден.",
        "delkey_success": "Код `{code}` удалён.",
        "keylist_empty": "Список кодов пуст.",
        "keylist_header": "Список кодов (все коды многоразовые):\n{rows}",
        "keylist_page_header": (
            "Общее количество: {total_count}\n\n"
            "КЛЮЧ | Почта | Дата начало - Дата конца\n"
            "{rows}"
        ),
        "keylist_prev_button": "‹ Назад",
        "keylist_next_button": "Вперёд ›",
        "keylist_page_button": "{page}/{total_pages}",
        "keylist_access_denied": "Эта кнопка не для вас.",
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
        "key_prompt": (
            "➡️ Введите полученный код от продавца:\n"
            "(например: XHASHDAUSHFAFS)"
        ),
        "key_invalid": (
            "📕 Код - {code} не существует, проверьте его правильность и сообщите продавцу."
        ),
        "key_expired": (
            "📕 У кода \"{code}\" истек срок годность, подписка закончилась {end_date}"
        ),
        "key_email_missing": (
            "📕 Для кода `{code}` не найдена почта в базе. Сообщите продавцу."
        ),
        "subscription_details": (
            "🆔 Ваш ID: {user_id}\n"
            "1️⃣ Почта для входа: {email}\n"
            "2️⃣ Срок подписки: {duration_days} дней\n"
            "3️⃣ Конец подписки: {end_date}\n"
            "4️⃣ Активированный код: {code}\n\n"
            "💬 Нажмите кнопку \"Запросить код\" чтобы получить код для входа в perplexity."
        ),
        "subscription_request_button": "Запросить код",
        "subscription_change_button": "Сменить аккаунт",
        "subscription_request_started": "Запрашиваю код для входа. Как только найду его, сразу пришлю.",
        "subscription_request_running": "Запрос кода уже выполняется, подожди немного.",
        "subscription_change_success": (
            "Текущий аккаунт отвязан.\n\n"
            "➡️ Введите полученный код от продавца:\n"
            "(например: XHASHDAUSHFAFS)"
        ),
        "subscription_inactive": (
            "Сейчас у вас нет активированного кода.\n\n"
            "➡️ Введите полученный код от продавца:\n"
            "(например: XHASHDAUSHFAFS)"
        ),
        "subscription_already_active": (
            "У вас уже активирован аккаунт. Нажмите `Запросить код` или `Сменить аккаунт`."
        ),
        "subscription_access_denied": "Эта кнопка не для тебя.",
        "email_invalid": "Нужен email в формате `example@outlook.com`.",
        "email_missing": "Почта не найдена.",
        "email_taken": "Эта почта уже закреплена за другим пользователем.",
        "email_waiting": "Почта `{email}` принята. Жду письмо с кодом.",
        "code_found": "Код для `{email}`: `{code}`",
        "code_timeout": "Не дождался нового кода для `{email}` за отведённое время.",
        "code_failed": "Не удалось получить код для `{email}`.",
        "legacy_unknown_text": (
            "Я жду email в формате `example@outlook.com` или команду `/start`."
        ),
    },
    "en": {
        "choose_language": "Choose your language / Выбери язык",
        "language_set": "Language saved: English.",
        "start_text": (
            "➡️ Enter the code you received from the seller:\n"
            "(example: XHASHDAUSHFAFS)"
        ),
        "legacy_start_text": (
            "Send an email in the `example@outlook.com` format and I will try "
            "to fetch the code for it."
        ),
        "help_text": (
            "What the bot can do:\n"
            "- `/start` — enter a seller code or open your active subscription\n"
            "- `Request code` button — fetch a login code for the activated account\n"
            "- `Change account` button — unlink the current code and enter a new one\n"
            "- `/start ru` — switch language to Russian"
        ),
        "legacy_help_text": (
            "What the bot can do:\n"
            "- `/start` — show instructions\n"
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
        "addkey_usage": "Usage: `/addkey <count> <duration_days> <email>`",
        "addkey_invalid": "Count and duration must be positive integers.",
        "addkey_email_missing": "Mailbox `{email}` was not found in `email.json`.",
        "addkey_success": (
            "Created `{count}` key(s) for `{email}` for `{duration_days}` days.\n"
            "Each key is reusable with no per-user or usage limit until `{end_date}`.\n\n"
            "{codes}"
        ),
        "delkey_usage": "Usage: `/delkey <CODE>`",
        "delkey_missing": "Code `{code}` was not found.",
        "delkey_success": "Code `{code}` has been deleted.",
        "keylist_empty": "The key list is empty.",
        "keylist_header": "Keys (all keys are reusable):\n{rows}",
        "keylist_page_header": (
            "Total count: {total_count}\n\n"
            "KEY | Email | Start date - End date\n"
            "{rows}"
        ),
        "keylist_prev_button": "‹ Prev",
        "keylist_next_button": "Next ›",
        "keylist_page_button": "{page}/{total_pages}",
        "keylist_access_denied": "This button is not for you.",
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
        "key_prompt": (
            "➡️ Enter the code you received from the seller:\n"
            "(example: XHASHDAUSHFAFS)"
        ),
        "key_invalid": (
            "📕 Code {code} does not exist. Please verify it and contact the seller."
        ),
        "key_expired": (
            "📕 Code \"{code}\" has expired. The subscription ended on {end_date}."
        ),
        "key_email_missing": (
            "📕 No mailbox was found in the database for code `{code}`. Please contact the seller."
        ),
        "subscription_details": (
            "🆔 Your ID: {user_id}\n"
            "1️⃣ Login email: {email}\n"
            "2️⃣ Subscription term: {duration_days} days\n"
            "3️⃣ Subscription ends: {end_date}\n"
            "4️⃣ Activated code: {code}\n\n"
            "💬 Tap the \"Request code\" button to get a Perplexity login code."
        ),
        "subscription_request_button": "Request code",
        "subscription_change_button": "Change account",
        "subscription_request_started": "Requesting a login code now. I will send it as soon as I find it.",
        "subscription_request_running": "A code request is already running. Please wait a bit.",
        "subscription_change_success": (
            "The current account has been unlinked.\n\n"
            "➡️ Enter the code you received from the seller:\n"
            "(example: XHASHDAUSHFAFS)"
        ),
        "subscription_inactive": (
            "You do not have an activated code right now.\n\n"
            "➡️ Enter the code you received from the seller:\n"
            "(example: XHASHDAUSHFAFS)"
        ),
        "subscription_already_active": (
            "You already have an activated account. Use `Request code` or `Change account`."
        ),
        "subscription_access_denied": "This button is not for you.",
        "email_invalid": "Please send an email in the `example@outlook.com` format.",
        "email_missing": "Mailbox not found.",
        "email_taken": "This email is already assigned to another user.",
        "email_waiting": "Mailbox `{email}` accepted. Waiting for the code email.",
        "code_found": "Code for `{email}`: `{code}`",
        "code_timeout": "Timed out waiting for a new code for `{email}`.",
        "code_failed": "I could not fetch a code for `{email}`.",
        "legacy_unknown_text": (
            "Send an email in the `example@outlook.com` format or use `/start`."
        ),
    },
}


def translate(locale: str, key: str, **kwargs: str) -> str:
    bundle = MESSAGES.get(locale, MESSAGES[DEFAULT_LOCALE])
    template = bundle.get(key) or MESSAGES[DEFAULT_LOCALE][key]
    return template.format(**kwargs)
