from __future__ import annotations

import asyncio
import logging
import os
import sqlite3
from datetime import datetime
from typing import Tuple, List

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.date import DateTrigger
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import (Application, CallbackContext, CallbackQueryHandler,
                          CommandHandler, ConversationHandler, MessageHandler,
                          filters)

from weather import WeatherService


logging.basicConfig(level=logging.INFO)
LOGGER = logging.getLogger(__name__)

DB_PATH = os.getenv("WEATHER_BOT_DB", "weather.db")

SELECTING_LOCATION, SELECTING_DATE = range(2)


class SubscriptionDB:
    """Simple sqlite storage for subscriptions."""

    def __init__(self, path: str = DB_PATH):
        self.path = path
        self._ensure_table()

    def _ensure_table(self) -> None:
        with sqlite3.connect(self.path) as conn:
            conn.execute(
                "CREATE TABLE IF NOT EXISTS subscriptions("
                "chat_id INTEGER, location TEXT, date TEXT, forecast TEXT, PRIMARY KEY(chat_id, location, date)"
                ")"
            )

    def add_subscription(self, chat_id: int, location: str, date: str, forecast: str) -> None:
        with sqlite3.connect(self.path) as conn:
            conn.execute(
                "INSERT OR REPLACE INTO subscriptions(chat_id, location, date, forecast) VALUES (?, ?, ?, ?)",
                (chat_id, location, date, forecast),
            )

    def get_subscriptions(self) -> list[Tuple[int, str, str, str]]:
        with sqlite3.connect(self.path) as conn:
            cur = conn.execute("SELECT chat_id, location, date, forecast FROM subscriptions")
            return cur.fetchall()

    def get_chat_subscriptions(self, chat_id: int) -> List[Tuple[str, str, str]]:
        """Return subscriptions for a specific chat."""
        with sqlite3.connect(self.path) as conn:
            cur = conn.execute(
                "SELECT location, date, forecast FROM subscriptions WHERE chat_id=?",
                (chat_id,),
            )
            return cur.fetchall()

    def remove_subscription(self, chat_id: int, location: str, date: str) -> None:
        with sqlite3.connect(self.path) as conn:
            conn.execute(
                "DELETE FROM subscriptions WHERE chat_id=? AND location=? AND date=?",
                (chat_id, location, date),
            )

    def update_forecast(self, chat_id: int, location: str, date: str, forecast: str) -> None:
        with sqlite3.connect(self.path) as conn:
            conn.execute(
                "UPDATE subscriptions SET forecast=? WHERE chat_id=? AND location=? AND date=?",
                (forecast, chat_id, location, date),
            )


class WeatherBot:
    def __init__(self, token: str, weather_service: WeatherService, db: SubscriptionDB):
        self.weather_service = weather_service
        self.db = db
        self.app = Application.builder().token(token).build()
        self.scheduler = AsyncIOScheduler()
        self._setup_handlers()

    def _setup_handlers(self) -> None:
        conv_handler = ConversationHandler(
            entry_points=[CallbackQueryHandler(self.start_add, pattern="^add$")],
            states={
                SELECTING_LOCATION: [MessageHandler(filters.TEXT & ~filters.COMMAND, self.location_selected)],
                SELECTING_DATE: [MessageHandler(filters.TEXT & ~filters.COMMAND, self.date_selected)],
            },
            fallbacks=[CommandHandler("cancel", self.cancel)],
        )
        self.app.add_handler(conv_handler)
        self.app.add_handler(CommandHandler("start", self.menu))
        self.app.add_handler(CommandHandler("help", self.help))
        self.app.add_handler(CallbackQueryHandler(self.button))

    async def menu(self, update: Update, context: CallbackContext) -> None:
        keyboard = [
            [InlineKeyboardButton("Добавить подписку", callback_data="add")],
            [InlineKeyboardButton("Мои подписки", callback_data="view")],
            [InlineKeyboardButton("Удалить подписку", callback_data="delete")],
        ]
        await update.message.reply_text(
            "Выберите действие:", reply_markup=InlineKeyboardMarkup(keyboard)
        )

    async def start_add(self, update: Update, context: CallbackContext) -> int:
        query = update.callback_query
        await query.answer()
        await query.message.reply_text("Введите интересующий вас город:")
        return SELECTING_LOCATION

    async def help(self, update: Update, context: CallbackContext) -> None:
        await update.message.reply_text(
            "Нажмите /start для отображения меню. Команда /cancel отменяет текущий диалог."
        )

    async def cancel(self, update: Update, context: CallbackContext) -> int:
        await update.message.reply_text("Диалог отменён.")
        return ConversationHandler.END

    async def location_selected(self, update: Update, context: CallbackContext) -> int:
        context.user_data["location"] = update.message.text
        await update.message.reply_text("Введите дату в формате ГГГГ-ММ-ДД:")
        return SELECTING_DATE

    async def date_selected(self, update: Update, context: CallbackContext) -> int:
        date_text = update.message.text
        location = context.user_data["location"]
        try:
            date = datetime.strptime(date_text, "%Y-%m-%d").date()
        except ValueError:
            await update.message.reply_text("Неверный формат даты. Попробуйте ещё раз.")
            return SELECTING_DATE

        forecast = self._get_weather_text(location)
        self.db.add_subscription(update.effective_chat.id, location, date_text, forecast)
        await update.message.reply_text(
            f"Подписка добавлена. Погода в {location} на {date_text}:\n{forecast}"
        )

        await self.menu(update, context)

        # Schedule first check a bit later
        self.scheduler.add_job(
            self.check_updates,
            trigger=DateTrigger(run_date=datetime.utcnow()),
            id="check_updates",
            replace_existing=True,
        )
        return ConversationHandler.END

    async def button(self, update: Update, context: CallbackContext) -> None:
        query = update.callback_query
        await query.answer()
        data = query.data

        if data == "view":
            subs = self.db.get_chat_subscriptions(query.message.chat_id)
            if not subs:
                await query.message.reply_text("У вас нет активных подписок.")
                return
            keyboard = [
                [
                    InlineKeyboardButton(
                        f"{loc} - {date}", callback_data=f"show|{loc}|{date}"
                    )
                ]
                for loc, date, _ in subs
            ]
            await query.message.reply_text(
                "Выберите подписку:", reply_markup=InlineKeyboardMarkup(keyboard)
            )
        elif data == "delete":
            subs = self.db.get_chat_subscriptions(query.message.chat_id)
            if not subs:
                await query.message.reply_text("У вас нет подписок для удаления.")
                return
            keyboard = [
                [
                    InlineKeyboardButton(
                        f"{loc} - {date}", callback_data=f"del|{loc}|{date}"
                    )
                ]
                for loc, date, _ in subs
            ]
            await query.message.reply_text(
                "Выберите подписку для удаления:",
                reply_markup=InlineKeyboardMarkup(keyboard),
            )
        elif data.startswith("del|"):
            _, loc, date = data.split("|", 2)
            self.db.remove_subscription(query.message.chat_id, loc, date)
            await query.message.reply_text(
                f"Подписка {loc} на {date} удалена."
            )
        elif data.startswith("show|"):
            _, loc, date = data.split("|", 2)
            forecast = self._get_weather_text(loc)
            await query.message.reply_text(
                f"Погода в {loc} на {date}:\n{forecast}"
            )
        # No action needed for 'add' here, handled by ConversationHandler

    def _get_weather_text(self, location: str) -> str:
        data = self.weather_service.get_forecast(location)
        if "list" not in data:
            return "Не удалось получить прогноз."
        item = data["list"][0]
        description = item["weather"][0]["description"]
        temp = item["main"]["temp"]
        return f"{description}, {temp}°C"

    async def check_updates(self) -> None:
        LOGGER.info("Checking weather updates...")
        for chat_id, location, date, old_forecast in self.db.get_subscriptions():
            try:
                new_forecast = self._get_weather_text(location)
            except Exception as exc:  # noqa: BLE001
                LOGGER.error("Error getting weather: %s", exc)
                continue
            if new_forecast != old_forecast:
                self.db.update_forecast(chat_id, location, date, new_forecast)
                await self.app.bot.send_message(
                    chat_id=chat_id,
                    text=(
                        f"Обновлённый прогноз погоды в {location} на {date}:\n{new_forecast}"
                    ),
                )

    def run(self) -> None:
        self.scheduler.start()
        LOGGER.info("Bot started")
        self.app.run_polling()


def main() -> None:
    token = os.getenv("TELEGRAM_TOKEN")
    if not token:
        raise RuntimeError("TELEGRAM_TOKEN environment variable is required")
    bot = WeatherBot(token, WeatherService(), SubscriptionDB())
    bot.run()


if __name__ == "__main__":
    main()
