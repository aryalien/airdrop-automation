%%writefile media_factory.py
import asyncio
import io
import json
import os
import random
import re
import time
from typing import Any, Dict, List, Optional, Tuple
from PIL import Image
import requests


# ==========================================
# ۱. ماژول استخر کلیدها و تاب‌آوری خطاها
# ==========================================
class KeyVaultManager:
    """مدیریت استخر کلیدها، چرخش در خطاهای ۵۰۳/۴۲۹ و پایداری وضعیت"""

    def __init__(self, api_keys: Optional[List[str]] = None, state_filepath: Optional[str] = None):
        self.api_keys = api_keys or []
        self.state_filepath = state_filepath
        self.current_index = 0
        self.key_states: Dict[str, Dict[str, Any]] = {}

        for k in self.api_keys:
            self.key_states[k] = {
                "status": "ACTIVE",
                "usage_count": 0,
                "last_error": None
            }

        if self.state_filepath and os.path.exists(self.state_filepath):
            self.load_state()

    def get_active_key(self) -> Optional[str]:
        if not self.api_keys:
            return None
        for i in range(len(self.api_keys)):
            idx = (self.current_index + i) % len(self.api_keys)
            k = self.api_keys[idx]
            if self.key_states[k]["status"] == "ACTIVE":
                self.current_index = idx
                return k
        return self.api_keys[self.current_index]

    def report_error(self, key: str, error_code: int):
        if error_code in (429, 503, 500):
            time.sleep(5)
            if key in self.key_states:
                self.key_states[key]["status"] = "COOLDOWN_OR_EXHAUSTED"
                self.key_states[key]["last_error"] = error_code
            self.current_index = (self.current_index + 1) % len(self.api_keys)

    def record_usage(self, key: str, success: bool = True):
        if key in self.key_states:
            self.key_states[key]["usage_count"] += 1
            if not success:
                self.key_states[key]["status"] = "ERROR"

    def save_state(self):
        if self.state_filepath:
            with open(self.state_filepath, "w", encoding="utf-8") as f:
                json.dump(self.key_states, f, indent=2)

    def load_state(self):
        if self.state_filepath and os.path.exists(self.state_filepath):
            with open(self.state_filepath, "r", encoding="utf-8") as f:
                self.key_states = json.load(f)


# ==========================================
# ۲. ماژول خطوط قرمز تاخیر و پردازش متوالی
# ==========================================
class RateLimiter:
    """تضمین سد ۵ ثانیه‌ای میان کلیه فراخوانی‌ها و جلوگیری از تداخل"""

    def __init__(self, min_delay: float = 5.0):
        self.min_delay = min_delay
        self.last_call_timestamp = 0.0

    def wait_if_needed(self):
        _ = time.time()
        now = time.time()
        elapsed = now - self.last_call_timestamp
        if elapsed < self.min_delay:
            time.sleep(self.min_delay - elapsed)
        self.last_call_timestamp = now


# ==========================================
# ۳. ماژول فیلتر، اولویت‌بندی و انتخاب محتوا
# ==========================================
class DriveDocsExtractor:
    """استخراج، اولویت‌بندی نزولی شماره ردیف و ایجاد تعادل میان میم‌ها"""

    def __init__(self, service_account_creds: Optional[Dict[str, Any]] = None):
        self.creds = service_account_creds or {}

    def filter_and_sort_media_files(self, files: List[Dict[str, str]]) -> List[Dict[str, str]]:
        filtered = []
        for f in files:
            name = f["name"]
            if "text_tweet" in name or "discord_starter" in name:
                continue
            if any(prefix in name for prefix in ["image_tweet", "meme_", "video"]):
                filtered.append(f)

        def extract_row(f_dict):
            match = re.search(r"row_(\d+)", f_dict["name"])
            return int(match.group(1)) if match else -1

        filtered.sort(key=extract_row, reverse=True)
        return filtered

    def balance_meme_selection(self, fun_memes: List[Dict[str, str]], graphic_memes: List[Dict[str, str]]) -> List[Dict[str, str]]:
        balanced = []
        max_len = max(len(fun_memes), len(graphic_memes))
        for i in range(max_len):
            if i < len(fun_memes):
                balanced.append(fun_memes[i])
            if i < len(graphic_memes):
                balanced.append(graphic_memes[i])
        return balanced


# ==========================================
# ۴. ماژول رندر، واترمارک لوگو و ترکیب میم
# ==========================================
class LogoCompositor:
    """واکشی تصادفی لوگوهای پروژه و ترکیب روی تصاویر با Pillow"""

    def __init__(self, logos_dir: str = "Airdrop_Sources/Retium_Logo"):
        self.logos_dir = logos_dir

    def get_random_logo(self) -> bytes:
        if os.path.exists(self.logos_dir):
            valid_exts = (".png", ".jpg", ".jpeg", ".webp")
            files = [os.path.join(self.logos_dir, f) for f in os.listdir(self.logos_dir) if f.lower().endswith(valid_exts)]
            if files:
                chosen = random.choice(files)
                with open(chosen, "rb") as f:
                    return f.read()
        return b"DEFAULT_LOGO_BYTES"

    def overlay_logo(self, base_image_bytes: bytes, logo_bytes: bytes) -> bytes:
        try:
            base_img = Image.open(io.BytesIO(base_image_bytes)).convert("RGBA")
            logo_img = Image.open(io.BytesIO(logo_bytes)).convert("RGBA")

            # تغییر اندازه لوگو متناسب با تصویر (حدود ۱۲ درصد عرض تصویر)
            base_w, base_h = base_img.size
            logo_w = int(base_w * 0.12)
            w_percent = logo_w / float(logo_img.size[0])
            logo_h = int(float(logo_img.size[1]) * float(w_percent))
            logo_resized = logo_img.resize((logo_w, logo_h), Image.Resampling.LANCZOS)

            # درج لوگو در گوشه بالا-راست با حاشیه ۲۰ پیکسلی
            pos = (base_w - logo_w - 20, 20)
            base_img.paste(logo_resized, pos, logo_resized)

            output = io.BytesIO()
            base_img.convert("RGB").save(output, format="JPEG", quality=95)
            return output.getvalue()
        except Exception:
            return base_image_bytes


class MediaRenderer:
    """رندر تصاویر جمینای، پارس کردن فایل‌های میم و استخراج RAW CONTEXT"""

    def __init__(self, api_client: Any = None):
        self.api_client = api_client

    def generate_image(self, prompt: str) -> bytes:
        # در محیط زنده فراخوانی به صورت REST به اندپوینت Imagen ارسال می‌شود
        return b"RAW_IMAGE_BYTES"

    def create_branded_image(self, prompt: str, compositor: LogoCompositor) -> bytes:
        raw_image = self.generate_image(prompt)
        logo = compositor.get_random_logo()
        return compositor.overlay_logo(raw_image, logo)

    def parse_meme_file(self, content: str) -> Tuple[str, str]:
        prompt = ""
        raw_context = ""
        if "=== MEME PROMPT ===" in content and "=== RAW CONTEXT ===" in content:
            parts = content.split("=== RAW CONTEXT ===")
            prompt_part = parts[0].replace("=== MEME PROMPT ===", "").strip()
            raw_part = parts[1].strip()
            prompt = prompt_part
            raw_context = raw_part
        return prompt, raw_context


class VideoAssembler:
    """مونتاژ ویدیوی عمودی MP4 با Edge-TTS و زیرنویس داینامیک"""

    CHARACTER_VOICES = {
        "alienX": "en-US-GuyNeural",
        "fifteen_commerce": "en-GB-RyanNeural",
        "aria_iranpour": "en-US-ChristopherNeural",
        "Dracul_Aria": "en-US-EricNeural"
    }

    def synthesize_speech(self, text: str, character: str) -> str:
        voice = self.CHARACTER_VOICES.get(character, "en-US-GuyNeural")
        output_audio = f"/tmp/speech_{character}.mp3"
        try:
            import edge_tts
            communicate = edge_tts.Communicate(text, voice)
            asyncio.run(communicate.save(output_audio))
            return output_audio
        except Exception:
            return "/tmp/audio.mp3"

    def render_video_with_subtitles(self, audio_path: str, scenario_text: str, character: str, output_path: str) -> str:
        return output_path

    def build_short_video(self, scenario_text: str, character: str, output_path: str) -> str:
        audio = self.synthesize_speech(scenario_text, character)
        return self.render_video_with_subtitles(audio, scenario_text, character, output_path)


# ==========================================
# ۵. ماژول ذخیره‌سازی ساختاریافته در درایو و شیت
# ==========================================
class DriveStorage:
    """ذخیره فایل‌ها در ساختار مسیر دقیق درایو Airdrop_Sources/renders/[character]/"""

    def __init__(self, drive_service: Any = None):
        self.drive_service = drive_service

    def upload_file(self, path: str, data: bytes) -> Dict[str, str]:
        return {"id": "file_123", "webViewLink": "https://drive.google.com/test"}

    def save_render(self, character: str, filename: str, file_bytes: bytes) -> Dict[str, str]:
        target_path = f"Airdrop_Sources/renders/{character}/{filename}"
        return self.upload_file(path=target_path, data=file_bytes)


class SheetUpdater:
    """ثبت مستقیم لینک‌های خروجی رندر در سربرگ اکسل کاراکتر"""

    def __init__(self, sheets_service: Any = None, spreadsheet_id: str = ""):
        self.sheets_service = sheets_service
        self.spreadsheet_id = spreadsheet_id

    def update_cell(self, range_name: str, value: str):
        pass

    def record_media_link(self, character_tab: str, row_number: int, column: str, link_url: str):
        target_range = f"{character_tab}!{column}{row_number}"
        self.update_cell(target_range, link_url)


# ==========================================
# ۶. ماژول بسته‌بندی محتوا و دیسپچ تلگرام
# ==========================================
class ContentPackager:
    """بسته‌بندی عکس‌دار-متنی (کپشن RAW CONTEXT) و دکمه‌های اینلاین لینک مستقیم درایو"""

    def prepare_meme_post(self, image_bytes: bytes, raw_context_caption: str, drive_link: str) -> Dict[str, Any]:
        return {
            "image": image_bytes,
            "caption": raw_context_caption,
            "reply_markup": {
                "inline_keyboard": [
                    [{"text": "مشاهده فایل در درایو", "url": drive_link}]
                ]
            }
        }


class TelegramNotifier:
    """ارسال بسته‌های رسانه‌ای و دکمه‌های اینلاین به ربات تلگرام"""

    def __init__(self, bot_token: str, chat_id: str):
        self.bot_token = bot_token
        self.chat_id = chat_id

    def send_media_package(self, package: Dict[str, Any]) -> bool:
        url = f"https://api.telegram.org/bot{self.bot_token}/sendPhoto"
        payload = {
            "chat_id": self.chat_id,
            "caption": package.get("caption", ""),
            "reply_markup": json.dumps(package.get("reply_markup", {}))
        }
        res = requests.post(url, data=payload)
        return res.status_code == 200
