-- GoBig Postgres DDL for M1-03 and M1-04 persistence.
-- Apply manually in your migration system when ready.

BEGIN;

CREATE TABLE IF NOT EXISTS users (
    user_id TEXT PRIMARY KEY,
    email TEXT UNIQUE,
    username TEXT UNIQUE,
    full_name TEXT,
    avatar_url TEXT,
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    profile_json JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS nlp_ingest_batches (
    batch_id TEXT PRIMARY KEY,
    status TEXT NOT NULL,
    ai_result_json JSONB,
    user_id TEXT REFERENCES users(user_id) ON DELETE SET NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS nlp_ingest_recipes (
    batch_id TEXT NOT NULL REFERENCES nlp_ingest_batches(batch_id) ON DELETE CASCADE,
    recipe_id TEXT NOT NULL,
    text TEXT NOT NULL,
    confidence DOUBLE PRECISION NOT NULL CHECK (confidence >= 0.0 AND confidence <= 1.0),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (batch_id, recipe_id)
);

CREATE INDEX IF NOT EXISTS idx_nlp_ingest_recipes_recipe_id
    ON nlp_ingest_recipes (recipe_id);

CREATE TABLE IF NOT EXISTS social_follows (
    follower_user_id TEXT NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
    target_user_id TEXT NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (follower_user_id, target_user_id),
    CONSTRAINT social_follows_no_self_follow CHECK (follower_user_id <> target_user_id)
);

CREATE INDEX IF NOT EXISTS idx_social_follows_target_user_id
    ON social_follows (target_user_id);

CREATE TABLE IF NOT EXISTS social_events (
    event_id TEXT PRIMARY KEY,
    actor_user_id TEXT NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
    event_type TEXT NOT NULL,
    ts_ms BIGINT NOT NULL,
    payload_json JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_social_events_actor_ts
    ON social_events (actor_user_id, ts_ms DESC);

CREATE TABLE IF NOT EXISTS social_potlucks (
    potluck_id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    creator_user_id TEXT NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
    ts_ms BIGINT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_social_potlucks_creator
    ON social_potlucks (creator_user_id, ts_ms DESC);

CREATE TABLE IF NOT EXISTS social_potluck_invites (
    potluck_id TEXT NOT NULL REFERENCES social_potlucks(potluck_id) ON DELETE CASCADE,
    inviter_user_id TEXT NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
    invitee_user_id TEXT NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (potluck_id, invitee_user_id)
);

CREATE INDEX IF NOT EXISTS idx_social_potluck_invites_invitee
    ON social_potluck_invites (invitee_user_id);

COMMIT;
