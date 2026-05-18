from __future__ import annotations

from dataclasses import dataclass


@dataclass
class NlpBatchModel:
    batch_id: str
    status: str
    ai_result_json: str | None = None


@dataclass
class NlpRecipeModel:
    batch_id: str
    recipe_id: str
    text: str
    confidence: float


@dataclass
class SocialFollowModel:
    follower_user_id: str
    target_user_id: str


@dataclass
class SocialEventModel:
    event_id: str
    actor_user_id: str
    event_type: str
    payload_json: str
    ts_ms: int


@dataclass
class PotluckModel:
    potluck_id: str
    title: str
    creator_user_id: str
    ts_ms: int


@dataclass
class PotluckInviteModel:
    potluck_id: str
    inviter_user_id: str
    invitee_user_id: str
