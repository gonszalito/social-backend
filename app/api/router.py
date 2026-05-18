from fastapi import APIRouter

from app.api.admin.flags import router as admin_flags_router
from app.api.admin.jobs import router as admin_jobs_router
from app.api.admin.playbook import router as admin_playbook_router
from app.api.admin.system_status import router as admin_system_status_router
from app.api.auth.dev_token import router as dev_token_router
from app.api.auth.revoke import router as auth_router
from app.api.mobile.social_router import router as social_router
from app.api.nlp.ingest import router as nlp_router
from app.api.storage.presign import router as storage_router
from app.api.storage.upload import router as upload_router

api_router = APIRouter()
api_router.include_router(auth_router)
api_router.include_router(dev_token_router)
api_router.include_router(admin_flags_router)
api_router.include_router(admin_playbook_router)
api_router.include_router(admin_jobs_router)
api_router.include_router(admin_system_status_router)
api_router.include_router(storage_router)
api_router.include_router(upload_router)
api_router.include_router(nlp_router)
api_router.include_router(social_router)

