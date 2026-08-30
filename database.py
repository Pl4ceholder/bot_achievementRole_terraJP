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