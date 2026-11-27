"""Адвент-календар «31 день до Прориву»: 31 віконце в грудні, тільки для учасниць групи.
Щоранку о T.ADVENT_HOUR агент надсилає учасницям «Відкрилось віконце N» з кнопкою, що відкриває сторінку
webapp/advent.html (кулі на ялинці). На сторінці: користь, завдання, поле для відповіді і «✓ Виконала».
Подарунки (знижки) у віконцях з T.ADVENT_GIFTS, розіграш 1 січня для тих, хто виконав T.ADVENT_RAFFLE_MIN.
Власниця: «⚙️ Керування» → «🎄 Адвент» (статистика, відповіді, файл, розіграш, текст анонсу).
Тексти: texts.py, розділ «Адвент-календар». Таблиці: advent (учасниці), advent_done (виконані віконця і відповіді)."""
import asyncio
import csv
import html
import io
import json
import logging
import random
import time
from datetime import date, datetime, timedelta
from datetime import time as dtime
from types import SimpleNamespace

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, WebAppInfo
from telegram.constants import ParseMode
from telegram.error import Forbidden

import config
import db
import texts as T
import webapp

log = logging.getLogger("advent")
HTML = ParseMode.HTML
DAYS = 31
SUB_CACHE = 600  # підписку на сторінці календаря перевіряємо не частіше, ніж раз на 10 хвилин
H = SimpleNamespace()   # функції агента (bot.py передає їх у bind), щоб не імпортувати bot.py


def esc(s):
    return html.escape(str(s or ""))


# ================== база ==================

def setup():
    with db.conn() as c:
        c.execute("""CREATE TABLE IF NOT EXISTS advent (user_id INTEGER PRIMARY KEY, joined INTEGER,
            notify INTEGER DEFAULT 1, pin_msg INTEGER DEFAULT 0)""")
        c.execute("""CREATE TABLE IF NOT EXISTS advent_done (user_id INTEGER, day INTEGER, at INTEGER,
            answer TEXT, share INTEGER DEFAULT 0, PRIMARY KEY (user_id, day))""")


def bind(**funcs):
    """bot.py передає: loop, bot, is_member, invite_kb, kb_bottom, ask_start, show_product, notify_admins, set_blocked."""
    H.__dict__.update(funcs)


def member(uid):
    with db.conn() as c:
        r = c.execute("SELECT * FROM advent WHERE user_id=?", (uid,)).fetchone()
    return dict(r) if r else None


def join(uid):
    """True, якщо людина щойно приєдналась."""
    with db.conn() as c:
        if c.execute("SELECT 1 FROM advent WHERE user_id=?", (uid,)).fetchone():
            return False
        c.execute("INSERT INTO advent (user_id, joined) VALUES (?,?)", (uid, int(time.time())))
    db.log_ev(uid, "adv_join")
    return True


def set_field(uid, field, value):
    if field not in ("notify", "pin_msg"):
        return
    with db.conn() as c:
        c.execute(f"UPDATE advent SET {field}=? WHERE user_id=?", (value, uid))


def participants():
    """Учасниці, які не зупинили агента."""
    with db.conn() as c:
        rows = c.execute("SELECT a.* FROM advent a LEFT JOIN users u ON u.user_id=a.user_id "
                         "WHERE COALESCE(u.blocked,0)=0").fetchall()
    return [dict(r) for r in rows]


def done_map(uid):
    """{день: {"answer": ..., "share": 0/1}}"""
    with db.conn() as c:
        rows = c.execute("SELECT day, answer, share FROM advent_done WHERE user_id=?", (uid,)).fetchall()
    return {r["day"]: {"answer": r["answer"] or "", "share": r["share"] or 0} for r in rows}


def save_done(uid, day, answer, share):
    """Записує виконання (і відповідь). True, якщо віконце виконане вперше."""
    with db.conn() as c:
        old = c.execute("SELECT 1 FROM advent_done WHERE user_id=? AND day=?", (uid, day)).fetchone()
        if old:
            c.execute("UPDATE advent_done SET answer=?, share=? WHERE user_id=? AND day=?", (answer, share, uid, day))
        else:
            c.execute("INSERT INTO advent_done (user_id, day, at, answer, share) VALUES (?,?,?,?,?)",
                      (uid, day, int(now().timestamp()), answer, share))
    return not old


# ================== час ==================

def now():
    return datetime.now(config.TZ)


def start_date():
    return date.fromisoformat(T.ADVENT_START)


def end_date():
    """Перший день після календаря (1 січня): кнопка зникає, віконця більше не виконуються."""
    return start_date() + timedelta(days=DAYS)


def opens_at(n):
    return datetime.combine(start_date() + timedelta(days=n - 1), dtime(T.ADVENT_HOUR), tzinfo=config.TZ)


def today_n(at=None):
    """Скільки віконець уже відкрито (0..31)."""
    at = at or now()
    n = 0
    while n < DAYS and at >= opens_at(n + 1):
        n += 1
    return n


def phase(at=None):
    """off: ще не час показувати кнопку; soon: чекаємо першого віконця; live: грудень; over: завершено."""
    at = at or now()
    if at.date() >= end_date():
        return "over"
    if at >= opens_at(1):
        return "live"
    if at.date() >= date.fromisoformat(T.ADVENT_ANNOUNCE):
        return "soon"
    return "off"


def user_phase(uid):
    """Власниця бачить календар ще до анонсу, щоб усе перевірити."""
    ph = phase()
    return "soon" if ph == "off" and config.is_admin(uid) else ph


def days_word(n):
    if n % 10 == 1 and n % 100 != 11:
        return f"{n} день"
    if 2 <= n % 10 <= 4 and not 12 <= n % 100 <= 14:
        return f"{n} дні"
    return f"{n} днів"


def left_text(at=None):
    at = at or now()
    left = (start_date() - at.date()).days
    return days_word(left) if left > 0 else "сьогодні о 8:00"


def ddmm(d):
    return d.strftime("%d.%m")


# ================== меню ==================

def menu_label(uid):
    """Назва кнопки в нижньому меню або "" (кнопки немає)."""
    if not webapp.app_url():
        return ""
    ph = user_phase(uid)
    if ph == "soon" and now().date() >= start_date():
        ph = "live"   # 1 грудня з самого ранку кнопка вже «АДВЕНТ-КАЛЕНДАР», без зайвого оновлення меню о 8:00
    return T.ADVENT_BTN_SOON if ph == "soon" else T.ADVENT_BTN if ph == "live" else ""


def menu_tag(uid):
    lab = menu_label(uid)
    return ("-adv-live" if lab == T.ADVENT_BTN else "-adv-soon") if lab else ""


def is_btn(text):
    return text in T.ADVENT_BTNS_ALL


def page_url(day=0):
    base = webapp.app_url()
    return (base + "advent" + (f"?d={day}" if day else "")) if base else ""


# ================== картка в чаті (закріплене повідомлення) ==================

def card_text(uid):
    ph = user_phase(uid)
    done = len(done_map(uid))
    if ph == "over":
        return T.ADVENT_CARD_OVER.format(done=done)
    if ph == "live":
        return T.ADVENT_CARD_LIVE.format(n=today_n(), done=done)
    return T.ADVENT_CARD_SOON.format(left=left_text())


def card_markup(uid, m=None):
    m = m or member(uid) or {}
    rows = []
    url = page_url()
    if url:
        rows.append([InlineKeyboardButton(T.ADVENT_OPEN_BTN, web_app=WebAppInfo(url))])
    if phase() != "over":
        rows.append([InlineKeyboardButton(T.ADVENT_UNMUTE_BTN if m.get("notify") == 0 else T.ADVENT_MUTE_BTN,
                                          callback_data="adv:unmute" if m.get("notify") == 0 else "adv:mute")])
    return InlineKeyboardMarkup(rows)


async def send_card(bot, chat_id, uid, pin=False):
    msg = await bot.send_message(chat_id, card_text(uid), parse_mode=HTML, reply_markup=card_markup(uid))
    if pin:
        m = member(uid) or {}
        if m.get("pin_msg"):
            try:
                await bot.unpin_chat_message(chat_id, m["pin_msg"])
            except Exception:
                pass
        try:
            await bot.pin_chat_message(chat_id, msg.message_id, disable_notification=True)
            set_field(uid, "pin_msg", msg.message_id)
        except Exception as e:
            log.warning("pin %s: %s", uid, e)
    return msg


async def entry(bot, chat_id, uid):
    """Кнопка меню «🎄 Адвент-календар» або посилання ?start=advent.
    Календар бачать усі одразу; віконця відкриваються лише підписницям каналу (перевірка на сторінці)."""
    new = join(uid)
    db.log_ev(uid, "adv_menu")
    await send_card(bot, chat_id, uid, pin=new or not (member(uid) or {}).get("pin_msg"))
    if not config.is_admin(uid) and await H.is_member(bot, uid) is False:
        db.log_ev(uid, "invite")
        await bot.send_message(chat_id, T.ADVENT_INVITE, parse_mode=HTML, reply_markup=sub_markup())


def sub_markup():
    return InlineKeyboardMarkup([[InlineKeyboardButton(T.ADVENT_SUB_BTN, url=T.CHANNEL_LINK)],
                                 [InlineKeyboardButton(T.ADVENT_INVITE_CHECK, callback_data="adv:check")]])


def subscribed(uid):
    """Чи підписана людина на канал (з потоку вебсервера). Не вдалося перевірити: пускаємо."""
    if config.is_admin(uid):
        return True
    last = db.kv(f"gate:{uid}")
    if last and last.isdigit() and time.time() - int(last) < SUB_CACHE:
        return True
    try:
        ok = asyncio.run_coroutine_threadsafe(H.is_member(H.bot, uid), H.loop).result(timeout=8)
    except Exception as e:
        log.warning("advent sub check %s: %s", uid, e)
        return True
    return ok is not False


# ================== знижки ==================

def price(key, pct):
    return int(round(T.ADVENT_BASE_PRICE[key] * (100 - pct) / 100))


def day_end(d):
    return int(datetime.combine(d, dtime(23, 59, 59), tzinfo=config.TZ).timestamp())


def gift_until(day):
    """Остання дата знижки віконця (дата) і час (timestamp)."""
    last = start_date() + timedelta(days=day - 1 + T.ADVENT_GIFT_DAYS - 1)
    return last, day_end(last)


def discount(uid, key):
    """(відсоток, до timestamp) активної знижки людини на продукт або None."""
    v = db.kv(f"advd:{uid}:{key}") or ""
    try:
        pct, until = (int(x) for x in v.split("|")[:2])
    except ValueError:
        return None
    return (pct, until) if until >= now().timestamp() else None


def pay_amount(key, uid):
    """Сума з урахуванням подарунка адвенту або None."""
    d = discount(uid, key) if uid else None
    if not d or key not in T.ADVENT_BASE_PRICE:
        return None
    return f"{price(key, d[0])} грн" + T.ADVENT_PAY_NOTE.format(pct=d[0])


def topic_title(topic):
    """Тема запису для власниці (write:adv_...)."""
    parts = (topic or "").split("_")
    if len(parts) == 3 and parts[0] == "adv" and parts[1] in T.ADVENT_PROD_NAMES and parts[2].isdigit():
        return T.ADVENT_TOPIC_GIFT.format(name=T.ADVENT_PROD_NAMES[parts[1]], pct=parts[2])
    if len(parts) == 3 and parts[0] == "adv" and parts[1] == "q" and parts[2].isdigit():
        return T.ADVENT_TOPIC_Q.format(n=parts[2])
    return ""


async def send_offer(bot, chat_id, uid, key, pct, until_d):
    name = T.ADVENT_PROD_NAMES[key]
    until = ddmm(until_d)
    text = T.ADVENT_OFFER_HEAD.format(pct=pct)
    if key in T.ADVENT_BASE_PRICE:
        text += T.ADVENT_OFFER_PRICE.format(name=name, old=T.ADVENT_BASE_PRICE[key], new=price(key, pct), until=until)
    else:
        text += T.ADVENT_OFFER_NOPRICE.format(name=name, pct=pct, until=until)
    rows = []
    if key in T.PAY_AMOUNT:
        text += T.ADVENT_OFFER_PAY
        rows.append([InlineKeyboardButton(T.BTN_PAY, callback_data=f"pay:{key}")])
    else:
        text += T.ADVENT_OFFER_WRITE
        rows.append([InlineKeyboardButton(T.ADVENT_BTN_DIAG if key == "regres" else T.ADVENT_BTN_WRITE,
                                          callback_data=f"write:adv_{key}_{pct}")])
    rows.append([InlineKeyboardButton(T.ADVENT_BTN_MORE, callback_data=f"prod:{key}"),
                 InlineKeyboardButton(T.BTN_HOME, callback_data="home")])
    await bot.send_message(chat_id, text, parse_mode=HTML, reply_markup=InlineKeyboardMarkup(rows))


async def take_gift(bot, chat_id, uid, day, key):
    """Людина обрала продукт у подарунковому віконці."""
    if day not in T.ADVENT_GIFTS or key not in T.ADVENT_GIFTS[day][1]:
        return
    pct = T.ADVENT_GIFTS[day][0]
    until_d, until_ts = gift_until(day)
    taken = db.kv(f"advg:{uid}:{day}") or ""
    if taken and taken != key:
        await bot.send_message(chat_id, T.ADVENT_OFFER_TAKEN.format(name=T.ADVENT_PROD_NAMES.get(taken, taken)))
        key = taken
    elif now().timestamp() > until_ts:
        await bot.send_message(chat_id, T.ADVENT_OFFER_EXPIRED)
        return
    if not taken:
        db.kv(f"advg:{uid}:{day}", key)
        db.kv(f"advd:{uid}:{key}", f"{pct}|{until_ts}")
        db.log_ev(uid, "adv_gift", f"{day}:{key}")
    await send_offer(bot, chat_id, uid, key, pct, until_d)


async def take_prize(bot, chat_id, uid, key):
    pct, keys = T.ADVENT_PRIZE
    if db.kv("adv_winner") != str(uid) or key not in keys:
        return
    until_d = date.fromisoformat(T.ADVENT_PRIZE_UNTIL)
    taken = db.kv(f"advp:{uid}") or ""
    if taken and taken != key:
        await bot.send_message(chat_id, T.ADVENT_OFFER_TAKEN.format(name=T.ADVENT_PROD_NAMES.get(taken, taken)))
        key = taken
    elif now().timestamp() > day_end(until_d):
        await bot.send_message(chat_id, T.ADVENT_OFFER_EXPIRED)
        return
    if not taken:
        db.kv(f"advp:{uid}", key)
        db.kv(f"advd:{uid}:{key}", f"{pct}|{day_end(until_d)}")
        db.log_ev(uid, "adv_prize", key)
    await send_offer(bot, chat_id, uid, key, pct, until_d)


# ================== кнопки під повідомленнями (adv:...) ==================

async def on_callback(update, context, data):
    q = update.callback_query
    uid, chat_id = update.effective_user.id, q.message.chat_id
    bot = context.bot
    parts = data.split(":")
    if data == "adv:check":
        if await H.is_member(bot, uid) is False:
            await bot.send_message(chat_id, T.GATE_NOT_YET)
        else:
            join(uid)
            db.log_ev(uid, "adv_sub")
            await bot.send_message(chat_id, T.ADVENT_SUB_OK, parse_mode=HTML, reply_markup=card_markup(uid))
    elif data in ("adv:mute", "adv:unmute"):
        if not member(uid):
            return
        set_field(uid, "notify", 0 if data == "adv:mute" else 1)
        db.log_ev(uid, "adv_mute" if data == "adv:mute" else "adv_unmute")
        try:
            await q.message.edit_reply_markup(card_markup(uid))
        except Exception:
            pass
        await bot.send_message(chat_id, T.ADVENT_MUTED if data == "adv:mute" else T.ADVENT_UNMUTED)
    elif parts[1] == "pz" and len(parts) == 3:
        await take_prize(bot, chat_id, uid, parts[2])
    elif not config.is_admin(uid):
        return
    elif data == "adv:a":
        await admin_view(bot, chat_id)
    elif data == "adv:ans":
        n = max(today_n(), 1) if phase() != "soon" else DAYS
        btns = [InlineKeyboardButton(str(d), callback_data=f"adv:ans:{d}") for d in range(1, max(n, 1) + 1)]
        rows = [btns[i:i + 6] for i in range(0, len(btns), 6)] + [[InlineKeyboardButton(T.BTN_BACK, callback_data="adv:a")]]
        await bot.send_message(chat_id, T.ADVENT_ANSWERS_PICK, reply_markup=InlineKeyboardMarkup(rows))
    elif parts[1] == "ans" and len(parts) == 3 and parts[2].isdigit():
        await send_answers(bot, chat_id, int(parts[2]))
    elif data == "adv:file":
        await send_file(bot, chat_id)
    elif data == "adv:raf":
        await bot.send_message(chat_id, raffle_now_text(), parse_mode=HTML)
    elif data == "adv:ann":
        link = f"https://t.me/{bot.username}?start=advent"
        await bot.send_message(chat_id, T.ADVENT_ANNOUNCE_TEXT.format(link=link), parse_mode=HTML,
                               disable_web_page_preview=True)
    elif parts[1] == "win" and len(parts) == 3 and parts[2].isdigit():
        await confirm_winner(bot, chat_id, int(parts[2]))
    elif data == "adv:reroll":
        await raffle(bot, reroll=True)
    return True


# ================== щоденна робота ==================

async def daily_job(context):
    """Раз на 2 хвилини: о T.ADVENT_HOUR оновлює картки учасниць і надсилає «Відкрилось віконце N»;
    1 січня відкріплює картки і проводить розіграш; щонеділі підсумок власниці."""
    bot = context.bot
    t = now()
    ph = phase(t)
    if t.hour >= T.ADVENT_HOUR and db.kv("adv_day") != t.strftime("%Y-%m-%d") and t.date() <= end_date():
        db.kv("adv_day", t.strftime("%Y-%m-%d"))  # ДО відправки: після перезапуску повтору не буде
        n = today_n(t)
        for m in participants():
            uid = m["user_id"]
            try:
                if m.get("pin_msg"):
                    try:
                        await bot.edit_message_text(card_text(uid), uid, m["pin_msg"], parse_mode=HTML,
                                                    reply_markup=card_markup(uid, m))
                    except Exception as e:
                        if "not modified" not in str(e):
                            log.warning("card edit %s: %s", uid, e)
                    if ph == "over":
                        try:
                            await bot.unpin_chat_message(uid, m["pin_msg"])
                        except Exception:
                            pass
                if ph == "live" and m.get("notify", 1) != 0 and n >= 1:
                    title = T.ADVENT_DAYS[n - 1][0]
                    text = T.ADVENT_PUSH.format(n=n, title=esc(title)) + (T.ADVENT_PUSH_GIFT if n in T.ADVENT_GIFTS else "")
                    url = page_url(n)
                    await bot.send_message(uid, text, parse_mode=HTML, reply_markup=InlineKeyboardMarkup(
                        [[InlineKeyboardButton(T.ADVENT_WINDOW_BTN.format(n=n), web_app=WebAppInfo(url))]]) if url else None)
            except Forbidden:
                H.set_blocked(uid)
            except Exception as e:
                log.warning("advent daily %s: %s", uid, e)
            await asyncio.sleep(0.05)
    if ph == "over" and t.hour >= T.ADVENT_RAFFLE_HOUR and not db.kv("adv_raffle"):
        db.kv("adv_raffle", "1")
        await raffle(bot)
    if ph == "live" and t.weekday() == 6 and t.hour >= T.ADVENT_WEEK_HOUR and db.kv("adv_week") != t.strftime("%Y-%m-%d"):
        db.kv("adv_week", t.strftime("%Y-%m-%d"))
        text = week_text(t)
        if text:
            await H.notify_admins(bot, text)


# ================== розіграш ==================

def candidates():
    """[(uid, виконано)] з T.ADVENT_RAFFLE_MIN і більше, без власниці."""
    with db.conn() as c:
        rows = c.execute("SELECT user_id, COUNT(*) FROM advent_done GROUP BY user_id HAVING COUNT(*)>=?",
                         (T.ADVENT_RAFFLE_MIN,)).fetchall()
    return [(r[0], r[1]) for r in rows if not config.is_admin(r[0])]


def name_of(uid, link=True):
    u = db.user(uid) or {}
    name = esc(u.get("name") or "без імені")
    if u.get("username"):
        return f"{name} (@{esc(u['username'])})"
    return f'<a href="tg://user?id={uid}">{name}</a>' if link else name


def raffle_now_text():
    c = candidates()
    rows = "\n".join(f"• {name_of(u)}: {n}" for u, n in sorted(c, key=lambda x: -x[1])) or "• поки нікого"
    return T.ADVENT_RAFFLE_NOW.format(need=T.ADVENT_RAFFLE_MIN, count=len(c), rows=rows)


async def raffle(bot, reroll=False):
    c = candidates()
    if not c:
        await H.notify_admins(bot, T.ADVENT_RAFFLE_NOBODY.format(need=T.ADVENT_RAFFLE_MIN))
        return
    tried = set(json.loads(db.kv("adv_tried") or "[]")) if reroll else set()
    pool = [x for x in c if x[0] not in tried] or c
    win = random.choice(pool)[0]
    tried.add(win)
    db.kv("adv_tried", json.dumps(sorted(tried)))
    rows = "\n".join(f"• {name_of(u)}: {n}" for u, n in sorted(c, key=lambda x: -x[1]))
    await H.notify_admins(bot, T.ADVENT_RAFFLE_ADMIN.format(need=T.ADVENT_RAFFLE_MIN, count=len(c), rows=rows,
                                                            winner=name_of(win)),
                          reply_markup=InlineKeyboardMarkup([
                              [InlineKeyboardButton(T.ADVENT_RAFFLE_CONFIRM, callback_data=f"adv:win:{win}")],
                              [InlineKeyboardButton(T.ADVENT_RAFFLE_REROLL, callback_data="adv:reroll")]]))


async def confirm_winner(bot, chat_id, uid):
    if db.kv("adv_winner"):
        await bot.send_message(chat_id, T.ADVENT_RAFFLE_SENT.format(who=name_of(int(db.kv("adv_winner")))),
                               parse_mode=HTML)
        return
    u = db.user(uid) or {}
    pct, keys = T.ADVENT_PRIZE
    rows = [[InlineKeyboardButton(T.ADVENT_PROD_NAMES[k], callback_data=f"adv:pz:{k}")] for k in keys]
    try:
        await bot.send_message(uid, T.ADVENT_WIN.format(name=esc((u.get("name") or "").split(" ")[0]),
                                                        done=len(done_map(uid))),
                               parse_mode=HTML, reply_markup=InlineKeyboardMarkup(rows))
    except Exception as e:
        log.warning("winner %s: %s", uid, e)
        await bot.send_message(chat_id, T.ADVENT_RAFFLE_FAIL)
        return
    db.kv("adv_winner", str(uid))
    db.log_ev(uid, "adv_win")
    await bot.send_message(chat_id, T.ADVENT_RAFFLE_SENT.format(who=name_of(uid)), parse_mode=HTML)


# ================== власниці ==================

def day_stats():
    """{день: (відкрили, виконали, написали)} без власниці."""
    ex = ",".join(str(int(a)) for a in config.ADMIN_IDS) or "0"
    out = {d: [0, 0, 0] for d in range(1, DAYS + 1)}
    with db.conn() as c:
        for extra, n in c.execute(f"SELECT extra, COUNT(DISTINCT user_id) FROM events WHERE ev='adv_window' "
                                  f"AND user_id NOT IN ({ex}) GROUP BY extra").fetchall():
            if str(extra).isdigit() and int(extra) in out:
                out[int(extra)][0] = n
        for d, n, w in c.execute(f"SELECT day, COUNT(*), SUM(CASE WHEN TRIM(COALESCE(answer,''))<>'' THEN 1 ELSE 0 END) "
                                 f"FROM advent_done WHERE user_id NOT IN ({ex}) GROUP BY day").fetchall():
            if d in out:
                out[d][1], out[d][2] = n, w or 0
    return out


async def admin_view(bot, chat_id):
    people = [m for m in participants() if not config.is_admin(m["user_id"])]
    ph = phase()
    n = today_n()
    st = day_stats()
    shown = range(1, n + 1) if ph in ("live", "over") else []
    days = "\n".join(T.ADVENT_ADMIN_DAY.format(n=d, opened=st[d][0], done=st[d][1], wrote=st[d][2]) for d in shown)
    text = T.ADVENT_ADMIN.format(people=len(people), muted=sum(1 for m in people if m.get("notify") == 0),
                                 phase=T.ADVENT_PHASE[ph].format(n=n), need=T.ADVENT_RAFFLE_MIN,
                                 raffle=len(candidates()), days=days or "• ще не почався")
    rows = [[InlineKeyboardButton(T.ADVENT_BTN_ANSWERS, callback_data="adv:ans"),
             InlineKeyboardButton(T.ADVENT_BTN_FILE, callback_data="adv:file")],
            [InlineKeyboardButton(T.ADVENT_BTN_RAFFLE, callback_data="adv:raf"),
             InlineKeyboardButton(T.ADVENT_BTN_ANNOUNCE, callback_data="adv:ann")]]
    if page_url():
        rows.append([InlineKeyboardButton(T.ADVENT_OPEN_BTN, web_app=WebAppInfo(page_url()))])
    rows.append([InlineKeyboardButton(T.BTN_HOME, callback_data="home")])
    await bot.send_message(chat_id, text, parse_mode=HTML, reply_markup=InlineKeyboardMarkup(rows))


def answers(day=None):
    q = ("SELECT d.user_id, d.day, d.at, d.answer, d.share, u.name, u.username FROM advent_done d "
         "LEFT JOIN users u ON u.user_id=d.user_id WHERE TRIM(COALESCE(d.answer,''))<>''")
    args = ()
    if day:
        q += " AND d.day=?"
        args = (day,)
    with db.conn() as c:
        return [dict(r) for r in c.execute(q + " ORDER BY d.day, d.at", args).fetchall()]


async def send_answers(bot, chat_id, day):
    if not 1 <= day <= DAYS:
        return
    rows = [r for r in answers(day) if not config.is_admin(r["user_id"])] or answers(day)
    if not rows:
        await bot.send_message(chat_id, T.ADVENT_ANSWERS_EMPTY)
        return
    chunk = T.ADVENT_ANSWERS_HEAD.format(n=day, title=esc(T.ADVENT_DAYS[day - 1][0]), count=len(rows))
    for r in rows:
        line = T.ADVENT_ANSWERS_ROW.format(who=name_of(r["user_id"]), ok=T.ADVENT_ANSWERS_OK if r["share"] else "",
                                           text=esc(r["answer"]))
        if len(chunk) + len(line) > 3900:
            await bot.send_message(chat_id, chunk, parse_mode=HTML, disable_web_page_preview=True)
            chunk = ""
        chunk += line
    if chunk:
        await bot.send_message(chat_id, chunk, parse_mode=HTML, disable_web_page_preview=True)


async def send_file(bot, chat_id):
    rows = answers()
    if not rows:
        await bot.send_message(chat_id, T.ADVENT_FILE_EMPTY)
        return
    head = ["Учасниця", "Нік", "Віконце", "Тема", "Відповідь", "Можна для контенту", "Дата"]
    data = [[r["name"] or "", ("@" + r["username"]) if r["username"] else "", r["day"], T.ADVENT_DAYS[r["day"] - 1][0],
             r["answer"], "✅" if r["share"] else "",
             datetime.fromtimestamp(r["at"] or 0, config.TZ).strftime("%d.%m.%Y %H:%M")] for r in rows]
    try:
        from openpyxl import Workbook
        from openpyxl.styles import Alignment, Font
        wb = Workbook()
        ws = wb.active
        ws.title = "Відповіді"
        ws.append(head)
        for row in data:
            ws.append(row)
        for cell in ws[1]:
            cell.font = Font(bold=True)
        for col, w in zip("ABCDEFG", (22, 16, 9, 34, 70, 12, 16)):
            ws.column_dimensions[col].width = w
        for row in ws.iter_rows(min_row=2):
            for cell in row:
                cell.alignment = Alignment(wrap_text=True, vertical="top")
        buf = io.BytesIO()
        wb.save(buf)
        name = "advent_vidpovidi.xlsx"
    except ImportError:
        s = io.StringIO()
        csv.writer(s).writerows([head] + data)
        buf = io.BytesIO(("﻿" + s.getvalue()).encode())
        name = "advent_vidpovidi.csv"
    buf.seek(0)
    await bot.send_document(chat_id, buf, filename=name, caption=T.ADVENT_FILE_CAPTION)


def week_text(t):
    since = int((t - timedelta(days=7)).timestamp())
    rows = [r for r in answers() if (r["at"] or 0) >= since and not config.is_admin(r["user_id"])]
    with db.conn() as c:
        ex = ",".join(str(int(a)) for a in config.ADMIN_IDS) or "0"
        people = c.execute(f"SELECT COUNT(DISTINCT user_id) FROM advent_done WHERE at>=? AND user_id NOT IN ({ex})",
                           (since,)).fetchone()[0]
    if not people:
        return ""
    best = sorted(rows, key=lambda r: (-(r["share"] or 0), -len(r["answer"] or "")))[:5]
    lines = ""
    for r in best:
        a = r["answer"] or ""
        a = a if len(a) <= 300 else a[:300] + "…"
        lines += T.ADVENT_ANSWERS_ROW.format(who=f"{name_of(r['user_id'])}, віконце {r['day']}",
                                             ok=T.ADVENT_ANSWERS_OK if r["share"] else "", text=esc(a))
    return T.ADVENT_WEEK.format(people=people, wrote=len(rows), rows=lines or "• поки без відповідей\n\n")


# ================== сторінка календаря (виклики з вебсервера, окремий потік) ==================

def _run(coro):
    """Запустити дію агента (повідомлення в чат) з потоку вебсервера."""
    try:
        asyncio.run_coroutine_threadsafe(coro, H.loop)
    except Exception as e:
        log.warning("advent run: %s", e)


def unlocked(uid, day):
    if config.is_admin(uid):
        return True   # власниця бачить усі віконця заздалегідь
    return 1 <= day <= today_n()


def page_state(uid, sub=True):
    ph = user_phase(uid)
    n = today_n()
    dm = done_map(uid)
    days = []
    for d in range(1, DAYS + 1):
        title, text, task = T.ADVENT_DAYS[d - 1]
        item = {"n": d, "gift": d in T.ADVENT_GIFTS, "done": d in dm, "open": unlocked(uid, d),
                "date": ddmm(start_date() + timedelta(days=d - 1))}
        if item["open"] and sub:
            item.update(title=title, text=text, task=task, answer=dm.get(d, {}).get("answer", ""),
                        share=bool(dm.get(d, {}).get("share")))
            if d in T.ADVENT_GIFTS:
                pct, keys = T.ADVENT_GIFTS[d]
                until_d, until_ts = gift_until(d)
                item["gift_info"] = {
                    "pct": pct, "until": ddmm(until_d), "over": now().timestamp() > until_ts,
                    "taken": db.kv(f"advg:{uid}:{d}") or "",
                    "options": [{"key": k, "name": T.ADVENT_PROD_NAMES[k],
                                 "old": T.ADVENT_BASE_PRICE.get(k), "new": price(k, pct) if k in T.ADVENT_BASE_PRICE else None}
                                for k in keys]}
        days.append(item)
    return {"ok": True, "phase": ph, "today": n, "left": left_text(), "done": len(dm), "need": T.ADVENT_RAFFLE_MIN,
            "days": days, "can_done": ph in ("live", "soon") and (ph == "live" or config.is_admin(uid)),
            "texts": T.ADVENT_PAGE, "sub": sub, "channel": T.CHANNEL_LINK}


def api(path, body):
    """POST /advent/<дія>, тіло JSON {"init": initData, ...}. Повертає (код, словник)."""
    try:
        req = json.loads(body or "{}")
    except Exception:
        return 400, {"ok": False}
    uid = webapp.user_from_init_data(str(req.get("init", "")))
    if not uid:
        return 403, {"ok": False, "error": "auth"}
    if not member(uid):
        join(uid)   # календар бачать усі; віконця відкриваються підписницям каналу
    action = path.rsplit("/", 1)[-1]
    day = req.get("day")
    day = int(day) if str(day).isdigit() else 0
    sub = subscribed(uid)
    if action == "state":
        db.log_ev(uid, "adv_page")
        return 200, page_state(uid, sub)
    if not sub:
        db.log_ev(uid, "adv_need_sub")
        return 200, {"ok": False, "need_sub": True}
    if not unlocked(uid, day):
        return 200, {"ok": False, "error": "locked"}
    if action == "seen":
        db.log_ev(uid, "adv_window", str(day))
        return 200, {"ok": True}
    if action == "done":
        st = page_state(uid, sub)
        if not st["can_done"] and not config.is_admin(uid):
            return 200, {"ok": False, "error": "over"}
        answer = str(req.get("answer") or "").strip()[:3000]
        share = 1 if req.get("share") and answer else 0
        before = len(done_map(uid))
        first = save_done(uid, day, answer, share)
        if first:
            db.log_ev(uid, "adv_done", str(day))
            done = before + 1
            nxt = T.ADVENT_DONE_LAST if day == DAYS or today_n() >= DAYS else T.ADVENT_DONE_NEXT
            text = T.ADVENT_DONE_MSG.format(n=day, done=done, next=nxt)
            if done == T.ADVENT_RAFFLE_MIN:
                text += T.ADVENT_DONE_RAFFLE
            _run(H.bot.send_message(uid, text, parse_mode=HTML))
        if answer:
            db.log_ev(uid, "adv_answer", str(day))
        return 200, page_state(uid, sub)
    if action == "gift":
        key = str(req.get("key") or "")
        _run(take_gift(H.bot, uid, uid, day, key))
        return 200, {"ok": True}
    if action == "ask":
        _run(H.ask_start(uid, SimpleNamespace(bot=H.bot), uid, f"adv_q_{day}", T.ADVENT_ASK.format(n=day)))
        return 200, {"ok": True}
    return 404, {"ok": False}
