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
STAGE1_CACHE   = BASE_DIR / "stage1_cache.json"     # 快取上次 Gemini 產出（測試用）
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
    "首句標題字數限制30字以內。\n"
    "以下為純格式範例（日期與數字皆為虛構，僅供參考文章結構，請勿使用這些數字）：\n\n"
    "2026/3/1 通膨高預期、以伊爆衝突，連假事件一次看！\n"
    "幫大家快速複習這幾天美股表現與國際大事！\n"
    "- 週五美股主要指數表現（2/27）：\n"
    "📍標普500指數：下跌0.43%\n"
    "📍道瓊工業指數：下跌1.05%\n"
    "📍那斯達克綜合指數：下跌0.92%\n"
    "📍費城半導體指數：下跌1.21%\n"
    "- 連假期間重點事件與市場影響：\n"
    "● 2/27 經濟數據 + AI疑慮雙重壓力\n"
    "➤ 1月PPI通膨大幅超預期（整體+0.5%、核心+0.8%），降息希望再降溫\n"
    "➤ AI對就業與商業模式的破壞性疑慮升溫（Block宣布裁員逾4,000人，近半員工），科技與金融股重挫\n"
    "➤ 資金明顯輪動至公用事業、醫療等防禦型類股\n"
    "● 2/28～3/1 美以對伊朗發動大規模軍事攻擊\n"
    "➤ 美國與以色列聯合先制空襲，成功擊殺伊朗最高領袖哈梅內伊及其多名高官\n"
    "➤ 伊朗立即報復，發射導彈攻擊以色列本土與美國在中東軍事基地\n"
    "➤ 川普親自宣布「轟炸將持續整個星期或必要時間」，衝突已進入第2天\n"
    "➤ 市場即時衝擊：油價急漲、黃金飆升、VIX恐慌指數預計大升\n"
    "- 整體市場情緒明顯避險為主。連假前本來就有通膨+AI疑慮，週末再爆發中東衝突，{week_ref}波動可能加大！\n\n"
    "文案修飾規則：\n"
    "1. 名詞避免使用中國常用說法，例如：❌特朗普 ⭕川普　❌霍爾木茲海峽 ⭕荷姆茲海峽\n"
    "2. 避免針對未來走勢使用過度肯定句，例如：❌短期內市場波動將持續居高不下！⭕{week_ref}波動可能仍劇烈！"
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

    header = f"- {wd}美股主要指數表現（{ds}）："
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

    header = f"- 連假期間美股主要指數總表現（{date_range}）："
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
            cache_used = False
            if (test_mode or test_holiday) and STAGE1_CACHE.exists():
                cache = json.loads(STAGE1_CACHE.read_text("utf-8"))
                title, body, push_text = cache["title"], cache["body"], cache["push_text"]
                ts(f"【快取】重用上次 Gemini 產出（標題：{title[:20]}...）")
                cache_used = True
            if not cache_used:
                title, body, push_text = await stage1_gemini(gemini_ctx, run_mode, holiday_start, holiday_end)
                if test_mode or test_holiday:
                    STAGE1_CACHE.write_text(
                        json.dumps({"title": title, "body": body, "push_text": push_text},
                                   ensure_ascii=False, indent=2), "utf-8"
                    )
                    ts("【快取】已儲存 Gemini 產出至 stage1_cache.json")

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

    publish_date = datetime.now().strftime("%-Y/%-m/%-d")
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
                "\n\n請重新輸出完整文章，確保所有指數數字與上面完全一致。"
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

    started = False
    for _ in range(120):
        if await page.locator(stop_sel).count() > 0:
            started = True
            break
        await page.wait_for_timeout(500)

    if not started:
        ts("  警告：60 秒內未偵測到 Gemini 開始生成，繼續等待...")

    deadline = time.time() + timeout_s
    while time.time() < deadline:
        if await page.locator(stop_sel).count() == 0:
            break
        await page.wait_for_timeout(1_000)
    else:
        ts("  警告：等待生成逾時，嘗試擷取目前內容")

    await page.wait_for_timeout(2_000)

    text = await page.evaluate("""() => {
        const allResponses = document.querySelectorAll('model-response');
        if (allResponses.length > 0) {
            const last = allResponses[allResponses.length - 1];
            const msgContent = last.querySelector(
                'message-content, .message-content, [class*="response-text"], .markdown'
            );
            if (msgContent) return msgContent.innerText.trim();
            const clone = last.cloneNode(true);
            clone.querySelectorAll(
                'thought-chunk, details, [class*="thought"], [class*="thinking"], ' +
                'button, [role="button"]'
            ).forEach(el => el.remove());
            const txt = clone.innerText.trim();
            if (txt) return txt;
        }
        const fallbackSels = [
            '[data-message-author-role="model"]',
            '.conversation-turn:last-child .response-content',
            '.response-container',
        ];
        for (const sel of fallbackSels) {
            const els = document.querySelectorAll(sel);
            if (els.length > 0) {
                const txt = els[els.length - 1].innerText.trim();
                if (txt) return txt;
            }
        }
        return '';
    }""")

    if text:
        return text

    raise RuntimeError("無法擷取 Gemini 回應內容")

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
        if re.search(r"\d{4}/\d{1,2}/\d{1,2}", line) and "！" in line:
            cut = line.index("！") + 1
            title = line[:cut].strip()
            body_start = i + 1
            remainder = line[cut:].strip()
            if remainder:
                lines[i] = remainder
            else:
                lines[i] = ""
            break
        if i == 0 or all(not l.strip() for l in lines[:i]):
            title = line
            body_start = i + 1
            lines[i] = ""
            break

    body = "\n".join(lines[body_start - 1:]).strip() if body_start > 0 else text.strip()
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
                    ts("  OAuth callback 卡住，強制導向同學會頁面驗證 session...")
                    try:
                        await page.goto(CMONEY_CLUB, wait_until="domcontentloaded", timeout=30_000)
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

    ts("前往 ETF存股計畫 社團...")
    await page.goto(CMONEY_CLUB, wait_until="domcontentloaded", timeout=60_000)
    await page.wait_for_timeout(3_000)

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
                ts(f"  已關閉彈窗 ({sel})")
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
                if (dialog && dialog.contains(el)) return false; 
                const r = el.getBoundingClientRect();
                return r.width > 0 && r.height > 0;
            });
            if (btn) { btn.click(); return btn.tagName + ' ' + btn.className.slice(0, 40); }
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
            # 絕對判定法：直接找最底下的「發文」大按鈕，只要它出現，彈窗絕對是準備好了
            return await page.evaluate("""() => {
                const btn = document.querySelector('button.messageModal__submit');
                if (!btn) return false;
                const r = btn.getBoundingClientRect();
                return r.width > 0 && r.height > 0;
            }""")
        except:
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
                ts("  發文 modal 已开启")
                break

        if modal_opened:
            break
        ts(f"  modal 未開啟，重新點發文按鈕 ({retry+1}/5)...")
        await page.wait_for_timeout(1_000)

    if not modal_opened:
        raise RuntimeError("點擊發文按鈕後 modal 未開啟，請確認是否已成功登入")
    await page.wait_for_timeout(1_500)

    # ── 輸入標題 (精準可見元件定位法) ──
    ts(f"輸入標題：{title}")
    # 使用 :visible 偽類，過濾掉所有隱藏的手機版或殘留元件，只抓畫面上真正顯示的那一個
    title_box = page.locator('textarea[name="postTitle"]:visible').first
    await title_box.fill(title, timeout=5000)
    await page.wait_for_timeout(500)

    # ── 輸入內文 (精準可見元件定位法) ──
    ts("輸入內文...")
    body_box = page.locator('textarea[name="inputValue"]:visible').first
    await body_box.fill(body, timeout=5000)
    await page.wait_for_timeout(500)

    # ── 發文模式選擇 ──────────────────────────────────────────
    if test_mode:
        ts("【測試】點擊「立即發文」下拉 → 選擇「排程發文」...")
        await _select_schedule_post(page)

        ts("等待排程發文完成...")
        await page.wait_for_timeout(5_000)

        article_id = await _get_scheduled_article_id(page)
    else:
        ts("點擊底部「發文」送出...")
        # 嚴格綁死 modal 內部的發文按鈕
        submit_btn = page.locator('.messageModal__submit').first
        await submit_btn.click(timeout=5000)
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


async def _select_schedule_post(page: Page):
    ts("展開排程下拉選單...")
    dropdown_btn = page.locator('.cm-dropdown__btn').first
    await dropdown_btn.click(timeout=5000)
    await page.wait_for_timeout(1000)

    ts("點擊排程發文選項...")
    schedule_opt = page.locator('text="排程發文"').last
    await schedule_opt.click(timeout=5000)
    await page.wait_for_timeout(1000)

    future = datetime.now() + timedelta(days=6)
    schedule_str = future.strftime("%Y-%m-%d 23:59")
    ts(f"  設定排程時間：{schedule_str}（6 天後，測試用不公開）")

    time_set = await page.evaluate(
        """(v) => {
            const hidden = document.querySelector('#addScheduleDate, input[data-input="true"]');
            if (hidden && hidden._flatpickr) {
                hidden._flatpickr.setDate(v, true);
                return true;
            }
            const el = hidden || document.querySelector('input.schedulePost__datePicker, input[name="addScheduleDate"]');
            if (el) {
                el.value = v;
                el.dispatchEvent(new Event('input', {bubbles: true}));
                el.dispatchEvent(new Event('change', {bubbles: true}));
                return true;
            }
            return false;
        }""", schedule_str
    )
    if time_set:
        ts("  排程時間設定完成（JS flatpickr）")
    else:
        ts("  警告：找不到 flatpickr 輸入欄位")

    await page.wait_for_timeout(800)

    # 嚴格綁死 modal 內部的排程送出按鈕
    submit_btn = page.locator('.messageModal__submit').first
    await submit_btn.click(timeout=5000)
    ts("  已點擊發文（排程）")


async def _get_scheduled_article_id(page: Page) -> str:
    ts("前往個人頁面取得排程文章 ID...")
    await page.goto(CMONEY_USER_PAGE, wait_until="domcontentloaded", timeout=60_000)
    await page.wait_for_timeout(3_000)

    ts("  點擊「已排程文章」...")
    clicked = await _click(page, [
        'button:has-text("已排程文章")',
        'a:has-text("已排程文章")',
        'li:has-text("已排程文章")',
        '[role="tab"]:has-text("已排程")',
    ])
    if not clicked:
        ts("  找不到「已排程文章」分頁，嘗試從頁面直接搜尋連結...")
    await page.wait_for_timeout(2_000)

    article_id = ""
    time_link_sels = [
        'a[href*="article"]:has(time)',
        'a[href*="/post/"]',
        '.post-time a',
        'time a',
        'a[href*="article"]',
    ]
    for sel in time_link_sels:
        try:
            els = page.locator(sel)
            cnt = await els.count()
            if cnt > 0:
                href = await els.first.get_attribute("href") or ""
                article_id = _extract_id(href)
                if article_id:
                    ts(f"  從連結取得文章 ID：{article_id}")
                    break
        except Exception:
            pass

    if not article_id:
        ts("  無法自動取得排程文章 ID")
        raise RuntimeError(f"無法自動取得排程文章 ID，請至 {CMONEY_USER_PAGE} → 已排程文章手動確認")

    return article_id


# ═══════════════════════════════════════════════════════════
#  第三階段：推播流程
# ═══════════════════════════════════════════════════════════
async def stage3_push(ctx: BrowserContext, push_content: str, deeplink: str):
    ts("─── 第三階段：推播流程 開始 ───")
    ts(f"  推播內容 : {push_content}")
    ts(f"  Deeplink : {deeplink}")

    page = await ctx.new_page()
    await page.goto(PUSH_URL, wait_until="domcontentloaded", timeout=60_000)
    await page.wait_for_timeout(3_000)

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
        ts("  推播後台 cookie 有有效，已略過登入")

    today = datetime.now().strftime("%Y-%m-%d")
    push_datetime = f"{today}T{PUSH_TIME_STR}"

    ts("選擇推廣 APP：194. ETF選股...")
    app_sel_opts = [
        'select[id*="app" i]',
        'select[name*="app" i]',
        'select[placeholder*="APP"]',
        'select',
    ]
    for sel in app_sel_opts:
        try:
            el = page.locator(sel).first
            if await el.count() > 0 and await el.is_visible():
                try:
                    await el.select_option(label="194. ETF選股")
                    ts(f"  已選擇 APP（by label）")
                    break
                except Exception:
                    pass
                try:
                    await el.select_option(value="194")
                    ts(f"  已選擇 APP（by value）")
                    break
                except Exception:
                    pass
        except Exception:
            pass

    await page.wait_for_timeout(1_000)

    ts(f"輸入推播標題：{PUSH_TITLE}")
    await _fill(page, [
        'input[placeholder*="標題"]',
        'input[name*="title" i]',
        '#push-title',
        'input[id*="title" i]',
    ], PUSH_TITLE)

    ts(f"輸入推播內容：{push_content}")
    await _fill(page, [
        'textarea[placeholder*="內容"]',
        'input[placeholder*="內容"]',
        'input[name*="content" i]',
        'textarea[name*="content" i]',
        '#push-content',
        'input[id*="content" i]',
        'textarea',
    ], push_content)

    ts(f"設定推播時間：{today} {PUSH_TIME_STR}")
    time_sels = [
        'input[type="datetime-local"]',
        'input[placeholder*="時間"]',
        'input[name*="time" i]',
        'input[id*="time" i]',
    ]
    for sel in time_sels:
        try:
            el = page.locator(sel).first
            if await el.count() > 0 and await el.is_visible():
                await el.fill(push_datetime)
                ts("  推播時間設定完成")
                break
        except Exception:
            pass

    ts(f"輸入 Deeplink...")
    await _fill(page, [
        'input[placeholder*="deeplink" i]',
        'input[placeholder*="Deeplink"]',
        'input[name*="deeplink" i]',
        'input[id*="deeplink" i]',
        '#deeplink',
    ], deeplink)

    await page.wait_for_timeout(1_000)

    ts("送出推播設定...")
    submitted = await _click(page, [
        'button:has-text("送出")',
        'button:has-text("確認")',
        'button:has-text("新增")',
        'button:has-text("儲存")',
        'button[type="submit"]',
    ])
    if not submitted:
        raise RuntimeError("找不到推播送出按鈕，請確認推播後台頁面結構是否有變更")

    await page.wait_for_timeout(3_000)
    ts("  推播設定送出完成")

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
