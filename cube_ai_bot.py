"""
Куб ИИ - Многофункциональный AI бот
Версия: 2.0
"""

import asyncio
import sqlite3
import json
import random
import io
import os
from datetime import datetime, timedelta
from typing import Optional, Tuple, List, Dict, Any

from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command, CommandObject
from aiogram.types import (
    InlineKeyboardMarkup, InlineKeyboardButton,
    ReplyKeyboardMarkup, KeyboardButton,
    FSInputFile, BufferedInputFile
)
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage

# ============================================
# НАСТРОЙКИ
# ============================================
TG_TOKEN = os.environ.get("TG_TOKEN", "8778804451:AAGk444Yqzl7xbbzQOE52j9NH6i6MsMJmUg")
ADMIN_IDS = [6871376010]  # Замените на свой ID (@userinfobot)

# Бесплатные лимиты
DAILY_FREE_LIMITS = {
    "text_gen": 10,      # Генерация текста
    "image_gen": 3,      # Генерация картинок
    "voice_gen": 5,      # Озвучка текста
    "video_gen": 1,      # Генерация видео
    "summary": 5,        # Саммари текста
    "translate": 10,     # Переводчик
    "code_gen": 5,       # Генерация кода
    "math_solve": 10,    # Решение математики
    "essay_write": 3,    # Написание эссе
    "rewrite": 5,        # Рерайт текста
    "chat": 20,          # Сообщений в чат
}

PREMIUM_PRICES = {
    "week": {"days": 7, "stars": 99, "name": "Неделя"},
    "month": {"days": 30, "stars": 199, "name": "Месяц"},
    "forever": {"days": 36500, "stars": 999, "name": "Навсегда"},
}

REFERRAL_BONUS = 3  # +3 генерации за приведенного друга

# ============================================
# БАЗА ДАННЫХ
# ============================================
conn = sqlite3.connect('cube_ai.db')
conn.row_factory = sqlite3.Row

def init_db():
    # Пользователи
    conn.execute('''
        CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY,
            username TEXT,
            first_name TEXT,
            joined_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            is_premium INTEGER DEFAULT 0,
            premium_until DATE,
            referrer_id INTEGER,
            balance_stars INTEGER DEFAULT 0,
            total_generations INTEGER DEFAULT 0
        )
    ''')
    
    # Ежедневные лимиты
    conn.execute('''
        CREATE TABLE IF NOT EXISTS daily_usage (
            user_id INTEGER,
            date DATE,
            feature TEXT,
            count INTEGER DEFAULT 0,
            PRIMARY KEY (user_id, date, feature)
        )
    ''')
    
    # История генераций
    conn.execute('''
        CREATE TABLE IF NOT EXISTS history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            feature TEXT,
            prompt TEXT,
            result TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    
    # Промокоды
    conn.execute('''
        CREATE TABLE IF NOT EXISTS promocodes (
            code TEXT PRIMARY KEY,
            days INTEGER,
            max_uses INTEGER,
            used INTEGER DEFAULT 0,
            created_by INTEGER
        )
    ''')
    
    # Чат-сессии (память для ИИ-чата)
    conn.execute('''
        CREATE TABLE IF NOT EXISTS chat_sessions (
            user_id INTEGER,
            session_id TEXT,
            messages TEXT,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (user_id, session_id)
        )
    ''')
    
    # Рекламные посты
    conn.execute('''
        CREATE TABLE IF NOT EXISTS ads (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            text TEXT,
            button_text TEXT,
            button_url TEXT,
            views INTEGER DEFAULT 0,
            max_views INTEGER,
            active INTEGER DEFAULT 1
        )
    ''')
    
    # Отзывы
    conn.execute('''
        CREATE TABLE IF NOT EXISTS reviews (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            rating INTEGER,
            text TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    
    conn.commit()

init_db()

# ============================================
# ИНИЦИАЛИЗАЦИЯ БОТА
# ============================================
bot = Bot(token=TG_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
storage = MemoryStorage()
dp = Dispatcher(storage=storage)

# ============================================
# СОСТОЯНИЯ FSM (для сложных диалогов)
# ============================================
class ChatState(StatesGroup):
    chatting = State()
    waiting_for_image_prompt = State()
    waiting_for_video_prompt = State()
    waiting_for_code_lang = State()
    waiting_for_translate_text = State()
    waiting_for_summary_text = State()
    waiting_for_essay_topic = State()
    waiting_for_rewrite_text = State()
    waiting_for_math = State()
    waiting_for_feedback = State()
    waiting_for_promo_input = State()

# ============================================
# ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ
# ============================================
def get_user(user_id: int) -> dict:
    cursor = conn.execute('SELECT * FROM users WHERE user_id = ?', (user_id,))
    row = cursor.fetchone()
    if not row:
        conn.execute(
            'INSERT INTO users (user_id) VALUES (?)',
            (user_id,)
        )
        conn.commit()
        return {'user_id': user_id, 'is_premium': 0, 'balance_stars': 0}
    return dict(row)

def is_premium(user_id: int) -> bool:
    user = get_user(user_id)
    if user['is_premium'] and user['premium_until']:
        until = datetime.strptime(user['premium_until'], '%Y-%m-%d').date()
        if until >= datetime.now().date():
            return True
        else:
            conn.execute('UPDATE users SET is_premium = 0 WHERE user_id = ?', (user_id,))
            conn.commit()
    return user_id in ADMIN_IDS

def check_limit(user_id: int, feature: str) -> Tuple[bool, int]:
    if is_premium(user_id):
        return True, 999
    
    today = datetime.now().date().isoformat()
    limit = DAILY_FREE_LIMITS.get(feature, 10)
    
    cursor = conn.execute(
        'SELECT count FROM daily_usage WHERE user_id = ? AND date = ? AND feature = ?',
        (user_id, today, feature)
    )
    row = cursor.fetchone()
    used = row['count'] if row else 0
    
    return used < limit, limit - used

def increment_usage(user_id: int, feature: str):
    today = datetime.now().date().isoformat()
    conn.execute('''
        INSERT INTO daily_usage (user_id, date, feature, count)
        VALUES (?, ?, ?, 1)
        ON CONFLICT(user_id, date, feature)
        DO UPDATE SET count = count + 1
    ''', (user_id, today, feature))
    conn.execute(
        'UPDATE users SET total_generations = total_generations + 1 WHERE user_id = ?',
        (user_id,)
    )
    conn.commit()

def add_referral_bonus(user_id: int):
    user = get_user(user_id)
    if user.get('referrer_id'):
        # Даем бонус пригласившему
        conn.execute(
            'UPDATE users SET balance_stars = balance_stars + ? WHERE user_id = ?',
            (REFERRAL_BONUS, user['referrer_id'])
        )
        conn.commit()

def get_main_keyboard(user_id: int) -> ReplyKeyboardMarkup:
    premium = is_premium(user_id)
    premium_badge = "💎 " if premium else ""
    
    kb = [
        [KeyboardButton(text="🤖 ИИ-Чат"), KeyboardButton(text="📝 Генерация текста")],
        [KeyboardButton(text="🎨 Создать картинку"), KeyboardButton(text="🎙 Озвучить текст")],
        [KeyboardButton(text="🎬 Сгенерировать видео"), KeyboardButton(text="📹 Видео по запросу")],
        [KeyboardButton(text="📊 Саммари текста"), KeyboardButton(text="🌐 Переводчик")],
        [KeyboardButton(text="💻 Написать код"), KeyboardButton(text="📐 Решить математику")],
        [KeyboardButton(text="✍️ Написать эссе"), KeyboardButton(text="🔄 Рерайт текста")],
        [KeyboardButton(text="📋 Мои лимиты"), KeyboardButton(text="👤 Профиль")],
        [KeyboardButton(text="🎁 Пригласить друга"), KeyboardButton(text="⭐ Отзыв")],
        [KeyboardButton(text="ℹ️ Помощь"), KeyboardButton(text="🛒 Premium")],
    ]
    return ReplyKeyboardMarkup(keyboard=kb, resize_keyboard=True)

def get_inline_voice_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="👨 Айдар (спокойный)", callback_data="voice_aidar")],
        [InlineKeyboardButton(text="👨 Бая (глубокий)", callback_data="voice_baya")],
        [InlineKeyboardButton(text="👨 Руслан (энергичный)", callback_data="voice_ruslan")],
        [InlineKeyboardButton(text="👩 Ксения (мягкий)", callback_data="voice_xenia")],
        [InlineKeyboardButton(text="👩 Евгения (официальный)", callback_data="voice_eugene")],
        [InlineKeyboardButton(text="👩 Наташа (быстрый)", callback_data="voice_natasha")],
        [InlineKeyboardButton(text="🎲 Случайный голос", callback_data="voice_random")],
    ])

def get_inline_style_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🎨 Реалистичное фото", callback_data="style_realistic")],
        [InlineKeyboardButton(text="🖼 Аниме", callback_data="style_anime")],
        [InlineKeyboardButton(text="🎭 3D-рендер", callback_data="style_3d")],
        [InlineKeyboardButton(text="🖌 Масляная живопись", callback_data="style_oil")],
        [InlineKeyboardButton(text="✏️ Карандашный рисунок", callback_data="style_sketch")],
        [InlineKeyboardButton(text="🌈 Пиксель-арт", callback_data="style_pixel")],
    ])

def format_number(num: int) -> str:
    if num >= 1_000_000:
        return f"{num/1_000_000:.1f}M"
    elif num >= 1_000:
        return f"{num/1_000:.1f}K"
    return str(num)

# ============================================
# ЭМУЛЯЦИЯ ИИ (заглушки для демо)
# ============================================
async def ai_generate_text(prompt: str, style: str = "default") -> str:
    responses = {
        "default": [
            f"По вашему запросу «{prompt[:50]}...» я подготовил следующий текст:\n\n" +
            "Искусственный интеллект продолжает развиваться с невероятной скоростью. " +
            "В 2026 году мы наблюдаем появление моделей, способных создавать реалистичное видео, " +
            "синтезировать естественную речь и писать код на уровне senior-разработчика.",
            f"Вот что я думаю по поводу «{prompt[:30]}...»\n\n" +
            "Это действительно интересная тема. Современные технологии позволяют решать " +
            "задачи, которые еще пару лет назад казались научной фантастикой. " +
            "Нейросети учатся на огромных массивах данных и находят закономерности, " +
            "недоступные человеческому восприятию.",
        ],
        "creative": [
            "Представьте себе мир, где каждый ваш запрос превращается в увлекательное приключение. " +
            "Именно это и происходит сейчас! Я генерирую для вас уникальный контент, " +
            "наполненный креативными идеями и неожиданными поворотами.",
        ],
        "business": [
            "С профессиональной точки зрения, рассматриваемый вопрос требует комплексного подхода. " +
            "Рекомендую обратить внимание на следующие ключевые аспекты:\n" +
            "1. Анализ текущей ситуации\n2. Определение целевых показателей\n" +
            "3. Разработка пошаговой стратегии\n4. Внедрение и мониторинг результатов.",
        ]
    }
    return random.choice(responses.get(style, responses["default"]))

async def ai_summarize(text: str) -> str:
    words = len(text.split())
    return f"📄 Саммари ({words} слов → кратко):\n\n" + \
           f"Основная мысль текста заключается в том, что {text[:100]}... " + \
           f"[Автоматическое сокращение выполнено ИИ]"

async def ai_translate(text: str, target_lang: str = "en") -> str:
    translations = {
        "en": f"Translation to English:\n\n{text[::-1][:50]}... [translated]",
        "ru": f"Перевод на русский:\n\n{text[:50]}... [переведено]",
        "es": f"Traducción al español:\n\n{text[:50]}... [traducido]",
        "fr": f"Traduction en français:\n\n{text[:50]}... [traduit]",
        "de": f"Übersetzung ins Deutsche:\n\n{text[:50]}... [übersetzt]",
        "zh": f"中文翻译:\n\n{text[:50]}... [已翻译]",
    }
    return translations.get(target_lang, translations["en"])

async def ai_generate_code(prompt: str, language: str = "python") -> str:
    code_examples = {
        "python": '''def solve_problem(data):
    """Решение задачи"""
    result = []
    for item in data:
        if item > 0:
            result.append(item ** 2)
    return result

# Пример использования
input_data = [1, -2, 3, -4, 5]
output = solve_problem(input_data)
print(f"Результат: {output}")''',
        "javascript": '''function solveProblem(data) {
    return data
        .filter(item => item > 0)
        .map(item => item ** 2);
}

// Пример использования
const inputData = [1, -2, 3, -4, 5];
const output = solveProblem(inputData);
console.log(`Результат: ${output}`);''',
        "html": '''<!DOCTYPE html>
<html>
<head>
    <title>Сгенерированная страница</title>
    <style>
        body { font-family: Arial; margin: 40px; }
        .container { max-width: 800px; margin: 0 auto; }
    </style>
</head>
<body>
    <div class="container">
        <h1>Привет, мир!</h1>
        <p>Это страница, созданная ИИ.</p>
    </div>
</body>
</html>'''
    }
    return f"💻 Код ({language}):\n\n<pre><code class='language-{language}'>{code_examples.get(language, code_examples['python'])}</code></pre>"

async def ai_solve_math(problem: str) -> str:
    return f"📐 Решение задачи «{problem}»:\n\n" + \
           "1. Анализ условия\n" + \
           "2. Применение формул\n" + \
           "3. Вычисления\n" + \
           "4. Ответ: 42 (шутка, но вычисления показывают примерно это)"

async def ai_write_essay(topic: str) -> str:
    return f"📝 Эссе на тему «{topic}»\n\n" + \
           "Введение\n" + \
           f"Тема {topic} является одной из наиболее обсуждаемых в современном обществе. " + \
           "Это неудивительно, учитывая её влияние на различные аспекты нашей жизни.\n\n" + \
           "Основная часть\n" + \
           "Рассматривая данный вопрос, важно отметить несколько ключевых аспектов. " + \
           "Во-первых, исторический контекст показывает, что эта тема всегда вызывала интерес. " + \
           "Во-вторых, современные реалии вносят свои коррективы в понимание проблемы.\n\n" + \
           "Заключение\n" + \
           "Подводя итог, можно сказать, что тема остаётся актуальной и требует дальнейшего изучения."

async def ai_rewrite(text: str) -> str:
    return f"🔄 Рерайт текста:\n\n{text[:100]}... [уникализированная версия с сохранением смысла]"

async def ai_chat_response(message: str, history: list = None) -> str:
    responses = [
        "Интересный вопрос! Давайте разберемся подробнее.",
        "Я понимаю, о чем вы говорите. Вот что я думаю по этому поводу...",
        "Спасибо за вопрос! Основываясь на имеющейся информации, могу сказать следующее.",
        "Это действительно важная тема. Многие эксперты считают, что...",
        "Я проанализировал ваш запрос и подготовил развернутый ответ.",
        "Отличный вопрос! Позвольте мне объяснить.",
    ]
    return f"🤖 {random.choice(responses)}\n\n{message[:50]}... [Ответ сгенерирован нейросетью]"

# ============================================
# ОБРАБОТЧИКИ КОМАНД
# ============================================
@dp.message(Command("start"))
async def cmd_start(message: types.Message, command: CommandObject):
    user_id = message.from_user.id
    user = get_user(user_id)
    
    # Обработка реферальной ссылки
    args = command.args
    if args and args.startswith("ref_"):
        try:
            referrer_id = int(args.replace("ref_", ""))
            if referrer_id != user_id and not user.get('referrer_id'):
                conn.execute(
                    'UPDATE users SET referrer_id = ? WHERE user_id = ?',
                    (referrer_id, user_id)
                )
                conn.commit()
                add_referral_bonus(user_id)
                await bot.send_message(
                    referrer_id,
                    f"🎉 По вашей реферальной ссылке присоединился новый пользователь!\n"
                    f"+{REFERRAL_BONUS} генераций в подарок!"
                )
        except:
            pass
    
    # Приветственное сообщение
    welcome_text = f"""
<b>🤖 Добро пожаловать в Куб ИИ!</b>

Я — многофункциональный бот с искусственным интеллектом. Вот что я умею:

<b>📝 ТЕКСТ И КОММУНИКАЦИЯ</b>
• ИИ-Чат — общайтесь с нейросетью
• Генерация текста — статьи, посты, описания
• Рерайт — уникализация текста
• Саммари — краткое содержание
• Переводчик — 6 языков

<b>🎨 КРЕАТИВ</b>
• Создание изображений (разные стили)
• Озвучка текста (6 голосов)
• Генерация видео по описанию
• Написание кода (Python, JS, HTML)

<b>📚 УЧЕБА</b>
• Решение математических задач
• Написание эссе и сочинений

<b>💎 ВАШ СТАТУС</b>
{'✅ Premium активен' if is_premium(user_id) else f'🆓 Бесплатный (обновляется ежедневно)'}

<i>Выберите действие на клавиатуре ниже 👇</i>
"""
    
    await message.answer(
        welcome_text,
        reply_markup=get_main_keyboard(user_id)
    )

@dp.message(Command("help"))
async def cmd_help(message: types.Message):
    help_text = """
<b>📚 Помощь по функциям Куб ИИ</b>

<b>🤖 ИИ-Чат</b>
Просто пишите сообщения — нейросеть ответит. Помнит контекст беседы.

<b>📝 Генерация текста</b>
Создает уникальный текст по вашему описанию.

<b>🎨 Создать картинку</b>
Генерирует изображение. Доступны стили: реализм, аниме, 3D, живопись, скетч, пиксель-арт.

<b>🎙 Озвучить текст</b>
Превращает текст в голосовое сообщение. 6 разных голосов.

<b>📊 Саммари</b>
Сокращает длинный текст до ключевых тезисов.

<b>🌐 Переводчик</b>
Переводит текст на английский, испанский, французский, немецкий, китайский.

<b>💻 Написать код</b>
Генерирует код на Python, JavaScript или HTML по описанию.

<b>📐 Решить математику</b>
Решает уравнения и задачи.

<b>✍️ Написать эссе</b>
Создает структурированное эссе на заданную тему.

<b>🔄 Рерайт</b>
Переписывает текст другими словами, сохраняя смысл.

<b>💎 Premium</b>
Снимает все лимиты, дает приоритет в очереди.

<i>Есть вопросы? Напишите @admin_username</i>
"""
    await message.answer(help_text)

@dp.message(Command("profile"))
async def cmd_profile(message: types.Message):
    user_id = message.from_user.id
    user = get_user(user_id)
    
    # Считаем статистику
    cursor = conn.execute(
        'SELECT COUNT(*) FROM history WHERE user_id = ?',
        (user_id,)
    )
    total_gen = cursor.fetchone()[0]
    
    cursor = conn.execute(
        'SELECT SUM(count) FROM daily_usage WHERE user_id = ? AND date = ?',
        (user_id, datetime.now().date().isoformat())
    )
    today_gen = cursor.fetchone()[0] or 0
    
    ref_cursor = conn.execute(
        'SELECT COUNT(*) FROM users WHERE referrer_id = ?',
        (user_id,)
    )
    referrals = ref_cursor.fetchone()[0]
    
    profile_text = f"""
<b>👤 ПРОФИЛЬ ПОЛЬЗОВАТЕЛЯ</b>

🆔 ID: <code>{user_id}</code>
📅 С нами с: {user.get('joined_at', 'сегодня')[:10]}

<b>📊 СТАТИСТИКА</b>
• Всего генераций: {format_number(total_gen)}
• Сегодня: {today_gen}
• Приглашено друзей: {referrals}
• Баланс Stars: {user.get('balance_stars', 0)} ⭐

<b>💎 СТАТУС</b>
{'✅ Premium до ' + user.get('premium_until', '') if is_premium(user_id) else '🆓 Бесплатный аккаунт'}

<b>🔗 ВАША РЕФЕРАЛЬНАЯ ССЫЛКА</b>
<code>https://t.me/{(await bot.get_me()).username}?start=ref_{user_id}</code>

<i>Приглашайте друзей и получайте +{REFERRAL_BONUS} генераций за каждого!</i>
"""
    await message.answer(profile_text)

@dp.message(Command("limits"))
async def cmd_limits(message: types.Message):
    user_id = message.from_user.id
    today = datetime.now().date().isoformat()
    
    limits_text = "<b>📋 ВАШИ ЛИМИТЫ НА СЕГОДНЯ</b>\n\n"
    
    if is_premium(user_id):
        limits_text += "<b>💎 PREMIUM — БЕЗЛИМИТ!</b>\n\n"
        for feature, limit in DAILY_FREE_LIMITS.items():
            limits_text += f"• {feature}: ∞\n"
    else:
        limits_text += f"<b>🆓 БЕСПЛАТНЫЙ ТАРИФ</b> (сброс в 00:00 МСК)\n\n"
        
        cursor = conn.execute(
            'SELECT feature, count FROM daily_usage WHERE user_id = ? AND date = ?',
            (user_id, today)
        )
        usage = {row['feature']: row['count'] for row in cursor.fetchall()}
        
        for feature, limit in DAILY_FREE_LIMITS.items():
            used = usage.get(feature, 0)
            emoji = "✅" if used < limit else "❌"
            feature_name = {
                "text_gen": "Генерация текста",
                "image_gen": "Создание картинок",
                "voice_gen": "Озвучка",
                "video_gen": "Генерация видео",
                "summary": "Саммари",
                "translate": "Переводчик",
                "code_gen": "Написание кода",
                "math_solve": "Решение математики",
                "essay_write": "Написание эссе",
                "rewrite": "Рерайт",
                "chat": "ИИ-Чат",
            }.get(feature, feature)
            limits_text += f"{emoji} {feature_name}: {used}/{limit}\n"
    
    limits_text += "\n💎 <b>Premium снимает все лимиты!</b> /buy"
    await message.answer(limits_text)

@dp.message(Command("buy"))
async def cmd_buy(message: types.Message):
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(
            text=f"📅 Неделя — {PREMIUM_PRICES['week']['stars']} ⭐",
            callback_data="buy_week"
        )],
        [InlineKeyboardButton(
            text=f"📅 Месяц — {PREMIUM_PRICES['month']['stars']} ⭐ (выгодно!)",
            callback_data="buy_month"
        )],
        [InlineKeyboardButton(
            text=f"🌟 Навсегда — {PREMIUM_PRICES['forever']['stars']} ⭐",
            callback_data="buy_forever"
        )],
        [InlineKeyboardButton(
            text="🎁 Ввести промокод",
            callback_data="enter_promo"
        )],
    ])
    
    await message.answer(
        "<b>💎 PREMIUM ПОДПИСКА</b>\n\n"
        "Что вы получаете:\n"
        "✅ Безлимитные генерации ВСЕХ типов\n"
        "✅ Приоритет в очереди (без ожидания)\n"
        "✅ Доступ к бета-функциям\n"
        "✅ Отключение рекламы\n"
        "✅ Поддержка 24/7\n\n"
        "<i>Выберите тариф:</i>",
        reply_markup=keyboard
    )

@dp.message(Command("referral"))
async def cmd_referral(message: types.Message):
    user_id = message.from_user.id
    bot_info = await bot.get_me()
    
    ref_link = f"https://t.me/{bot_info.username}?start=ref_{user_id}"
    
    cursor = conn.execute('SELECT COUNT(*) FROM users WHERE referrer_id = ?', (user_id,))
    count = cursor.fetchone()[0]
    
    await message.answer(
        f"<b>🎁 РЕФЕРАЛЬНАЯ ПРОГРАММА</b>\n\n"
        f"Приглашайте друзей и получайте бонусы!\n\n"
        f"<b>Ваша ссылка:</b>\n"
        f"<code>{ref_link}</code>\n\n"
        f"<b>Статистика:</b>\n"
        f"👥 Приглашено: {count}\n"
        f"⭐ За каждого друга: +{REFERRAL_BONUS} генераций\n\n"
        f"<i>Скопируйте ссылку и отправьте друзьям!</i>"
    )

@dp.message(Command("promo"))
async def cmd_create_promo(message: types.Message):
    user_id = message.from_user.id
    if user_id not in ADMIN_IDS:
        await message.answer("❌ Только для администраторов")
        return
    
    args = message.text.split()
    if len(args) < 3:
        await message.answer("Использование: /promo КОД ДНИ [МАКС_ИСПОЛЬЗОВАНИЙ]")
        return
    
    code = args[1].upper()
    days = int(args[2])
    max_uses = int(args[3]) if len(args) > 3 else 100
    
    conn.execute(
        'INSERT OR REPLACE INTO promocodes (code, days, max_uses, created_by) VALUES (?, ?, ?, ?)',
        (code, days, max_uses, user_id)
    )
    conn.commit()
    
    await message.answer(f"✅ Промокод <code>{code}</code> создан!\nДней: {days}\nИспользований: {max_uses}")

@dp.message(Command("stats"))
async def cmd_stats(message: types.Message):
    user_id = message.from_user.id
    if user_id not in ADMIN_IDS:
        await message.answer("❌ Только для администраторов")
        return
    
    # Общая статистика
    cursor = conn.execute('SELECT COUNT(*) FROM users')
    total_users = cursor.fetchone()[0]
    
    cursor = conn.execute('SELECT COUNT(*) FROM users WHERE is_premium = 1')
    premium_users = cursor.fetchone()[0]
    
    cursor = conn.execute('SELECT SUM(total_generations) FROM users')
    total_gens = cursor.fetchone()[0] or 0
    
    cursor = conn.execute(
        'SELECT COUNT(*) FROM daily_usage WHERE date = ?',
        (datetime.now().date().isoformat(),)
    )
    today_active = cursor.fetchone()[0]
    
    stats_text = f"""
<b>📊 СТАТИСТИКА БОТА</b>

👥 Всего пользователей: {format_number(total_users)}
💎 Premium: {format_number(premium_users)}
🔄 Всего генераций: {format_number(total_gens)}
📅 Активных сегодня: {format_number(today_active)}

<b>📈 Рост по дням:</b>
"""
    await message.answer(stats_text)

@dp.message(Command("broadcast"))
async def cmd_broadcast(message: types.Message, state: FSMContext):
    user_id = message.from_user.id
    if user_id not in ADMIN_IDS:
        await message.answer("❌ Только для администраторов")
        return
    
    # Здесь логика рассылки
    await message.answer("Функция рассылки в разработке")

# ============================================
# ОБРАБОТЧИКИ КНОПОК
# ============================================
@dp.message(lambda m: m.text == "🤖 ИИ-Чат")
async def btn_chat(message: types.Message, state: FSMContext):
    user_id = message.from_user.id
    can, remaining = check_limit(user_id, "chat")
    
    if not can:
        await message.answer(f"❌ Лимит сообщений исчерпан. /buy для безлимита")
        return
    
    await state.set_state(ChatState.chatting)
    await message.answer(
        "<b>🤖 ИИ-ЧАТ АКТИВИРОВАН</b>\n\n"
        "Теперь вы общаетесь с нейросетью. Я помню контекст беседы!\n"
        "Пишите что угодно — я отвечу.\n\n"
        "<i>Для выхода нажмите кнопку «🏠 Главное меню»</i>"
    )

@dp.message(ChatState.chatting)
async def chat_handler(message: types.Message, state: FSMContext):
    user_id = message.from_user.id
    text = message.text
    
    if text == "🏠 Главное меню":
        await state.clear()
        await message.answer("Вы вышли из чата", reply_markup=get_main_keyboard(user_id))
        return
    
    can, remaining = check_limit(user_id, "chat")
    if not can:
        await state.clear()
        await message.answer("Лимит исчерпан", reply_markup=get_main_keyboard(user_id))
        return
    
    # Имитация "печатания"
    await bot.send_chat_action(message.chat.id, "typing")
    await asyncio.sleep(1.5)
    
    response = await ai_chat_response(text)
    increment_usage(user_id, "chat")
    
    await message.answer(response)

@dp.message(lambda m: m.text == "📝 Генерация текста")
async def btn_text_gen(message: types.Message):
    user_id = message.from_user.id
    can, remaining = check_limit(user_id, "text_gen")
    
    if not can:
        await message.answer(f"❌ Лимит исчерпан ({DAILY_FREE_LIMITS['text_gen']}/{DAILY_FREE_LIMITS['text_gen']})")
        return
    
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📄 Обычный", callback_data="text_style_default")],
        [InlineKeyboardButton(text="🎨 Креативный", callback_data="text_style_creative")],
        [InlineKeyboardButton(text="💼 Деловой", callback_data="text_style_business")],
    ])
    
    await message.answer(
        f"<b>📝 ГЕНЕРАЦИЯ ТЕКСТА</b>\n\n"
        f"Осталось генераций: {remaining}\n\n"
        "Отправьте тему или описание, а я создам текст.\n"
        "Сначала выберите стиль:",
        reply_markup=keyboard
    )

@dp.message(lambda m: m.text == "🎨 Создать картинку")
async def btn_image_gen(message: types.Message, state: FSMContext):
    user_id = message.from_user.id
    can, remaining = check_limit(user_id, "image_gen")
    
    if not can:
        await message.answer(f"❌ Лимит картинок исчерпан ({DAILY_FREE_LIMITS['image_gen']}/{DAILY_FREE_LIMITS['image_gen']})")
        return
    
    await state.set_state(ChatState.waiting_for_image_prompt)
    await message.answer(
        f"<b>🎨 СОЗДАНИЕ ИЗОБРАЖЕНИЯ</b>\n\n"
        f"Осталось: {remaining}\n\n"
        "Опишите, что нарисовать.\n"
        "Например: «Кот в космосе ест пиццу, реалистичное фото»",
        reply_markup=get_inline_style_keyboard()
    )

@dp.message(ChatState.waiting_for_image_prompt)
async def image_prompt_handler(message: types.Message, state: FSMContext):
    user_id = message.from_user.id
    prompt = message.text
    
    can, remaining = check_limit(user_id, "image_gen")
    if not can:
        await state.clear()
        await message.answer("Лимит исчерпан", reply_markup=get_main_keyboard(user_id))
        return
    
    await bot.send_chat_action(message.chat.id, "upload_photo")
    await asyncio.sleep(2)
    
    increment_usage(user_id, "image_gen")
    await state.clear()
    
    # Заглушка изображения
    await message.answer(
        f"✅ <b>Изображение сгенерировано!</b>\n\n"
        f"Запрос: {prompt[:100]}...\n\n"
        f"<i>🎨 В реальной версии здесь будет сгенерированная картинка</i>\n\n"
        f"Осталось генераций: {remaining - 1}",
        reply_markup=get_main_keyboard(user_id)
    )

@dp.message(lambda m: m.text == "🎙 Озвучить текст")
async def btn_voice_gen(message: types.Message, state: FSMContext):
    user_id = message.from_user.id
    can, remaining = check_limit(user_id, "voice_gen")
    
    if not can:
        await message.answer(f"❌ Лимит озвучки исчерпан ({DAILY_FREE_LIMITS['voice_gen']}/{DAILY_FREE_LIMITS['voice_gen']})")
        return
    
    await state.set_state(ChatState.waiting_for_rewrite_text)  # Используем существующее состояние
    await message.answer(
        f"<b>🎙 ОЗВУЧКА ТЕКСТА</b>\n\n"
        f"Осталось: {remaining}\n\n"
        "Отправьте текст для озвучки (до 500 символов)\n"
        "И выберите голос:",
        reply_markup=get_inline_voice_keyboard()
    )

@dp.message(lambda m: m.text == "📊 Саммари текста")
async def btn_summary(message: types.Message, state: FSMContext):
    user_id = message.from_user.id
    can, remaining = check_limit(user_id, "summary")
    
    if not can:
        await message.answer(f"❌ Лимит исчерпан ({DAILY_FREE_LIMITS['summary']}/{DAILY_FREE_LIMITS['summary']})")
        return
    
    await state.set_state(ChatState.waiting_for_summary_text)
    await message.answer(
        f"<b>📊 САММАРИ ТЕКСТА</b>\n\n"
        f"Осталось: {remaining}\n\n"
        "Отправьте текст (до 2000 символов), и я сделаю краткое содержание."
    )

@dp.message(ChatState.waiting_for_summary_text)
async def summary_handler(message: types.Message, state: FSMContext):
    user_id = message.from_user.id
    text = message.text
    
    can, remaining = check_limit(user_id, "summary")
    if not can:
        await state.clear()
        await message.answer("Лимит исчерпан", reply_markup=get_main_keyboard(user_id))
        return
    
    await bot.send_chat_action(message.chat.id, "typing")
    await asyncio.sleep(1)
    
    summary = await ai_summarize(text)
    increment_usage(user_id, "summary")
    await state.clear()
    
    await message.answer(
        summary,
        reply_markup=get_main_keyboard(user_id)
    )

@dp.message(lambda m: m.text == "🌐 Переводчик")
async def btn_translate(message: types.Message, state: FSMContext):
    user_id = message.from_user.id
    can, remaining = check_limit(user_id, "translate")
    
    if not can:
        await message.answer(f"❌ Лимит переводов исчерпан")
        return
    
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🇬🇧 Английский", callback_data="trans_en")],
        [InlineKeyboardButton(text="🇪🇸 Испанский", callback_data="trans_es")],
        [InlineKeyboardButton(text="🇫🇷 Французский", callback_data="trans_fr")],
        [InlineKeyboardButton(text="🇩🇪 Немецкий", callback_data="trans_de")],
        [InlineKeyboardButton(text="🇨🇳 Китайский", callback_data="trans_zh")],
        [InlineKeyboardButton(text="🇷🇺 Русский", callback_data="trans_ru")],
    ])
    
    await state.set_state(ChatState.waiting_for_translate_text)
    await message.answer(
        f"<b>🌐 ПЕРЕВОДЧИК</b>\n\n"
        f"Осталось: {remaining}\n\n"
        "Выберите язык и отправьте текст.",
        reply_markup=keyboard
    )

@dp.message(lambda m: m.text == "💻 Написать код")
async def btn_code_gen(message: types.Message, state: FSMContext):
    user_id = message.from_user.id
    can, remaining = check_limit(user_id, "code_gen")
    
    if not can:
        await message.answer(f"❌ Лимит генерации кода исчерпан")
        return
    
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🐍 Python", callback_data="code_python")],
        [InlineKeyboardButton(text="📜 JavaScript", callback_data="code_javascript")],
        [InlineKeyboardButton(text="🌐 HTML/CSS", callback_data="code_html")],
    ])
    
    await state.set_state(ChatState.waiting_for_code_lang)
    await message.answer(
        f"<b>💻 НАПИСАНИЕ КОДА</b>\n\n"
        f"Осталось: {remaining}\n\n"
        "Выберите язык программирования:",
        reply_markup=keyboard
    )

@dp.message(lambda m: m.text == "📐 Решить математику")
async def btn_math_solve(message: types.Message, state: FSMContext):
    user_id = message.from_user.id
    can, remaining = check_limit(user_id, "math_solve")
    
    if not can:
        await message.answer(f"❌ Лимит решения задач исчерпан")
        return
    
    await state.set_state(ChatState.waiting_for_math)
    await message.answer(
        f"<b>📐 РЕШЕНИЕ МАТЕМАТИКИ</b>\n\n"
        f"Осталось: {remaining}\n\n"
        "Отправьте задачу или уравнение.\n"
        "Например: «2x + 5 = 15» или «Найти производную x²»"
    )

@dp.message(ChatState.waiting_for_math)
async def math_handler(message: types.Message, state: FSMContext):
    user_id = message.from_user.id
    problem = message.text
    
    can, remaining = check_limit(user_id, "math_solve")
    if not can:
        await state.clear()
        await message.answer("Лимит исчерпан", reply_markup=get_main_keyboard(user_id))
        return
    
    await bot.send_chat_action(message.chat.id, "typing")
    await asyncio.sleep(1)
    
    solution = await ai_solve_math(problem)
    increment_usage(user_id, "math_solve")
    await state.clear()
    
    await message.answer(
        solution,
        reply_markup=get_main_keyboard(user_id)
    )

@dp.message(lambda m: m.text == "✍️ Написать эссе")
async def btn_essay_write(message: types.Message, state: FSMContext):
    user_id = message.from_user.id
    can, remaining = check_limit(user_id, "essay_write")
    
    if not can:
        await message.answer(f"❌ Лимит эссе исчерпан")
        return
    
    await state.set_state(ChatState.waiting_for_essay_topic)
    await message.answer(
        f"<b>✍️ НАПИСАНИЕ ЭССЕ</b>\n\n"
        f"Осталось: {remaining}\n\n"
        "Отправьте тему для эссе.\n"
        "Например: «Влияние технологий на образование»"
    )

@dp.message(ChatState.waiting_for_essay_topic)
async def essay_handler(message: types.Message, state: FSMContext):
    user_id = message.from_user.id
    topic = message.text
    
    can, remaining = check_limit(user_id, "essay_write")
    if not can:
        await state.clear()
        await message.answer("Лимит исчерпан", reply_markup=get_main_keyboard(user_id))
        return
    
    await bot.send_chat_action(message.chat.id, "typing")
    await asyncio.sleep(2)
    
    essay = await ai_write_essay(topic)
    increment_usage(user_id, "essay_write")
    await state.clear()
    
    await message.answer(
        essay,
        reply_markup=get_main_keyboard(user_id)
    )

@dp.message(lambda m: m.text == "🔄 Рерайт текста")
async def btn_rewrite(message: types.Message, state: FSMContext):
    user_id = message.from_user.id
    can, remaining = check_limit(user_id, "rewrite")
    
    if not can:
        await message.answer(f"❌ Лимит рерайта исчерпан")
        return
    
    await state.set_state(ChatState.waiting_for_rewrite_text)
    await message.answer(
        f"<b>🔄 РЕРАЙТ ТЕКСТА</b>\n\n"
        f"Осталось: {remaining}\n\n"
        "Отправьте текст для уникализации."
    )

@dp.message(ChatState.waiting_for_rewrite_text)
async def rewrite_handler(message: types.Message, state: FSMContext):
    user_id = message.from_user.id
    text = message.text
    
    can, remaining = check_limit(user_id, "rewrite")
    if not can:
        await state.clear()
        await message.answer("Лимит исчерпан", reply_markup=get_main_keyboard(user_id))
        return
    
    await bot.send_chat_action(message.chat.id, "typing")
    await asyncio.sleep(1.5)
    
    rewritten = await ai_rewrite(text)
    increment_usage(user_id, "rewrite")
    await state.clear()
    
    await message.answer(
        rewritten,
        reply_markup=get_main_keyboard(user_id)
    )

@dp.message(lambda m: m.text == "📋 Мои лимиты")
async def btn_limits(message: types.Message):
    await cmd_limits(message)

@dp.message(lambda m: m.text == "👤 Профиль")
async def btn_profile(message: types.Message):
    await cmd_profile(message)

@dp.message(lambda m: m.text == "🎁 Пригласить друга")
async def btn_referral(message: types.Message):
    await cmd_referral(message)

@dp.message(lambda m: m.text == "⭐ Отзыв")
async def btn_feedback(message: types.Message, state: FSMContext):
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="⭐", callback_data="rate_1"),
         InlineKeyboardButton(text="⭐⭐", callback_data="rate_2"),
         InlineKeyboardButton(text="⭐⭐⭐", callback_data="rate_3"),
         InlineKeyboardButton(text="⭐⭐⭐⭐", callback_data="rate_4"),
         InlineKeyboardButton(text="⭐⭐⭐⭐⭐", callback_data="rate_5")],
    ])
    
    await state.set_state(ChatState.waiting_for_feedback)
    await message.answer(
        "<b>⭐ ОСТАВИТЬ ОТЗЫВ</b>\n\n"
        "Оцените бота от 1 до 5 звезд:",
        reply_markup=keyboard
    )

@dp.message(lambda m: m.text == "ℹ️ Помощь")
async def btn_help(message: types.Message):
    await cmd_help(message)

@dp.message(lambda m: m.text == "🛒 Premium")
async def btn_premium(message: types.Message):
    await cmd_buy(message)

@dp.message(lambda m: m.text == "🏠 Главное меню")
async def btn_main_menu(message: types.Message, state: FSMContext):
    await state.clear()
    await message.answer(
        "Главное меню:",
        reply_markup=get_main_keyboard(message.from_user.id)
    )

# ============================================
# CALLBACK ОБРАБОТЧИКИ
# ============================================
@dp.callback_query(lambda c: c.data.startswith('voice_'))
async def voice_callback(callback: types.CallbackQuery, state: FSMContext):
    voice = callback.data.replace('voice_', '')
    voices = {
        'aidar': 'Айдар', 'baya': 'Бая', 'ruslan': 'Руслан',
        'xenia': 'Ксения', 'eugene': 'Евгения', 'natasha': 'Наташа',
        'random': 'Случайный'
    }
    
    await state.update_data(voice=voice)
    await callback.message.edit_text(f"✅ Выбран голос: {voices.get(voice, voice)}\n\nТеперь отправьте текст для озвучки.")
    await callback.answer()

@dp.callback_query(lambda c: c.data.startswith('style_'))
async def style_callback(callback: types.CallbackQuery, state: FSMContext):
    style = callback.data.replace('style_', '')
    styles = {
        'realistic': 'Реалистичное фото',
        'anime': 'Аниме',
        '3d': '3D-рендер',
        'oil': 'Масляная живопись',
        'sketch': 'Карандашный рисунок',
        'pixel': 'Пиксель-арт',
    }
    
    await state.update_data(style=style)
    await callback.message.edit_text(f"✅ Выбран стиль: {styles.get(style, style)}\n\nТеперь опишите, что нарисовать.")
    await callback.answer()

@dp.callback_query(lambda c: c.data.startswith('text_style_'))
async def text_style_callback(callback: types.CallbackQuery, state: FSMContext):
    style = callback.data.replace('text_style_', '')
    styles = {
        'default': 'Обычный',
        'creative': 'Креативный',
        'business': 'Деловой',
    }
    
    await state.update_data(text_style=style)
    await callback.message.edit_text(f"✅ Выбран стиль: {styles.get(style, style)}\n\nОтправьте тему для генерации текста.")
    await callback.answer()

@dp.callback_query(lambda c: c.data.startswith('trans_'))
async def trans_callback(callback: types.CallbackQuery, state: FSMContext):
    lang = callback.data.replace('trans_', '')
    langs = {
        'en': 'Английский', 'es': 'Испанский', 'fr': 'Французский',
        'de': 'Немецкий', 'zh': 'Китайский', 'ru': 'Русский',
    }
    
    await state.update_data(trans_lang=lang)
    await callback.message.edit_text(f"✅ Выбран язык: {langs.get(lang, lang)}\n\nОтправьте текст для перевода.")
    await callback.answer()

@dp.callback_query(lambda c: c.data.startswith('code_'))
async def code_callback(callback: types.CallbackQuery, state: FSMContext):
    lang = callback.data.replace('code_', '')
    langs = {'python': 'Python', 'javascript': 'JavaScript', 'html': 'HTML/CSS'}
    
    await state.update_data(code_lang=lang)
    await callback.message.edit_text(f"✅ Выбран язык: {langs.get(lang, lang)}\n\nОпишите, какой код нужен.")
    await callback.answer()

@dp.callback_query(lambda c: c.data.startswith('rate_'))
async def rate_callback(callback: types.CallbackQuery, state: FSMContext):
    rating = int(callback.data.replace('rate_', ''))
    
    await state.update_data(rating=rating)
    await callback.message.edit_text(
        f"Спасибо за оценку {rating}⭐!\n\n"
        "Напишите ваш отзыв (или отправьте /skip чтобы пропустить):"
    )
    await callback.answer()

@dp.callback_query(lambda c: c.data.startswith('buy_'))
async def buy_callback(callback: types.CallbackQuery):
    plan = callback.data.replace('buy_', '')
    plan_info = PREMIUM_PRICES.get(plan)
    
    if not plan_info:
        await callback.answer("Ошибка выбора тарифа")
        return
    
    # Создаем инвойс в Telegram Stars
    await bot.send_invoice(
        chat_id=callback.from_user.id,
        title=f"Premium {plan_info['name']}",
        description=f"Безлимитный доступ ко всем функциям на {plan_info['days']} дней",
        payload=f"premium_{plan}",
        provider_token="",
        currency="XTR",
        prices=[types.LabeledPrice(
            label=f"Premium {plan_info['name']}",
            amount=plan_info['stars']
        )],
        start_parameter=f"premium_{plan}"
    )
    await callback.answer()

@dp.callback_query(lambda c: c.data == "enter_promo")
async def enter_promo_callback(callback: types.CallbackQuery, state: FSMContext):
    await state.set_state(ChatState.waiting_for_promo_input)
    await callback.message.edit_text(
        "🎁 <b>ВВЕДИТЕ ПРОМОКОД</b>\n\n"
        "Отправьте промокод для активации."
    )
    await callback.answer()

@dp.message(ChatState.waiting_for_promo_input)
async def promo_input_handler(message: types.Message, state: FSMContext):
    user_id = message.from_user.id
    code = message.text.strip().upper()
    
    cursor = conn.execute(
        'SELECT * FROM promocodes WHERE code = ? AND used < max_uses',
        (code,)
    )
    promo = cursor.fetchone()
    
    if not promo:
        await message.answer("❌ Промокод не найден или исчерпан")
        await state.clear()
        return
    
    days = promo['days']
    premium_until = (datetime.now() + timedelta(days=days)).strftime('%Y-%m-%d')
    
    conn.execute(
        'UPDATE users SET is_premium = 1, premium_until = ? WHERE user_id = ?',
        (premium_until, user_id)
    )
    conn.execute('UPDATE promocodes SET used = used + 1 WHERE code = ?', (code,))
    conn.commit()
    
    await state.clear()
    await message.answer(
        f"✅ <b>ПРОМОКОД АКТИВИРОВАН!</b>\n\n"
        f"Premium активен до {premium_until}\n"
        f"Наслаждайтесь безлимитом! 🎉",
        reply_markup=get_main_keyboard(user_id)
    )

# ============================================
# ОБРАБОТЧИКИ ПЛАТЕЖЕЙ
# ============================================
@dp.pre_checkout_query()
async def pre_checkout(pre_checkout_query: types.PreCheckoutQuery):
    await pre_checkout_query.answer(ok=True)

@dp.message(lambda m: m.successful_payment)
async def successful_payment(message: types.Message):
    user_id = message.from_user.id
    payload = message.successful_payment.invoice_payload
    
    # Определяем тариф
    if payload == "premium_week":
        days = PREMIUM_PRICES['week']['days']
    elif payload == "premium_month":
        days = PREMIUM_PRICES['month']['days']
    elif payload == "premium_forever":
        days = PREMIUM_PRICES['forever']['days']
    else:
        days = 30
    
    premium_until = (datetime.now() + timedelta(days=days)).strftime('%Y-%m-%d')
    
    conn.execute(
        'UPDATE users SET is_premium = 1, premium_until = ? WHERE user_id = ?',
        (premium_until, user_id)
    )
    conn.commit()
    
    await message.answer(
        f"✅ <b>ОПЛАТА ПРОШЛА УСПЕШНО!</b>\n\n"
        f"Premium активен до {premium_until}\n"
        f"Спасибо за покупку! 🎉\n\n"
        f"Теперь вам доступны ВСЕ функции без ограничений!",
        reply_markup=get_main_keyboard(user_id)
    )

# ============================================
# ОБРАБОТЧИК НЕИЗВЕСТНЫХ СООБЩЕНИЙ
# ============================================
@dp.message()
async def unknown_message(message: types.Message, state: FSMContext):
    # Если есть активное состояние - не обрабатываем здесь
    current_state = await state.get_state()
    if current_state:
        return
    
    await message.answer(
        "Используйте кнопки меню для навигации 👇",
        reply_markup=get_main_keyboard(message.from_user.id)
    )

# ============================================
# ЗАПУСК БОТА
# ============================================
async def main():
    print("🤖 Куб ИИ запускается...")
    await bot.delete_webhook(drop_pending_updates=True)
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())