import os
import requests


class WeatherService:
    """Service to fetch weather data from OpenWeatherMap."""

    def __init__(self, api_key: str | None = None):
        self.api_key = api_key or os.getenv("WEATHER_API_KEY")
        if not self.api_key:
            raise ValueError("WEATHER_API_KEY is not set")

    def get_forecast(self, location: str) -> dict:
        """Return weather forecast for the given location."""
        url = "https://api.openweathermap.org/data/2.5/forecast"
        params = {
            "q": location,
            "appid": self.api_key,
            "units": "metric",
            "lang": "ru",
        }
        resp = requests.get(url, params=params, timeout=10)
        resp.raise_for_status()
        return resp.json()
