# checkWeatherBot

Телеграм-бот, позволяющий подписаться на прогноз погоды на выбранную дату.

## Запуск

1. Создайте виртуальное окружение и установите зависимости:

```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

2. Создайте бота в [BotFather](https://t.me/BotFather) и получите токен.
   Задайте переменные окружения:

```bash
export TELEGRAM_TOKEN=ВашТелеграмТокен
export WEATHER_API_KEY=КлючOpenWeather
```

3. Запустите бота:

```bash
python -m weatherbot.bot
```

## Использование

Отправьте команду `/start`, введите город и выберите дату через встроенный календарь.
Бот пришлёт актуальный прогноз на указанную дату и будет уведомлять о его изменениях.
