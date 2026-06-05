"""
╔══════════════════════════════════════════════════════════════╗
║  fetch.py — генератор архива Telegram-канала                 ║
║                                                              ║
║  Установка (один раз):                                       ║
║      pip install requests beautifulsoup4                     ║
║                                                              ║
║  Настройка:                                                  ║
║      Измени CHANNEL ниже на username своего канала           ║
║                                                              ║
║  Запуск:                                                     ║
║      python fetch.py                                         ║
║                                                              ║
║  Результат: posts.js рядом с index.html                      ║
╚══════════════════════════════════════════════════════════════╝
"""

import requests
import json
import re
import time
import os
import hashlib
from bs4 import BeautifulSoup
from datetime import datetime

# ╔══════════════════════════════╗
# ║  ЕДИНСТВЕННОЕ МЕСТО НАСТРОЙКИ ║
# ╚══════════════════════════════╝
CHANNEL        = "blacktraced"   # ← меняй только это, без @
MAX_POSTS      = 500             # сколько постов максимум
OUT_FILE       = "posts.js"
DOWNLOAD_MEDIA = True            # скачивать картинки локально (надёжно, не протухают)
IMAGES_DIR     = "images"        # папка для скачанных картинок
HIGHRES_MEDIA  = True            # тянуть картинки покрупнее с og:image (медленнее)
# ════════════════════════════════

BASE_URL = f"https://t.me/s/{CHANNEL}"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0 Safari/537.36"
    ),
    "Accept-Language": "ru-RU,ru;q=0.9,en;q=0.8",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}


def fetch_page(before=None, retries=3):
    params = {"before": before} if before else {}
    for attempt in range(1, retries + 1):
        try:
            r = requests.get(BASE_URL, params=params, headers=HEADERS, timeout=20)
            r.raise_for_status()
            html = r.text
            # Telegram sometimes returns a stub page without the message feed.
            # Detect it: real feed contains tgme_widget_message_wrap.
            if "tgme_widget_message_wrap" in html or before:
                return html
            # Stub detected — wait and retry
            if attempt < retries:
                print(f"  ⏳ Лента пустая (попытка {attempt}/{retries}), повтор через 3с...")
                time.sleep(3)
                continue
            return html  # return whatever we got on last attempt
        except Exception as e:
            print(f"  ⚠️  Ошибка загрузки (попытка {attempt}/{retries}): {e}")
            if attempt < retries:
                time.sleep(3)
    return None


def get_channel_title(html):
    """Вытаскиваем название канала из страницы."""
    soup = BeautifulSoup(html, "html.parser")
    # og:title или заголовок канала
    og = soup.find("meta", property="og:title")
    if og and og.get("content"):
        return og["content"].strip()
    title_el = soup.select_one(".tgme_channel_info_header_title")
    if title_el:
        return title_el.get_text().strip()
    return CHANNEL.upper()


def parse_views(text):
    """'1.2K' → 1200"""
    if not text:
        return 0
    text = text.strip().replace("\xa0", "").replace(" ", "")
    try:
        if "K" in text or "k" in text:
            return int(float(text.replace("K","").replace("k","")) * 1000)
        if "M" in text or "m" in text:
            return int(float(text.replace("M","").replace("m","")) * 1_000_000)
        digits = re.sub(r"\D", "", text)
        return int(digits) if digits else 0
    except:
        return 0


def parse_reactions(msg_soup):
    """
    Суммируем эмодзи-реакции на посте.

    ВАЖНО: публичное веб-превью t.me/s/ в большинстве случаев НЕ отдаёт
    реакции в HTML — Telegram их туда не выводит. Поэтому чаще всего
    здесь будет 0. Это ограничение Telegram, а не ошибка парсера.
    Реакции доступны только через полноценный Bot API / MTProto.
    """
    total = 0
    # Пробуем все известные варианты классов реакций
    selectors = [
        ".tgme_widget_message_reaction",
        ".tgme_reaction_count",
        "[class*='reaction'] [class*='count']",
    ]
    for sel in selectors:
        for el in msg_soup.select(sel):
            # Ищем число внутри элемента реакции
            txt = el.get_text(" ", strip=True)
            # Вытаскиваем все числа (включая формат 1.2K)
            for m in re.findall(r"\d[\d.,]*[KkMm]?", txt):
                total += parse_views(m)
        if total:
            break
    return total


def split_title_body(text):
    """
    Разделяет текст на заголовок (первый абзац) и тело.
    Заголовок — первая строка/абзац до первого двойного переноса (\n\n),
    либо первая строка если двойных переносов нет.
    Возвращает (title, body).
    """
    if not text:
        return "", ""

    text = text.strip()

    # Если есть двойной перенос — title до него, body после
    if "\n\n" in text:
        parts = text.split("\n\n", 1)
        title = parts[0].replace("\n", " ").strip()
        body  = parts[1].strip() if len(parts) > 1 else ""
    else:
        # Нет двойного переноса — первая строка как заголовок
        lines = [l for l in text.split("\n")]
        title = lines[0].strip() if lines else text
        body  = "\n".join(lines[1:]).strip() if len(lines) > 1 else ""

    # Ограничиваем длины для превью
    title = title[:160]
    body  = body[:400]
    return title, body


def get_highres_image(post_id, fallback_url):
    """
    Заходит на отдельную страницу поста t.me/{channel}/{id} и берёт og:image —
    она обычно крупнее ленточного превью. Если не вышло — возвращает fallback.
    """
    if not HIGHRES_MEDIA:
        return fallback_url
    try:
        url = f"https://t.me/{CHANNEL}/{post_id}"
        r = requests.get(url, headers=HEADERS, timeout=20)
        r.raise_for_status()
        # og:image содержит картинку покрупнее
        m = re.search(
            r'<meta[^>]+property=["\']og:image["\'][^>]+content=["\']([^"\']+)["\']',
            r.text, re.I
        )
        if not m:
            m = re.search(
                r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+property=["\']og:image["\']',
                r.text, re.I
            )
        if m:
            og = m.group(1).replace("&amp;", "&")
            if og.startswith("http"):
                return og
    except Exception:
        pass
    return fallback_url


def download_image(url, post_id):
    """
    Скачивает картинку локально в IMAGES_DIR и возвращает относительный путь
    (например 'images/123.jpg'). Если уже скачана — пропускает.
    При ошибке возвращает исходный URL (чтобы хоть что-то показать).
    """
    if not url:
        return None
    if not DOWNLOAD_MEDIA:
        return url

    os.makedirs(IMAGES_DIR, exist_ok=True)

    # Определяем расширение из URL (по умолчанию .jpg)
    ext = ".jpg"
    m = re.search(r"\.(jpe?g|png|webp|gif)(\?|$)", url, re.I)
    if m:
        ext = "." + m.group(1).lower().replace("jpeg", "jpg")

    # Имя файла: ID поста + короткий хэш URL (на случай нескольких картинок)
    h = hashlib.md5(url.encode()).hexdigest()[:6]
    fname = f"{post_id}_{h}{ext}"
    fpath = os.path.join(IMAGES_DIR, fname)
    rel   = f"{IMAGES_DIR}/{fname}"

    # Уже скачано — пропускаем
    if os.path.exists(fpath) and os.path.getsize(fpath) > 0:
        return rel

    try:
        r = requests.get(url, headers=HEADERS, timeout=25)
        r.raise_for_status()
        with open(fpath, "wb") as f:
            f.write(r.content)
        return rel
    except Exception as e:
        print(f"    ⚠️  не скачалась картинка #{post_id}: {e}")
        return url   # фолбэк на CDN-ссылку


def parse_posts(html):
    soup = BeautifulSoup(html, "html.parser")
    messages = soup.select(".tgme_widget_message_wrap")
    posts = []
    min_id = None

    for wrap in messages:
        msg = wrap.select_one(".tgme_widget_message")
        if not msg:
            continue

        # Skip Telegram service messages (channel created, pinned, etc.)
        # They carry .tgme_widget_message_service on the message element itself
        msg_classes = msg.get("class", [])
        if "tgme_widget_message_service" in msg_classes:
            continue
        # Also skip if there's no data-post attribute (service messages lack it)
        if not msg.get("data-post", ""):
            continue

        # ID
        msg_id = None
        link = msg.get("data-post", "")
        if "/" in link:
            try:
                msg_id = int(link.split("/")[-1])
            except:
                pass
        if not msg_id:
            continue

        if min_id is None or msg_id < min_id:
            min_id = msg_id

        # Date
        date_str = ""
        time_el = msg.select_one(".tgme_widget_message_date time")
        if time_el and time_el.get("datetime"):
            try:
                dt = datetime.fromisoformat(time_el["datetime"].replace("Z", "+00:00"))
                date_str = dt.strftime("%Y-%m-%d")
            except:
                date_str = time_el["datetime"][:10]

        # Text
        text_el = msg.select_one(".tgme_widget_message_text")
        raw_text = text_el.get_text("\n") if text_el else ""
        # Also check caption
        if not raw_text:
            cap_el = msg.select_one(".tgme_widget_message_caption")
            raw_text = cap_el.get_text("\n") if cap_el else ""
        title, body = split_title_body(raw_text)

        # Media
        image_url = None
        post_type = "text"

        def extract_bg_url(el):
            if not el:
                return None
            style = el.get("style", "")
            m = re.search(r"url\(['\"]?(https?://[^'\")\s]+)['\"]?\)", style)
            return m.group(1) if m else None

        # Photo
        photo_el = msg.select_one(".tgme_widget_message_photo_wrap")
        if photo_el:
            image_url = extract_bg_url(photo_el)
            post_type = "image"

        # Video thumb
        if not image_url:
            vid_thumb = msg.select_one(".tgme_widget_message_video_thumb")
            if vid_thumb:
                image_url = extract_bg_url(vid_thumb)
                post_type = "video"
            elif msg.select_one(".tgme_widget_message_video"):
                post_type = "video"

        # Animation/GIF
        if not image_url:
            anim = msg.select_one(".tgme_widget_message_animated_wrap")
            if anim:
                image_url = extract_bg_url(anim)
                post_type = "video"

        # Grouped album — first photo
        if not image_url:
            grouped = msg.select(".tgme_widget_message_grouped_wrap .tgme_widget_message_photo_wrap")
            if grouped:
                image_url = extract_bg_url(grouped[0])
                post_type = "image"

        # Document with preview
        if not image_url:
            doc_thumb = msg.select_one(".tgme_widget_message_document_thumb")
            if doc_thumb:
                image_url = extract_bg_url(doc_thumb)
                post_type = "image"

        # Views
        views = 0
        views_el = msg.select_one(".tgme_widget_message_views")
        if views_el:
            views = parse_views(views_el.get_text())

        # Reactions
        reactions = parse_reactions(msg)

        # Skip service messages with no content
        if not title and not image_url:
            continue

        # Download media locally so links never expire.
        # First try to get a higher-res version via the post's og:image.
        local_image = None
        if image_url:
            best_url = get_highres_image(msg_id, image_url)
            local_image = download_image(best_url, msg_id)

        posts.append({
            "id":        msg_id,
            "date":      date_str,
            "title":     title,
            "body":      body,
            "image":     local_image,
            "type":      post_type,
            "tags":      [],
            "views":     views,
            "reactions": reactions,
            "forwards":  0,   # репосты — впиши вручную (веб-превью их не отдаёт)
        })

    return posts, min_id


def write_js(posts, title):
    meta = {
        "username": CHANNEL,
        "title":    title,
    }
    lines = [
        "// Автоматически сгенерировано fetch.py",
        f"// Канал: @{CHANNEL}",
        f"// Обновлено: {datetime.utcnow().strftime('%Y-%m-%d %H:%M UTC')}",
        f"// Постов: {len(posts)}",
        "",
        "const CHANNEL_META = " + json.dumps(meta, ensure_ascii=False) + ";",
        "",
        "const POSTS = " + json.dumps(posts, ensure_ascii=False, indent=2) + ";",
    ]
    with open(OUT_FILE, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))


def merge_manual_values(new_posts):
    """
    Читает существующий posts.js и переносит вручную вписанные значения
    forwards и reactions в свежие посты (чтобы повторный запуск их не обнулял).
    Сопоставление по id поста.
    """
    if not os.path.exists(OUT_FILE):
        return
    try:
        with open(OUT_FILE, "r", encoding="utf-8") as f:
            txt = f.read()
        # вытащить JSON-массив POSTS
        m = re.search(r"const POSTS\s*=\s*(\[.*?\]);", txt, re.S)
        if not m:
            return
        old = json.loads(m.group(1))
        old_by_id = {p.get("id"): p for p in old}
        kept = 0
        for p in new_posts:
            o = old_by_id.get(p.get("id"))
            if not o:
                continue
            # переносим только если в старом было вручную проставлено (>0)
            if o.get("forwards", 0):
                p["forwards"] = o["forwards"]; kept += 1
            if o.get("reactions", 0) and not p.get("reactions", 0):
                p["reactions"] = o["reactions"]; kept += 1
        if kept:
            print(f"  ↻ Перенесено вручную проставленных значений: {kept}")
    except Exception as e:
        print(f"  ⚠️  Не удалось перенести ручные значения: {e}")


def main():
    print("=" * 55)
    print(f"  fetch.py  →  @{CHANNEL}")
    if DOWNLOAD_MEDIA:
        hr = " (HD через og:image)" if HIGHRES_MEDIA else ""
        print(f"  Картинки скачиваются в {IMAGES_DIR}/{hr} — может занять время")
    print("=" * 55)

    all_posts = []
    seen_ids  = set()
    before_id = None
    page      = 1
    channel_title = CHANNEL.upper()

    while len(all_posts) < MAX_POSTS:
        label = f"страница {page}" + (f" (до #{before_id})" if before_id else "")
        print(f"\n⏳ {label}...")

        html = fetch_page(before=before_id)
        if not html:
            print("  Не удалось загрузить. Остановка.")
            break

        # Grab title on first page
        if page == 1:
            channel_title = get_channel_title(html)
            print(f"  Канал: {channel_title}")

        posts, min_id = parse_posts(html)

        if not posts:
            if page == 1:
                # Save the HTML so we can see what Telegram actually returned
                try:
                    with open("debug_page.html", "w", encoding="utf-8") as f:
                        f.write(html)
                    has_feed = "tgme_widget_message_wrap" in html
                    print(f"  Постов не найдено на первой странице.")
                    print(f"  Лента в HTML присутствует: {has_feed}")
                    print(f"  HTML сохранён в debug_page.html ({len(html)} символов)")
                    if not has_feed:
                        print(f"  → Telegram отдал страницу без ленты постов.")
                        print(f"    Попробуй ещё раз через минуту, либо открой")
                        print(f"    в браузере: {BASE_URL}")
                except Exception as e:
                    print(f"  (не удалось сохранить debug: {e})")
            else:
                print("  Постов больше нет — конец канала.")
            break

        new = 0
        for p in posts:
            if p["id"] not in seen_ids:
                seen_ids.add(p["id"])
                all_posts.append(p)
                new += 1
                print(f"  ✓ #{p['id']}  {p['date']}  {p['title'][:45]}{'…' if len(p['title'])>45 else ''}")

        print(f"  → {new} новых  (всего: {len(all_posts)})")

        if new == 0 or min_id is None:
            break

        before_id = min_id - 1
        page += 1
        time.sleep(1.2)

    if not all_posts:
        print("\n❌ Посты не найдены.")
        print("   • Убедись что канал публичный")
        print("   • Проверь username (без @)")
        return

    all_posts.sort(key=lambda p: p["id"], reverse=True)

    # Preserve manually-entered forwards/reactions from a previous posts.js
    # (web preview can't read them, so you fill them by hand — don't wipe them)
    merge_manual_values(all_posts)

    write_js(all_posts, channel_title)

    # Count downloaded images
    img_count = 0
    if DOWNLOAD_MEDIA and os.path.isdir(IMAGES_DIR):
        img_count = len([f for f in os.listdir(IMAGES_DIR) if os.path.isfile(os.path.join(IMAGES_DIR, f))])

    print(f"\n{'=' * 55}")
    print(f"  ✅ {len(all_posts)} постов  →  {OUT_FILE}")
    if DOWNLOAD_MEDIA:
        print(f"  🖼  {img_count} картинок  →  {IMAGES_DIR}/")
        print(f"\n  Залей на GitHub Pages ВСЕ ТРИ:")
        print(f"     • index.html")
        print(f"     • posts.js")
        print(f"     • папку {IMAGES_DIR}/ целиком")
    else:
        print(f"  Положи posts.js рядом с index.html и открывай.")
    print(f"{'=' * 55}")


if __name__ == "__main__":
    main()
