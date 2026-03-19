import logging
from fastapi import APIRouter, Depends, HTTPException, WebSocket, WebSocketDisconnect, Query
from sqlalchemy.orm import Session
from datetime import datetime, timezone
import asyncio

from app.models.base import get_db
from app.models.job import Job, JOB_CREDITS
from app.models.image import Image
from app.models.user import User
from app.schemas.job import JobCreateRequest, StagingJobCreateRequest, RemovalJobCreateRequest, VideoJobCreateRequest, VoiceoverJobCreateRequest, JobResponse, BulkEnhanceRequest
from app.dependencies import get_current_user

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/jobs", tags=["jobs"])


# ── POST /api/jobs/bulk-enhance ──

@router.post("/bulk-enhance", response_model=list[JobResponse], status_code=201)
def create_bulk_enhance(
    body: BulkEnhanceRequest,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Enhance all images with one click using admin-configured prompt."""
    if not body.image_ids:
        raise HTTPException(status_code=400, detail="At least one image is required")

    if len(body.image_ids) > 40:
        raise HTTPException(status_code=400, detail="Maximum 40 images per bulk enhance")

    # Verify all images belong to user
    images = db.query(Image).filter(Image.id.in_(body.image_ids), Image.user_id == user.id).all()
    if len(images) != len(body.image_ids):
        raise HTTPException(status_code=400, detail="One or more images not found")

    # Credits: 1 per image
    total_credits = len(body.image_ids)
    if not user.can_afford(total_credits):
        raise HTTPException(status_code=402, detail={
            "message": "Insufficient credits",
            "credits_needed": total_credits,
            "credits_remaining": user.credits_remaining,
        })

    user.deduct_credits(total_credits)

    # Create one job per image for parallel processing
    jobs = []
    for image_id in body.image_ids:
        job = Job(
            user_id=user.id,
            type="enhance",
            status="pending",
            input_data={
                "image_ids": [image_id],
                "listing_id": body.listing_id,
                "settings": {"level": 2, "style": "natural", "output_size": "4k"},
                "use_admin_prompt": True,
            },
            credits_used=1,
        )
        db.add(job)
        jobs.append(job)

    db.commit()

    # Start processing each job
    for job in jobs:
        db.refresh(job)
        try:
            from app.tasks.enhance import process_enhance_job
            task = process_enhance_job.delay(job.id)
            job.status = "processing"
            db.commit()
        except Exception as e:
            logger.warning(f"Could not dispatch task for {job.id}: {e}")

    logger.info(f"Bulk enhance: {len(jobs)} jobs for user {user.id}")
    return jobs


# ── POST /api/jobs/enhance ──

@router.post("/enhance", response_model=JobResponse, status_code=201)
def create_enhance_job(
    body: JobCreateRequest,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if not body.image_ids:
        raise HTTPException(status_code=400, detail="At least one image is required")

    if len(body.image_ids) > 10:
        raise HTTPException(status_code=400, detail="Maximum 10 images per job")

    # Verify all images belong to user
    images = db.query(Image).filter(
        Image.id.in_(body.image_ids),
        Image.user_id == user.id,
    ).all()

    if len(images) != len(body.image_ids):
        raise HTTPException(status_code=400, detail="One or more images not found")

    # Validate image formats
    for img in images:
        if img.mime_type not in {"image/jpeg", "image/png", "image/webp"}:
            raise HTTPException(
                status_code=400,
                detail=f"Unsupported image format: {img.file_name} ({img.mime_type})",
            )

    # Calculate credits
    credits_per = JOB_CREDITS["enhance"]
    total_credits = credits_per * len(body.image_ids)

    # Credit check
    if not user.can_afford(total_credits):
        raise HTTPException(
            status_code=402,
            detail={
                "message": "Insufficient credits",
                "credits_needed": total_credits,
                "credits_remaining": user.credits_remaining,
            },
        )

    # Deduct credits
    user.deduct_credits(total_credits)

    # Create job
    job = Job(
        user_id=user.id,
        type="enhance",
        status="pending",
        input_data={
            "image_ids": body.image_ids,
            "settings": body.settings.model_dump(),
        },
        credits_used=total_credits,
    )
    db.add(job)
    db.commit()
    db.refresh(job)

    logger.info(f"Job {job.id} created for user {user.id} — {len(body.image_ids)} images, {total_credits} credits")

    # Dispatch Celery task
    try:
        from app.tasks.enhance import process_enhance_job
        task = process_enhance_job.delay(job.id)
        job.celery_task_id = task.id
        job.status = "processing"
        db.commit()
    except Exception as e:
        logger.warning(f"Could not dispatch Celery task: {e} — job stays pending")

    return job


# ── POST /api/jobs/stage ──

@router.post("/stage", response_model=JobResponse, status_code=201)
def create_staging_job(
    body: StagingJobCreateRequest,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if not body.image_ids:
        raise HTTPException(status_code=400, detail="At least one image is required")

    if len(body.image_ids) > 10:
        raise HTTPException(status_code=400, detail="Maximum 10 images per job")

    # Verify all images belong to user
    images = db.query(Image).filter(
        Image.id.in_(body.image_ids),
        Image.user_id == user.id,
    ).all()

    if len(images) != len(body.image_ids):
        raise HTTPException(status_code=400, detail="One or more images not found")

    # Calculate credits (staging = 2 per image)
    credits_per = JOB_CREDITS["stage"]
    total_credits = credits_per * len(body.image_ids)

    if not user.can_afford(total_credits):
        raise HTTPException(
            status_code=402,
            detail={
                "message": "Insufficient credits",
                "credits_needed": total_credits,
                "credits_remaining": user.credits_remaining,
            },
        )

    user.deduct_credits(total_credits)

    job = Job(
        user_id=user.id,
        type="stage",
        status="pending",
        input_data={
            "image_ids": body.image_ids,
            "settings": body.settings.model_dump(),
        },
        credits_used=total_credits,
    )
    db.add(job)
    db.commit()
    db.refresh(job)

    logger.info(f"Staging job {job.id} created for user {user.id} — {len(body.image_ids)} images, {total_credits} credits")

    # Dispatch Celery task
    try:
        from app.tasks.staging import process_staging_job
        task = process_staging_job.delay(job.id)
        job.celery_task_id = task.id
        job.status = "processing"
        db.commit()
    except Exception as e:
        logger.warning(f"Could not dispatch staging task: {e} — job stays pending")

    return job


# ── POST /api/jobs/remove ──

@router.post("/remove", response_model=JobResponse, status_code=201)
def create_removal_job(
    body: RemovalJobCreateRequest,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if not body.image_ids:
        raise HTTPException(status_code=400, detail="At least one image is required")

    if len(body.image_ids) > 10:
        raise HTTPException(status_code=400, detail="Maximum 10 images per job")

    # Verify images
    images = db.query(Image).filter(
        Image.id.in_(body.image_ids),
        Image.user_id == user.id,
    ).all()

    if len(images) != len(body.image_ids):
        raise HTTPException(status_code=400, detail="One or more images not found")

    # Verify masks provided for all images
    for img_id in body.image_ids:
        if img_id not in body.mask_data:
            raise HTTPException(status_code=400, detail=f"Missing mask for image {img_id}")

    # Credits (removal = 2 per image)
    credits_per = JOB_CREDITS["remove"]
    total_credits = credits_per * len(body.image_ids)

    if not user.can_afford(total_credits):
        raise HTTPException(
            status_code=402,
            detail={
                "message": "Insufficient credits",
                "credits_needed": total_credits,
                "credits_remaining": user.credits_remaining,
            },
        )

    user.deduct_credits(total_credits)

    job = Job(
        user_id=user.id,
        type="remove",
        status="pending",
        input_data={
            "image_ids": body.image_ids,
            "mask_data": body.mask_data,
        },
        credits_used=total_credits,
    )
    db.add(job)
    db.commit()
    db.refresh(job)

    logger.info(f"Removal job {job.id} created — {len(body.image_ids)} images, {total_credits} credits")

    try:
        from app.tasks.remove import process_removal_job
        task = process_removal_job.delay(job.id)
        job.celery_task_id = task.id
        job.status = "processing"
        db.commit()
    except Exception as e:
        logger.warning(f"Could not dispatch removal task: {e}")

    return job


# ── POST /api/jobs/video ──

@router.post("/video", response_model=JobResponse, status_code=201)
def create_video_job(
    body: VideoJobCreateRequest,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if not body.image_ids:
        raise HTTPException(status_code=400, detail="At least one image is required")

    if len(body.image_ids) > 10:
        raise HTTPException(status_code=400, detail="Maximum 10 images per job")

    if body.settings.duration not in (3, 5, 10):
        raise HTTPException(status_code=400, detail="Duration must be 3, 5, or 10 seconds")

    images = db.query(Image).filter(
        Image.id.in_(body.image_ids),
        Image.user_id == user.id,
    ).all()

    if len(images) != len(body.image_ids):
        raise HTTPException(status_code=400, detail="One or more images not found")

    credits_per = JOB_CREDITS["video"]
    total_credits = credits_per * len(body.image_ids)

    if not user.can_afford(total_credits):
        raise HTTPException(
            status_code=402,
            detail={
                "message": "Insufficient credits",
                "credits_needed": total_credits,
                "credits_remaining": user.credits_remaining,
            },
        )

    user.deduct_credits(total_credits)

    job = Job(
        user_id=user.id,
        type="video",
        status="pending",
        input_data={
            "image_ids": body.image_ids,
            "settings": body.settings.model_dump(),
        },
        credits_used=total_credits,
    )
    db.add(job)
    db.commit()
    db.refresh(job)

    logger.info(f"Video job {job.id} created — {len(body.image_ids)} images, {total_credits} credits")

    try:
        from app.tasks.video import process_video_job
        task = process_video_job.delay(job.id)
        job.celery_task_id = task.id
        job.status = "processing"
        db.commit()
    except Exception as e:
        logger.warning(f"Could not dispatch video task: {e}")

    return job


# ── POST /api/jobs/voiceover ──

@router.post("/voiceover", response_model=JobResponse, status_code=201)
def create_voiceover_job(
    body: VoiceoverJobCreateRequest,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if not body.settings.text or not body.settings.text.strip():
        raise HTTPException(status_code=400, detail="Text is required")

    if len(body.settings.text) > 4096:
        raise HTTPException(status_code=400, detail="Text must be under 4096 characters")

    valid_voices = {"alloy", "echo", "fable", "onyx", "nova", "shimmer"}
    if body.settings.voice not in valid_voices:
        raise HTTPException(status_code=400, detail=f"Invalid voice. Choose from: {', '.join(valid_voices)}")

    credits_per = JOB_CREDITS["voiceover"]
    total_credits = credits_per

    if not user.can_afford(total_credits):
        raise HTTPException(
            status_code=402,
            detail={
                "message": "Insufficient credits",
                "credits_needed": total_credits,
                "credits_remaining": user.credits_remaining,
            },
        )

    user.deduct_credits(total_credits)

    job = Job(
        user_id=user.id,
        type="voiceover",
        status="pending",
        input_data={"settings": body.settings.model_dump()},
        credits_used=total_credits,
    )
    db.add(job)
    db.commit()
    db.refresh(job)

    logger.info(f"Voiceover job {job.id} created — {total_credits} credits, voice={body.settings.voice}")

    try:
        from app.tasks.voiceover import process_voiceover_job
        task = process_voiceover_job.delay(job.id)
        job.celery_task_id = task.id
        job.status = "processing"
        db.commit()
    except Exception as e:
        logger.warning(f"Could not dispatch voiceover task: {e}")

    return job


# ── GET /api/jobs/{job_id} ──

@router.get("/{job_id}", response_model=JobResponse)
def get_job(
    job_id: str,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    job = db.query(Job).filter(Job.id == job_id, Job.user_id == user.id).first()
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    return job


# ── GET /api/jobs ──

@router.get("", response_model=list[JobResponse])
def list_jobs(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    limit: int = Query(default=20, le=100),
    offset: int = Query(default=0, ge=0),
):
    jobs = (
        db.query(Job)
        .filter(Job.user_id == user.id)
        .order_by(Job.created_at.desc())
        .offset(offset)
        .limit(limit)
        .all()
    )
    return jobs


# ── WebSocket /api/jobs/{job_id}/ws ──

@router.websocket("/{job_id}/ws")
async def job_status_ws(websocket: WebSocket, job_id: str):
    """
    Real-time job progress stream.
    Sends JSON updates whenever status or progress changes:
      { "status": "processing", "progress": 45, "result_url": null }
    Closes automatically when job reaches a terminal state.
    """
    await websocket.accept()

    try:
        last_status = None
        last_progress = -1

        while True:
            db = next(get_db())
            try:
                job = db.query(Job).filter(Job.id == job_id).first()
                if not job:
                    await websocket.send_json({"error": "Job not found"})
                    break

                # Send update only if something changed
                if job.status != last_status or job.progress != last_progress:
                    last_status = job.status
                    last_progress = job.progress

                    payload = {
                        "status": job.status.value,
                        "progress": job.progress,
                        "result_url": job.result_url,
                    }

                    if job.status == "failed":
                        payload["error"] = job.error_message

                    await websocket.send_json(payload)

                # Stop if terminal
                if job.status in ("completed", "failed"):
                    break
            finally:
                db.close()

            await asyncio.sleep(1)

    except WebSocketDisconnect:
        pass
    except Exception as e:
        logger.error(f"WebSocket error for job {job_id}: {e}")
