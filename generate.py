#!/usr/bin/env python3
"""每日英文天气播报：取数 → 生成文稿 → TTS → 更新 RSS"""

import asyncio
import datetime
import json
import os
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
KEEP_EPISODES = 7           # RSS 里保留最近几集
OWNER_EMAIL = "youyoutime@sohu.com"   # 播客联系邮箱，部分客户端会校验
BASE_URL = os.environ["BASE_URL"].rstrip("/")
# =================================

TZ = ZoneInfo(TZNAME)
OUT = Path("docs")
AUDIO_DIR = OUT / "audio"
AUDIO_DIR.mkdir(parents=True, exist_ok=True)

# WMO 天气代码 → 英文描述
WMO = {
    0: "clear and sunny",
    1: "mostly sunny",
    2: "partly cloudy",
    3: "cloudy",
    45: "foggy", 48: "foggy",
    51: "drizzly", 53: "drizzly", 55: "drizzly",
    61: "rainy", 63: "rainy", 65: "very rainy",
    66: "icy and rainy", 67: "icy and rainy",
    71: "snowy", 73: "snowy", 75: "very snowy", 77: "snowy",
    80: "showery", 81: "showery", 82: "stormy with heavy showers",
    85: "snowy", 86: "snowy",
    95: "thundery", 96: "thundery with hail", 99: "thundery with hail",
}


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


def build_script(data, today):
    d = data["daily"]
    code = d["weather_code"][0]
    desc = WMO.get(code, "changeable")
    hi = round(d["temperature_2m_max"][0])
    lo = round(d["temperature_2m_min"][0])
    pop = d["precipitation_probability_max"][0] or 0
    wind = round(d["wind_speed_10m_max"][0])

    tmr_desc = WMO.get(d["weather_code"][1], "changeable")
    tmr_hi = round(d["temperature_2m_max"][1])

    weekday = today.strftime("%A")
    datestr = today.strftime("%B %-d")  # Windows 上用 %#d

    # 穿衣 / 携带物建议
    if pop >= 50:
        tip = "Don't forget your umbrella today!"
    elif hi >= 30:
        tip = "It's going to be hot. Drink lots of water and wear a hat!"
    elif lo <= 5:
        tip = "It's very cold. Please wear your warm coat, hat and gloves!"
    elif wind >= 30:
        tip = "It's quite windy today, so hold on to your hat!"
    else:
        tip = "It's a lovely day. Have fun outside!"

    return (
        f"Good morning! Here is your weather report for {weekday}, {datestr}. "
        f"Today in {CITY}, it will be {desc}. "
        f"The high will be {hi} degrees, and the low will be {lo} degrees. "
        f"There is a {pop} percent chance of rain. "
        f"{tip} "
        f"And here's a look ahead: tomorrow will be {tmr_desc}, "
        f"with a high of {tmr_hi} degrees. "
        f"That's your weather. Have a wonderful day!"
    )


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
    slug = today.isoformat()
    mp3_path = AUDIO_DIR / f"{slug}.mp3"

    data = fetch_weather()
    text = build_script(data, today)
    print("SCRIPT:", text)

    asyncio.run(synthesize(text, mp3_path))

    # 只保留最近 N 天的音频
    all_mp3 = sorted(AUDIO_DIR.glob("*.mp3"), reverse=True)
    for old in all_mp3[KEEP_EPISODES:]:
        old.unlink()

    # 读取历史文稿（用于 RSS 描述）
    meta_file = OUT / "episodes.json"
    meta = json.loads(meta_file.read_text()) if meta_file.exists() else {}
    meta[slug] = text

    episodes = []
    for p in sorted(AUDIO_DIR.glob("*.mp3"), reverse=True):
        day = p.stem
        dt = datetime.datetime.fromisoformat(day).replace(
            hour=6, minute=0, tzinfo=TZ
        )
        episodes.append({
            "title": f"Weather for {dt.strftime('%A, %B %d')}",
            "text": meta.get(day, "Daily weather report."),
            "pubdate": format_datetime(dt),
            "guid": f"weather-{day}",
            "url": f"{BASE_URL}/audio/{p.name}",
            "size": p.stat().st_size,
            "duration": "00:00:45",
        })

    meta = {k: v for k, v in meta.items()
            if k in {p.stem for p in AUDIO_DIR.glob('*.mp3')}}
    meta_file.write_text(json.dumps(meta, ensure_ascii=False, indent=2))
    (OUT / "feed.xml").write_text(build_rss(episodes), encoding="utf-8")
    print(f"Done. Feed: {BASE_URL}/feed.xml")


if __name__ == "__main__":
    main()
