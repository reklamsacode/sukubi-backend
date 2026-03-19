import logging
import base64
from datetime import datetime, timezone

from app.tasks.enhance import celery_app, _update_job, _refund_credits

logger = logging.getLogger(__name__)


@celery_app.task(bind=True, max_retries=1, name="remove_objects")
def process_removal_job(self, job_id: str):
    """
    Process an object removal job via Stability AI inpainting.

    Pipeline per image:
      1. Get presigned URL for the original image
      2. Upload mask from base64 to R2 (temporary)
      3. Call Stability AI inpainting
      4. Mark job as completed
    """
    from app.models.base import SessionLocal
    from app.models.job import Job
    from app.models.image import Image
    from app.services.ai_service import (
        fal_service,
        FalError,
        FalRateLimit,
    )
    from app.services.storage import (
        generate_presigned_url,
        upload_file,
        delete_file,
    )

    db = SessionLocal()

    try:
        job = db.query(Job).filter(Job.id == job_id).first()
        if not job:
            logger.error(f"Job {job_id} not found")
            return {"error": "Job not found"}

        _update_job(db, job, status="processing", progress=5)
        logger.info(f"Processing removal job {job_id}")

        input_data = job.input_data or {}
        image_ids = input_data.get("image_ids", [])
        mask_data = input_data.get("mask_data", {})

        if not image_ids:
            _update_job(db, job, status="failed", error_message="No images provided")
            _refund_credits(db, job)
            return {"error": "No images provided"}

        result_urls = []
        total_images = len(image_ids)
        temp_mask_keys = []

        for idx, image_id in enumerate(image_ids):
            base_progress = int((idx / total_images) * 80) + 10

            # Step 1: Get source image URL
            image = db.query(Image).filter(Image.id == image_id).first()
            if not image:
                logger.warning(f"Image {image_id} not found, skipping")
                continue

            source_url = generate_presigned_url(image.s3_key, expires_in=600)
            _update_job(db, job, progress=base_progress)

            # Step 2: Upload mask to R2
            mask_b64 = mask_data.get(image_id, "")
            if not mask_b64:
                logger.warning(f"No mask for image {image_id}, skipping")
                continue

            # Strip data URL prefix if present
            if "," in mask_b64:
                mask_b64 = mask_b64.split(",", 1)[1]

            mask_bytes = base64.b64decode(mask_b64)
            mask_key, mask_url = upload_file(mask_bytes, f"mask-{image_id}.png", "image/png")
            temp_mask_keys.append(mask_key)

            step2_progress = base_progress + int(20 / total_images)
            _update_job(db, job, progress=step2_progress)

            # Step 3: Call Stability AI inpainting
            try:
                result_url = fal_service.remove_objects(
                    image_url=source_url,
                    mask_url=mask_url,
                )
            except FalRateLimit as e:
                logger.warning(f"Rate limited: {e}")
                raise self.retry(exc=e, countdown=60)

            result_urls.append(result_url)
            step3_progress = base_progress + int(70 / total_images)
            _update_job(db, job, progress=min(step3_progress, 90))
            logger.info(f"  [{idx+1}/{total_images}] Removal complete")

        # Cleanup temp mask files
        for key in temp_mask_keys:
            try:
                delete_file(key)
            except Exception:
                pass

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

        logger.info(f"Removal job {job_id} completed — {len(result_urls)} image(s)")
        return {"job_id": job_id, "status": "completed", "result_urls": result_urls}

    except FalError as exc:
        logger.error(f"Removal job {job_id} AI error: {exc}")
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
        logger.exception(f"Removal job {job_id} unexpected error: {exc}")
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
