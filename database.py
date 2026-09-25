import json
import redis
from config import REDIS_URL

r = redis.from_url(REDIS_URL, decode_responses=True)


def get_user_achievements(user_id: int) -> list[int]:
    """ユーザーが解放済みの実績ロールIDリストを取得"""
    role_ids = r.smembers(f"user:{user_id}:achievements")
    return [int(rid) for rid in role_ids]


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
