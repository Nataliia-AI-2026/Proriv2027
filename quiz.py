"""Тести (квізи): питання кнопками, бали результатам, результат з порадою і кнопкою на продукт.
Тексти всіх тестів у texts.py (QUIZZES). Тут лише логіка: що показати і як порахувати.
Стан людини (який тест, яке питання, бали) зберігається в базі (db.kv), тож переживає перезапуск агента."""
import html
import json

import db
import texts as T

NUMS = ["1️⃣", "2️⃣", "3️⃣", "4️⃣", "5️⃣"]


def link_key(src):
    """?start=test → "" (список), ?start=test_programa → "programa", інше → None."""
    if src == "test":
        return ""
    if src.startswith("test_") and src[5:] in T.QUIZZES:
        return src[5:]
    return None


def topic_title(topic):
    """Назва тесту для «Поставити питання» (тема test_КЛЮЧ)."""
    if topic.startswith("test_") and topic[5:] in T.QUIZZES:
        return "Тест: " + T.QUIZZES[topic[5:]]["title"]
    return ""


def list_view():
    rows = [[(q["button"], f"qz:in:{k}")] for k, q in T.QUIZZES.items()]
    rows.append([(T.BTN_HOME, "home")])
    return T.QUIZ_LIST, rows


def intro_view(key):
    q = T.QUIZZES[key]
    return q["intro"], [[(T.QUIZ_START_BTN, f"qz:go:{key}")], [(T.QUIZ_ALL_BTN, "qz:list"), (T.BTN_HOME, "home")]]


def _state(uid, value=None):
    if value is None:
        raw = db.kv(f"qz:{uid}")
        return json.loads(raw) if raw else None
    db.kv(f"qz:{uid}", json.dumps(value) if value else "")


def begin(uid, key):
    """Почати (або почати знову) тест: новий прохід у базі, нульові бали."""
    run = db.quiz_start(uid, key)
    _state(uid, {"k": key, "i": 0, "s": {}, "run": run})
    db.log_ev(uid, "quiz_start", key)
    return question_view(key, 0)


def question_view(key, i):
    q = T.QUIZZES[key]
    text, answers = q["questions"][i]
    lines = [T.QUIZ_PROGRESS.format(n=i + 1, total=len(q["questions"])), "", f"<b>{html.escape(text)}</b>", ""]
    lines += [f"{NUMS[j]} {html.escape(a)}" for j, (a, _) in enumerate(answers)]
    rows = [[(NUMS[j], f"qz:a:{key}:{i}:{j}") for j in range(len(answers))],
            [(T.QUIZ_RESTART_BTN, f"qz:go:{key}")]]
    return "\n".join(lines), rows


def answer(uid, key, i, j):
    """Записати відповідь. Повертає ("q", текст, кнопки) — наступне питання,
    ("r", текст, кнопки, ключ_результату, run) — результат, або None, якщо кнопка застаріла."""
    st = _state(uid)
    q = T.QUIZZES.get(key)
    if not st or not q or st["k"] != key or st["i"] != i or not (0 <= j < len(q["questions"][i][1])):
        return None
    res = q["questions"][i][1][j][1]
    st["s"][res] = st["s"].get(res, 0) + 1
    st["i"] = i + 1
    if st["i"] < len(q["questions"]):
        _state(uid, st)
        text, rows = question_view(key, st["i"])
        return ("q", text, rows)
    # кінець: найбільше балів; при рівності перемагає той, що вище в списку results
    order = list(q["results"])
    best = max(order, key=lambda r: (st["s"].get(r, 0), -order.index(r)))
    _state(uid, "")
    db.quiz_finish(st["run"], best)
    db.log_ev(uid, "quiz_done", f"{key}:{best}")
    text, rows = result_view(key, best)
    return ("r", text, rows, best, st["run"])


def result_view(key, res):
    name, body, advice, (btn, cb) = T.QUIZZES[key]["results"][res]
    text = f"<b>{html.escape(name)}</b>\n\n{html.escape(body)}\n\n" + T.QUIZ_ADVICE.format(text=html.escape(advice))
    rows = [[(btn, cb)], [(T.QUIZ_ASK_BTN, f"write:test_{key}")],
            [(T.QUIZ_AGAIN_BTN, f"qz:go:{key}"), (T.QUIZ_ALL_BTN, "qz:list")], [(T.BTN_HOME, "home")]]
    return text, rows


def stats_text(exclude):
    st = db.quiz_stats(exclude)
    if not st:
        return T.QUIZ_STAT_EMPTY
    blocks = []
    for key, q in T.QUIZZES.items():
        started, done, res = st.get(key, (0, 0, {}))
        lines = "\n".join(f"• {html.escape(q['results'][r][0])}: {res.get(r, 0)}" for r in q["results"])
        blocks.append(T.QUIZ_STAT_ROW.format(title=html.escape(q["title"]), started=started, done=done, results=lines))
    return T.QUIZ_STAT.format(rows="\n\n".join(blocks))
