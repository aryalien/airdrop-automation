import json
import os
import time
from unittest.mock import MagicMock, call, patch
import pytest

# وارد کردن کلاس‌ها و توابع هدف از سلول اصلی
from media_factory import (
    ContentPackager,
    DriveDocsExtractor,
    DriveStorage,
    KeyVaultManager,
    LogoCompositor,
    MediaRenderer,
    RateLimiter,
    SheetUpdater,
    TelegramNotifier,
    VideoAssembler,
)


# ==========================================
# ۱. ماژول استخر کلیدها و تاب‌آوری خطاها
# ==========================================
def test_scenario_1_key_vault_resilience_and_rotation():
    """سناریو ۱: برخورد با خطای ۵۰۳/۴۲۹، توقف ۵ ثانیه‌ای، چرخش به کلید بعدی و تلاش مجدد"""
    keys = ["KEY_A", "KEY_B", "KEY_C"]
    vault = KeyVaultManager(api_keys=keys)

    with patch("time.sleep") as mock_sleep:
        # کلید اول با خطای 503 مواجه می‌شود
        active_key = vault.get_active_key()
        assert active_key == "KEY_A"

        # گزارش ارور 503
        vault.report_error(active_key, error_code=503)

        # باید حداقل ۵ ثانیه توقف اعمال شده باشد
        mock_sleep.assert_called_with(5)

        # کلید جدید باید جایگزین شده باشد و کلید قبلی از دسترس خارج شود
        new_key = vault.get_active_key()
        assert new_key == "KEY_B"
        assert vault.key_states["KEY_A"]["status"] == "COOLDOWN_OR_EXHAUSTED"


def test_scenario_2_key_vault_state_persistence(tmp_path):
    """سناریو ۲: ذخیره و بازیابی وضعیت سلامت کلیدها در فایل keys_state.json"""
    state_file = tmp_path / "keys_state.json"
    vault = KeyVaultManager(api_keys=["KEY_1", "KEY_2"], state_filepath=str(state_file))

    vault.record_usage("KEY_1", success=True)
    vault.save_state()

    assert os.path.exists(state_file)
    with open(state_file, "r", encoding="utf-8") as f:
        data = json.load(f)
    assert "KEY_1" in data
    assert data["KEY_1"]["usage_count"] == 1


# ==========================================
# ۲. ماژول خطوط قرمز تاخیر و پردازش متوالی
# ==========================================
def test_scenario_3_rate_limiter_5s_delay():
    """سناریو ۳: اعمال تاخیر قطعی حداقل ۵ ثانیه‌ای میان هر فراخوانی"""
    limiter = RateLimiter(min_delay=5.0)

    with patch("time.sleep") as mock_sleep, patch("time.time") as mock_time:
        mock_time.side_effect = [100.0, 102.0]  # ۲ ثانیه سپری شده
        limiter.last_call_timestamp = 100.0
        limiter.wait_if_needed()

        # باید مابه‌التفاوت تا ۵ ثانیه یعنی ۳ ثانیه خواب اعمال شود
        mock_sleep.assert_called_once_with(3.0)


# ==========================================
# ۳. ماژول فیلتر، اولویت‌بندی و انتخاب محتوا
# ==========================================
def test_scenario_4_drive_extractor_filter_and_descending_sort():
    """سناریو ۴: فیلتر کردن اسناد متنی و انتخاب فقط مدیاها با اولویت ردیف‌های بزرگ‌تر"""
    extractor = DriveDocsExtractor(service_account_creds={})
    mock_files = [
        {"name": "text_tweet_row_12.md", "id": "f1"},
        {"name": "image_tweet_row_5.md", "id": "f2"},
        {"name": "discord_starter_row_9.md", "id": "f3"},
        {"name": "video_row_25.md", "id": "f4"},
        {"name": "meme_fun_row_18.md", "id": "f5"},
    ]

    filtered = extractor.filter_and_sort_media_files(mock_files)

    # نباید فایل‌های متنی text_tweet و discord_starter در لیست باشند
    names = [f["name"] for f in filtered]
    assert "text_tweet_row_12.md" not in names
    assert "discord_starter_row_9.md" not in names

    # باید بر اساس شماره ردیف نزولی مرتب شده باشند: 25 -> 18 -> 5
    assert names == ["video_row_25.md", "meme_fun_row_18.md", "image_tweet_row_5.md"]


def test_scenario_5_meme_alternating_selection():
    """سناریو ۵: انتخاب متناوب/شانسی متعادل بین meme_fun و meme_graphic"""
    extractor = DriveDocsExtractor(service_account_creds={})
    fun_memes = [{"name": f"meme_fun_row_{i}.md"} for i in range(1, 4)]
    graphic_memes = [{"name": f"meme_graphic_row_{i}.md"} for i in range(1, 4)]

    selected_queue = extractor.balance_meme_selection(fun_memes, graphic_memes)

    # بررسی یکی‌درمیان بودن یا تنوع متوازن در لیست نهایی انتخاب‌شده
    types = [
        "fun" if "meme_fun" in item["name"] else "graphic"
        for item in selected_queue[:4]
    ]
    assert "fun" in types and "graphic" in types


# ==========================================
# ۴. ماژول رندر، واترمارک لوگو و ترکیب میم
# ==========================================
def test_scenario_6_image_render_and_logo_watermark():
    """سناریو ۶: رندر تصویر و ترکیب تصادفی یکی از لوگوها با Pillow"""
    renderer = MediaRenderer(api_client=MagicMock())
    compositor = LogoCompositor(logos_dir="Airdrop_Sources/Retium_Logo")

    with patch.object(renderer, "generate_image", return_value=b"RAW_IMAGE_BYTES"), \
         patch.object(compositor, "get_random_logo", return_value=b"LOGO_BYTES"), \
         patch.object(compositor, "overlay_logo", return_value=b"COMPOSITED_IMAGE_BYTES") as mock_overlay:

        final_image = renderer.create_branded_image(
            prompt="A futuristic scene",
            compositor=compositor
        )

        assert final_image == b"COMPOSITED_IMAGE_BYTES"
        mock_overlay.assert_called_once()


def test_scenario_7_meme_render_and_raw_context_extraction():
    """سناریو ۷: رندر میم و جداسازی بخش RAW CONTEXT جهت استفاده به عنوان کپشن"""
    renderer = MediaRenderer(api_client=MagicMock())
    mock_file_content = (
        "=== MEME PROMPT ===\n"
        "Alien smiling in front of broken bridge\n"
        "=== RAW CONTEXT ===\n"
        "Retium RPC latency dropped below 15ms today while others failed."
    )

    prompt, raw_context = renderer.parse_meme_file(mock_file_content)

    assert prompt == "Alien smiling in front of broken bridge"
    assert raw_context == "Retium RPC latency dropped below 15ms today while others failed."


def test_scenario_8_video_shorts_assembly(tmp_path):
    """سناریو ۸: مونتاژ ویدیوی عمودی MP4 با Edge-TTS و زیرنویس داینامیک"""
    assembler = VideoAssembler()
    output_video_path = tmp_path / "video_row_25.mp4"

    with patch.object(assembler, "synthesize_speech", return_value=str(tmp_path / "audio.mp3")), \
         patch.object(assembler, "render_video_with_subtitles", return_value=str(output_video_path)):

        result_path = assembler.build_short_video(
            scenario_text="30-second script for Dracul Aria",
            character="Dracul_Aria",
            output_path=str(output_video_path)
        )

        assert result_path == str(output_video_path)


# ==========================================
# ۵. ماژول ذخیره‌سازی ساختاریافته در درایو و شیت
# ==========================================
def test_scenario_9_drive_storage_target_path():
    """سناریو ۹: ذخیره مستقیم فایل‌ها در مسیر Airdrop_Sources/renders/[character]/"""
    storage = DriveStorage(drive_service=MagicMock())

    with patch.object(storage, "upload_file", return_value={"id": "file_123", "webViewLink": "https://drive.google.com/test"}):
        result = storage.save_render(
            character="alienX",
            filename="image_tweet_row_18.jpg",
            file_bytes=b"DATA"
        )

        assert result["id"] == "file_123"
        storage.upload_file.assert_called_once_with(
            path="Airdrop_Sources/renders/alienX/image_tweet_row_18.jpg",
            data=b"DATA"
        )


def test_scenario_10_sheet_updater():
    """سناریو ۱۰: درج لینک‌های خروجی رندر در سربرگ شیت مربوط به کاراکتر"""
    updater = SheetUpdater(sheets_service=MagicMock(), spreadsheet_id="test_sheet_id")

    with patch.object(updater, "update_cell") as mock_update:
        updater.record_media_link(
            character_tab="alienX",
            row_number=18,
            column="O",
            link_url="https://drive.google.com/test"
        )

        mock_update.assert_called_once_with("alienX!O18", "https://drive.google.com/test")


# ==========================================
# ۶. ماژول بسته‌بندی محتوا و دیسپچ تلگرام
# ==========================================
def test_scenario_11_telegram_packager_and_notifier():
    """سناریو ۱۱: آماده‌سازی بسته پست عکس‌دار-متنی (کپشن RAW CONTEXT) و دکمه‌های اینلاین لینک"""
    packager = ContentPackager()
    notifier = TelegramNotifier(bot_token="FAKE_TOKEN", chat_id="12345")

    package = packager.prepare_meme_post(
        image_bytes=b"MEME_IMG",
        raw_context_caption="Retium RPC latency dropped below 15ms today while others failed.",
        drive_link="https://drive.google.com/view_meme"
    )

    assert package["caption"] == "Retium RPC latency dropped below 15ms today while others failed."
    assert "reply_markup" in package
    assert package["reply_markup"]["inline_keyboard"][0][0]["url"] == "https://drive.google.com/view_meme"

    with patch("requests.post") as mock_post:
        mock_post.return_value.status_code = 200
        mock_post.return_value.json.return_value = {"ok": True}

        success = notifier.send_media_package(package)
        assert success is True
        mock_post.assert_called_once()
