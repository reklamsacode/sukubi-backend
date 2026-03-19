import logging
from celery import Celery
from datetime import datetime, timezone

from app.config import get_settings

logger = logging.getLogger(__name__)
settings = get_settings()

celery_app = Celery(
    "nescora",
    broker=settings.REDIS_URL,
    backend=settings.REDIS_URL,
)

celery_app.conf.update(
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    task_track_started=True,
    task_acks_late=True,  # re-deliver if worker crashes
)


def _update_job(db, job, *, status=None, progress=None, result_url=None, error_message=None, completed_at=None):
    """Helper: update job fields and commit."""
    if status is not None:
        job.status = status
    if progress is not None:
        job.progress = progress
    if result_url is not None:
        job.result_url = result_url
    if error_message is not None:
        job.error_message = error_message
    if completed_at is not None:
        job.completed_at = completed_at
    db.commit()


def _refund_credits(db, job):
    """Refund credits to the user when a job fails permanently."""
    from app.models.user import User

    user = db.query(User).filter(User.id == job.user_id).first()
    if user and job.credits_used > 0:
        user.refund_credits(job.credits_used)
        logger.info(f"Refunded {job.credits_used} credits to user {user.id}")
        db.commit()


@celery_app.task(bind=True, max_retries=1, name="enhance_image")
def process_enhance_job(self, job_id: str):
    """
    Process an image enhancement job via Replicate API.

    Pipeline per image:
      1. Get presigned URL for the original image    (progress → 10)
      2. Call ReplicateService.enhance_image()        (progress → 50)
      3. Copy result from Replicate CDN → R2          (progress → 90)
      4. Mark job as completed                        (progress → 100)

    On failure:
      - Retry once (for transient errors / timeouts)
      - After max retries, mark as FAILED and refund credits
    """
    from app.models.base import SessionLocal
    from app.models.job import Job
    from app.models.image import Image
    from app.services.ai_service import (
        fal_service,
        FalError,
        FalTimeout,
        FalRateLimit,
    )
    from app.services.storage import generate_presigned_url, download_from_url_and_upload

    db = SessionLocal()

    try:
        job = db.query(Job).filter(Job.id == job_id).first()
        if not job:
            logger.error(f"Job {job_id} not found")
            return {"error": "Job not found"}

        # ── Start processing ──
        _update_job(db, job, status="processing", progress=5)
        logger.info(f"Processing job {job_id}")

        input_data = job.input_data or {}
        image_ids = input_data.get("image_ids", [])
        enhance_settings = input_data.get("settings", {})

        if not image_ids:
            _update_job(
                db, job,
                status="failed",
                error_message="No images provided",
            )
            _refund_credits(db, job)
            return {"error": "No images provided"}

        # Resolve settings
        level = enhance_settings.get("level", 1)
        style = enhance_settings.get("style", "natural")
        output_size = enhance_settings.get("output_size", "original")

        result_urls = []
        total_images = len(image_ids)

        for idx, image_id in enumerate(image_ids):
            base_progress = int((idx / total_images) * 80) + 10  # 10-90 range

            # ── Step 1: Get source image URL ──
            image = db.query(Image).filter(Image.id == image_id).first()
            if not image:
                logger.warning(f"Image {image_id} not found, skipping")
                continue

            # Use presigned URL so Replicate can access the image
            source_url = generate_presigned_url(image.s3_key, expires_in=600)
            _update_job(db, job, progress=base_progress)
            logger.info(f"  [{idx+1}/{total_images}] Source ready: {image.file_name}")

            # ── Step 2: Call Replicate API ──
            try:
                enhanced_url = fal_service.enhance_image(
                    image_url=source_url,
                    level=level,
                    style=style,
                    output_size=output_size,
                )
            except FalRateLimit as e:
                # Wait and retry the whole task
                logger.warning(f"Rate limited: {e}")
                raise self.retry(exc=e, countdown=60)

            step2_progress = base_progress + int(40 / total_images)
            _update_job(db, job, progress=step2_progress)
            logger.info(f"  [{idx+1}/{total_images}] Enhancement complete")

            # ── Step 3: Copy result to R2 for permanent storage ──
            r2_key, permanent_url = download_from_url_and_upload(
                enhanced_url,
                dest_prefix=f"results/{job_id}",
            )

            result_urls.append(permanent_url)
            step3_progress = base_progress + int(60 / total_images)
            _update_job(db, job, progress=min(step3_progress, 90))
            logger.info(f"  [{idx+1}/{total_images}] Saved to R2: {r2_key}")

        # ── Step 4: Mark completed ──
        final_url = result_urls[0] if len(result_urls) == 1 else result_urls[0]
        # For multi-image jobs, store all URLs in input_data
        if len(result_urls) > 1:
            input_data["result_urls"] = result_urls
            job.input_data = input_data

        _update_job(
            db, job,
            status="completed",
            progress=100,
            result_url=final_url,
            completed_at=datetime.now(timezone.utc),
        )

        logger.info(f"Job {job_id} completed — {len(result_urls)} image(s) enhanced")
        return {
            "job_id": job_id,
            "status": "completed",
            "result_urls": result_urls,
        }

    except (FalError, FalTimeout) as exc:
        logger.error(f"Job {job_id} Replicate error: {exc}")
        db.rollback()

        if self.request.retries < self.max_retries:
            # Retry once for transient failures
            _update_job(
                db, job,
                error_message=f"Retrying: {str(exc)[:200]}",
            )
            raise self.retry(exc=exc, countdown=30)
        else:
            # Final failure — mark as failed and refund
            job = db.query(Job).filter(Job.id == job_id).first()
            if job:
                _update_job(
                    db, job,
                    status="failed",
                    error_message=str(exc)[:500],
                )
                _refund_credits(db, job)
            return {"error": str(exc)}

    except Exception as exc:
        logger.exception(f"Job {job_id} unexpected error: {exc}")
        db.rollback()

        # Mark as failed and refund
        try:
            job = db.query(Job).filter(Job.id == job_id).first()
            if job:
                _update_job(
                    db, job,
                    status="failed",
                    error_message=f"Internal error: {str(exc)[:400]}",
                )
                _refund_credits(db, job)
        except Exception:
            logger.exception("Failed to update job status after error")

        if self.request.retries < self.max_retries:
            raise self.retry(exc=exc, countdown=60)

        return {"error": str(exc)}

    finally:
        db.close()
