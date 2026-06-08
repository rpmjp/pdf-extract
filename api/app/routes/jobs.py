"""Job polling routes for asynchronous parse work."""

from typing import Annotated

from fastapi import APIRouter, Depends

from ..auth import AuthUser, get_current_user
from ..celery_helpers import serialize_job

router = APIRouter(tags=["Jobs"])


@router.get("/jobs/{job_id}")
def get_job(job_id: str, user: Annotated[AuthUser, Depends(get_current_user)]):
    """Return the current Celery/DB job status in frontend-friendly form."""

    return serialize_job(job_id)
