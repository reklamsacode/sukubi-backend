import logging
from datetime import datetime, timezone

from app.tasks.enhance import celery_app, _update_job, _refund_credits

logger = logging.getLogger(__name__)


@celery_app.task(bind=True, max_retries=1, name="create_voiceover")
def process_voiceover_job(self, job_id: str):
    """
    Process a voiceover job via OpenAI TTS API.

    Pipeline:
      1. Call OpenAI TTS to generate audio
      2. If attach_to_video specified, merge audio with video via ffmpeg
      3. Store result in R2
    """
    from app.models.base import SessionLocal
    from app.models.job import Job
    from app.services.ai_service import (
        fal_service,
        FalError,
        FalRateLimit,
    )

    db = SessionLocal()

    try:
        job = db.query(Job).filter(Job.id == job_id).first()
        if not job:
            logger.error(f"Job {job_id} not found")
            return {"error": "Job not found"}

        _update_job(db, job, status="processing", progress=10)
        logger.info(f"Processing voiceover job {job_id}")

        input_data = job.input_data or {}
        vo_settings = input_data.get("settings", {})

        text = vo_settings.get("text", "")
        voice = vo_settings.get("voice", "alloy")
        speed = vo_settings.get("speed", 1.0)
        attach_to_video = vo_settings.get("attach_to_video")

        if not text:
            _update_job(db, job, status="failed", error_message="No text provided")
            _refund_credits(db, job)
            return {"error": "No text provided"}

        # Step 1: Generate voiceover
        _update_job(db, job, progress=20)
        try:
            audio_url = fal_service.generate_voiceover(
                text=text,
                voice=voice,
                speed=speed,
            )
        except FalRateLimit as e:
            logger.warning(f"Rate limited: {e}")
            raise self.retry(exc=e, countdown=60)

        _update_job(db, job, progress=70)
        logger.info(f"Voiceover generated: {audio_url}")

        result_url = audio_url

        # Step 2: If attach_to_video, merge (placeholder — needs ffmpeg)
        if attach_to_video:
            video_job = db.query(Job).filter(Job.id == attach_to_video).first()
            if video_job and video_job.result_url:
                # In production: download video + audio, ffmpeg merge, upload result
                # For now, store both URLs
                input_data["video_url"] = video_job.result_url
                input_data["audio_url"] = audio_url
                job.input_data = input_data
                logger.info(f"Attached to video job {attach_to_video} (merge pending ffmpeg)")

            _update_job(db, job, progress=90)

        # Complete
        _update_job(
            db, job,
            status="completed",
            progress=100,
            result_url=result_url,
            completed_at=datetime.now(timezone.utc),
        )

        logger.info(f"Voiceover job {job_id} completed")
        return {"job_id": job_id, "status": "completed", "result_url": result_url}

    except FalError as exc:
        logger.error(f"Voiceover job {job_id} error: {exc}")
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
        logger.exception(f"Voiceover job {job_id} unexpected error: {exc}")
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
