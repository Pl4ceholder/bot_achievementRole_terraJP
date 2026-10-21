import json
import redis
from config import REDIS_URL

r = redis.from_url(REDIS_URL, decode_responses=True)


def get_user_achievements(user_id: int) -> list[int]:
    """ユーザーが解放済みの実績ロールIDリストを取得"""
    role_ids = r.smembers(f"user:{user_id}:achievements")
    return [int(rid) for rid in role_ids]


def list_all_achievements() -> dict[int, list[int]]:
    """全員ぶんの解放済み実績（ユーザーID → ロールIDのリスト）。

    Redis にはユーザーの一覧が無いので、キーの形（user:*:achievements）を拾って回る。
    実績の付与は手動でたまにしか起きないので、この走査で足りる。壊れた値は飛ばして、
    1 件のせいで同期が止まらないようにする。
    """
    found = {}

    for key in r.scan_iter(match="user:*:achievements", count=100):
        parts = str(key).split(":")
        if len(parts) != 3:
            continue

        try:
            user_id = int(parts[1])
        except ValueError:
            continue

        role_ids = []
        for role_id in r.smembers(key):
            try:
                role_ids.append(int(role_id))
            except (TypeError, ValueError):
                continue

        if role_ids:
            found[user_id] = role_ids

    return found


def grant_achievement(user_id: int, role_id: int):
    """ユーザーに実績を解放（DBに追加）"""
    r.sadd(f"user:{user_id}:achievements", role_id)


def revoke_achievement(user_id: int, role_id: int):
    """ユーザーの実績を剥奪（DBから削除）"""
    r.srem(f"user:{user_id}:achievements", role_id)


def create_quiz(
    question: str,
    kind: str,
    category: str | None,
    choices: list[str],
    answers: list[str],
    answer_mode: str = "any",
) -> str:
    """クイズを登録し、クイズIDを返す。"""
    quiz_id = str(r.incr("quiz:next_id"))
    quiz = {
        "id": quiz_id,
        "question": question,
        "kind": kind,
        "category": category or "",
        "choices": choices,
        "answers": answers,
        "answer_mode": answer_mode,
    }
    r.set(f"quiz:{quiz_id}", json.dumps(quiz, ensure_ascii=False))
    r.sadd("quizzes", quiz_id)
    return quiz_id


def get_quiz(quiz_id: str) -> dict | None:
    """IDでクイズを取得する。"""
    value = r.get(f"quiz:{quiz_id}")
    return json.loads(value) if value else None


def get_quizzes(category: str | None = None) -> list[dict]:
    """登録済みクイズを取得し、必要ならカテゴリで絞り込む。"""
    quizzes = []
    for quiz_id in r.smembers("quizzes"):
        quiz = get_quiz(quiz_id)
        if quiz is None:
            r.srem("quizzes", quiz_id)
            continue
        if category is None or quiz["category"].casefold() == category.casefold():
            quizzes.append(quiz)
    return sorted(quizzes, key=lambda quiz: int(quiz["id"]))


def delete_quiz(quiz_id: str) -> bool:
    """クイズを削除する。存在した場合はTrueを返す。"""
    deleted = bool(r.delete(f"quiz:{quiz_id}"))
    r.srem("quizzes", quiz_id)
    return deleted


def get_quiz_categories() -> list[str]:
    """登録済みクイズのカテゴリ一覧を取得する。"""
    return sorted({quiz["category"] for quiz in get_quizzes() if quiz["category"]})


# --- スターボード ---------------------------------------------------------
# スターボードに載せた投稿は、Bot を再起動しても消えないように Redis に覚えておく。
# 元の投稿 ID からスターボードの投稿 ID を引けるようにして、リアクションが減ったときや
# 元の投稿が消えたときに、同じ投稿を二重に載せず片付けられるようにする。

STARBOARD_ENTRY_PREFIX = "starboard:entry:"
STARBOARD_REVERSE_PREFIX = "starboard:reverse:"


def get_starboard_entry(message_id: int) -> dict | None:
    """元の投稿 ID に対応するスターボードの記録を返す。無ければ None。"""
    value = r.get(f"{STARBOARD_ENTRY_PREFIX}{message_id}")
    if not value:
        return None

    try:
        entry = json.loads(value)
    except (TypeError, ValueError):
        r.delete(f"{STARBOARD_ENTRY_PREFIX}{message_id}")
        return None

    return entry if isinstance(entry, dict) else None


def get_starboard_source(starboard_message_id: int) -> int | None:
    """スターボードの投稿 ID から、元の投稿 ID を返す。無ければ None。"""
    value = r.get(f"{STARBOARD_REVERSE_PREFIX}{starboard_message_id}")
    try:
        return int(value) if value else None
    except (TypeError, ValueError):
        return None


def save_starboard_entry(
    message_id: int,
    starboard_message_id: int,
    starboard_channel_id: int,
    count: int,
) -> None:
    """スターボードに載せたことを記録する。"""
    old = get_starboard_entry(message_id)
    if old:
        try:
            old_id = int(old.get("starboard_message_id") or 0)
        except (TypeError, ValueError):
            old_id = 0
        if old_id and old_id != int(starboard_message_id):
            r.delete(f"{STARBOARD_REVERSE_PREFIX}{old_id}")

    entry = {
        "starboard_message_id": str(starboard_message_id),
        "starboard_channel_id": str(starboard_channel_id),
        "count": int(count),
    }
    r.set(f"{STARBOARD_ENTRY_PREFIX}{message_id}", json.dumps(entry))
    r.set(f"{STARBOARD_REVERSE_PREFIX}{starboard_message_id}", str(message_id))


def delete_starboard_entry(message_id: int) -> None:
    """スターボードの記録を消す（元の投稿が消えた・リアクションが減ったとき）。"""
    entry = get_starboard_entry(message_id)
    r.delete(f"{STARBOARD_ENTRY_PREFIX}{message_id}")

    if entry:
        starboard_message_id = entry.get("starboard_message_id")
        if starboard_message_id:
            r.delete(f"{STARBOARD_REVERSE_PREFIX}{starboard_message_id}")
