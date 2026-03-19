import logging
from datetime import datetime, timezone

from app.tasks.enhance import celery_app, _update_job, _refund_credits

logger = logging.getLogger(__name__)


@celery_app.task(bind=True, max_retries=1, name="stage_room")
def process_staging_job(self, job_id: str):
    """
    Process a virtual staging job via Stability AI.

    Pipeline per image:
      1. Get presigned URL for the original image
      2. If remove_existing → inpaint to clear furniture first
      3. Call Stability AI image-to-image for staging
      4. Copy result to R2
      5. Mark job as completed
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

        _update_job(db, job, status="processing", progress=5)
        logger.info(f"Processing staging job {job_id}")

        input_data = job.input_data or {}
        image_ids = input_data.get("image_ids", [])
        staging_settings = input_data.get("settings", {})

        if not image_ids:
            _update_job(db, job, status="failed", error_message="No images provided")
            _refund_credits(db, job)
            return {"error": "No images provided"}

        room_type = staging_settings.get("room_type", "living_room")
        furniture_style = staging_settings.get("furniture_style", "modern")
        remove_existing = staging_settings.get("remove_existing", False)
        keep_decorations = staging_settings.get("keep_decorations", True)

        result_urls = []
        total_images = len(image_ids)

        for idx, image_id in enumerate(image_ids):
            base_progress = int((idx / total_images) * 80) + 10

            # Step 1: Get source image URL
            image = db.query(Image).filter(Image.id == image_id).first()
            if not image:
                logger.warning(f"Image {image_id} not found, skipping")
                continue

            source_url = generate_presigned_url(image.s3_key, expires_in=600)
            _update_job(db, job, progress=base_progress)
            logger.info(f"  [{idx+1}/{total_images}] Source ready: {image.file_name}")

            # Step 2: Call Stability AI for staging
            try:
                staged_url = fal_service.virtual_stage(
                    image_url=source_url,
                    room_type=room_type,
                    style=furniture_style,
                    remove_existing=remove_existing,
                    keep_decorations=keep_decorations,
                )
            except FalRateLimit as e:
                logger.warning(f"Rate limited: {e}")
                raise self.retry(exc=e, countdown=60)

            step2_progress = base_progress + int(50 / total_images)
            _update_job(db, job, progress=step2_progress)
            logger.info(f"  [{idx+1}/{total_images}] Staging complete")

            # Step 3: Copy result to R2 (if not already there from Stability handler)
            if "mock-stability" in staged_url or "mock-r2" in staged_url:
                # Mock mode — URL is already "permanent"
                permanent_url = staged_url
            else:
                # In production, the Stability result is already uploaded by _run_stability_img2img
                permanent_url = staged_url

            result_urls.append(permanent_url)
            step3_progress = base_progress + int(70 / total_images)
            _update_job(db, job, progress=min(step3_progress, 90))

        # Step 4: Mark completed
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

        logger.info(f"Staging job {job_id} completed — {len(result_urls)} image(s)")
        return {"job_id": job_id, "status": "completed", "result_urls": result_urls}

    except (FalError, FalTimeout) as exc:
        logger.error(f"Staging job {job_id} AI error: {exc}")
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
        logger.exception(f"Staging job {job_id} unexpected error: {exc}")
        db.rollback()
        try:
            job = db.query(Job).filter(Job.id == job_id).first()
            if job:
                _update_job(db, job, status="failed", error_message=f"Internal error: {str(exc)[:400]}")
                _refund_credits(db, job)
        except Exception:
            logger.exception("Failed to update job status after error")

        if self.request.retries < self.max_retries:
            raise self.retry(exc=exc, countdown=60)
        return {"error": str(exc)}

    finally:
        db.close()
