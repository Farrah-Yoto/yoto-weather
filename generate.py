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
from lunardate import LunarDate

# ========== 可自定义区域 ==========
CITY = "Shanghai"
LAT, LON = 31.2304, 121.4737
TZNAME = "Asia/Shanghai"
VOICE = "en-GB-RyanNeural"  # 英式男声，见文末可选音色列表
RATE = "-10%"               # 语速放慢，适合启蒙
KEEP_FILES = 8              # 磁盘上保留几个旧音频作缓冲（feed 里仍只列 1 集）
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

# ---------- 节日 / 生日彩蛋 ----------

# 公历固定日期
SOLAR_SPECIAL = {
    "01-01": "Happy New Year!",
    "02-14": "Happy Valentine's Day!",
    "03-08": "It's Women's Day. Say something kind to Mum today!",
    "03-12": "It's Tree Planting Day. A good day to look after a plant!",
    "04-13": "And happy birthday! I hope you have the most wonderful day!",
    "04-22": "It's Earth Day. Let's take good care of our planet!",
    "05-01": "Happy Labour Day! Enjoy the holiday!",
    "06-01": "Happy Children's Day! Today is all about you!",
    "09-10": "It's Teachers' Day. Don't forget to thank your teacher!",
    "10-01": "Happy National Day! Enjoy the holiday!",
    "10-31": "Happy Halloween! Trick or treat!",
    "12-24": "It's Christmas Eve. Father Christmas is on his way!",
    "12-25": "Merry Christmas!",
    "12-31": "It's New Year's Eve. Goodbye to this year!",
}

# 农历节日（农历月, 农历日）—— 每年公历日期不同，脚本自动换算
LUNAR_SPECIAL = {
    (1, 1): "Happy Chinese New Year! Gong xi fa cai!",
    (1, 2): "It's the second day of Chinese New Year. Time to visit family!",
    (1, 15): "It's the Lantern Festival. Time for sweet tangyuan!",
    (2, 2): "It's Dragon Head Raising Day, the start of spring farming.",
    (5, 5): "It's the Dragon Boat Festival. Enjoy your zongzi!",
    (7, 7): "It's Qixi, the Chinese Valentine's Day.",
    (7, 15): "It's the Zhongyuan Festival today.",
    (8, 15): "It's the Mid-Autumn Festival. Look for the big round moon tonight!",
    (9, 9): "It's the Double Ninth Festival, a day to care for grandparents.",
    (12, 8): "It's Laba Festival. Time for warm laba porridge!",
}

WEEKEND_LINES = [
    "Happy weekend!",
    "It's the weekend. No school today!",
    "Happy weekend! A whole day to play.",
]

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

# ---------- 方向一：天气驱动的生活场景 ----------
SCENES = {
    "rain": [
        "It's a puddle day! Let's put on our wellies and see how many "
        "puddles we can jump in.",
        "Rainy days are good for staying in. Maybe we can build a den "
        "with the blankets.",
        "Listen out for the rain on the window today. It sounds like "
        "tiny drums.",
        "Take your raincoat with you. Splashing is allowed!",
        "If it rains later, look up afterwards. Sometimes a rainbow "
        "comes out to say hello.",
        "A good day for a warm drink and a story.",
        "Watch out for snails after the rain. They love wet weather!",
    ],
    "sunny": [
        "Perfect weather for the playground. Shall we take the scooter?",
        "A lovely day to be outside. Let's find some shade and have "
        "a picnic.",
        "Look for your shadow today. Is it long or short?",
        "Sunny days are good for the park. Don't forget your sun hat.",
        "See if you can spot some flowers or bees while you are out today.",
        "A great day for the garden, or for drawing with chalk outside.",
        "The sun is out, so it's a good day for a long walk.",
    ],
    "cloudy": [
        "Look up at the clouds today. Can you find one shaped like "
        "an animal?",
        "Grey skies are still good for the park. Bring your ball!",
        "A comfortable day for a walk, not too hot and not too cold.",
        "Cloudy days are perfect for the library or a museum.",
        "Keep an eye on the sky. The clouds might break and let the sun "
        "through.",
        "A good day to ride your bike, with no hot sun in your eyes.",
    ],
    "hot": [
        "It's a hot one. Let's stay in the shade and drink lots of water.",
        "Perfect weather for water play, or maybe an ice lolly later.",
        "Try to play outside in the morning today, and rest when it gets "
        "hottest.",
        "Remember your water bottle. Take a big sip every time you "
        "think of it.",
        "A good day for the swimming pool!",
        "Wear something light and cool today, and don't forget your hat.",
    ],
    "cold": [
        "Wrap up warm today. Can you find your gloves before we leave?",
        "See if you can see your breath in the cold air this morning. "
        "It looks like smoke!",
        "A cold day is a good day for hot soup at lunchtime.",
        "Put your coat on before you open the door. It's chilly out there!",
        "Check the puddles today. Is there ice on top?",
        "Cold outside means cosy inside. A good day for a blanket and "
        "a book.",
    ],
    "windy": [
        "The wind is strong today. Perfect weather for flying a kite!",
        "Hold on to your hat, and listen to the wind in the trees.",
        "Watch the leaves dancing in the wind today.",
        "Button up your jacket. The wind likes to sneak inside!",
        "See if you can feel which way the wind is blowing.",
    ],
    "snow": [
        "Snow day! Let's see if there is enough to build a snowman.",
        "Wear your warmest boots today, and look at the footprints "
        "you leave behind.",
        "Catch a snowflake on your glove and look at it closely.",
        "A day for snowballs, and then hot chocolate to warm up.",
        "Everything looks quiet and white when it snows. Have a good look "
        "out of the window.",
    ],
    "fog": [
        "It's foggy today, so everything looks a bit like a dream.",
        "See how far you can see through the fog this morning.",
        "Foggy mornings are quiet. Try listening instead of looking.",
        "Hold hands when we walk today, because it's hard to see far.",
    ],
}

# ---------- 方向二：今日一问 ----------
QUESTIONS = [
    "Here's a question for you today: if you could bring one thing to "
    "the park, what would it be?",
    "Something to think about: what is your favourite kind of weather, "
    "and why?",
    "Here's a question: what did you dream about last night?",
    "Think about this one: if you could talk to one animal today, "
    "which one would you choose?",
    "A question for you: what made you laugh yesterday?",
    "Here's a question: what would you like to eat for dinner tonight?",
    "Something to wonder about: where do you think the clouds are going?",
    "Here's a question: what is the best thing about today?",
    "Think about this: if you could build anything at all, what would "
    "you build?",
    "A question for you: who would you like to give a hug to today?",
    "Here's a question: what new thing would you like to try this week?",
    "Something to think about: what colour is today, do you think?",
    "Here's a question: if today was a story, what would it be called?",
    "A question for you: what are you looking forward to?",
    "Think about this one: what is the funniest sound you can make?",
    "Here's a question: if you had a boat, where would you sail to?",
    "Something to wonder about: what do you think birds talk about?",
    "A question for you: what is something you are really good at?",
    "Here's a question: what would you plant if you had a garden?",
    "Think about this: if you could be very tall for one day, what "
    "would you do?",
    "A question for you: what song would you like to hear today?",
    "Here's a question: what is the kindest thing you can do today?",
    "Something to think about: what would you put in a treasure box?",
    "Here's a question: if you could invent a new weather, what would "
    "it be like?",
]

# ---------- 方向三：一周节奏 ----------
WEEKDAY_LINES = {
    0: [
        "It's Monday, the start of a brand new week.",
        "A fresh new week begins today.",
        "Monday again. Let's start the week well!",
    ],
    1: [
        "It's Tuesday, and the week is getting going.",
        "Tuesday already. Well done for a good start!",
    ],
    2: [
        "It's Wednesday, right in the middle of the week.",
        "Wednesday. We are halfway through the week!",
    ],
    3: [
        "It's Thursday, almost the end of the week.",
        "Thursday. Nearly there!",
    ],
    4: [
        "It's Friday. The weekend is very nearly here!",
        "Friday at last. One more day and it's the weekend.",
    ],
    5: [
        "It's Saturday, a whole day to do what you like.",
        "Saturday! No school and no hurry today.",
    ],
    6: [
        "It's Sunday, a good day to rest and get ready for the week.",
        "Sunday. A slow and cosy sort of day.",
    ],
}

CLOSERS = [
    "That's your weather. Have a wonderful day!",
    "And that's the weather. Have a brilliant day!",
    "That's all from the weather desk. Enjoy your day!",
    "That's your weather report. See you tomorrow!",
    "And that's it for today's weather. Have a lovely time!",
]
# ============================================================


def pick_scene_key(group, pop, hi, lo, wind):
    """根据当天天气选一个生活场景类别"""
    if group in ("snow", "heavy_snow"):
        return "snow"
    if pop >= 50 or group in (
        "rain", "heavy_rain", "drizzle",
        "showers", "heavy_showers", "thunder", "hail",
    ):
        return "rain"
    if hi >= 30:
        return "hot"
    if lo <= 5:
        return "cold"
    if wind >= 30:
        return "windy"
    if group == "foggy":
        return "fog"
    if group in ("sunny", "partly_cloudy"):
        return "sunny"
    return "cloudy"


def special_greeting(d):
    """返回当天的节日 / 生日祝福语，没有则返回 None"""
    # 除夕：判断“明天是正月初一”
    tmr = d + datetime.timedelta(days=1)
    tmr_lunar = LunarDate.fromSolarDate(tmr.year, tmr.month, tmr.day)
    if (tmr_lunar.month, tmr_lunar.day) == (1, 1):
        return "It's Chinese New Year's Eve! Time for the big family dinner!"

    lunar = LunarDate.fromSolarDate(d.year, d.month, d.day)
    if (lunar.month, lunar.day) in LUNAR_SPECIAL:
        return LUNAR_SPECIAL[(lunar.month, lunar.day)]

    if d.strftime("%m-%d") in SOLAR_SPECIAL:
        return SOLAR_SPECIAL[d.strftime("%m-%d")]

    # 母亲节：5 月第二个周日；父亲节：6 月第三个周日
    if d.month == 5 and d.weekday() == 6 and 8 <= d.day <= 14:
        return "Happy Mother's Day! Give Mum a big hug today!"
    if d.month == 6 and d.weekday() == 6 and 15 <= d.day <= 21:
        return "Happy Father's Day! Give Dad a big hug today!"

    # 普通周末
    if d.weekday() >= 5:
        return random.choice(WEEKEND_LINES)

    return None


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

    special = special_greeting(today)
    scene_key = pick_scene_key(group, pop, hi, lo, wind)

    parts = [greeting]
    if special:
        parts.append(special)
    parts += [
        random.choice(OPENERS).format(weekday=weekday, datestr=datestr),
        random.choice(WEEKDAY_LINES[today.weekday()]),
        random.choice(TODAY_LINES).format(city=CITY, desc=desc),
        random.choice(TEMP_LINES).format(hi=hi, lo=lo),
        rain_line,
        random.choice(TIPS[tip_key]),
        random.choice(SCENES[scene_key]),
        random.choice(TOMORROW_LINES).format(
            tmr_desc=tmr_desc, tmr_hi=tmr_hi
        ),
        random.choice(QUESTIONS),
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


SLOT_ORDER = {"morning": 0, "afternoon": 1, "evening": 2}


def sort_key(p):
    """按日期 + 时段排序（文件名字典序会把 evening 排到 morning 前面）"""
    date_part, _, slot = p.stem.rpartition("-")
    return (date_part, SLOT_ORDER.get(slot, 0))


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

    # feed 里只列最新一集，但磁盘上多留几个旧文件做缓冲：
    # Yoto 可能还缓存着上一集的地址，立刻删掉会让它 404
    for old in sorted(
        AUDIO_DIR.glob("*.mp3"), key=sort_key, reverse=True
    )[KEEP_FILES:]:
        old.unlink()

    episodes = [{
        "title": f"{label} Weather for {today.strftime('%A, %B %d')}",
        "text": text,
        "pubdate": format_datetime(now),
        "guid": f"weather-{slug}",
        "url": f"{BASE_URL}/audio/{mp3_path.name}",
        "size": mp3_path.stat().st_size,
        "duration": "00:01:20",
    }]

    (OUT / "episodes.json").write_text(
        json.dumps({slug: text}, ensure_ascii=False, indent=2)
    )
    (OUT / "feed.xml").write_text(build_rss(episodes), encoding="utf-8")
    print(f"Done. Feed: {BASE_URL}/feed.xml")


if __name__ == "__main__":
    main()
