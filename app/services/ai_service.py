import time
import logging
import httpx

from app.config import get_settings

logger = logging.getLogger(__name__)
settings = get_settings()

# ═══════════════════════════════════════════
#  fal.ai Model Registry
#
#  Pricing (per request):
#    enhance (Topaz)    : $0.08–$0.32 depending on output resolution
#    enhance (SeedVR)   : ~$0.002 per 2MP (budget)
#    staging (Kontext)  : $0.04 per image
#    inpaint (Flux Fill): $0.05 per MP
#    video (Kling v3)   : $0.112/s → ~$0.56 for 5s
#    video (SVD)        : $0.075 flat (budget)
#    bg-remove (BiRef)  : free
# ═══════════════════════════════════════════

FAL_MODELS = {
    "enhance": "fal-ai/topaz/upscale/image",
    "enhance_budget": "fal-ai/seedvr/upscale/image",
    "stage": "fal-ai/flux-pro/kontext",
    "inpaint": "fal-ai/flux-pro/v1/fill",
    "video": "fal-ai/kling-video/v3/pro/image-to-video",
    "video_budget": "fal-ai/stable-video",
    "bg_remove": "fal-ai/birefnet",
}

FAL_QUEUE_BASE = "https://queue.fal.run"

POLL_INTERVAL = 2  # seconds
MAX_POLL_TIME = 300  # 5 minutes (video can be slow)

# ── Virtual staging prompts ──

STAGING_PROMPTS = {
    "living_room": "a beautifully staged living room",
    "bedroom": "a professionally staged bedroom",
    "kitchen": "a modern staged kitchen",
    "bathroom": "a clean staged bathroom",
    "dining_room": "an elegant staged dining room",
    "office": "a professional staged home office",
    "empty_room": "a staged room",
}

STYLE_MODIFIERS = {
    "modern": "modern contemporary furniture, clean lines, neutral colors",
    "scandinavian": "scandinavian style, light wood, minimalist, hygge",
    "industrial": "industrial style, metal accents, exposed brick, urban",
    "luxury": "luxury high-end furniture, marble, gold accents, premium",
    "minimalist": "minimalist design, very few carefully chosen pieces",
    "traditional": "traditional classic furniture, warm colors, elegant",
}


# ═══════════════════════════════════════════
#  Exceptions
# ═══════════════════════════════════════════

class FalError(Exception):
    pass


class FalTimeout(Exception):
    pass


class FalRateLimit(Exception):
    pass


# ═══════════════════════════════════════════
#  FalService
# ═══════════════════════════════════════════

class FalService:
    """Client for fal.ai queue API."""

    def __init__(self):
        self.key = settings.FAL_KEY
        self._mock = not self.key

    @property
    def _headers(self) -> dict:
        return {
            "Authorization": f"Key {self.key}",
            "Content-Type": "application/json",
        }

    # ────────────────────────────────────────
    #  Core: submit → poll → result
    # ────────────────────────────────────────

    def _run(self, model: str, input_data: dict, max_poll: int | None = None) -> dict:
        """
        Submit a job to fal.ai queue, poll until done, return output dict.

        Queue flow:
          POST queue.fal.run/{model}  →  { request_id, status_url, response_url }
          GET  .../requests/{id}/status  →  IN_QUEUE | IN_PROGRESS | COMPLETED
          GET  .../requests/{id}  →  result payload
        """
        if self._mock:
            logger.info(f"Mock mode — skipping fal.ai call to {model}")
            time.sleep(2)
            h = hash(str(input_data)) & 0xFFFFFFFF
            return {
                "image": {"url": f"https://mock-fal.nescora.dev/{h}.jpg"},
                "images": [{"url": f"https://mock-fal.nescora.dev/{h}.jpg"}],
                "video": {"url": f"https://mock-fal.nescora.dev/{h}.mp4"},
            }

        timeout = max_poll or MAX_POLL_TIME

        # 1. Submit to queue
        submit_url = f"{FAL_QUEUE_BASE}/{model}"
        with httpx.Client(timeout=30.0) as client:
            resp = client.post(submit_url, headers=self._headers, json=input_data)

            if resp.status_code == 429:
                raise FalRateLimit(f"fal.ai rate limited on {model}")
            if resp.status_code >= 400:
                raise FalError(f"fal.ai submit error {resp.status_code}: {resp.text[:300]}")

            data = resp.json()

        request_id = data.get("request_id")
        status_url = data.get("status_url")
        response_url = data.get("response_url")

        if not request_id:
            # Some models return result directly (sync)
            return data

        logger.info(f"fal.ai submitted: {model} → {request_id}")

        # 2. Poll for completion
        if not status_url:
            status_url = f"{FAL_QUEUE_BASE}/{model}/requests/{request_id}/status"
        if not response_url:
            response_url = f"{FAL_QUEUE_BASE}/{model}/requests/{request_id}"

        elapsed = 0.0
        with httpx.Client(timeout=30.0) as client:
            while elapsed < timeout:
                time.sleep(POLL_INTERVAL)
                elapsed += POLL_INTERVAL

                poll = client.get(f"{status_url}?logs=1", headers=self._headers)
                if poll.status_code >= 400:
                    raise FalError(f"fal.ai status error: {poll.text[:300]}")

                status_data = poll.json()
                status = status_data.get("status", "")

                if status == "COMPLETED":
                    # 3. Fetch result
                    result = client.get(response_url, headers=self._headers)
                    result.raise_for_status()
                    logger.info(f"fal.ai completed: {request_id} ({elapsed:.0f}s)")
                    return result.json()

                if status == "FAILED":
                    error = status_data.get("error", "Unknown error")
                    raise FalError(f"fal.ai failed: {error}")

                pos = status_data.get("queue_position", "?")
                logger.debug(f"fal.ai {request_id}: {status} (pos={pos}, {elapsed:.0f}s)")

        raise FalTimeout(f"fal.ai {request_id} timed out after {timeout}s")

    def _url_from(self, result: dict, key: str = "image") -> str:
        """Extract output URL from fal.ai result. Handles multiple response shapes."""
        # { image: { url } }
        if key in result and isinstance(result[key], dict) and "url" in result[key]:
            return result[key]["url"]

        # { images: [{ url }] }
        images = result.get("images")
        if isinstance(images, list) and images:
            return images[0].get("url", "")

        # { video: { url } }
        video = result.get("video")
        if isinstance(video, dict) and "url" in video:
            return video["url"]

        # { output: "url" }
        output = result.get("output")
        if isinstance(output, str) and output.startswith("http"):
            return output

        raise FalError(f"Cannot extract URL from fal.ai result keys: {list(result.keys())}")

    # ────────────────────────────────────────
    #  ENHANCE — Topaz upscale
    # ────────────────────────────────────────

    def enhance_image(
        self,
        image_url: str,
        level: int = 1,
        style: str = "natural",
        output_size: str = "original",
    ) -> str:
        """
        Enhance a real-estate photo via fal-ai/topaz/upscale/image.

        level 0=low → 1x sharpen only, level 1=medium → 2x, level 2=high → 4x.
        output_size "4k" forces 4x regardless.
        """
        scale = 4.0 if output_size == "4k" else (4.0 if level >= 2 else 2.0 if level >= 1 else 1.0)
        model_preset = "High Fidelity V2" if style == "hdr" else "Standard V2"

        logger.info(f"enhance: model=Topaz, scale={scale}, preset={model_preset}")

        result = self._run(FAL_MODELS["enhance"], {
            "image_url": image_url,
            "model": model_preset,
            "upscale_factor": scale,
            "output_format": "jpeg",
            "face_enhancement": True,
            "face_enhancement_strength": 0.8,
        })

        return self._url_from(result, "image")

    # ────────────────────────────────────────
    #  VIRTUAL STAGING — Flux Pro Kontext
    # ────────────────────────────────────────

    def virtual_stage(
        self,
        image_url: str,
        room_type: str = "living_room",
        style: str = "modern",
        remove_existing: bool = False,
        keep_decorations: bool = True,
    ) -> str:
        """
        Virtually stage a room via fal-ai/flux-pro/kontext.

        Kontext preserves room structure while adding furniture via prompt.
        """
        room_desc = STAGING_PROMPTS.get(room_type, "a staged room")
        style_desc = STYLE_MODIFIERS.get(style, "modern furniture")
        keep_clause = ", keeping existing wall art and decorations" if keep_decorations else ""
        remove_clause = "Remove all existing furniture and clutter first, then add " if remove_existing else "Add "

        prompt = (
            f"{remove_clause}{style_desc} to this {room_desc}{keep_clause}. "
            f"Real estate photography, bright natural lighting, photorealistic, 8k."
        )

        logger.info(f"stage: room={room_type}, style={style}, remove={remove_existing}")

        result = self._run(FAL_MODELS["stage"], {
            "image_url": image_url,
            "prompt": prompt,
            "guidance_scale": 3.5,
            "num_inference_steps": 50,
            "output_format": "jpeg",
            "safety_tolerance": "6",
        })

        return self._url_from(result)

    # ────────────────────────────────────────
    #  OBJECT REMOVAL — Flux Pro Fill (inpainting)
    # ────────────────────────────────────────

    def remove_objects(
        self,
        image_url: str,
        mask_url: str,
    ) -> str:
        """
        Remove objects via fal-ai/flux-pro/v1/fill.

        mask_url: white = area to fill/remove.
        """
        prompt = (
            "Clean empty room, same wall color, same flooring, same lighting "
            "as surrounding area, photorealistic, high quality, real estate photography"
        )

        logger.info("remove_objects via Flux Pro Fill")

        result = self._run(FAL_MODELS["inpaint"], {
            "image_url": image_url,
            "mask_url": mask_url,
            "prompt": prompt,
            "output_format": "jpeg",
            "safety_tolerance": "6",
        })

        return self._url_from(result)

    # ────────────────────────────────────────
    #  VIDEO — Kling v3 Pro image-to-video
    # ────────────────────────────────────────

    def create_video(
        self,
        image_url: str,
        motion_type: str = "pan_right",
        duration: int = 5,
    ) -> str:
        """
        Create a cinematic video from a still image via fal-ai/kling-video/v3/pro.

        Kling v3 Pro: $0.112/s → ~$0.56 for 5s, ~$1.12 for 10s.
        """
        # Build motion prompt from type
        motion_prompts = {
            "pan_right": "Slow cinematic camera pan from left to right, smooth steady movement",
            "pan_left": "Slow cinematic camera pan from right to left, smooth steady movement",
            "zoom_in": "Dramatic slow zoom in towards the center, cinematic focus",
            "zoom_out": "Slow zoom out revealing the full scene, wide establishing shot",
            "dolly_forward": "Smooth dolly forward into the room, first-person walkthrough",
            "orbit": "Slow 360 degree orbit around the room, cinematic rotating view",
        }
        prompt = motion_prompts.get(motion_type, "Slow cinematic camera movement, smooth and steady")
        prompt += ", real estate property tour, professional videography, stable footage"

        logger.info(f"video: motion={motion_type}, duration={duration}s, model=Kling v3 Pro")

        result = self._run(FAL_MODELS["video"], {
            "prompt": prompt,
            "start_image_url": image_url,
            "duration": duration,
            "aspect_ratio": "16:9",
            "cfg_scale": 0.5,
            "negative_prompt": "blurry, shaky, distorted, low quality, watermark",
        }, max_poll=MAX_POLL_TIME)

        return self._url_from(result, "video")

    # ────────────────────────────────────────
    #  BACKGROUND REMOVAL — BiRefNet (free)
    # ────────────────────────────────────────

    def remove_background(self, image_url: str, output_mask: bool = False) -> str:
        """
        Remove background via fal-ai/birefnet. Effectively free.

        If output_mask=True, returns mask URL instead (useful for inpainting pipeline).
        """
        logger.info(f"bg_remove: mask_only={output_mask}")

        result = self._run(FAL_MODELS["bg_remove"], {
            "image_url": image_url,
            "model": "General Use (Light)",
            "operating_resolution": "1024x1024",
            "output_format": "png",
            "output_mask": output_mask,
            "refine_foreground": True,
        })

        if output_mask and "mask_image" in result:
            return result["mask_image"]["url"]

        return self._url_from(result, "image")

    # ────────────────────────────────────────
    #  VOICEOVER — OpenAI TTS (fal.ai doesn't offer TTS)
    # ────────────────────────────────────────

    def generate_voiceover(
        self,
        text: str,
        voice: str = "alloy",
        speed: float = 1.0,
    ) -> str:
        """Generate voiceover via OpenAI TTS API. Returns URL of MP3 on R2."""
        openai_key = settings.OPENAI_API_KEY

        if not openai_key:
            if self._mock:
                logger.info("Mock mode — skipping OpenAI TTS")
                time.sleep(2)
                return f"https://mock-openai.nescora.dev/tts/{hash(text) & 0xFFFFFFFF}.mp3"
            raise FalError("OPENAI_API_KEY not configured for voiceover")

        logger.info(f"voiceover: voice={voice}, speed={speed}, chars={len(text)}")

        with httpx.Client(timeout=60.0) as client:
            resp = client.post(
                "https://api.openai.com/v1/audio/speech",
                headers={
                    "Authorization": f"Bearer {openai_key}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": "tts-1-hd",
                    "input": text[:4096],
                    "voice": voice,
                    "speed": speed,
                    "response_format": "mp3",
                },
            )

            if resp.status_code == 429:
                raise FalRateLimit("OpenAI TTS rate limited")
            if resp.status_code != 200:
                raise FalError(f"OpenAI TTS error {resp.status_code}: {resp.text[:200]}")

            audio_bytes = resp.content

        from app.services.storage import upload_file
        key, public_url = upload_file(audio_bytes, "voiceover.mp3", "audio/mpeg")

        logger.info(f"Voiceover → R2: {key} ({len(audio_bytes)} bytes)")
        return public_url


# Singleton
fal_service = FalService()
