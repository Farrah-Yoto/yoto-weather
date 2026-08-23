#!/usr/bin/env python3
"""每日英文天气播报：取数 → 生成文稿 → TTS → 更新 RSS"""

import asyncio
import datetime
import json
import os
import random
import urllib.request
from email.utils import format_datetime
from pathlib import Path
from xml.sax.saxutils import escape
from zoneinfo import ZoneInfo

import edge_tts

# ========== 可自定义区域 ==========
CITY = "Shanghai"
LAT, LON = 31.2304, 121.4737
TZNAME = "Asia/Shanghai"
VOICE = "en-GB-RyanNeural"  # 英式男声，见文末可选音色列表
RATE = "-10%"               # 语速放慢，适合启蒙
OWNER_EMAIL = "weather@example.com"   # 占位邮箱即可，不需要真实地址
BASE_URL = os.environ["BASE_URL"].rstrip("/")
# =================================

TZ = ZoneInfo(TZNAME)
OUT = Path("docs")
AUDIO_DIR = OUT / "audio"
AUDIO_DIR.mkdir(parents=True, exist_ok=True)

# ========== 表达样本库：每句都随机抉，天天不重样 ==========

# WMO 天气代码 → 天气类别
WMO_GROUP = {
    0: "sunny", 1: "sunny",
    2: "partly_cloudy",
    3: "cloudy",
    45: "foggy", 48: "foggy",
    51: "drizzle", 53: "drizzle", 55: "drizzle",
    61: "rain", 63: "rain", 65: "heavy_rain",
    66: "sleet", 67: "sleet",
    71: "snow", 73: "snow", 75: "heavy_snow", 77: "snow",
    80: "showers", 81: "showers", 82: "heavy_showers",
    85: "snow", 86: "snow",
    95: "thunder", 96: "hail", 99: "hail",
}

# 每个类别的多种说法
DESC = {
    "sunny": [
        "bright and sunny", "lovely and sunny", "clear and sunny",
        "full of sunshine", "beautifully clear",
    ],
    "partly_cloudy": [
        "partly cloudy", "a mix of sun and clouds",
        "sunny with a few clouds", "bright with some cloud",
    ],
    "cloudy": [
        "cloudy", "rather grey and cloudy", "overcast",
        "covered in clouds",
    ],
    "foggy": [
        "foggy and misty", "quite foggy", "misty and grey",
    ],
    "drizzle": [
        "drizzly", "a little bit rainy", "damp with light drizzle",
    ],
    "rain": [
        "rainy", "wet and rainy", "a rainy sort of day",
    ],
    "heavy_rain": [
        "very rainy", "wet with heavy rain", "pouring with rain",
    ],
    "showers": [
        "showery", "sunny with a few showers", "on and off showers",
    ],
    "heavy_showers": [
        "stormy with heavy showers", "wild with heavy showers",
    ],
    "sleet": [
        "icy and rainy", "cold with sleet",
    ],
    "snow": [
        "snowy", "sprinkled with snow", "white with falling snow",
    ],
    "heavy_snow": [
        "very snowy", "deep with heavy snow",
    ],
    "thunder": [
        "thundery", "rumbling with thunderstorms",
    ],
    "hail": [
        "thundery with hail", "stormy with thunder and hail",
    ],
}

OPENERS = [
    "Here is your weather report for {weekday}, {datestr}.",
    "It's {weekday}, {datestr}. Time for the weather!",
    "Welcome to your weather report for {weekday}, {datestr}.",
    "Here's your weather for {weekday}, {datestr}.",
    "Let's find out what the weather is like on this {weekday}, {datestr}.",
]

TODAY_LINES = [
    "Today in {city}, it will be {desc}.",
    "In {city} today, expect it to be {desc}.",
    "Here in {city}, the day will be {desc}.",
    "{city} is going to be {desc} today.",
    "Looking outside in {city}, today will be {desc}.",
]

TEMP_LINES = [
    "The high will be {hi} degrees, and the low will be {lo} degrees.",
    "It will reach {hi} degrees, and drop down to {lo} degrees.",
    "Temperatures will go up to {hi} degrees, with a low of {lo}.",
    "The warmest part of the day will be {hi} degrees, and the coolest {lo}.",
]

RAIN_LINES = [
    "There is a {pop} percent chance of rain.",
    "The chance of rain is {pop} percent.",
    "Rain is about {pop} percent likely today.",
]

NO_RAIN_LINES = [
    "There is almost no chance of rain today.",
    "Rain is very unlikely today.",
    "You probably won't need a raincoat today.",
]

TIPS = {
    "umbrella": [
        "Don't forget your umbrella today!",
        "Remember to take an umbrella with you!",
        "Pack your umbrella and your wellies!",
    ],
    "hot": [
        "It's going to be hot. Drink lots of water and wear a hat!",
        "Stay cool, drink plenty of water, and find some shade!",
        "It's a hot one. A sun hat and a water bottle are a good idea!",
    ],
    "cold": [
        "It's very cold. Please wear your warm coat, hat and gloves!",
        "Wrap up warm today, with a big coat and cosy gloves!",
        "Brrr, it's chilly. Don't forget your scarf!",
    ],
    "windy": [
        "It's quite windy today, so hold on to your hat!",
        "The wind is strong today. Perfect weather for flying a kite!",
        "It's blustery out there, so button up your jacket!",
    ],
    "nice": [
        "It's a lovely day. Have fun outside!",
        "What a lovely day to play outside!",
        "A great day for the park or the playground!",
        "Perfect weather for an adventure outdoors!",
    ],
}

TOMORROW_LINES = [
    "And here's a look ahead: tomorrow will be {tmr_desc}, "
    "with a high of {tmr_hi} degrees.",
    "Looking ahead to tomorrow, it will be {tmr_desc}, "
    "reaching {tmr_hi} degrees.",
    "As for tomorrow, expect it to be {tmr_desc}, "
    "with a high of {tmr_hi} degrees.",
]

CLOSERS = [
    "That's your weather. Have a wonderful day!",
    "And that's the weather. Have a brilliant day!",
    "That's all from the weather desk. Enjoy your day!",
    "That's your weather report. See you tomorrow!",
    "And that's it for today's weather. Have a lovely time!",
]
# ============================================================


def fetch_weather():
    url = (
        "https://api.open-meteo.com/v1/forecast"
        f"?latitude={LAT}&longitude={LON}"
        "&daily=weather_code,temperature_2m_max,temperature_2m_min,"
        "precipitation_probability_max,wind_speed_10m_max"
        "&current=temperature_2m,weather_code"
        f"&timezone={TZNAME.replace('/', '%2F')}&forecast_days=2"
    )
    with urllib.request.urlopen(url, timeout=30) as r:
        return json.load(r)


def build_script(data, today, greeting):
    d = data["daily"]
    group = WMO_GROUP.get(d["weather_code"][0], "cloudy")
    desc = random.choice(DESC[group])
    hi = round(d["temperature_2m_max"][0])
    lo = round(d["temperature_2m_min"][0])
    pop = d["precipitation_probability_max"][0] or 0
    wind = round(d["wind_speed_10m_max"][0])

    tmr_group = WMO_GROUP.get(d["weather_code"][1], "cloudy")
    tmr_desc = random.choice(DESC[tmr_group])
    tmr_hi = round(d["temperature_2m_max"][1])

    weekday = today.strftime("%A")
    datestr = today.strftime("%B %-d")  # Windows 上用 %#d

    # 穿衣 / 携带物建议
    if pop >= 50:
        tip_key = "umbrella"
    elif hi >= 30:
        tip_key = "hot"
    elif lo <= 5:
        tip_key = "cold"
    elif wind >= 30:
        tip_key = "windy"
    else:
        tip_key = "nice"

    rain_line = (
        random.choice(RAIN_LINES).format(pop=pop) if pop >= 10
        else random.choice(NO_RAIN_LINES)
    )

    parts = [
        greeting,
        random.choice(OPENERS).format(weekday=weekday, datestr=datestr),
        random.choice(TODAY_LINES).format(city=CITY, desc=desc),
        random.choice(TEMP_LINES).format(hi=hi, lo=lo),
        rain_line,
        random.choice(TIPS[tip_key]),
        random.choice(TOMORROW_LINES).format(
            tmr_desc=tmr_desc, tmr_hi=tmr_hi
        ),
        random.choice(CLOSERS),
    ]
    return " ".join(parts)


async def synthesize(text, path):
    tts = edge_tts.Communicate(text, voice=VOICE, rate=RATE)
    await tts.save(str(path))


def build_rss(episodes):
    items = []
    for ep in episodes:
        items.append(f"""    <item>
      <title>{escape(ep['title'])}</title>
      <description>{escape(ep['text'])}</description>
      <itunes:summary>{escape(ep['text'])}</itunes:summary>
      <pubDate>{ep['pubdate']}</pubDate>
      <guid isPermaLink="false">{ep['guid']}</guid>
      <enclosure url="{ep['url']}" length="{ep['size']}" type="audio/mpeg"/>
      <itunes:duration>{ep['duration']}</itunes:duration>
      <itunes:explicit>false</itunes:explicit>
    </item>""")

    now_str = format_datetime(datetime.datetime.now(TZ))
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0"
     xmlns:itunes="http://www.itunes.com/dtds/podcast-1.0.dtd"
     xmlns:atom="http://www.w3.org/2005/Atom">
  <channel>
    <title>Daily Weather for Kids</title>
    <link>{BASE_URL}/</link>
    <atom:link href="{BASE_URL}/feed.xml" rel="self" type="application/rss+xml"/>
    <language>en-gb</language>
    <description>A short, friendly English weather report every morning.</description>
    <lastBuildDate>{now_str}</lastBuildDate>
    <itunes:author>Family</itunes:author>
    <itunes:summary>A short, friendly English weather report every morning.</itunes:summary>
    <itunes:type>episodic</itunes:type>
    <itunes:explicit>false</itunes:explicit>
    <itunes:owner>
      <itunes:name>Family</itunes:name>
      <itunes:email>{OWNER_EMAIL}</itunes:email>
    </itunes:owner>
    <itunes:category text="Education"/>
    <itunes:image href="{BASE_URL}/cover.jpg"/>
{chr(10).join(items)}
  </channel>
</rss>
"""


def main():
    now = datetime.datetime.now(TZ)
    today = now.date()

    # 按生成时刻选问候语
    if now.hour < 12:
        greeting, slot, label = "Good morning!", "morning", "Morning"
    elif now.hour < 18:
        greeting, slot, label = "Good afternoon!", "afternoon", "Afternoon"
    else:
        greeting, slot, label = "Good evening!", "evening", "Evening"

    slug = f"{today.isoformat()}-{slot}"
    mp3_path = AUDIO_DIR / f"{slug}.mp3"

    data = fetch_weather()
    text = build_script(data, today, greeting)
    print("SCRIPT:", text)

    asyncio.run(synthesize(text, mp3_path))

    # 只保留刚生成的这一集，其余全部删除
    for old in AUDIO_DIR.glob("*.mp3"):
        if old.name != mp3_path.name:
            old.unlink()

    episodes = [{
        "title": f"{label} Weather for {today.strftime('%A, %B %d')}",
        "text": text,
        "pubdate": format_datetime(now),
        "guid": f"weather-{slug}",
        "url": f"{BASE_URL}/audio/{mp3_path.name}",
        "size": mp3_path.stat().st_size,
        "duration": "00:00:45",
    }]

    (OUT / "episodes.json").write_text(
        json.dumps({slug: text}, ensure_ascii=False, indent=2)
    )
    (OUT / "feed.xml").write_text(build_rss(episodes), encoding="utf-8")
    print(f"Done. Feed: {BASE_URL}/feed.xml")


if __name__ == "__main__":
    main()
