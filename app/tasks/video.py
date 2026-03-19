import logging
from datetime import datetime, timezone

from app.tasks.enhance import celery_app, _update_job, _refund_credits

logger = logging.getLogger(__name__)


@celery_app.task(bind=True, max_retries=1, name="create_video")
def process_video_job(self, job_id: str):
    """
    Process an image-to-video job via Stability AI SVD.

    Pipeline:
      1. For each image, generate a video clip
      2. Store result in R2
      3. Mark job as completed
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
    from app.services.storage import generate_presigned_url

    db = SessionLocal()

    try:
        job = db.query(Job).filter(Job.id == job_id).first()
        if not job:
            logger.error(f"Job {job_id} not found")
            return {"error": "Job not found"}

        _update_job(db, job, status="processing", progress=5)
        logger.info(f"Processing video job {job_id}")

        input_data = job.input_data or {}
        image_ids = input_data.get("image_ids", [])
        video_settings = input_data.get("settings", {})

        if not image_ids:
            _update_job(db, job, status="failed", error_message="No images provided")
            _refund_credits(db, job)
            return {"error": "No images provided"}

        motion_type = video_settings.get("motion_type", "pan_right")
        duration = video_settings.get("duration", 5)

        result_urls = []
        total_images = len(image_ids)

        for idx, image_id in enumerate(image_ids):
            base_progress = int((idx / total_images) * 85) + 10

            # Step 1: Get source image
            image = db.query(Image).filter(Image.id == image_id).first()
            if not image:
                logger.warning(f"Image {image_id} not found, skipping")
                continue

            source_url = generate_presigned_url(image.s3_key, expires_in=600)
            _update_job(db, job, progress=base_progress)
            logger.info(f"  [{idx+1}/{total_images}] Source ready: {image.file_name}")

            # Step 2: Generate video
            try:
                video_url = fal_service.create_video(
                    image_url=source_url,
                    motion_type=motion_type,
                    duration=duration,
                )
            except FalRateLimit as e:
                logger.warning(f"Rate limited: {e}")
                raise self.retry(exc=e, countdown=60)

            result_urls.append(video_url)
            step_progress = base_progress + int(75 / total_images)
            _update_job(db, job, progress=min(step_progress, 92))
            logger.info(f"  [{idx+1}/{total_images}] Video generated")

        # Complete
        final_url = result_urls[0] if result_urls else None
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

        logger.info(f"Video job {job_id} completed — {len(result_urls)} video(s)")
        return {"job_id": job_id, "status": "completed", "result_urls": result_urls}

    except (FalError, FalTimeout) as exc:
        logger.error(f"Video job {job_id} error: {exc}")
        db.rollback()
        if self.request.retries < self.max_retries:
            _update_job(db, job, error_message=f"Retrying: {str(exc)[:200]}")
            raise self.retry(exc=exc, countdown=30)
        else:
            job = db.query(Job).filter(Job.id == job_id).first()
            if job:
                _update_job(db, job, status="failed", error_message=str(exc)[:500])
                _refund_credits(db, job)
            return {"error": str(exc)}

    except Exception as exc:
        logger.exception(f"Video job {job_id} unexpected error: {exc}")
        db.rollback()
        try:
            job = db.query(Job).filter(Job.id == job_id).first()
            if job:
                _update_job(db, job, status="failed", error_message=f"Internal error: {str(exc)[:400]}")
                _refund_credits(db, job)
        except Exception:
            pass
        if self.request.retries < self.max_retries:
            raise self.retry(exc=exc, countdown=60)
        return {"error": str(exc)}

    finally:
        db.close()
