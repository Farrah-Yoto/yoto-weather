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
AUDIO_NAME = "latest.mp3"   # 音频文件名永远不变（关键，见排查里的说明）
OWNER_EMAIL = "weather@example.com"   # 占位邮箱即可，不需要真实地址
BASE_URL = os.environ["BASE_URL"].rstrip("/")
FORCE = os.environ.get("FORCE") == "1"   # 手动运行时强制重新生成
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
        "wall to wall sunshine", "sunny from start to finish",
        "glorious and sunny", "bright as anything",
        "sunny with clear blue skies",
    ],
    "partly_cloudy": [
        "partly cloudy", "a mix of sun and clouds",
        "sunny with a few clouds", "bright with some cloud",
        "sunny in between the clouds", "part sun and part cloud",
        "mostly bright with the odd cloud",
    ],
    "cloudy": [
        "cloudy", "rather grey and cloudy", "overcast",
        "covered in clouds", "grey from start to finish",
        "under a blanket of cloud", "dull and cloudy",
        "cloudy with hardly any sun",
    ],
    "foggy": [
        "foggy and misty", "quite foggy", "misty and grey",
    ],
    "drizzle": [
        "drizzly", "a little bit rainy", "damp with light drizzle",
    ],
    "rain": [
        "rainy", "wet and rainy", "a rainy sort of day",
        "wet right through the day", "rainy on and off",
        "a proper wet one", "grey and rainy",
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

# 不带时间的问候语：一天只生成一次，任何时刻听都不穿帮
GREETINGS = [
    "Hello!",
    "Hello there!",
    "Hi there!",
    "Hello, and welcome!",
    "Hello! Lovely to see you.",
    "Hello! Ready for today?",
    "Hello there! Let's get started.",
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

# 体感分档：比单纯报数字更有画面，也都是英文里真正常用的说法
FEEL_LINES = {
    "freezing": [
        "It will feel properly freezing out there.",
        "It's bitterly cold today, so every layer counts.",
        "That's cold enough to make your cheeks go pink.",
        "It will feel icy the moment you step outside.",
        "Freezing cold today, so keep those fingers covered.",
    ],
    "cold": [
        "It will feel quite nippy, especially first thing.",
        "A bit parky today, as they say.",
        "It's chilly out, so a coat is a good idea.",
        "Cold enough for a hat, but not for gloves.",
        "There's a real chill in the air today.",
    ],
    "mild": [
        "It should feel pleasantly mild.",
        "Nice and comfortable today, not too hot and not too cold.",
        "It will feel just right out there.",
        "A mild sort of day, easy to be outside in.",
        "Comfortable enough to forget about the weather altogether.",
    ],
    "warm": [
        "It will feel lovely and warm.",
        "Pleasantly warm today, perfect for being outdoors.",
        "Warm enough for short sleeves.",
        "It should feel gently warm all day.",
        "A warm one, but nothing too fierce.",
    ],
    "hot": [
        "It's going to be a real scorcher.",
        "Baking hot today, so take it slowly.",
        "It will feel hot and sticky out there.",
        "Properly hot today, the kind that makes you seek out shade.",
        "Muggy and hot, so keep that water bottle close.",
    ],
}

# 风只在够大的时候提一句
WIND_LINES = [
    "There's a fair breeze about today too.",
    "It will be breezy as well, so things might rattle about.",
    "Add a bit of wind to that, so it may feel cooler than it is.",
    "The wind will be picking up as well.",
    "It's a blowy one out there today.",
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

# ---------- 方向二：一周一个主题（周一提出，周内每天推进一句） ----------
# 每项是（主题词, 周一的开场句）。主题词会被填进下面的通用句式，
# 所以 52 + 10 条素材就能产出两百多种说法。学校主题放在开学的周。
WEEKLY_THEMES = [
    ("frost",
     "This week we are looking out for frost. See if you can find any on "
     "a window or on the grass."),
    ("warm clothes",
     "This week our theme is warm clothes. Notice which ones keep you "
     "warmest."),
    ("breath in cold air",
     "This week we are watching our breath in the cold air. Try breathing "
     "out slowly and see what happens."),
    ("red things",
     "This week we are looking for red things. Red is the colour of luck "
     "and celebration."),
    ("family",
     "This week our theme is family. Think about who is in yours."),
    ("food from far away",
     "This week we are thinking about food from far away. Some of what we "
     "eat travels a very long way."),
    ("sleep and dreams",
     "This week our theme is sleep and dreams. See if you can remember one "
     "dream this week."),
    ("birds in winter",
     "This week we are watching birds in winter. Notice where they go to "
     "keep warm."),
    ("bare trees",
     "This week our theme is bare trees. Look closely at their shapes "
     "without any leaves."),
    ("going back to school",
     "This week our theme is going back to school. Think about what you "
     "are looking forward to."),
    ("rain",
     "This week our theme is rain. Listen to it, and watch where it goes."),
    ("puddles and mud",
     "This week we are thinking about puddles and mud. Find the biggest "
     "puddle you can."),
    ("seeds",
     "This week our theme is seeds. Every big plant started as a tiny one."),
    ("classmates",
     "This week our theme is classmates. Think about someone you sit near."),
    ("bees and butterflies",
     "This week we are looking for bees and butterflies. Watch which "
     "flowers they choose."),
    ("the colour green",
     "This week our theme is the colour green. Count how many different "
     "greens you can find."),
    ("reading and books",
     "This week our theme is reading and books. Think about a story you "
     "would read again."),
    ("kites",
     "This week our theme is kites. Notice how the wind carries things."),
    ("birdsong",
     "This week we are listening to birdsong. Try to hear how many "
     "different birds there are."),
    ("numbers",
     "This week our theme is numbers. Numbers are hiding almost "
     "everywhere."),
    ("worms and snails",
     "This week we are looking for worms and snails. They come out when "
     "the ground is wet."),
    ("growing taller",
     "This week our theme is growing taller. You are a little bigger than "
     "you were last year."),
    ("sports and running",
     "This week our theme is sports and running. Notice how your body "
     "feels afterwards."),
    ("water",
     "This week our theme is water. Think about all the ways you use it in "
     "one day."),
    ("ice and cold things",
     "This week our theme is ice and cold things. Watch how quickly ice "
     "melts."),
    ("helping others",
     "This week our theme is helping others. Look for one small way to "
     "help each day."),
    ("clouds",
     "This week our theme is clouds. Find one shaped like something you "
     "know."),
    ("thunder",
     "This week our theme is thunder. Thunder is only a sound, and sound "
     "cannot hurt you."),
    ("long evenings",
     "This week our theme is long evenings. Notice how late it stays "
     "light."),
    ("insects",
     "This week our theme is insects. Look carefully, because they are "
     "small."),
    ("fruit",
     "This week our theme is fruit. Try to notice the colours before you "
     "eat them."),
    ("swimming",
     "This week our theme is swimming. Think about how water holds you "
     "up."),
    ("the sun",
     "This week our theme is the sun. It is very far away, and still warms "
     "your face."),
    ("staying cool",
     "This week our theme is staying cool. Find the shadiest place you "
     "know."),
    ("holidays",
     "This week our theme is holidays. Think about your favourite day so "
     "far."),
    ("a new school year",
     "This week our theme is a new school year. Everything is a little new "
     "again."),
    ("the moon",
     "This week our theme is the moon. Look for it each night and see how "
     "it changes."),
    ("school bags and desks",
     "This week our theme is school bags and desks. Notice how you like to "
     "keep yours."),
    ("harvest",
     "This week our theme is harvest. This is the time when food is "
     "gathered in."),
    ("blustery days",
     "This week our theme is blustery days. Watch what the wind moves and "
     "what it does not."),
    ("teachers",
     "This week our theme is teachers. Think about something one of them "
     "taught you."),
    ("jumpers and socks",
     "This week our theme is jumpers and socks. Notice which ones are "
     "cosiest."),
    ("getting dark early",
     "This week our theme is how early it gets dark. See what time the "
     "lights come on."),
    ("trying something hard",
     "This week our theme is trying something hard. Hard things get easier "
     "with practice."),
    ("animals getting ready for winter",
     "This week our theme is animals getting ready for winter. They are "
     "busy now."),
    ("warm drinks",
     "This week our theme is warm drinks. Notice how they warm your hands "
     "first."),
    ("taking turns",
     "This week our theme is taking turns. Notice how it feels to wait, "
     "and to be waited for."),
    ("rain on the window",
     "This week our theme is rain on the window. Watch one drop and follow "
     "it down."),
    ("lights",
     "This week our theme is lights. Notice all the lights on the way "
     "home."),
    ("giving",
     "This week our theme is giving. Think of something you could give "
     "that is not a thing."),
    ("writing and letters",
     "This week our theme is writing and letters. Letters make words, and "
     "words carry ideas."),
    ("looking back",
     "This week our theme is looking back. Think about one thing you "
     "learned this year."),
]

# 周二到周五用的通用推进句式，{theme} 会被替成上面的主题词
THEME_LINES = [
    "Remember, our theme this week is {theme}. What have you noticed today?",
    "This week we are thinking about {theme}. Anything new today?",
    "Keep {theme} in mind today.",
    "Have you thought any more about {theme}?",
    "What would you tell a friend about {theme}?",
    "Still thinking about {theme} this week. Has anything surprised you?",
    "Tell someone about {theme} today.",
    "This week is all about {theme}. What is your favourite part?",
    "Think about {theme} today. Would you like to know more about it?",
    "One more day of thinking about {theme}. What stands out?",
]

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


def weekly_theme(today):
    """本周主题：周一提出，周二到周五用通用句式推进，周末换成轻松提问"""
    week = today.isocalendar()[1]
    theme, opening = WEEKLY_THEMES[(week - 1) % len(WEEKLY_THEMES)]
    if today.weekday() == 0:
        return opening
    if today.weekday() >= 5:
        # 周末不推进主题，用那 24 条轻松问题（严格轮转，不会撞车）
        return pick(QUESTIONS, today)
    return pick(THEME_LINES, today).format(theme=theme)


def already_done_today(today):
    """今天已经生成过就跳过，让后面几次定时运行变成免费的兜底"""
    p = OUT / "episodes.json"
    if not p.exists():
        return False
    try:
        return json.loads(p.read_text()).get("date") == today.isoformat()
    except Exception:
        return False


def pick(seq, today, offset=0):
    """按日期严格轮转：用完整个库才回头，不会连着撞同一条"""
    return seq[(today.toordinal() + offset) % len(seq)]


def days_to_next_festival(d, limit=45):
    """往后找最近的一个节日，返回还有几天；找不到返回 None"""
    for i in range(1, limit + 1):
        n = d + datetime.timedelta(days=i)
        lunar = LunarDate.fromSolarDate(n.year, n.month, n.day)
        if (lunar.month, lunar.day) in LUNAR_SPECIAL:
            return i
        if n.strftime("%m-%d") in SOLAR_SPECIAL:
            return i
    return None


def data_question(d, today):
    """今日观察：全部从当天真实数据现算，所以永远不会重复"""
    cands = []

    # 一、和昨天比气温
    y_hi = round(d["temperature_2m_max"][0])
    t_hi = round(d["temperature_2m_max"][1])
    diff = t_hi - y_hi
    if diff >= 2:
        cands.append(
            f"Here's something to notice: today is about {diff} degrees "
            "warmer than yesterday. Do you think you can feel it?"
        )
    elif diff <= -2:
        cands.append(
            f"Here's something to notice: today is about {abs(diff)} degrees "
            "cooler than yesterday. Will you need an extra layer?"
        )
    else:
        cands.append(
            "Here's something to notice: today is almost exactly as warm as "
            "yesterday. Two days the same in a row!"
        )

    # 二、白天在变长还是变短
    delta = round(
        (d["daylight_duration"][1] - d["daylight_duration"][0]) / 60
    )
    sunset = datetime.datetime.fromisoformat(
        d["sunset"][1]
    ).strftime("%-I:%M")
    if delta <= -1:
        cands.append(
            f"The sun goes down at {sunset} this evening, about "
            f"{abs(delta)} minutes earlier than yesterday. "
            "Can you feel the days getting shorter?"
        )
    elif delta >= 1:
        cands.append(
            f"The sun goes down at {sunset} this evening, about "
            f"{delta} minutes later than yesterday. "
            "Can you feel the days getting longer?"
        )
    else:
        cands.append(
            f"The sun goes down at {sunset} this evening, at almost exactly "
            "the same time as yesterday."
        )

    # 三、今年过了多少天
    doy = today.timetuple().tm_yday
    left = (datetime.date(today.year, 12, 31) - today).days
    cands.append(
        f"Did you know today is day {doy} of the year? "
        f"There are {left} days to go until the new one."
    )

    # 四、还有几天到下一个节日
    togo = days_to_next_festival(today)
    if togo:
        cands.append(
            f"There are {togo} days to go until a special day. "
            "Can you guess which one?"
        )

    return pick(cands, today)


def fetch_weather():
    url = (
        "https://api.open-meteo.com/v1/forecast"
        f"?latitude={LAT}&longitude={LON}"
        "&daily=weather_code,temperature_2m_max,temperature_2m_min,"
        "precipitation_probability_max,wind_speed_10m_max,"
        "sunrise,sunset,daylight_duration"
        "&current=temperature_2m,weather_code"
        # past_days=1 把昨天也取回来，用于“今日观察”的对比
        # 注意：因此 daily 数组变成 [昨天, 今天, 明天]
        f"&timezone={TZNAME.replace('/', '%2F')}"
        "&past_days=1&forecast_days=2"
    )
    with urllib.request.urlopen(url, timeout=30) as r:
        return json.load(r)


def build_script(data, today, greeting):
    d = data["daily"]
    # 开了 past_days=1 之后：0=昨天，1=今天，2=明天
    TDY, TMR = 1, 2
    group = WMO_GROUP.get(d["weather_code"][TDY], "cloudy")
    desc = random.choice(DESC[group])
    hi = round(d["temperature_2m_max"][TDY])
    lo = round(d["temperature_2m_min"][TDY])
    pop = d["precipitation_probability_max"][TDY] or 0
    wind = round(d["wind_speed_10m_max"][TDY])

    tmr_group = WMO_GROUP.get(d["weather_code"][TMR], "cloudy")
    tmr_desc = random.choice(DESC[tmr_group])
    tmr_hi = round(d["temperature_2m_max"][TMR])

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

    # 第⑥段：天气极端时给实用提醒，天气平淡时给生活画面
    # 体感分档
    if hi <= 5:
        feel_key = "freezing"
    elif hi <= 14:
        feel_key = "cold"
    elif hi <= 22:
        feel_key = "mild"
    elif hi <= 29:
        feel_key = "warm"
    else:
        feel_key = "hot"

    wind_line = random.choice(WIND_LINES) if wind >= 20 else ""

    if tip_key == "nice":
        advice = random.choice(SCENES[scene_key])
    else:
        advice = random.choice(TIPS[tip_key])

    parts = [greeting]
    if special:
        parts.append(special)
    parts += [
        random.choice(OPENERS).format(weekday=weekday, datestr=datestr),
        random.choice(WEEKDAY_LINES[today.weekday()]),
        random.choice(TODAY_LINES).format(city=CITY, desc=desc),
        random.choice(TEMP_LINES).format(hi=hi, lo=lo),
        random.choice(FEEL_LINES[feel_key]),
        rain_line,
        wind_line,
        data_question(d, today),
        advice,
        random.choice(TOMORROW_LINES).format(
            tmr_desc=tmr_desc, tmr_hi=tmr_hi
        ),
        weekly_theme(today),
        random.choice(CLOSERS),
    ]
    # 过滤空字符串（比如风不够大时 wind_line 是空的）
    return " ".join(p for p in parts if p)


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
    # 阀值向前挪，和生成时刻对齐：
    # 05:00 跑→morning，11:xx 跑→afternoon，17:00 跑→evening
    if already_done_today(today) and not FORCE:
        print("Today's episode already exists. Nothing to do.")
        return

    greeting = pick(GREETINGS, today)
    slug = today.isoformat()

    # 文件名永远不变：Yoto 手里的 feed 可能是几小时前的，
    # 但它播放时会去这个固定地址取，取到的就是刚覆盖进去的最新音频
    mp3_path = AUDIO_DIR / AUDIO_NAME

    data = fetch_weather()
    text = build_script(data, today, greeting)
    print("SCRIPT:", text)

    asyncio.run(synthesize(text, mp3_path))

    # 清掉旧方案遗留的带日期文件（之后目录里只会有 latest.mp3）
    for old in AUDIO_DIR.glob("*.mp3"):
        if old.name != AUDIO_NAME:
            old.unlink()

    episodes = [{
        # 标题和 guid 也保持不变：Yoto 永远认为这是同一集，
        # 不会堆积多集，也不会因为标题过期而显示错的日期
        "title": "Daily Weather",
        "text": text,
        "pubdate": format_datetime(now),
        "guid": "daily-weather",
        "url": f"{BASE_URL}/audio/{AUDIO_NAME}",
        "size": mp3_path.stat().st_size,
        "duration": "00:01:20",
    }]

    # 记下本次生成的时刻和文稿，方便你事后核对是哪一版
    (OUT / "episodes.json").write_text(
        json.dumps(
            {"date": slug, "generated": now.isoformat(), "text": text},
            ensure_ascii=False, indent=2,
        )
    )
    (OUT / "feed.xml").write_text(build_rss(episodes), encoding="utf-8")
    print(f"Done. Feed: {BASE_URL}/feed.xml")


if __name__ == "__main__":
    main()
