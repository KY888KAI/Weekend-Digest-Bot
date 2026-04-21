#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
週末國際大事回顧 自動化流程
執行時間：週日 18:00 / 台灣連假最後一天 18:00
"""

import asyncio
import json
import logging
import os
import re
import sys
import time
import urllib.request
from datetime import datetime, timedelta
from pathlib import Path

import yfinance as yf

from playwright.async_api import async_playwright, Page, BrowserContext, TimeoutError as PwTimeout

# ─────────────────────────── 路徑 ───────────────────────────
BASE_DIR       = Path(__file__).parent
LOG_FILE       = BASE_DIR / "run.log"
STAGE1_CACHE_SUNDAY  = BASE_DIR / "stage1_cache_sunday.json"   # 快取：週日測試模式
STAGE1_CACHE_HOLIDAY = BASE_DIR / "stage1_cache_holiday.json"  # 快取：連假測試模式
DEBUG_DIR      = BASE_DIR / "debug_snapshots"                  # 自動截圖 + HTML 存放
GEMINI_COOKIE  = BASE_DIR / "gemini_cookies.json"   # 個人 Google 帳號
CMONEY_COOKIE  = BASE_DIR / "cmoney_cookies.json"   # 公司帳號（同學會）
PUSH_COOKIE    = BASE_DIR / "push_cookies.json"     # 公司帳號（推播後台，獨立帳號）

# ─────────────────────────── 常數 ───────────────────────────
GEMINI_URL      = "https://gemini.google.com/app?hl=zh-TW"
CMONEY_LOGIN    = (
    "https://auth.cmoney.tw/identity/account/login"
    "?returnUrl=%2Fauthorize%2Fcallback%3Fclient_id%3Dcmstockcommunity-web"
    "%26redirect_uri%3Dhttps%253A%252F%252Fwww.cmoney.tw%252Fforum%252Flogin"
    "%26response_type%3Dcode%26scope%3Dopenid%2520nickname"
    "%26state%3Ded6b4765423d436c8fd496cff5315732"
    "%26code_challenge%3DP0sWLXAHsmfqTnYhhR24hXVHv8UpoBQsLcOzU57reFw"
    "%26code_challenge_method%3DS256"
)
CMONEY_CLUB     = "https://www.cmoney.tw/forum/club/2874"
PUSH_URL        = "https://cmsv.cmoney.tw/push-notification/IntegratePush/list?menu=menu-tab-0&application=45"

CMONEY_EMAIL    = "etffast@gmail.com"
CMONEY_PASSWORD = "Cmoney987654321"

PUSH_EMAIL      = "cmchipk@cmoney.com.tw"
PUSH_PASSWORD   = "987654321"

DEEPLINK_TPL    = "https://www.cmoney.tw/app?page=content&subpage=0&nestedpage=0&articleid={}"
PUSH_TITLE      = "👆週末美股大事一次看！"
PUSH_TIME_STR   = "20:25"    # HH:MM，當天日期自動填入

# GEMINI_PROMPT_1 有兩個版本，依模式選用
GEMINI_PROMPT_1_SUNDAY = (
    "【重要指示】以下數據已100%確認，撰文時必須原封不動照用，"
    "包含日期、百分比、漲跌方向，絕對不可自行搜尋、推算或更改任何數字與日期：\n\n"
    "{market_data_block}\n\n"
    "根據以上確認數據，統整這幾天的重點事件與市場情緒，以下面格式範例整理，"
)
GEMINI_PROMPT_1_HOLIDAY = (
    "【重要指示】以下是連假期間美股各指數的總漲跌數據（連假前最後交易日收盤至連假結尾交易日收盤的整段漲跌），"
    "已100%確認，撰文時必須原封不動照用，包含日期區間、百分比、漲跌方向，"
    "絕對不可自行搜尋、推算、拆分成逐日或更改任何數字與日期：\n\n"
    "{market_data_block}\n\n"
    "根據以上確認數據，統整連假期間的重點事件與市場情緒，以下面格式範例整理，"
)
# 共用的後半段 prompt
GEMINI_PROMPT_1_TEMPLATE = (
    "{intro}"
    "【發文日期】今天是 {publish_date}，文章標題首句日期請使用此日期。\n"
    "首句標題字數限制30字以內，且句尾必須包含「！」驚嘆號。\n\n"
    "【排版與格式絕對要求】（非常重要，請嚴格遵守）：\n"
    "1. 絕對禁止使用 Markdown 標題語法（如 # 或 ##）。\n"
    "2. 段落之間必須使用單獨一行的「-」作為分隔線。\n"
    "3. 必須保留原本的換行與空行，不可自行將文字擠成一團。\n"
    "4. 請勿在開頭加入「標題：」或「以下是文章：」等冗言贅字，直接輸出標題與內文即可。\n\n"
    "以下為純格式範例（日期與數字皆為虛構，僅供參考文章結構，請嚴格模仿此排版）：\n\n"
    "2026/3/1 通膨高預期、以伊爆衝突，連假事件一次看！\n"
    "幫大家快速複習這幾天美股表現與國際大事！\n"
    "-\n"
    "週五美股主要指數表現（2/27）：\n"
    "📍標普500指數：下跌0.43%\n"
    "📍道瓊工業指數：下跌1.05%\n"
    "📍那斯達克綜合指數：下跌0.92%\n"
    "📍費城半導體指數：下跌1.21%\n"
    "-\n"
    "連假期間重點事件與市場影響：\n"
    "● 2/27 經濟數據 + AI疑慮雙重壓力\n"
    "➤ 1月PPI通膨大幅超預期（整體+0.5%、核心+0.8%），降息希望再降溫\n"
    "➤ 資金明顯輪動至公用事業等防禦型類股\n"
    "● 2/28～3/1 美以對伊朗發動大規模軍事攻擊\n"
    "➤ 伊朗立即報復，發射導彈攻擊以色列本土與美國在中東軍事基地\n"
    "-\n"
    "整體市場情緒明顯避險為主。連假前本來就有通膨+AI疑慮，週末再爆發中東衝突，{week_ref}波動可能加大！\n\n"
    "文案修飾規則：\n"
    "1. 名詞避免使用中國常用說法，例如：❌特朗普 ⭕川普　❌霍爾木茲海峽 ⭕荷姆茲海峽\n"
    "2. 避免針對未來走勢使用過度肯定句，例如：❌短期內市場波動將持續居高不下！⭕{week_ref}波動可能仍劇烈！\n"
)

# ─── 市場數據抓取 ─────────────────────────────────────────────
_INDICES = {
    "標普500指數":    "^GSPC",
    "道瓊工業指數":   "^DJI",
    "那斯達克綜合指數": "^IXIC",
    "費城半導體指數":  "^SOX",
}

def _fetch_market_data(run_mode: str = "sunday", holiday_start=None, holiday_end=None) -> str:
    if run_mode == "sunday":
        return _fetch_single_day()
    else:
        return _fetch_holiday_days(holiday_start, holiday_end)

def _fetch_single_day() -> str:
    ts("抓取週五收盤數據...")
    weekday_map = {0:"週一",1:"週二",2:"週三",3:"週四",4:"週五",5:"週六",6:"週日"}
    lines = []
    trade_dt = None

    for name, sym in _INDICES.items():
        try:
            hist = yf.Ticker(sym).history(period="5d")
            if len(hist) < 2:
                lines.append(f"📍{name}：（資料暫無法取得）")
                continue
            pct = (hist["Close"].iloc[-1] - hist["Close"].iloc[-2]) / hist["Close"].iloc[-2] * 100
            if trade_dt is None:
                trade_dt = hist.index[-1].to_pydatetime()
            d = "上漲" if pct >= 0 else "下跌"
            lines.append(f"📍{name}：{d}{abs(pct):.2f}%")
            ts(f"  {name}：{d}{abs(pct):.2f}%")
        except Exception as e:
            ts(f"  {name} 抓取失敗：{e}")
            lines.append(f"📍{name}：（資料暫無法取得）")

    if trade_dt:
        wd = weekday_map[trade_dt.weekday()]
        ds = trade_dt.strftime("%-m/%-d")
        ts(f"  數據日期：{ds}（{wd}）")
    else:
        wd, ds = "週五", "N/A"

    header = f"{wd}美股主要指數表現（{ds}）："
    return header + "\n" + "\n".join(lines)

def _fetch_holiday_days(holiday_start, holiday_end=None) -> str:
    ts("抓取連假期間美股歷史數據（需稍候 10-20 秒）...")
    from datetime import date as date_type
    weekday_map = {0:"週一",1:"週二",2:"週三",3:"週四",4:"週五",5:"週六",6:"週日"}

    if holiday_start is None:
        holiday_start = (datetime.now() - timedelta(days=5)).date()
    if not isinstance(holiday_start, date_type):
        holiday_start = holiday_start.date()

    today = holiday_end if holiday_end else datetime.now().date()
    if not isinstance(today, date_type):
        today = today.date()

    trading_days = []
    d = holiday_start
    while d <= today:
        if d.weekday() < 5:
            trading_days.append(d)
        d += timedelta(days=1)

    if not trading_days:
        ts("警告：連假期間無美股交易日，改用單日模式")
        return _fetch_single_day()

    last_day = trading_days[-1]
    pre_holiday = holiday_start - timedelta(days=1)
    while pre_holiday.weekday() >= 5:
        pre_holiday -= timedelta(days=1)

    ts(f"連假期間：{trading_days[0]} ~ {last_day}，基準交易日：{pre_holiday}")
    ts("向 Yahoo Finance 抓取歷史數據（約需 10-20 秒，請稍候）...")

    fetch_start = pre_holiday - timedelta(days=3)
    fetch_end   = last_day   + timedelta(days=1)

    holiday_start_ts = datetime.combine(holiday_start, datetime.min.time())
    last_day_ts      = datetime.combine(last_day, datetime.min.time()) + timedelta(hours=23)

    lines = []
    actual_last_date = None
    for name, sym in _INDICES.items():
        try:
            hist = yf.Ticker(sym).history(start=str(fetch_start), end=str(fetch_end))
            if hist.index.tz is not None:
                hist.index = hist.index.tz_localize(None)

            pre_rows = hist[hist.index < holiday_start_ts]
            last_rows = hist[(hist.index >= holiday_start_ts) & (hist.index <= last_day_ts)]

            if len(pre_rows) == 0 or len(last_rows) == 0:
                ts(f"  {name}：資料不足（前：{len(pre_rows)}筆，後：{len(last_rows)}筆）")
                lines.append(f"📍{name}：（資料暫無法取得）")
                continue

            pre_close  = pre_rows["Close"].iloc[-1]
            last_close = last_rows["Close"].iloc[-1]
            real_last  = last_rows.index[-1].date() if hasattr(last_rows.index[-1], 'date') else last_rows.index[-1].to_pydatetime().date()

            if actual_last_date is None:
                actual_last_date = real_last

            pct = (last_close - pre_close) / pre_close * 100
            direction = "上漲" if pct >= 0 else "下跌"
            lines.append(f"📍{name}：{direction}{abs(pct):.2f}%")
            ts(f"  {name}：{direction}{abs(pct):.2f}%（{pre_holiday} {pre_close:.2f} → {real_last} {last_close:.2f}）")
        except Exception as e:
            ts(f"  {name} 抓取失敗：{e}")
            lines.append(f"📍{name}：（資料暫無法取得）")

    first_ds = trading_days[0].strftime("%-m/%-d") if trading_days else holiday_start.strftime("%-m/%-d")
    real_last_date = actual_last_date or last_day
    last_ds  = real_last_date.strftime("%-m/%-d")
    date_range = f"{first_ds}～{last_ds}"

    header = f"連假期間美股主要指數總表現（{date_range}）："
    return header + "\n" + "\n".join(lines)


GEMINI_PROMPT_2 = "擷取貼文重點摘要，提供能吸引用戶點擊的app推播文案，字數上限20字內"


# ─────────────────────────── 日誌 ───────────────────────────
def _setup_log():
    fmt = "%(asctime)s [%(levelname)s] %(message)s"
    logging.basicConfig(
        level=logging.INFO,
        format=fmt,
        datefmt="%Y-%m-%d %H:%M:%S",
        handlers=[
            logging.FileHandler(LOG_FILE, encoding="utf-8"),
            logging.StreamHandler(sys.stdout),
        ],
    )

log = logging.getLogger(__name__)
ts  = log.info


# ═══════════════════════════════════════════════════════════
#  台灣假日判斷
# ═══════════════════════════════════════════════════════════
def _fetch_tw_calendar(year: int) -> dict:
    url = f"https://cdn.jsdelivr.net/gh/ruyut/TaiwanCalendar/data/{year}.json"
    try:
        with urllib.request.urlopen(url, timeout=10) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        return {entry["date"]: entry.get("isHoliday", False) for entry in data}
    except Exception as e:
        log.warning(f"無法取得台灣行事曆資料：{e}，改用本地判斷")
        return {}

def is_holiday(date: datetime, calendar: dict) -> bool:
    key = date.strftime("%Y%m%d")
    if key in calendar:
        return calendar[key]
    return date.weekday() >= 5

def get_run_mode(force: bool = False, test_mode: bool = False, test_holiday: bool = False):
    today    = datetime.now().date()
    now_hour = datetime.now().hour

    if force or test_mode:
        return "sunday", None, None

    if test_holiday:
        ts("抓取台灣行事曆資料（判斷最近連假）...")
        cal = {}
        for attempt in range(3):
            cal = _fetch_tw_calendar(today.year)
            cal.update(_fetch_tw_calendar(today.year - 1))
            if cal:
                break
            ts(f"  行事曆 API 失敗，重試 ({attempt+1}/3)...")
        if not cal:
            raise RuntimeError("台灣行事曆 API 無法取得，請確認網路連線後重試")

        h_start, h_end = _find_last_real_holiday(today, cal)
        if h_start is None:
            raise RuntimeError("無法在近 180 天內找到台灣連假，請確認行事曆資料是否正確")
        ts(f"測試用連假：{h_start} ~ {h_end}")
        return "holiday", h_start, h_end

    year     = today.year
    yesterday = today - timedelta(days=1)
    calendar  = _fetch_tw_calendar(year)
    if yesterday.year != year:
        calendar.update(_fetch_tw_calendar(yesterday.year))

    today_dt     = datetime.combine(today,     datetime.min.time())
    yesterday_dt = datetime.combine(yesterday, datetime.min.time())

    if today.weekday() == 6 and now_hour >= 17:
        ts(f"今天是週日 ({today})，模式：sunday")
        return "sunday", None, None

    if (now_hour < 12
            and not is_holiday(today_dt, calendar)
            and is_holiday(yesterday_dt, calendar)
            and yesterday.weekday() != 6):
        start = _find_holiday_start(yesterday, calendar)
        ts(f"昨天是連假最後一天 ({yesterday})，今早執行，連假起點：{start}，模式：holiday")
        return "holiday", start, yesterday

    if not calendar:
        log.warning("台灣行事曆 API 取得失敗，且今天非週日，無法判斷連假，略過執行")
    else:
        ts(f"今天 ({today}) 不符合執行條件，略過")
    return None, None, None

def _find_last_real_holiday(today, cal: dict):
    def _is_hol(d):
        return bool(cal.get(d.strftime("%Y%m%d"), False))

    d = today - timedelta(days=1)
    limit = today - timedelta(days=180)
    while d >= limit:
        if _is_hol(d) or d.weekday() >= 5:
            end = d
            start = d
            while start > limit:
                prev = start - timedelta(days=1)
                if _is_hol(prev) or prev.weekday() >= 5:
                    start = prev
                else:
                    break
            has_real = any(
                _is_hol(start + timedelta(days=i)) and
                (start + timedelta(days=i)).weekday() < 5
                for i in range((end - start).days + 1)
            )
            if has_real:
                return start, end
            d = start - timedelta(days=1)
            continue
        d -= timedelta(days=1)
    return None, None

def _find_holiday_start(last_day, calendar: dict = None) -> "date":
    from datetime import date as date_type
    day = last_day if isinstance(last_day, date_type) else last_day.date()
    while True:
        prev = day - timedelta(days=1)
        prev_dt = datetime.combine(prev, datetime.min.time())
        if calendar and not is_holiday(prev_dt, calendar):
            break
        if not calendar and prev.weekday() < 5:
            break
        day = prev
    return day


# ═══════════════════════════════════════════════════════════
#  MAIN
# ═══════════════════════════════════════════════════════════
async def main():
    _setup_log()

    force        = "--force"        in sys.argv
    test_mode    = "--test"         in sys.argv and "--test-holiday" not in sys.argv
    test_holiday = "--test-holiday" in sys.argv

    run_mode, holiday_start, holiday_end = get_run_mode(
        force=force, test_mode=test_mode, test_holiday=test_holiday
    )
    if run_mode is None:
        return

    if test_mode:
        ts("=" * 60)
        ts("【測試模式 - 週日】週末國際大事回顧流程（排程發文，不公開）")
        ts("=" * 60)
    elif test_holiday:
        ts("=" * 60)
        ts("【測試模式 - 連假】週末國際大事回顧流程（排程發文，不公開）")
        ts("=" * 60)
    else:
        ts("=" * 60)
        ts("週末國際大事回顧自動化流程 開始")
        ts("=" * 60)

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(
            headless=False,
            args=["--start-maximized", "--disable-blink-features=AutomationControlled"],
        )

        gemini_ctx = await browser.new_context(
            viewport=None, locale="zh-TW", timezone_id="Asia/Taipei",
        )
        if GEMINI_COOKIE.exists():
            cookies = json.loads(GEMINI_COOKIE.read_text("utf-8"))
            await gemini_ctx.add_cookies(cookies)
            ts("已載入 Gemini cookies（個人 Google 帳號）")

        cmoney_ctx = await browser.new_context(
            viewport=None, locale="zh-TW", timezone_id="Asia/Taipei",
        )
        if CMONEY_COOKIE.exists():
            cookies = json.loads(CMONEY_COOKIE.read_text("utf-8"))
            await cmoney_ctx.add_cookies(cookies)
            ts("已載入 CMoney cookies（同學會公司帳號）")

        push_ctx = await browser.new_context(
            viewport=None, locale="zh-TW", timezone_id="Asia/Taipei",
        )
        if PUSH_COOKIE.exists():
            cookies = json.loads(PUSH_COOKIE.read_text("utf-8"))
            await push_ctx.add_cookies(cookies)
            ts("已載入推播後台 cookies（推播公司帳號）")

        try:
            cache_file = STAGE1_CACHE_HOLIDAY if test_holiday else STAGE1_CACHE_SUNDAY
            cache_used = False
            if (test_mode or test_holiday) and cache_file.exists():
                cache = json.loads(cache_file.read_text("utf-8"))
                title, body, push_text = cache["title"], cache["body"], cache["push_text"]
                ts(f"【快取】重用上次 Gemini 產出（{cache_file.name}，標題：{title[:20]}...）")
                cache_used = True
            if not cache_used:
                title, body, push_text = await stage1_gemini(gemini_ctx, run_mode, holiday_start, holiday_end)
                if test_mode or test_holiday:
                    cache_file.write_text(
                        json.dumps({"title": title, "body": body, "push_text": push_text},
                                   ensure_ascii=False, indent=2), "utf-8"
                    )
                    ts(f"【快取】已儲存 Gemini 產出至 {cache_file.name}")

            article_id = await stage2_post(cmoney_ctx, title, body, test_mode=(test_mode or test_holiday))

            deeplink = DEEPLINK_TPL.format(article_id)
            await stage3_push(push_ctx, push_text, deeplink)

            if test_mode or test_holiday:
                mode_label = "週日" if test_mode else "連假"
                ts("=" * 60)
                ts(f"【測試模式 - {mode_label}】全流程完成 ✓")
                ts(f"  文章標題  : {title}")
                ts(f"  排程文章ID: {article_id}（可手動刪除）")
                ts(f"  推播文案  : {push_text}")
                ts(f"  Deeplink  : {deeplink}")
                ts("=" * 60)
            else:
                ts("=" * 60)
                ts("所有流程執行完成 ✓")
                ts(f"  文章標題 : {title}")
                ts(f"  文章ID   : {article_id}")
                ts(f"  推播文案 : {push_text}")
                ts(f"  Deeplink : {deeplink}")
                ts("=" * 60)

        except Exception as exc:
            log.exception(f"[FATAL] 流程中斷：{exc}")
            raise
        finally:
            await browser.close()


# ═══════════════════════════════════════════════════════════
#  第一階段：Gemini 文案生成
# ═══════════════════════════════════════════════════════════
async def stage1_gemini(ctx: BrowserContext, run_mode: str, holiday_start, holiday_end=None):
    ts("─── 第一階段：Gemini 文案生成 開始 ───")
    page = await ctx.new_page()

    await page.goto(GEMINI_URL, wait_until="domcontentloaded", timeout=60_000)
    await page.wait_for_timeout(3_000)

    if not GEMINI_COOKIE.exists():
        need_login = True
        ts("Gemini 首次執行，請在瀏覽器視窗中手動登入 Google 帳號...")
    else:
        need_login = not await _gemini_is_logged_in(page)
        if need_login:
            ts("Gemini cookie 已失效，請重新登入...")
        else:
            ts("  Gemini cookie 有效，已略過登入")

    if need_login:
        ts("等待登入中（最多 10 分鐘）...")
        deadline = time.time() + 600
        while time.time() < deadline:
            await page.wait_for_timeout(2_000)
            if await _gemini_is_logged_in(page):
                break
        else:
            raise RuntimeError("Gemini 登入等待逾時")
        ts("偵測到已登入，儲存 cookies...")
        await _save_cookies(ctx, GEMINI_COOKIE)

    if "gemini.google.com" not in page.url:
        await page.goto(GEMINI_URL, wait_until="domcontentloaded", timeout=60_000)
        await page.wait_for_timeout(3_000)

    ts("開啟新的 Gemini 對話...")
    await _gemini_new_chat(page)

    ts("嘗試選擇 Gemini Pro 模型...")
    await _gemini_select_pro(page)

    market_block = _fetch_market_data(run_mode, holiday_start, holiday_end)

   # 修正：連假模式下標題應使用連假最後一天的日期，而非今天
    target_dt = holiday_end if run_mode == "holiday" and holiday_end else datetime.now()
    publish_date = target_dt.strftime("%-Y/%-m/%-d")
    
    week_ref = "下週" if run_mode == "sunday" else "本週"

    if run_mode == "sunday":
        intro = GEMINI_PROMPT_1_SUNDAY.format(market_data_block=market_block)
    else:
        intro = GEMINI_PROMPT_1_HOLIDAY.format(market_data_block=market_block)
    prompt1 = GEMINI_PROMPT_1_TEMPLATE.format(
        intro=intro,
        publish_date=publish_date,
        week_ref=week_ref,
    )

    ts("送出文案生成 Prompt（含確認市場數據）...")
    await _gemini_send(page, prompt1)
    ts("等待 Gemini 生成文章（可能需要 1-2 分鐘）...")
    response1 = await _gemini_get_last_response(page)
    ts(f"取得回應 ({len(response1)} 字)")

    ts("驗證 Gemini 輸出數字是否與 yfinance 數據一致...")
    for attempt in range(3):
        errors = _verify_numbers(market_block, response1)
        if not errors:
            ts(f"  數字驗證通過 ✓（第 {attempt + 1} 次）")
            break
        ts(f"  發現 {len(errors)} 處數字錯誤（第 {attempt + 1} 次）：")
        for e in errors:
            ts(f"    ✗ {e}")
        if attempt < 2:
            ts("  送出修正 Prompt，要求 Gemini 重新輸出...")
            correction = (
                "你的文章中指數數字與確認數據不符，以下是唯一正確數據，"
                "請嚴格照用這些數字（包含日期、方向、百分比），重新輸出完整修正後的文章：\n\n"
                f"{market_block}\n\n"
                "須修正的錯誤項目：\n" +
                "\n".join(f"✗ {e}" for e in errors) +
                "\n\n"
                "【格式與排版絕對要求】：\n"
                "1. 必須保留段落間的「空行」。\n"
                "2. 指數表現與重點事件之間必須加入一行「-」作為分隔線。\n"
                "3. 請完全依照需求範例的結構輸出，不可將文字擠在一起。"
            )
            await _gemini_send(page, correction)
            ts("  等待 Gemini 修正回應...")
            response1 = await _gemini_get_last_response(page)
        else:
            ts("  [警告] 重試 2 次後數字仍有誤，請事後人工確認以下項目：")
            for e in errors:
                ts(f"    ✗ {e}")

    await _save_cookies(ctx, GEMINI_COOKIE)

    title, body = _parse_article(response1)
    ts(f"文章標題：{title}")

    ts("送出推播文案 Prompt...")
    await _gemini_send(page, GEMINI_PROMPT_2)
    ts("等待推播文案回應...")
    push_text = await _gemini_get_last_response(page)
    push_text = _clean_push_text(push_text)
    ts(f"推播文案：{push_text}")

    await page.close()
    ts("─── 第一階段完成 ───")
    return title, body, push_text


async def _gemini_is_logged_in(page: Page) -> bool:
    try:
        cookies = await page.context.cookies()
        google_auth_names = {"SAPISID", "SID", "__Secure-1PSID", "SSID"}
        for c in cookies:
            if c.get("name") in google_auth_names and "google" in c.get("domain", ""):
                return True
    except Exception:
        pass
    return False

async def _gemini_new_chat(page: Page):
    candidates = [
        'a:has-text("新的對話")',
        'button:has-text("新的對話")',
        '[aria-label*="新的對話"]',
        'a[href="/app"]',
        'button[aria-label*="New chat"]',
        '[data-test-id="new-chat-button"]',
    ]
    for sel in candidates:
        try:
            el = page.locator(sel).first
            if await el.count() > 0 and await el.is_visible():
                await el.click()
                await page.wait_for_timeout(2_000)
                ts(f"  點擊新對話按鈕 ({sel})")
                return
        except Exception:
            pass

    ts("  找不到新的對話按鈕，直接導向新對話頁面")
    await page.goto("https://gemini.google.com/app", wait_until="domcontentloaded", timeout=60_000)
    await page.wait_for_timeout(2_000)

async def _gemini_select_pro(page: Page):
    trigger_sels = [
        'button:has-text("Pro")',
        '[aria-label*="切換模型"]',
        '[aria-label*="model"]',
        'button[data-test-id*="model"]',
        '.model-selection-button',
    ]
    for sel in trigger_sels:
        try:
            els = page.locator(sel)
            cnt = await els.count()
            if cnt > 0:
                btn = els.last
                if await btn.is_visible():
                    await btn.click()
                    await page.wait_for_timeout(1_000)
                    pro_opt = page.locator(
                        'li:has-text("Pro"):not(:has-text("Gemini")), '
                        '[role="menuitem"]:has-text("Pro"):not(:has-text("Gemini")), '
                        '[role="option"]:has-text("Pro")'
                    ).last
                    if await pro_opt.count() > 0 and await pro_opt.is_visible():
                        await pro_opt.click()
                        ts("  已選擇 Pro 模型")
                        await page.wait_for_timeout(1_000)
                        return
                    await page.keyboard.press("Escape")
                    ts("  Pro 已是目前模型，無需切換")
                    return
        except Exception:
            pass
    ts("  提示：未找到模型切換按鈕，繼續使用目前模型")

async def _gemini_send(page: Page, message: str):
    input_sels = [
        'div[contenteditable="true"].ql-editor',
        'rich-textarea div[contenteditable="true"]',
        'div[contenteditable="true"][class*="input"]',
        'div[contenteditable="true"]',
        'textarea[placeholder]',
    ]
    inp = None
    for sel in input_sels:
        try:
            el = page.locator(sel).last
            if await el.count() > 0 and await el.is_visible():
                inp = el
                break
        except Exception:
            pass

    if inp is None:
        raise RuntimeError("找不到 Gemini 訊息輸入框")

    await inp.click()
    await page.wait_for_timeout(300)

    await page.evaluate(
        """(text) => {
            const el = document.activeElement;
            if (el) {
                el.focus();
                document.execCommand('selectAll');
                document.execCommand('insertText', false, text);
            }
        }""",
        message,
    )
    await page.wait_for_timeout(500)

    await page.keyboard.press("Enter")
    await page.wait_for_timeout(1_000)

async def _gemini_get_last_response(page: Page, timeout_s: int = 180) -> str:
    stop_sel = (
        'button[aria-label*="Stop"], button[aria-label*="停止"], '
        'button[aria-label*="stop generating"]'
    )

    # 擴大監控的標籤範圍，預防 Google 改名
    pre_count = await page.evaluate(
        "() => document.querySelectorAll('model-response, gemini-message, .message-content, .markdown').length"
    )

    started = False
    for _ in range(120):  # 最多等 60 秒
        has_stop = await page.locator(stop_sel).count() > 0
        cur_count = await page.evaluate(
            "() => document.querySelectorAll('model-response, gemini-message, .message-content, .markdown').length"
        )
        if has_stop or cur_count > pre_count:
            started = True
            break
        await page.wait_for_timeout(500)

    if not started:
        ts("  警告：60 秒內未偵測到 Gemini 開始生成，繼續等待...")

    # 等待 stop 按鈕消失（生成完成）
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        if await page.locator(stop_sel).count() == 0:
            break
        await page.wait_for_timeout(1_000)
    else:
        ts("  警告：等待生成逾時，嘗試擷取目前內容")

    await page.wait_for_timeout(2_000)

    # 增強版：多階層、多標籤的文字擷取邏輯
    text = await page.evaluate("""() => {
        const SKIP = new Set(['顯示思路','隱藏思路','思路','Show thinking','Hide thinking']);
        
        // 加入所有 Google 可能替換的 class 與 tag
        const candidates = document.querySelectorAll('model-response, gemini-message, .message-content, .markdown, .response-text');
        
        if (candidates.length > 0) {
            // 從最後一個（最新的回應）開始找
            for (let i = candidates.length - 1; i >= 0; i--) {
                const node = candidates[i];
                const clone = node.cloneNode(true);
                
                // 拔除擾亂視聽的思路區塊與按鈕
                clone.querySelectorAll(
                    'thought-chunk, details, summary, [class*="thought"], [class*="thinking"],' +
                    'button, [role="button"], [aria-label]'
                ).forEach(el => el.remove());
                
                const txt = clone.innerText.trim();
                // 確保抓到的不是空殼或單純的 UI 文字
                if (txt && !SKIP.has(txt) && txt.length > 10) { 
                    return txt;
                }
            }
        }
        return '';
    }""")

    if text:
        return text

    # 殺手鐧：如果真的抓不到，呼叫你寫好的除錯工具拍照存證
    try:
        await _save_debug_snapshot(page, "gemini_extract_failed")
    except Exception:
        pass
        
    raise RuntimeError("無法擷取 Gemini 回應內容（已儲存畫面截圖至 debug_snapshots 資料夾）")

async def _save_cookies(ctx: BrowserContext, cookie_file: Path):
    cookies = await ctx.cookies()
    cookie_file.write_text(json.dumps(cookies, ensure_ascii=False, indent=2), "utf-8")
    ts(f"  已儲存 cookies → {cookie_file.name}（{len(cookies)} 筆）")

def _extract_index_lines(text: str) -> list:
    result = []
    for m in re.finditer(r'📍([^：\n]+)：(上漲|下跌)([\d.]+)%', text):
        name = m.group(1).strip()
        sign = 1.0 if m.group(2) == "上漲" else -1.0
        pct  = float(m.group(3)) * sign
        result.append((name, pct))
    return result

def _verify_numbers(market_block: str, gemini_text: str, tolerance: float = 0.05) -> list:
    expected  = _extract_index_lines(market_block)
    remaining = _extract_index_lines(gemini_text)
    errors    = []

    for name, exp_pct in expected:
        matched = False
        for i, (aname, apct) in enumerate(remaining):
            if aname == name and abs(apct - exp_pct) <= tolerance:
                remaining.pop(i)
                matched = True
                break
        if not matched:
            exp_dir = "上漲" if exp_pct >= 0 else "下跌"
            errors.append(f"{name}：應為{exp_dir}{abs(exp_pct):.2f}%（未在輸出中找到正確數字）")

    return errors

def _clean_body(text: str) -> str:
    """清理 Gemini 輸出殘留的 markdown 格式（**粗體**、*斜體*、## 標題符號）"""
    lines = text.splitlines()
    cleaned = []
    for line in lines:
        # 移除粗體/斜體 markdown，保留內文
        line = re.sub(r'\*{1,3}([^*]*?)\*{1,3}', r'\1', line)
        # 移除標題符號
        line = re.sub(r'^#{1,6}\s*', '', line)
        cleaned.append(line)
    result = '\n'.join(cleaned)
    # 移除開頭殘留的孤立 ** 或 *
    result = re.sub(r'^\*+\s*\n?', '', result)
    return result.strip()


def _parse_article(text: str):
    lines = text.splitlines()
    title = ""
    body_start = 0

    for i, raw in enumerate(lines):
        line = raw.strip()
        if not line:
            continue
        if line in ("顯示思路", "隱藏思路", "思路", "Show thinking", "Hide thinking"):
            continue
            
        # 清理可能出現的贅字與 Markdown 星號
        clean_line = re.sub(r'^(\*\*|標題：|【標題】|\*)+', '', line).strip()
        
        # 兼容全形「！」與半形「!」
        if re.search(r"\d{4}/\d{1,2}/\d{1,2}", clean_line) and ("！" in clean_line or "!" in clean_line):
            cut = clean_line.find("！")
            if cut == -1:
                cut = clean_line.find("!")
            
            # 擷取驚嘆號為止作為標題，並清除剩餘的 Markdown
            title = re.sub(r'\*+', '', clean_line[:cut+1]).strip()
            body_start = i + 1   # body 從下一行開始
            break
            
        if i == 0 or all(not l.strip() for l in lines[:i]):
            title = re.sub(r'\*+', '', clean_line).strip()
            body_start = i + 1
            break

    body = _clean_body("\n".join(lines[body_start:]).strip()) if body_start > 0 else _clean_body(text.strip())
    if not title:
        title = lines[0].strip() if lines else ""
    return title, body

def _clean_push_text(raw: str) -> str:
    for line in raw.splitlines():
        line = line.strip().strip('"').strip("「」""''")
        if line:
            return line[:20]
    return raw.strip()[:20]


# ═══════════════════════════════════════════════════════════
#  第二階段：CMoney 同學會發文
# ═══════════════════════════════════════════════════════════
CMONEY_USER_PAGE = "https://www.cmoney.tw/forum/user/10331995"
STOCK_TAG        = "加權指數"   # 發文預設標記指數


async def _set_stock_tag(page: Page, tag_query: str = STOCK_TAG):
    """在發文 modal 中設定股票/指數標記（加權指數）"""
    ts(f"設定股票標記：{tag_query}")

    # 最優先：點擊「熱門標記」按鈕（modalTags__btn--popular）
    popular_clicked = await page.evaluate("""(query) => {
        const isVis = el => { const r = el.getBoundingClientRect(); return r.width > 0 && r.height > 0; };
        const btns = Array.from(document.querySelectorAll('.modalTags__btn--popular'));
        const btn = btns.find(el => isVis(el) && el.innerText && el.innerText.trim().includes(query));
        if (btn) { btn.click(); return btn.innerText.trim().slice(0, 30); }
        return null;
    }""", tag_query)
    if popular_clicked:
        ts(f"  已點擊熱門標記按鈕：{popular_clicked}")
        await page.wait_for_timeout(500)
        return

    # 次優先：Vue state 直接注入
    injected = await page.evaluate("""(query) => {
        for (const el of document.querySelectorAll('*')) {
            const v = el.__vue__;
            if (!v || !v.$data) continue;
            const d = v.$data;
            for (const key of ['stockSearchText', 'stockKeyword', 'searchStockText', 'stockSearch']) {
                if (key in d) {
                    d[key] = query;
                    try { v.$emit('input', query); } catch(e) {}
                    return 'vue:' + key;
                }
            }
        }
        return null;
    }""", tag_query)

    if injected:
        ts(f"  Vue注入：{injected}，等待搜尋結果...")
        await page.wait_for_timeout(1000)
        clicked = await page.evaluate("""(query) => {
            const isVis = el => { const r = el.getBoundingClientRect(); return r.width > 0 && r.height > 0; };
            for (const sel of ['li', '.cm-dropdown__item', '[role="option"]']) {
                const found = Array.from(document.querySelectorAll(sel))
                    .find(el => isVis(el) && el.innerText && el.innerText.includes(query));
                if (found) { found.click(); return found.innerText.trim().slice(0, 30); }
            }
            return null;
        }""", tag_query)
        if clicked:
            ts(f"  已選擇標記：{clicked}")
            await page.wait_for_timeout(500)
            return

    # Fallback：DOM 輸入框搜尋
    tag_input_sels = [
        'input[placeholder*="股票"]',
        'input[placeholder*="標記"]',
        'input[placeholder*="搜尋股票"]',
        '.messageModal__searchStock input',
        '.stockSearch input',
    ]
    for sel in tag_input_sels:
        try:
            el = page.locator(sel).first
            if await el.count() > 0 and await el.is_visible():
                await el.fill(tag_query)
                await page.wait_for_timeout(1000)
                for opt_sel in [f'li:has-text("{tag_query}")', '.cm-dropdown__item', '[role="option"]']:
                    try:
                        opt_el = page.locator(opt_sel).first
                        if await opt_el.count() > 0 and await opt_el.is_visible():
                            text = await opt_el.inner_text()
                            await opt_el.click()
                            ts(f"  已選擇標記：{text[:30]}")
                            await page.wait_for_timeout(500)
                            return
                    except Exception:
                        pass
                ts(f"  輸入了搜尋字但找不到下拉選項")
                return
        except Exception:
            pass

    # 最終 Fallback：點擊「標個股」按鈕 → 在 modalSearch__input 搜尋框輸入 → Enter
    ts("  嘗試透過「標個股」按鈕開啟搜尋介面...")
    tag_btn_clicked = await page.evaluate("""() => {
        const isVis = el => { const r = el.getBoundingClientRect(); return r.width > 0 && r.height > 0; };
        const btns = Array.from(document.querySelectorAll('.modalAttach__btn'));
        const btn = btns.find(el => isVis(el) && el.innerText && el.innerText.includes('標個股'));
        if (btn) { btn.click(); return true; }
        return false;
    }""")
    if tag_btn_clicked:
        await page.wait_for_timeout(800)
        search_input = page.locator('input.modalSearch__input[type="search"]').first
        if await search_input.count() > 0 and await search_input.is_visible():
            await search_input.fill(tag_query)
            await page.wait_for_timeout(600)
            await search_input.press("Enter")
            await page.wait_for_timeout(800)
            ts(f"  已透過「標個股」搜尋框標記：{tag_query}")
            return
        else:
            ts("  「標個股」按鈕已點擊但找不到搜尋輸入框")
    else:
        ts("  未找到「標個股」按鈕")

    ts("  未找到股票標記輸入框，跳過")


async def _delete_attached_images(page: Page):
    """刪除發文 modal 中所有已附加的圖片（標記個股後自帶的圖片）"""
    deleted = await page.evaluate("""() => {
        const isVis = el => { const r = el.getBoundingClientRect(); return r.width > 0 && r.height > 0; };
        const btns = Array.from(document.querySelectorAll('button.modalImages__delete'));
        const visible = btns.filter(isVis);
        visible.forEach(btn => btn.click());
        return visible.length;
    }""")
    if deleted:
        ts(f"  已刪除 {deleted} 張附帶圖片")
        await page.wait_for_timeout(400)


async def _save_debug_snapshot(page: Page, label: str):
    """自動儲存截圖與 HTML，供事後分析未知彈窗"""
    try:
        DEBUG_DIR.mkdir(exist_ok=True)
        ts_str = datetime.now().strftime("%Y%m%d_%H%M%S")
        shot_path = DEBUG_DIR / f"{ts_str}_{label}.png"
        html_path = DEBUG_DIR / f"{ts_str}_{label}.html"
        await page.screenshot(path=str(shot_path), full_page=True)
        html = await page.content()
        html_path.write_text(html, encoding="utf-8")
        ts(f"  [DEBUG] 快照已儲存 → debug_snapshots/{shot_path.name}")
        ts(f"  [DEBUG] HTML  已儲存 → debug_snapshots/{html_path.name}")
    except Exception as e:
        ts(f"  [DEBUG] 快照儲存失敗：{e}")


async def _dismiss_any_popup(page: Page) -> bool:
    """
    嘗試關閉當前畫面上任何可見的彈窗/廣告/系統通知。
    返回 True 代表有成功關閉某個元素。
    """
    try:
        closed = await page.evaluate("""() => {
            const DISMISS_TEXTS = [
                '下次再說','稍後再說','稍後','略過','跳過','不要','否',
                '關閉','知道了','確定','取消','不用了','先不要','以後再說',
                'Close','×','✕','X'
            ];
            const CLOSE_SELECTORS = [
                '.cm-modal__close','.dialog__close','.modal-close-btn','.ad-banner__close',
                'button[aria-label="Close"]','button[aria-label="關閉"]',
                '.el-dialog__headerbtn','.el-message-box__headerbtn',
                '.el-notification__closeBtn','.el-alert__closebtn',
                '.cm-blackbar__sidebarClose','[data-dismiss="modal"]',
                '[aria-label*="dismiss"]','.popup-close','.overlay-close','.toast-close',
            ];
            const isVisible = el => {
                if (!el || el.disabled) return false;
                const r = el.getBoundingClientRect();
                if (r.width === 0 && r.height === 0) return false;
                const s = window.getComputedStyle(el);
                return s.display !== 'none' && s.visibility !== 'hidden' && parseFloat(s.opacity) > 0;
            };
            for (const sel of CLOSE_SELECTORS) {
                for (const el of Array.from(document.querySelectorAll(sel))) {
                    if (isVisible(el)) { el.click(); return true; }
                }
            }
            const clickables = Array.from(document.querySelectorAll(
                'button, a[role="button"], [role="button"], .btn, .cm-btn'
            ));
            for (const txt of DISMISS_TEXTS) {
                const btn = clickables.find(b => isVisible(b) && b.innerText && b.innerText.trim() === txt);
                if (btn) { btn.click(); return true; }
            }
            return false;
        }""")
        if closed:
            await page.wait_for_timeout(400)
            return True
    except Exception:
        pass
    return False


async def _close_annoying_modals(page: Page, max_rounds: int = 3):
    """發文前清場：連續多輪嘗試關閉所有干擾彈窗"""
    ts("正在檢查是否有干擾彈窗...")
    closed_total = 0
    for _ in range(max_rounds):
        if await _dismiss_any_popup(page):
            closed_total += 1
            ts(f"  已自動關閉彈窗（共 {closed_total} 次）")
        else:
            break
    if closed_total == 0:
        ts("  目前畫面無明顯干擾彈窗。")


async def stage2_post(ctx: BrowserContext, title: str, body: str, test_mode: bool = False) -> str:
    mode_label = "【排程發文-測試】" if test_mode else "【立即發文】"
    ts(f"─── 第二階段：CMoney 同學會發文 開始 {mode_label} ───")
    page = await ctx.new_page()

    if not CMONEY_COOKIE.exists():
        need_login = True
        ts("CMoney 首次執行，需要手動登入公司帳號...")
    else:
        ts("驗證 CMoney cookie 是否有效...")
        test_page = await ctx.new_page()
        await test_page.goto("https://www.cmoney.tw/forum/me", wait_until="domcontentloaded", timeout=30_000)
        await test_page.wait_for_timeout(2_000)
        need_login = "login" in test_page.url.lower() or "auth" in test_page.url.lower()
        await test_page.close()
        if need_login:
            ts("CMoney cookie 已失效，需要重新登入...")
        else:
            ts("  CMoney cookie 有效，已略過登入")

    if need_login:
        ts("自動登入 CMoney 帳號...")
        await page.goto(CMONEY_LOGIN, wait_until="domcontentloaded", timeout=60_000)

        try:
            await page.wait_for_selector(
                'input[type="email"], input[name="Email"], input[id*="email" i]',
                state="visible", timeout=8_000
            )
            await page.fill('input[type="email"], input[name="Email"], input[id*="email" i]', CMONEY_EMAIL)
            await page.wait_for_timeout(300)
            await page.fill('input[type="password"], input[name="Password"], input[id*="password" i]', CMONEY_PASSWORD)
            await page.wait_for_timeout(300)
            await _click(page, [
                'button[type="submit"]',
                'input[type="submit"]',
                'button:has-text("登入")',
            ])
            ts("  已送出帳密，等待登入回應...")
        except Exception:
            ts("  未出現帳密輸入框（頁面已跳 2FA 或 SSO），繼續等待...")

        deadline = time.time() + 600
        logged_in = False
        while time.time() < deadline:
            await page.wait_for_timeout(1_000)

            try:
                chk = page.locator('input[type="checkbox"]').first
                if await chk.count() > 0 and await chk.is_visible():
                    if not await chk.is_checked():
                        await chk.check()
                        ts("  已勾選「不要再顯示此訊息」")
                    await page.wait_for_timeout(300)
                    if await _click(page, ['button:has-text("略過")', 'a:has-text("略過")']):
                        ts("  已點擊略過雙重驗證")
            except Exception:
                pass

            if "/forum/login" in page.url and "code=" in page.url:
                ts("  偵測到 OAuth callback，等待換 session...")
                for _ in range(20):
                    await page.wait_for_timeout(1_000)
                    if "login" not in page.url:
                        break
                else:
                    ts("  OAuth callback 卡住，強制導向用戶頁面驗證 session...")
                    try:
                        await page.goto(CMONEY_USER_PAGE, wait_until="domcontentloaded", timeout=30_000)
                    except Exception:
                        pass

            if "cmoney.tw/forum" in page.url and "login" not in page.url and "auth" not in page.url:
                logged_in = True
                break

        if not logged_in:
            raise RuntimeError("CMoney 自動登入失敗，請確認帳密是否正確或網站結構是否有變更")

        ts("CMoney 登入成功")
        await page.wait_for_timeout(1_000)
        await _save_cookies(ctx, CMONEY_COOKIE)

    ts("前往用戶頁面（發文入口）...")
    await page.goto(CMONEY_USER_PAGE, wait_until="domcontentloaded", timeout=60_000)
    await page.wait_for_timeout(3_000)

    # ---------- 在準備發文操作前，先呼叫關閉彈窗機制 ----------
    await _close_annoying_modals(page)

    dismiss_sels = [
        'button:has-text("下次再說")',
        'button:has-text("稍後再說")',
        'button:has-text("關閉")',
        'button:has-text("取消")',
        '[class*="dialog"] button:has-text("否")',
    ]
    for sel in dismiss_sels:
        try:
            el = page.locator(sel).first
            if await el.count() > 0 and await el.is_visible():
                await el.click()
                ts(f"  已關閉常規彈窗 ({sel})")
                await page.wait_for_timeout(1_000)
                break
        except Exception:
            pass

    ts("點擊發文按鈕...")

    async def _click_post_btn() -> bool:
        result = await page.evaluate("""() => {
            const dialog = document.querySelector('.dialog__content');
            const candidates = Array.from(document.querySelectorAll('a, button, [role="button"]'));
            const btn = candidates.find(el => {
                if (!el.innerText || el.innerText.trim() !== '發文') return false;
                // 排除 modal 內的元素
                if (dialog && dialog.contains(el)) return false;
                if (el.className && (el.className.includes('messageModal') || el.className.includes('modalAction'))) return false;
                const r = el.getBoundingClientRect();
                return r.width > 0 && r.height > 0;
            });
            if (btn) { btn.click(); return btn.tagName + ' ' + btn.className.slice(0, 50); }
            return null;
        }""")
        if result:
            ts(f"  已點擊發文按鈕：{result}")
            return True
        return False

    async def _dismiss_popup() -> bool:
        result = await page.evaluate("""() => {
            const keywords = ['下次再說', '稍後再說', '下次再说', '稍後'];
            const btns = Array.from(document.querySelectorAll('button, a'));
            for (const kw of keywords) {
                const btn = btns.find(b => {
                    if (!b.innerText || !b.innerText.trim().includes(kw)) return false;
                    const r = b.getBoundingClientRect();
                    return r.width > 0 && r.height > 0;
                });
                if (btn) { btn.click(); return btn.innerText.trim(); }
            }
            return null;
        }""")
        if result:
            ts(f"  已關閉彈窗（JS）：「{result}」")
            await page.wait_for_timeout(800)
            return True
        return False

    async def _modal_appeared() -> bool:
        try:
            return await page.evaluate("""() => {
                const isElVisible = el => {
                    const r = el.getBoundingClientRect();
                    if (r.width > 0 && r.height > 0) return true;
                    let p = el.parentElement;
                    while (p && p !== document.documentElement) {
                        if (window.getComputedStyle(p).display === 'none') return false;
                        p = p.parentElement;
                    }
                    return true;
                };
                const sels = [
                    'button.messageModal__submit',
                    'textarea[name="postTitle"]',
                    'textarea[name="inputValue"]',
                ];
                for (const sel of sels) {
                    const els = Array.from(document.querySelectorAll(sel));
                    if (els.some(isElVisible)) return true;
                }
                return false;
            }""")
        except Exception:
            return False

    await _dismiss_popup()

    modal_opened = False
    for retry in range(5):
        if await _modal_appeared():
            modal_opened = True
            ts("  發文 modal 已開啟")
            break

        await _click_post_btn()

        for _ in range(16):
            await page.wait_for_timeout(500)
            dismissed = await _dismiss_popup()
            if dismissed:
                ts("  關閉彈窗後等待 modal 重新出現...")
                await page.wait_for_timeout(1_000)
            if await _modal_appeared():
                modal_opened = True
                ts("  發文 modal 已開啟")
                break

        if modal_opened:
            break
        ts(f"  modal 未開啟，重新點發文按鈕 ({retry+1}/5)...")
        await page.wait_for_timeout(1_000)

    if not modal_opened:
        raise RuntimeError("點擊發文按鈕後 modal 未開啟，請確認是否已成功登入")
    await page.wait_for_timeout(1_500)

    # ── 輸入標題：DOM + Vue model 同步 ──
    ts(f"輸入標題：{title}")
    title_r = await page.evaluate("""(text) => {
        // 1. DOM
        const el = Array.from(document.querySelectorAll('textarea[name="postTitle"]'))
                       .find(e => e.getBoundingClientRect().width > 0);
        if (el) {
            el.value = text;
            el.dispatchEvent(new Event('input',  { bubbles: true }));
            el.dispatchEvent(new Event('change', { bubbles: true }));
        }
        // 2. Vue model（找含 postTitle 的元件直接設值）
        for (const domEl of document.querySelectorAll('*')) {
            const vue = domEl.__vue__;
            if (vue && 'postTitle' in (vue.$data || {})) {
                vue.$data.postTitle = text;
                return 'vue-set:postTitle ✓';
            }
        }
        return el ? 'dom-only' : 'not-found';
    }""", title)
    ts(f"  {title_r}")
    await page.wait_for_timeout(400)

    # ── 設定股票標記（加權指數）──
    await _set_stock_tag(page)
    await page.wait_for_timeout(400)
    await _delete_attached_images(page)

    # ── 輸入內文：native value setter + Vue model 同步 ──
    # 放在標個股／刪圖片之後，避免標記操作干擾已輸入的內文
    ts("輸入內文...")
    body_r = await page.evaluate("""(text) => {
        // 1. 找到可見的 inputValue textarea
        const el = Array.from(document.querySelectorAll('textarea[name="inputValue"]'))
                       .find(e => e.getBoundingClientRect().width > 0);
        if (el) {
            // native value setter 可繞過 Vue 的 property descriptor
            const setter = Object.getOwnPropertyDescriptor(
                window.HTMLTextAreaElement.prototype, 'value').set;
            setter.call(el, text);
            el.dispatchEvent(new Event('input',  { bubbles: true }));
            el.dispatchEvent(new Event('change', { bubbles: true }));
        }
        // 2. Vue model 直接注入
        for (const domEl of document.querySelectorAll('*')) {
            const vue = domEl.__vue__;
            if (vue && vue.$data && 'inputValue' in vue.$data) {
                vue.$data.inputValue = text;
                return 'vue-set:inputValue ✓';
            }
        }
        return el ? 'dom-only' : 'not-found';
    }""", body)
    ts(f"  內文輸入：{body_r}")
    await page.wait_for_timeout(400)

    # ── 發文模式選擇 ──────────────────────────
    if test_mode:
        ts("【測試】點擊「立即發文」下拉 → 選擇「排程發文」...")
        article_id = await _select_schedule_post(page)
        if not article_id:
            ts("  API 未取得文章 ID，改從已排程頁面抓取...")
            article_id = await _get_scheduled_article_id(page)
    else:
        ts("點擊底部「發文」送出...")
        await page.evaluate("""() => {
            const btn = document.querySelector('button.messageModal__submit');
            if (btn) btn.click();
        }""")
        ts("  已點擊發文")

        ts("等待發文完成...")
        await page.wait_for_timeout(5_000)

        article_id = _extract_id(page.url)
        if not article_id:
            for sel in ['a[href*="article"]', 'a[href*="post"]', 'time a', '.post-time a']:
                try:
                    el = page.locator(sel).last
                    if await el.count() > 0:
                        href = await el.get_attribute("href") or ""
                        article_id = _extract_id(href)
                        if article_id:
                            break
                except Exception:
                    pass

        if not article_id:
            raise RuntimeError("無法自動取得文章 ID，請至同學會個人頁面手動確認文章是否已發出")

    await page.close()
    ts(f"  文章 ID：{article_id}")
    ts("─── 第二階段完成 ───")
    return article_id


async def _select_schedule_post(page: Page) -> str:
    """
    排程發文完整流程：
      1. 展開下拉 → 選「排程發文」
      2. flatpickr 設日期 + Vue state 注入（scheduledPostDate 父層用 ms 數字、子層用字串）
      3. 等發文按鈕就緒（去除 --disable）→ 直接點擊 DOM 按鈕
      4. 處理彈窗（確認類→點確認；錯誤類→點關閉）
      5. 等 API 回應 → 回傳 articleId
    """
    import json as _json

# ─── 步驟 1：展開下拉選單 ──────────────────────────────────────
    ts("展開排程下拉選單...")
    try:
        await page.wait_for_timeout(2000)
        
        # 終極 JS 注入法：找出所有按鈕，過濾出「有寬度(真實顯示)」且包含文字的按鈕直接點擊
        clicked = await page.evaluate("""() => {
            const btns = Array.from(document.querySelectorAll('button.cm-dropdown__btn'));
            let target = btns.find(b => b.innerText && (b.innerText.includes('排程發文') || b.innerText.includes('立即發文')) && b.getBoundingClientRect().width > 0);
            
            // 如果真的沒有寬度大於0的，退而求其次點擊第一個找到的
            if (!target) {
                target = btns.find(b => b.innerText && (b.innerText.includes('排程發文') || b.innerText.includes('立即發文')));
            }
            if (target) {
                target.click();
                return true;
            }
            return false;
        }""")
        
        if not clicked:
            raise Exception("畫面上找不到任何 cm-dropdown__btn")
            
        ts("  已成功點擊下拉按鈕")
    except Exception as e:
        ts(f"  [ERROR] 找不到下拉按鈕: {e}")
        await _save_debug_snapshot(page, "error_dropdown_btn")
        raise RuntimeError("找不到下拉選單按鈕，請查看 debug_snapshots 截圖")

    await page.wait_for_timeout(1200)

    # ─── 步驟 2：點擊「排程發文」選項 ────────────────────────────
    ts("點擊排程發文選項...")
    try:
        clicked_opt = await page.evaluate("""() => {
            const opts = Array.from(document.querySelectorAll('.cm-dropdown__item, span, li'));
            // 尋找文字為排程發文，且排除掉剛剛被點擊的父按鈕
            let target = opts.find(o => 
                o.innerText && 
                o.innerText.includes('排程發文') && 
                o.getBoundingClientRect().width > 0 &&
                !o.closest('.cm-dropdown__btn')
            );
            
            if (!target) {
                target = opts.find(o => o.innerText && o.innerText.includes('排程發文') && !o.closest('.cm-dropdown__btn'));
            }
            if (target) {
                target.click();
                return true;
            }
            return false;
        }""")
        
        if not clicked_opt:
            raise Exception("畫面上找不到排程發文的下拉選項")
            
        ts("  已成功選擇「排程發文」")
    except Exception as e:
        ts(f"  [ERROR] 點擊排程選項失敗: {e}")
        await _save_debug_snapshot(page, "error_schedule_opt")
        raise RuntimeError("找不到「排程發文」選項，請查看 debug_snapshots 截圖")

    await page.wait_for_timeout(1500)
    
# ─── 步驟 3：設定「3 天後 10:00」的安全測試時間 ────────────────────
    now = datetime.now()
    future = (now + timedelta(days=3)).replace(hour=10, minute=0, second=0, microsecond=0)
    future_ms = int(future.timestamp() * 1000)
    schedule_str = future.strftime("%Y-%m-%d %H:%M")
    
    ts(f"  [測試設定] 準備設定排程時間：{schedule_str}")

    state_diag = await page.evaluate("""(args) => {
        const { ms, fmt } = args;
        
        // 1. 同步 Flatpickr 插件 (強制使用字串傳入，破解 Playwright UTC 時區偏差 8 小時的坑)
        const fpInput = document.querySelector('#addScheduleDate, input.schedulePost__datePicker');
        if (fpInput && fpInput._flatpickr) {
            fpInput._flatpickr.setDate(fmt, true, "Y-m-d H:i");
            fpInput._flatpickr.close(); // 安全關閉日曆
        }

        // 2. 暴力同步 Vue State
        let vueSynced = false;
        document.querySelectorAll('*').forEach(el => {
            const v = el.__vue__;
            if (v && v.$data) {
                if ('isSchedulePost' in v.$data) {
                    v.$data.isSchedulePost = true;
                    v.$data.scheduledPostDate = ms;
                    vueSynced = true;
                }
                if ('scheduledPostDate' in v.$data && typeof v.$data.scheduledPostDate === 'string') {
                    v.$data.scheduledPostDate = fmt;
                    if ('isLegalTime' in v.$data) v.$data.isLegalTime = true;
                    if ('isValidate' in v.$data) v.$data.isValidate = true;
                }
            }
        });
        return { vueSynced, timeSent: fmt };
    }""", {"ms": future_ms, "fmt": schedule_str})
    
    ts(f"  [Vue State] 注入結果: {state_diag}")
    await page.wait_for_timeout(1000)

    # ─── 步驟 3.5：診斷 Vue state 確認 buildSubmitApiData ────────
    diag = await page.evaluate("""() => {
        const results = [];
        document.querySelectorAll('*').forEach(el => {
            const v = el.__vue__;
            if (!v || !v.$data) return;
            const d = v.$data;
            if ('scheduledPostDate' in d || 'isSchedulePost' in d) {
                const bd = (typeof v.buildSubmitApiData === 'function')
                    ? (() => { try { return JSON.stringify(v.buildSubmitApiData()); } catch(e) { return 'ERR:'+e; } })()
                    : 'N/A';
                results.push({
                    tag: el.tagName,
                    cls: (el.className||'').slice(0,60),
                    isSchedulePost: d.isSchedulePost,
                    scheduledPostDate: d.scheduledPostDate,
                    spType: typeof d.scheduledPostDate,
                    isLegalTime: d.isLegalTime,
                    isValidate: d.isValidate,
                    hasBuild: typeof v.buildSubmitApiData === 'function',
                    buildResult: bd ? bd.slice(0,150) : null
                });
            }
        });
        return results;
    }""")
    ts(f"  [Vue診斷] 共找到 {len(diag)} 個排程相關元件：")
    for item in diag:
        ts(f"    {item['tag']} | isSchedulePost={item['isSchedulePost']} | spDate={item['scheduledPostDate']}({item['spType']}) | isLegalTime={item['isLegalTime']} | buildResult={item['buildResult']}")

    # ─── 步驟 4：等發文按鈕就緒（去除 --disable class）──────────
    ts("等待發文按鈕就緒...")
    btn_ready = False
    for _ in range(10):   # 最多 5 秒
        btn_ready = await page.evaluate("""() => {
            const btn = Array.from(document.querySelectorAll('button.messageModal__submit'))
                .find(b => { const r = b.getBoundingClientRect(); return r.width > 0 && r.height > 0; });
            return !!btn && !btn.classList.contains('messageModal__submit--disable');
        }""")
        if btn_ready:
            ts("  發文按鈕已就緒")
            break
        await page.wait_for_timeout(500)

    if not btn_ready:
        ts("  按鈕仍 disabled，強制移除 --disable class...")
        await page.evaluate("""() => {
            for (const btn of document.querySelectorAll('button.messageModal__submit')) {
                if (btn.getBoundingClientRect().width > 0) {
                    btn.classList.remove('messageModal__submit--disable');
                    btn.disabled = false;
                }
            }
        }""")
        await page.wait_for_timeout(300)

    # ─── 步驟 5：掛上 API 攔截器 ──────────────────────────────────
    collected_ids: list[str] = []

    async def _on_response(response):
        try:
            url = response.url
            if "cmoney.tw" not in url:
                return
            if not any(kw in url.lower() for kw in ["forum","post","article","schedule","community","mach"]):
                return
            status = response.status
            ct = response.headers.get("content-type", "")
            if status not in (200, 201):
                try:
                    body_bytes = await response.body()
                    ts(f"  [RESP {status}] {url.split('?')[0][-100:]} → {body_bytes[:300].decode('utf-8','replace')}")
                except Exception:
                    ts(f"  [RESP {status}] {url.split('?')[0][-80:]}")
                return
            if "json" not in ct:
                return
            body = await response.body()
            data = _json.loads(body)
            ts(f"  [RESP 200] {url.split('?')[0][-80:]} → {str(data)[:200]}")

            def find_id(obj, depth=0):
                if depth > 5: return None
                if isinstance(obj, dict):
                    for k in ("articleId","ArticleId","article_id","id","postId","PostId"):
                        v = obj.get(k)
                        if v and str(v).isdigit() and len(str(v)) >= 6:
                            return str(v)
                    for v in obj.values():
                        r = find_id(v, depth+1)
                        if r: return r
                elif isinstance(obj, list):
                    for item in obj[:3]:
                        r = find_id(item, depth+1)
                        if r: return r
                return None

            aid = find_id(data)
            if aid:
                collected_ids.append(aid)
        except Exception:
            pass

    async def _on_request(request):
        if request.method != "POST": return
        try:
            ts(f"  [REQ] POST {request.url.split('?')[0][-100:]}")
            ts(f"  [REQ] body: {(request.post_data or '')[:400]}")
        except Exception:
            pass

    async def _auto_dismiss(dialog):
        try:
            await (dialog.accept() if dialog.type in ("confirm","prompt") else dialog.dismiss())
        except Exception:
            pass

    page.on("response", _on_response)
    page.on("request",  _on_request)
    page.on("dialog",   _auto_dismiss)

    # ─── 步驟 6：點擊發文按鈕 ─────────────────────────────────────
    ts("點擊排程發文按鈕...")
    clicked = await page.evaluate("""() => {
        const btn = Array.from(document.querySelectorAll('button.messageModal__submit'))
            .find(b => { const r = b.getBoundingClientRect(); return r.width > 0 && r.height > 0; });
        if (btn) { btn.click(); return btn.className; }
        return null;
    }""")
    if not clicked:
        raise RuntimeError("找不到可見的 messageModal__submit 按鈕，無法送出")
    ts(f"  已點擊按鈕：{clicked[:80]}")

# ─── 步驟 7：處理送出後彈窗（僅在 modal 容器內操作）────────────
    ts("  [防呆機制] 啟動監測：等待並處理送出後彈窗...")

    for attempt in range(20):  # 最多 10 秒 (每 500ms)
        handled = await page.evaluate("""() => {
            const isVis = el => {
                if (!el) return false;
                const r = el.getBoundingClientRect();
                const s = window.getComputedStyle(el);
                return r.width > 0 && r.height > 0 && s.display !== 'none' && s.visibility !== 'hidden';
            };

            // ★ 關鍵：只在可見的 overlay/modal 容器內操作，不碰背景頁面按鈕
            const OVERLAY_SEL = [
                '.dialog', '.cm-modal', '[role="dialog"]', '.el-dialog',
                '.modal-content', '.modal-body', '.cm-dialog',
                '.modalPredictStock', '.modalImages', '.guide'
            ].join(', ');
            const overlays = Array.from(document.querySelectorAll(OVERLAY_SEL)).filter(isVis);

            if (overlays.length === 0) return null;

            for (const ov of overlays) {
                const btns = Array.from(ov.querySelectorAll('button, [role="button"]')).filter(b => isVis(b));
                if (btns.length === 0) continue;

                // 0. 救命機制：不能關掉「編輯中」的視窗
                const continueEdit = btns.find(b => b.innerText && b.innerText.trim() === '繼續編輯');
                if (continueEdit) { continueEdit.click(); return '繼續編輯'; }

                // 1. AI推薦股標彈窗：點「發佈文章」完成提交（最優先！）
                const publishBtn = btns.find(b =>
                    b.innerText && b.innerText.trim().includes('發佈文章') &&
                    !b.classList.contains('messageModal__submit')
                );
                if (publishBtn) { publishBtn.click(); return 'PredictStock:發佈文章'; }

                // 2. 確認類按鈕（排除主發文按鈕；不自動全部標記股票，避免誤標半導體）
                const CONFIRM_TEXTS = ['確認', '確定', '繼續', '同意'];
                for (const txt of CONFIRM_TEXTS) {
                    const b = btns.find(btn =>
                        btn.innerText && btn.innerText.trim() === txt &&
                        !btn.classList.contains('messageModal__submit')
                    );
                    if (b) { b.click(); return 'Confirm:' + txt; }
                }

                // 3. 關閉非主編輯視窗的 X 按鈕（主編輯視窗含 .messageModal__submit，不碰）
                const hasSubmit = ov.querySelector('.messageModal__submit') !== null;
                if (!hasSubmit) {
                    const xBtn = btns.find(b =>
                        b.getAttribute('aria-label') === 'Close' ||
                        b.classList.contains('dialog__closeBtn')
                    );
                    if (xBtn) { xBtn.click(); return 'XClose:' + (ov.className||'').slice(0,30); }
                }
            }
            return null;
        }""")

        if handled:
            ts(f"  [彈窗] 已處理：{handled}")
            await page.wait_for_timeout(1500)
        else:
            await page.wait_for_timeout(500)

        # API 回應已收到就提早結束
        if collected_ids:
            ts("  已收到 API 回應，結束彈窗監測")
            break

    await _save_debug_snapshot(page, "after_submit_click")

    # ─── 步驟 8：等待 API 回應（最多 10 秒）──────────────────────
    ts("  等待發文 API 回應（最多 10 秒）...")
    deadline = time.time() + 10
    while time.time() < deadline:
        if collected_ids:
            break
        await page.wait_for_timeout(300)

    page.remove_listener("response", _on_response)
    page.remove_listener("request",  _on_request)
    page.remove_listener("dialog",   _auto_dismiss)

    if collected_ids:
        ts(f"  從 API 回應取得文章 ID：{collected_ids[0]}")
        return collected_ids[0]

    # 沒有 ID → 看有沒有錯誤彈窗
    error_msg = await page.evaluate("""() => {
        const el = Array.from(document.querySelectorAll('*')).find(
            e => e.children.length <= 3 && e.innerText &&
                 (e.innerText.includes('設定失敗') || e.innerText.includes('發文失敗'))
        );
        return el ? el.innerText.trim() : null;
    }""")
    if error_msg:
        raise RuntimeError(f"排程發文失敗：{error_msg[:120]}")

    ts("  未截取到 API 文章 ID，將改用頁面抓取")
    return ""



async def _get_scheduled_article_id(page: Page) -> str:
    """
    導向個人頁面（CMONEY_USER_PAGE），點擊「已排程文章」tab，抓取最新排程文章 ID。
    注意：/scheduled-article 子路徑不存在，正確入口就是用戶主頁再點 tab。
    """
    ts(f"前往個人頁面：{CMONEY_USER_PAGE}")
    await page.goto(CMONEY_USER_PAGE, wait_until="domcontentloaded", timeout=60_000)
    await page.wait_for_timeout(3_000)

    # 點擊「已排程文章」tab
    async def _click_scheduled_tab() -> bool:
        return await page.evaluate("""() => {
            const isVisible = el => { const r = el.getBoundingClientRect(); return r.width > 0 && r.height > 0; };
            const all = Array.from(document.querySelectorAll('button, a, [role="tab"]'));
            const tab = all.find(el => isVisible(el) && el.innerText && el.innerText.trim().includes('已排程'));
            if (tab) { tab.click(); return true; }
            return false;
        }""")

    if await _click_scheduled_tab():
        ts("  已點擊「已排程文章」tab，等待內容載入...")
        await page.wait_for_timeout(3_000)
    else:
        ts("  未找到「已排程文章」tab，嘗試重整後再找...")
        await page.reload(wait_until="domcontentloaded")
        await page.wait_for_timeout(3_000)
        if await _click_scheduled_tab():
            ts("  重整後已點擊「已排程文章」tab")
            await page.wait_for_timeout(3_000)

    hrefs = await page.evaluate("""() => {
        return Array.from(document.querySelectorAll('a[href]'))
            .map(a => a.href)
            .filter(h => h.includes('articleid=') || /\\/article\\/\\d{8,}/.test(h) || /\\/post\\/\\d{8,}/.test(h));
    }""")

    for href in hrefs:
        aid = _extract_id(href)
        if aid:
            ts(f"  成功從已排程列表取得文章 ID：{aid}")
            return aid

    raise RuntimeError(
        f"無法自動取得排程文章 ID，請手動至 {CMONEY_USER_PAGE} 確認（點「已排程文章」tab）"
    )


# ═══════════════════════════════════════════════════════════
#  第三階段：推播流程
# ═══════════════════════════════════════════════════════════
async def _cm_select_by_label(page: Page, label_text: str, option_text: str) -> bool:
    """
    在推播表單中找到對應 label 的 cm-select 並選擇指定選項。
    流程：點擊下拉開啟 → 輸入篩選字 → 點擊匹配選項。
    """
    ts(f"  選擇「{label_text}」→「{option_text}」")
    try:
        # 找到該 label 所在的 formItem，再往下找 cm-select__input
        clicked_input = await page.evaluate("""([label, option]) => {
            const items = Array.from(document.querySelectorAll('.cm-formItem'));
            const item = items.find(el => el.innerText.includes(label));
            if (!item) return 'no-item';
            const selectInput = item.querySelector('.cm-select__input');
            if (!selectInput) return 'no-select';
            selectInput.click();
            return 'clicked';
        }""", [label_text, option_text])
        if clicked_input != 'clicked':
            ts(f"    找不到 cm-select（{clicked_input}），跳過")
            return False

        await page.wait_for_timeout(600)

        # 嘗試在已展開的下拉中輸入篩選字
        await page.evaluate("""(text) => {
            const inp = document.querySelector('.cm-select__defaultInput:not([placeholder]),' +
                '.cm-popup .cm-select__defaultInput, .cm-virtual-list input');
            if (inp && document.activeElement !== inp) inp.focus();
        }""", option_text)
        focused_input = page.locator(
            '.cm-popup .cm-select__defaultInput, .cm-virtual-list input, .cm-select__defaultInput'
        ).last
        if await focused_input.count() > 0:
            await focused_input.fill(option_text, force=True)
            await page.wait_for_timeout(500)

        # 點擊匹配選項（常見 class：cm-option、cm-select-dropdown__item 等）
        clicked_opt = await page.evaluate("""(text) => {
            const isVis = el => { const r = el.getBoundingClientRect(); return r.width > 0 && r.height > 0; };
            const sels = ['.cm-option', '.cm-select-dropdown__item', '[class*="option"]',
                          '.cm-virtual-list__item', 'li[class*="item"]'];
            for (const sel of sels) {
                const opt = Array.from(document.querySelectorAll(sel))
                    .find(el => isVis(el) && el.innerText && el.innerText.includes(text));
                if (opt) { opt.click(); return opt.innerText.trim().slice(0, 40); }
            }
            return null;
        }""", option_text)
        if clicked_opt:
            ts(f"    已選擇：{clicked_opt}")
            await page.wait_for_timeout(400)
            return True

        ts(f"    下拉選項中找不到「{option_text}」")
        return False
    except Exception as e:
        ts(f"    cm-select 操作失敗：{e}")
        return False


async def _set_cm_datepicker(page: Page, date_str: str, time_str: str) -> bool:
    """
    設定 cm-datePicker 的日期時間。
    優先嘗試 Vue data 注入，再試 native value setter + 多種格式。
    date_str: "YYYY/MM/DD"，time_str: "HH:MM"
    """
    full_slash  = f"{date_str} {time_str}"                          # 2026/04/21 20:25
    full_dash   = date_str.replace("/", "-") + " " + time_str       # 2026-04-21 20:25
    ts(f"  設定推播時間：{full_slash}")

    # ── 方法1：Vue data 直接注入 ────────────────────────────────
    injected = await page.evaluate("""([slash, dash]) => {
        for (const el of document.querySelectorAll('*')) {
            const v = el.__vue__;
            if (!v || !v.$data) continue;
            const d = v.$data;
            // 常見 cm-datePicker binding key
            for (const key of ['value', 'modelValue', 'dateValue', 'datetimeValue',
                                'selectedDate', 'date', 'time', 'datetime']) {
                if (key in d) {
                    d[key] = slash;
                    try { v.$emit('input', slash); v.$emit('update:modelValue', slash); } catch(e){}
                    return 'vue:' + key;
                }
            }
        }
        return null;
    }""", [full_slash, full_dash])
    if injected:
        ts(f"  推播時間 Vue 注入：{injected}")
        await page.wait_for_timeout(400)

    # ── 方法2：native value setter + 多種格式 ─────────────────
    set_result = await page.evaluate("""([slash, dash]) => {
        const sels = [
            '.cm-datePicker input.cm-input__defaultInput',
            'input[placeholder*="日期"]',
            'input[placeholder*="請選擇日期"]',
        ];
        const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value').set;
        for (const sel of sels) {
            const inp = document.querySelector(sel);
            if (!inp) continue;
            // 試 slash 格式，再試 dash 格式
            for (const val of [slash, dash]) {
                setter.call(inp, val);
                inp.dispatchEvent(new Event('input',  { bubbles: true }));
                inp.dispatchEvent(new Event('change', { bubbles: true }));
            }
            return 'dom-set:' + inp.placeholder;
        }
        return null;
    }""", [full_slash, full_dash])
    if set_result:
        ts(f"  推播時間 DOM 設定：{set_result}")
    await page.wait_for_timeout(300)
    await page.keyboard.press("Escape")   # 關閉可能展開的日曆彈窗
    await page.wait_for_timeout(300)

    if injected or set_result:
        ts("  推播時間設定完成（請人工確認欄位是否正確顯示）")
        return True

    ts("  [警告] 推播時間欄位找不到，請確認頁面結構")
    return False


async def stage3_push(ctx: BrowserContext, push_content: str, deeplink: str):
    ts("─── 第三階段：推播流程 開始 ───")
    ts(f"  推播內容 : {push_content}")
    ts(f"  Deeplink : {deeplink}")

    page = await ctx.new_page()
    await page.goto(PUSH_URL, wait_until="domcontentloaded", timeout=60_000)
    await page.wait_for_timeout(3_000)

    # ── 登入檢查 ──────────────────────────────────────────────
    if "login" in page.url.lower() or "signin" in page.url.lower():
        ts("推播後台 cookie 已失效，自動重新登入...")
        try:
            await page.wait_for_selector(
                'input[type="email"], input[name*="email" i], input[id*="email" i], input[name*="account" i]',
                state="visible", timeout=15_000
            )
            await page.fill('input[type="email"], input[name*="email" i], input[id*="email" i], input[name*="account" i]', PUSH_EMAIL)
            await page.wait_for_timeout(300)
            await page.fill('input[type="password"], input[name*="password" i], input[id*="password" i]', PUSH_PASSWORD)
            await page.wait_for_timeout(300)
            await _click(page, [
                'button[type="submit"]',
                'input[type="submit"]',
                'button:has-text("登入")',
            ])
            ts("  已送出帳密，等待登入完成...")
        except Exception as e:
            ts(f"  自動填入失敗：{e}")

        deadline = time.time() + 60
        while time.time() < deadline:
            await page.wait_for_timeout(1_000)
            if "login" not in page.url.lower() and "signin" not in page.url.lower():
                break
        else:
            raise RuntimeError("推播後台自動登入失敗，請確認帳密或網站結構是否有變更")

        await page.wait_for_timeout(2_000)
        await _save_cookies(ctx, PUSH_COOKIE)
        await page.goto(PUSH_URL, wait_until="domcontentloaded", timeout=60_000)
        await page.wait_for_timeout(3_000)
    else:
        ts("  推播後台 cookie 有效，已略過登入")

    # ── 點擊「新增」進入填寫表單頁 ────────────────────────────
    ts("點擊「新增」按鈕，進入推播表單...")
    new_btn_clicked = await _click(page, [
        'button:has-text("新增")',
        'a:has-text("新增")',
        '[class*="btn"]:has-text("新增")',
    ])
    if not new_btn_clicked:
        raise RuntimeError("找不到推播後台的「新增」按鈕，請確認頁面結構是否有變更")
    await page.wait_for_timeout(2_000)

    # 等待表單出現（以「推播標題」輸入框作為判斷依據）
    try:
        await page.wait_for_selector(
            'input[placeholder*="推播標題"], input[placeholder*="標題"]',
            state="visible", timeout=10_000
        )
        ts("  推播表單已出現")
    except Exception:
        ts("  等待表單逾時，繼續嘗試...")

    # ── 選擇推播 APP（cm-select 自定義下拉） ──────────────────
    ts("選擇推播 APP：ETF選股...")
    await _cm_select_by_label(page, "推播APP", "ETF")
    await page.wait_for_timeout(500)

    # ── 推播標題 ──────────────────────────────────────────────
    ts(f"輸入推播標題：{PUSH_TITLE}")
    await _fill(page, [
        'input[placeholder*="推播標題"]',
        'input[placeholder*="標題"]',
        'input[name*="title" i]',
        '#push-title',
    ], PUSH_TITLE)

    # ── 推播內容 ──────────────────────────────────────────────
    ts(f"輸入推播內容：{push_content}")
    await _fill(page, [
        'input[placeholder*="推播內容"]',
        'input[placeholder*="內容"]',
        'textarea[placeholder*="內容"]',
        'input[name*="content" i]',
        'textarea[name*="content" i]',
        '#push-content',
    ], push_content)

    # ── 推播時間（cm-datePicker） ──────────────────────────────
    today_slash = datetime.now().strftime("%Y/%m/%d")
    await _set_cm_datepicker(page, today_slash, PUSH_TIME_STR)

    # ── DeepLink ──────────────────────────────────────────────
    ts("輸入 Deeplink...")
    await _fill(page, [
        'input[placeholder*="deepLink"]',
        'input[placeholder*="deeplink" i]',
        'input[name*="deeplink" i]',
        '#deeplink',
    ], deeplink)

    await page.wait_for_timeout(800)

    # ── 送出（form submit button，排除「取消」） ───────────────
    ts("送出推播設定...")
    submitted = await page.evaluate("""() => {
        const isVis = el => { const r = el.getBoundingClientRect(); return r.width > 0 && r.height > 0; };
        const btn = Array.from(document.querySelectorAll('button[type="submit"], button.cm-btn-interactive'))
            .find(el => isVis(el) && el.innerText && el.innerText.trim() === '送出');
        if (btn) { btn.click(); return true; }
        return false;
    }""")
    if not submitted:
        # fallback：Playwright locator（避免誤點「取消」）
        submit_btn = page.locator('button[type="submit"]:has-text("送出"), button.cm-btn-interactive:has-text("送出")').first
        if await submit_btn.count() > 0 and await submit_btn.is_visible():
            await submit_btn.click()
            submitted = True

    if not submitted:
        raise RuntimeError("找不到推播「送出」按鈕，請確認推播後台頁面結構是否有變更")

    # ── 等待後台回應，真正偵測成功/失敗 ──────────────────────
    await page.wait_for_timeout(3_000)

    result = await page.evaluate("""() => {
        const isVis = el => { const r = el.getBoundingClientRect(); return r.width > 0 && r.height > 0; };

        // 1. 偵測表單驗證紅字錯誤
        const errorSels = [
            '.cm-formItem__error', '.cm-input--error', '[class*="is-error"]',
            '.text-danger-basic', '[class*="error-msg"]',
        ];
        const errors = [];
        for (const sel of errorSels) {
            for (const el of document.querySelectorAll(sel)) {
                if (isVis(el)) {
                    const t = el.innerText?.trim();
                    if (t) errors.push(t.slice(0, 60));
                }
            }
        }
        if (errors.length) return { status: 'error', msg: errors.join(' | ') };

        // 2. 偵測 toast / notification 成功訊息
        const successSels = [
            '.cm-message--success', '.el-message--success',
            '.cm-notification--success', '[class*="success"]',
            '.cm-message', '.el-message',
        ];
        for (const sel of successSels) {
            const el = document.querySelector(sel);
            if (el && isVis(el) && el.innerText) {
                return { status: 'success', msg: el.innerText.trim().slice(0, 60) };
            }
        }

        // 3. 偵測是否已跳回列表頁（表單消失）
        const formStillVisible = !!document.querySelector('button[type="submit"]');
        if (!formStillVisible) return { status: 'success', msg: '表單已關閉，已跳回列表頁' };

        return { status: 'unknown', msg: '無法判斷結果，請人工確認推播後台' };
    }""")

    status = result.get("status", "unknown")
    msg    = result.get("msg", "")

    if status == "error":
        await _save_debug_snapshot(page, "push_form_error")
        raise RuntimeError(f"推播表單驗證失敗：{msg}")
    elif status == "success":
        ts(f"  推播設定送出成功：{msg}")
    else:
        await _save_debug_snapshot(page, "push_unknown")
        ts(f"  [警告] 推播結果不明：{msg}（截圖已存 debug_snapshots/）")

    await page.close()
    ts("─── 第三階段完成 ───")


# ═══════════════════════════════════════════════════════════
#  工具函式
# ═══════════════════════════════════════════════════════════
async def _click(page: Page, selectors: list) -> bool:
    for sel in selectors:
        try:
            el = page.locator(sel).first
            if await el.count() > 0 and await el.is_visible():
                await el.click()
                await page.wait_for_timeout(500)
                return True
        except Exception:
            pass
    return False


async def _fill(page: Page, selectors: list, value: str) -> bool:
    for sel in selectors:
        try:
            el = page.locator(sel).first
            if await el.count() > 0:
                await el.fill(value, force=True, timeout=5_000)
                return True
        except Exception:
            pass
    return False


async def _fill_rich(page: Page, selectors: list, value: str) -> bool:
    for sel in selectors:
        try:
            el = page.locator(sel).first
            if await el.count() > 0 and await el.is_visible():
                await el.click()
                await page.wait_for_timeout(300)
                escaped = value.replace("\\", "\\\\").replace("`", "\\`")
                await page.evaluate(
                    f"""
                    const el = document.activeElement;
                    if (el) {{
                        el.focus();
                        document.execCommand('selectAll');
                        document.execCommand('insertText', false, `{escaped}`);
                    }}
                    """
                )
                return True
        except Exception:
            pass
    return False


def _extract_id(url: str) -> str:
    m = re.search(r"articleid=(\d+)", url)
    if m:
        return m.group(1)
    m = re.search(r"/(\d{8,})", url)
    if m:
        return m.group(1)
    matches = re.findall(r"\d{8,}", url)
    return matches[-1] if matches else ""


# ═══════════════════════════════════════════════════════════
if __name__ == "__main__":
    asyncio.run(main())
