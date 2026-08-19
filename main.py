import asyncio, json, shutil, zipfile, tempfile
from pathlib import Path
import re
from aiogram import Bot, Dispatcher, F
from aiogram.filters import CommandStart, Command
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import StatesGroup, State
from aiogram.filters import StateFilter
from config import BOT_TOKEN, FORCE_JOIN_CHANNEL, PUBLIC_BASE_URL, ADMIN_IDS
from db import *
from templates import (list_templates, get_template, get_template_by_key, list_categories,
                       category_display_name, validate_template_dir, get_template_fields)
from builder import build_all
from netlify import deploy_directory

bot=Bot(BOT_TOKEN);dp=Dispatcher()

TEMPLATE_INBOX=Path("template_uploads")

# Field behavior is defined by each template's manifest.json.
# The bot never assumes a fixed number of intros/photos/etc.
PHOTO_TYPES={"photo","photos","image","images","gallery"}
AUDIO_TYPES={"audio","music","song","voice"}
TEXT_TYPES={"text","textarea","longtext","number","date","datetime","time","url","email","tel","select","choice","password"}


def _clean_name(name):
    name = re.sub(r"[^A-Za-z0-9 _.-]+", "", str(name)).strip()
    name = re.sub(r"\s+", " ", name)
    return name[:80]


def _safe_template_folder(category, template_name):
    category = _clean_name(category)
    template_name = _clean_name(template_name)
    if not category or not template_name:
        return None
    root=Path("templates").resolve()
    target=(Path("templates")/category/template_name).resolve()
    try:
        target.relative_to(root)
    except ValueError:
        return None
    return target


def _find_template_root(extracted):
    extracted=Path(extracted)
    if (extracted/"index.html").exists():
        return extracted

    dirs=[p for p in extracted.iterdir() if p.is_dir()]
    if len(dirs)==1 and (dirs[0]/"index.html").exists():
        return dirs[0]

    # Common ZIP layout: one wrapper directory containing the template.
    for p in extracted.rglob("index.html"):
        return p.parent

    return None


def _install_template_zip(zip_path, category, template_name):
    target=_safe_template_folder(category, template_name)
    if not target:
        raise RuntimeError("Nama kategori/template tidak valid.")

    with tempfile.TemporaryDirectory(prefix="template_extract_") as td:
        with zipfile.ZipFile(zip_path) as z:
            bad=z.testzip()
            if bad:
                raise RuntimeError(f"ZIP rusak: {bad}")
            z.extractall(td)

        source=_find_template_root(Path(td))
        if not source:
            raise RuntimeError("ZIP tidak menemukan index.html.")

        ok,msg=validate_template_dir(source)
        if not ok:
            raise RuntimeError(msg)

        if target.exists():
            raise RuntimeError(
                f"Template '{template_name}' sudah ada di kategori '{category}'."
            )

        target.parent.mkdir(parents=True,exist_ok=True)
        shutil.copytree(source,target)

    return target


def _template_folder(category, template_id):
    root=Path("templates").resolve()
    target=(Path("templates")/category/template_id).resolve()
    try:
        target.relative_to(root)
    except ValueError:
        return None
    return target

class Form(StatesGroup):
    collecting=State()
class AdminForm(StatesGroup):
    broadcast=State()
    add_template=State()
    add_template_zip=State()
    delete_template=State()

class FeedbackForm(StatesGroup):
    collecting=State()

# Pesan yang terkait sesi wizard disimpan di FSM agar /batal dapat
# membersihkannya tanpa menyentuh website yang sudah tersimpan di database.
async def persist_wizard(state, user_id):
    """Persist the active wizard without allowing a stale DB row to replace FSM data."""
    try:
        current = await state.get_state()
        if current != Form.collecting.state:
            return False
        data = await state.get_data()
        save_wizard_session(user_id, data)
        return True
    except Exception:
        import logging
        logging.getLogger(__name__).exception("Failed to persist wizard session")
        return False

async def restore_wizard(state, user_id):
    """Restore only when the in-memory wizard is actually missing.

    The previous implementation always overwrote the live FSM with the last
    SQLite snapshot. If one SQLite write failed/transiently lagged, that stale
    snapshot erased answers already collected in memory, producing errors such
    as `Field wajib belum lengkap: title, intro1, gallery` at the final step.
    """
    current_state = await state.get_state()
    current = await state.get_data()
    if current_state == Form.collecting.state and current.get("category") and current.get("template_id") and "fields" in current:
        # A live FSM is authoritative. Never overwrite it with an older DB snapshot.
        return True

    saved = get_wizard_session(user_id)
    if not saved:
        return False
    await state.update_data(**saved)
    await state.set_state(Form.collecting)
    return True

async def clear_wizard(user_id):
    try:
        clear_wizard_session(user_id)
    except Exception:
        pass

async def track_message(state, message_id):
    if not message_id:
        return
    d=await state.get_data()
    ids=d.get("session_message_ids",[])
    if message_id not in ids:
        ids.append(message_id)
    await state.update_data(session_message_ids=ids)

async def delete_session_messages(m,state):
    d=await state.get_data()
    ids=d.get("session_message_ids",[])
    # Hapus pesan bot/user yang berhasil dihapus oleh Telegram.
    # Jika Telegram menolak penghapusan salah satu pesan, proses tetap lanjut.
    for mid in ids:
        try:
            await bot.delete_message(m.chat.id, mid)
        except Exception:
            pass
    # Hapus file upload yang baru dibuat pada sesi yang dibatalkan.
    for key in ("data",):
        for value in (d.get(key,{}) or {}).values():
            paths=value if isinstance(value,list) else [value]
            for raw in paths:
                if not raw:
                    continue
                try:
                    Path(raw).unlink(missing_ok=True)
                except Exception:
                    pass

async def send_main_menu(m,state,delete_old=False):
    if delete_old:
        await delete_session_messages(m,state)
    await state.clear()
    menu=await m.answer("🌐 <b>Web Creator</b>\n\nPilih jenis website:",reply_markup=main_menu(),parse_mode="HTML")
    # Simpan menu baru hanya sebagai referensi sesi berikutnya. /batal akan
    # menghapusnya lalu membuat menu baru lagi, sehingga menu tetap tersedia.
    await state.update_data(session_message_ids=[menu.message_id])
    return menu

async def explain_current_field(m,state):
    d=await state.get_data()
    fields=d.get("fields",[])
    i=d.get("index",0)
    if i >= len(fields):
        await m.answer("ℹ️ Semua data sudah diterima. Website sedang/siap dibuat.")
        return
    f=fields[i]
    label=f.get("label",f.get("id","field"))
    desc=f.get("description","").strip()
    typ=str(f.get("type","text")).lower().strip()
    if typ in PHOTO_TYPES:
        type_hint=("Kirim satu foto." if typ=="photo" else
                   "Kirim foto satu per satu, lalu ketik /selesai.")
    elif typ in AUDIO_TYPES:
        type_hint="Kirim audio atau voice; setelah diterima bot lanjut otomatis."
    else:
        type_hint="Kirim teks/data sesuai field template."
    required="\n\n⚠️ Field ini wajib diisi." if f.get("required") else "\n\nField ini boleh dilewati dengan /skip."
    text=f"ℹ️ <b>Keterangan: {label}</b>\n\n{desc or type_hint}\n\n{type_hint}{required}\n\nGunakan /batal untuk membatalkan proses."
    msg=await m.answer(text,parse_mode="HTML")
    await track_message(state,msg.message_id)

CATEGORY_META={
"kenangan":("💌","Abadikan foto, chat, cerita, dan momen berharga dalam satu website interaktif."),
"confess":("💭","Sampaikan perasaan secara personal dengan website penuh kejutan."),
"birthday":("🎂","Buat ucapan ulang tahun interaktif berisi foto, pesan, musik, dan kejutan."),
"anniversary":("❤️","Rayakan perjalanan hubungan dengan timeline, foto, cerita, dan pesan spesial."),
"farewell":("💐","Simpan pesan, kenangan, dan ucapan terakhir dalam satu website."),
"surprise":("🎁","Buat website kejutan dengan pesan, animasi, musik, dan efek interaktif.")
}

def main_menu():
    rows=[]
    for category_dir in list_categories():
        key=category_dir.name
        icon,desc=CATEGORY_META.get(key,("🌐","Buat website dengan template ini."))
        rows.append([
            InlineKeyboardButton(
                text=f"{icon} {category_display_name(key)}",
                callback_data=f"cat:{key}"
            )
        ])
    rows.append([InlineKeyboardButton(text="📁 Web Saya",callback_data="mine")])
    rows.append([InlineKeyboardButton(text="💡 Kritik & Saran",callback_data="feedback")])
    return InlineKeyboardMarkup(inline_keyboard=rows)

async def is_joined(uid):
    if not FORCE_JOIN_CHANNEL:return True
    try:
        m=await bot.get_chat_member(FORCE_JOIN_CHANNEL,uid)
        return m.status in {"creator","administrator","member"} or (m.status=="restricted" and bool(getattr(m,"is_member",False)))
    except Exception:return False

def join_kb():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📢 Join Channel",url=f"https://t.me/{FORCE_JOIN_CHANNEL.lstrip('@')}")],
        [InlineKeyboardButton(text="✅ Saya Sudah Join",callback_data="joincheck")]])

async def ensure_access(event):
    uid=event.from_user.id

    # Admin/owner selalu mendapat akses tanpa wajib join channel.
    # Ini mencegah owner terkena force-join saat menguji bot.
    if uid in ADMIN_IDS:
        upsert_user(event.from_user)
        return True

    if await is_joined(uid):
        upsert_user(event.from_user)
        return True
    if isinstance(event,CallbackQuery):
        await event.answer("❌ Join channel terlebih dahulu.",show_alert=True)
        await event.message.answer("🔒 Akses terbatas. Silakan join channel terlebih dahulu.",reply_markup=join_kb())
    else:
        await event.answer("🔒 <b>Akses Terbatas</b>\n\nIkuti channel kami terlebih dahulu.",reply_markup=join_kb(),parse_mode="HTML")
    return False

@dp.message(CommandStart())
async def start(m,state):
    if await ensure_access(m):
        await send_main_menu(m,state,delete_old=True)

@dp.callback_query(F.data=="joincheck")
async def joincheck(c,state):
    if not await is_joined(c.from_user.id):
        await c.answer("❌ Kamu belum join channel.",show_alert=True);return
    upsert_user(c.from_user);await state.clear();await c.answer("✅ Verifikasi berhasil!")
    await c.message.edit_text("🌐 <b>Web Creator</b>\n\nPilih jenis website:",reply_markup=main_menu(),parse_mode="HTML")
    await track_message(state,c.message.message_id)

@dp.callback_query(F.data.startswith("cat:"))
async def category(c,state):
    if not await ensure_access(c):return
    cat=c.data.split(":",1)[1]
    ts=list_templates(cat)
    if not ts:
        await c.answer("Belum ada template untuk kategori ini.",show_alert=True)
        return

    buttons=[]
    for t in ts:
        # Short hash key keeps Telegram callback_data below the 64-byte limit.
        buttons.append([
            InlineKeyboardButton(
                text=f"🎨 {t['name']}",
                callback_data=f"tpl:{t['key']}"
            )
        ])
    buttons.append([InlineKeyboardButton(text="⬅️ Kembali",callback_data="back")])

    await state.update_data(category=cat)
    icon,desc=CATEGORY_META.get(cat,("🌐","Pilih template website."))
    await c.message.edit_text(
        f"<b>{icon} {category_display_name(cat)}</b>\n\n"
        f"{desc}\n\n<b>Pilih template:</b>",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons),
        parse_mode="HTML"
    )
    await track_message(state,c.message.message_id)
    await c.answer()

@dp.callback_query(F.data.startswith("tpl:"))
async def choose_template(c,state):
    if not await ensure_access(c):return
    d=await state.get_data()
    t=get_template_by_key(c.data.split(":",1)[1])
    if not t:
        await c.answer("Template tidak ditemukan.",show_alert=True)
        return

    # Manifest is the source of truth. Re-read it now, immediately before the
    # wizard starts, instead of relying on cached/template metadata fields.
    try:
        fresh, manifest, fields = get_template_fields(t["category"], t["id"])
    except Exception as e:
        await c.answer("Manifest template tidak valid.",show_alert=True)
        await c.message.answer(f"❌ Template tidak bisa dipakai.\n\n<code>{str(e)[:1000]}</code>",parse_mode="HTML")
        return

    await state.update_data(
        category=fresh["category"],
        template_id=fresh["id"],
        fields=fields,
        manifest=manifest,
        index=0,
        data={},
        list_index=0,
    )
    await track_message(state,c.message.message_id)
    await state.set_state(Form.collecting)
    await persist_wizard(state, c.from_user.id)
    await c.answer()
    await ask_next(c.message,state)

async def ask_next(m,state):
    d=await state.get_data()
    fields=d.get("fields",[])
    i=d.get("index",0)

    if i >= len(fields):
        await finish_creation(m,state)
        return

    f=fields[i]
    typ=str(f.get("type","text")).lower().strip()
    label=f.get("label",f["id"])
    desc=f.get("description","").strip()
    required=f.get("required",False)

    if typ == "text_list":
        n=int(d.get("list_index",0))
        minimum=int(f.get("min",1))
        maximum=int(f.get("max",minimum))
        label=f"{label} {n+1}"
        suffix=desc or "Masukkan teks."
        suffix += f"\n\nItem {n+1} dari {maximum}."
        if n >= minimum:
            suffix += "\nKetik /selesai jika semua item yang dibutuhkan sudah cukup."
    elif typ in PHOTO_TYPES:
        suffix="Kirim 1 foto." if typ=="photo" else "Kirim foto satu per satu, lalu ketik /selesai."
        if not required:
            suffix+=" Ketik /skip jika kosong."
    elif typ in AUDIO_TYPES:
        suffix="Kirim audio/voice."
        if not required:
            suffix+=" Ketik /skip jika kosong."
    else:
        suffix=desc or "Masukkan data untuk field ini."
        example=f.get("example") or f.get("placeholder")
        if example:
            suffix += f"\nContoh: <code>{str(example)}</code>"

    required_text="\n\n⚠️ Field ini wajib diisi." if required else ""
    msg=await m.answer(
        f"✏️ <b>{label}</b>\n{suffix}{required_text}\n\n"
        f"💡 Ketik /keterangan jika belum paham field ini.\n"
        f"🛑 Ketik /batal untuk membatalkan.",
        parse_mode="HTML"
    )
    await track_message(state,msg.message_id)

async def skip_current(m,state):
    await restore_wizard(state,m.from_user.id)
    d=await state.get_data()
    fields=d.get("fields",[])
    i=d.get("index",0)
    if i >= len(fields):
        await finish_creation(m,state)
        return

    f=fields[i]
    if f.get("required"):
        await m.answer("❌ Field ini wajib diisi, jadi tidak bisa dilewati.")
        return

    data=dict(d.get("data") or {})
    typ=str(f.get("type","text")).lower().strip()
    if typ == "text_list":
        data[f["id"]]=[]
    elif typ in PHOTO_TYPES and typ != "photo":
        data[f["id"]]=[]
    else:
        data[f["id"]]=""

    await state.update_data(data=data,index=i+1,list_index=0,photos_buffer=[])
    await persist_wizard(state, m.from_user.id)
    await ask_next(m,state)

@dp.message(Command("keterangan"))
async def keterangan_command(m,state):
    # Bisa dipakai kapan saja. Saat wizard aktif, penjelasan mengikuti field saat ini.
    if await ensure_access(m):
        if await state.get_state():
            await track_message(state,m.message_id)
            await explain_current_field(m,state)
        else:
            msg=await m.answer(
                "ℹ️ <b>Keterangan Web Creator</b>\n\n"
                "Pilih kategori → pilih template → isi data yang diminta bot.\n"
                "📷 Foto: kirim foto satu per satu lalu /selesai.\n"
                "🎵 Musik: kirim audio/voice, bot lanjut otomatis.\n"
                "⏭️ /skip: lewati field yang tidak wajib.\n"
                "🛑 /batal: batalkan sesi saat ini tanpa menghapus website yang sudah dibuat.\n"
                "📁 Web Saya: melihat website yang sudah tersimpan.",
                parse_mode="HTML"
            )

@dp.message(Command("batal"))
async def cancel_command(m,state):
    if not await ensure_access(m):
        return
    active=await state.get_state()
    if active:
        await track_message(state,m.message_id)
        await delete_session_messages(m,state)
        await state.clear()
        await clear_wizard(m.from_user.id)
        menu=await m.answer("🌐 <b>Web Creator</b>\n\nPilih jenis website:",reply_markup=main_menu(),parse_mode="HTML")
        await state.update_data(session_message_ids=[menu.message_id])
    else:
        await m.answer("ℹ️ Tidak ada proses pembuatan yang sedang berjalan.",reply_markup=main_menu())

@dp.message(Command("skip"), StateFilter(Form.collecting))
async def skip_command(m,state):
    await track_message(state,m.message_id)
    await skip_current(m,state)

async def collect_photo(m,state):
    await restore_wizard(state,m.from_user.id)
    d=await state.get_data()
    fields=d.get("fields",[])
    i=d.get("index",0)
    if i >= len(fields):
        return
    f=fields[i]
    typ=str(f.get("type","text")).lower().strip()
    if typ not in PHOTO_TYPES:
        await m.answer("⚠️ Saat ini bot sedang meminta data lain sesuai template.")
        return

    Path("uploads").mkdir(exist_ok=True)
    fn=f"{m.from_user.id}_{m.message_id}.jpg"
    p=Path("uploads")/fn
    tg=await bot.get_file(m.photo[-1].file_id)
    await bot.download_file(tg.file_path,destination=p)

    data=dict(d.get("data") or {})
    if typ == "photo":
        data[f["id"]]=str(p)
        await state.update_data(data=data,index=i+1)
        await persist_wizard(state, m.from_user.id)
        await m.answer("📸 Foto diterima.")
        await ask_next(m,state)
    else:
        photos=list(data.get(f["id"]) or [])
        photos.append(str(p))
        data[f["id"]]=photos
        await state.update_data(data=data,photos_buffer=photos)
        await persist_wizard(state, m.from_user.id)
        await m.answer("📸 Foto diterima. Kirim lagi atau /selesai.")

@dp.message(StateFilter(Form.collecting),F.photo)
async def collect_photo_wrapper(m,state):
    await track_message(state,m.message_id)
    await collect_photo(m,state)

@dp.message(StateFilter(Form.collecting),F.audio)
@dp.message(StateFilter(Form.collecting),F.voice)
@dp.message(StateFilter(Form.collecting),F.document)
async def collect_audio(m,state):
    """Terima musik sebagai Audio, Voice, atau file dokumen audio."""
    await track_message(state,m.message_id)
    d=await state.get_data()
    fields=d.get("fields",[])
    i=d.get("index",0)
    if i >= len(fields):
        return
    f=fields[i]
    typ=str(f.get("type","text")).lower().strip()
    if typ not in AUDIO_TYPES:
        await m.answer("⚠️ Saat ini bot tidak meminta audio.")
        return

    obj=m.audio or m.voice or m.document
    if not obj:
        await m.answer("❌ File musik tidak terbaca. Kirim sebagai Audio/Voice atau file audio.")
        return

    # Dokumen harus benar-benar berupa audio agar file lain tidak dianggap musik.
    if m.document:
        mime=(m.document.mime_type or "").lower()
        name=(m.document.file_name or "").lower()
        if not (mime.startswith("audio/") or name.endswith((".mp3",".m4a",".wav",".ogg",".oga",".flac",".aac"))):
            await m.answer("❌ File itu bukan file audio. Kirim lagu dalam format MP3/M4A/WAV/OGG atau gunakan Audio Telegram.")
            return

    try:
        Path("uploads").mkdir(exist_ok=True)
        if m.audio:
            ext=Path(m.audio.file_name or "music.mp3").suffix or ".mp3"
        elif m.voice:
            ext=".ogg"
        else:
            ext=Path(m.document.file_name or "music.mp3").suffix or ".mp3"
        p=Path("uploads")/f"{m.from_user.id}_{m.message_id}{ext}"
        tg=await bot.get_file(obj.file_id)
        await bot.download_file(tg.file_path,destination=p)

        data=dict(d.get("data") or {})
        data[f["id"]]=str(p)
        await state.update_data(data=data,index=i+1)
        await persist_wizard(state, m.from_user.id)

    except Exception as e:
        await m.answer(f"❌ Musik gagal serahkan ke bot. Coba kirim ulang sebagai Audio Telegram.\n\n<code>{str(e)[:500]}</code>",parse_mode="HTML")
        return

    await m.answer("🎵 Musik diterima. Sedang memproses website...")
    try:
        await ask_next(m,state)
    except Exception as e:
        await m.answer(f"❌ Musik sudah diterima, tetapi proses website gagal.\n\n<code>{str(e)[:1000]}</code>",parse_mode="HTML")

@dp.message(Command("selesai"),StateFilter(Form.collecting))
async def done(m,state):
    await track_message(state,m.message_id)
    d=await state.get_data()
    fields=d.get("fields",[])
    i=d.get("index",0)

    if i >= len(fields):
        await finish_creation(m,state)
        return

    f=fields[i]
    typ=str(f.get("type","text")).lower().strip()

    if typ in PHOTO_TYPES and typ != "photo":
        data=dict(d.get("data") or {})
        photos=list(data.get(f["id"]) or d.get("photos_buffer") or [])
        if f.get("required") and not photos:
            await m.answer("❌ Foto wajib diisi.")
            return
        data[f["id"]]=photos
        await state.update_data(data=data,index=i+1,photos_buffer=[],list_index=0)
        await persist_wizard(state, m.from_user.id)
        await ask_next(m,state)
        return

    if typ == "text_list":
        data=dict(d.get("data") or {})
        values=list(data.get(f["id"]) or [])
        minimum=int(f.get("min",1))
        if len(values) < minimum:
            await m.answer(f"❌ Minimal {minimum} item untuk field ini.")
            return
        await state.update_data(data=data,index=i+1,list_index=0)
        await persist_wizard(state, m.from_user.id)
        await ask_next(m,state)
        return

    await m.answer("ℹ️ /selesai hanya digunakan untuk menyelesaikan foto atau daftar teks.")

@dp.message(StateFilter(Form.collecting),F.text)
async def collect_text(m,state):
    if m.text.startswith("/"):
        return
    await track_message(state,m.message_id)
    await restore_wizard(state,m.from_user.id)
    d=await state.get_data()
    fields=d.get("fields",[])
    i=d.get("index",0)
    if i >= len(fields):
        return
    f=fields[i]
    typ=str(f.get("type","text")).lower().strip()
    if typ in PHOTO_TYPES or typ in AUDIO_TYPES:
        await m.answer("⚠️ Kirim file yang diminta, atau gunakan /skip.")
        return

    data=dict(d.get("data") or {})
    if typ == "text_list":
        values=list(data.get(f["id"]) or [])
        maximum=int(f.get("max",f.get("min",1)))
        if len(values) >= maximum:
            await state.update_data(index=i+1,list_index=0)
            await persist_wizard(state,m.from_user.id)
            await ask_next(m,state)
            return
        values.append(m.text)
        data[f["id"]]=values
        n=len(values)
        if n >= maximum:
            await state.update_data(data=data,index=i+1,list_index=0)
        else:
            await state.update_data(data=data,list_index=n)
        await persist_wizard(state,m.from_user.id)
        await ask_next(m,state)
        return

    data[f["id"]]=m.text
    await state.update_data(data=data,index=i+1,list_index=0)
    await persist_wizard(state,m.from_user.id)
    await ask_next(m,state)

async def finish_creation(m,state):
    await restore_wizard(state, m.from_user.id)
    d=await state.get_data()
    category=d.get("category")
    template_id=d.get("template_id")
    data=dict(d.get("data") or {})
    if not category or not template_id:
        raise RuntimeError("Data sesi template tidak lengkap. Silakan mulai lagi dengan /start.")
    # Re-read manifest one more time at build boundary. This catches a template
    # change made between selection and completion instead of silently building
    # against stale metadata.
    try:
        fresh, manifest, fields = get_template_fields(category, template_id)
        allowed = {f["id"] for f in fields}
        data = {k:v for k,v in data.items() if k in allowed}
        missing = [
            f["id"] for f in fields
            if f.get("required") and (
                data.get(f["id"]) in (None, "", []) or
                (f.get("type") == "text_list" and len(data.get(f["id"]) or []) < int(f.get("min",1)))
            )
        ]
        if missing:
            raise RuntimeError("Field wajib belum lengkap: " + ", ".join(missing))
    except Exception as e:
        await m.answer(f"❌ Website tidak dibuat karena manifest berubah/tidak valid.\n\n<code>{str(e)[:1000]}</code>",parse_mode="HTML")
        return

    wid=create_website(m.from_user.id,category,template_id,data)
    progress=await m.answer("⏳ <b>Data lengkap.</b> Website sedang dibuat dan dipublikasikan...",parse_mode="HTML")
    try:
        published=build_all()
        await deploy_directory(published)
        await progress.edit_text(
            f"✨ <b>Website berhasil dibuat!</b>\n\n"
            f"🔗 <a href='{PUBLIC_BASE_URL}/w/{wid}/'>Buka Website</a>\n"
            f"🆔 <code>{wid}</code>",
            parse_mode="HTML"
        )
    except Exception as e:
        await progress.edit_text(
            f"⚠️ Data website tersimpan, tetapi deploy gagal.\n\n<code>{str(e)[:1000]}</code>",
            parse_mode="HTML"
        )
    await state.clear()
    await clear_wizard(m.from_user.id)

@dp.callback_query(F.data=="mine")
async def mine(c):
    if not await ensure_access(c):return
    items=get_user_websites(c.from_user.id)
    if not items:await c.message.answer("📁 Kamu belum memiliki website.");return
    kb=[]
    for x in items[:30]:
        kb.append([InlineKeyboardButton(text=f"🌐 {x['id']} · {x['template_id']}",url=f"{PUBLIC_BASE_URL}/w/{x['id']}/")])
    await c.message.answer("📁 <b>Web Saya</b>",reply_markup=InlineKeyboardMarkup(inline_keyboard=kb),parse_mode="HTML")

@dp.callback_query(F.data=="feedback")
async def feedback_start(c,state):
    if not await ensure_access(c):return
    await state.set_state(FeedbackForm.collecting)
    await c.message.answer(
        "💡 <b>Kritik & Saran</b>\\n\\n"
        "Kirim kritik, saran fitur, ide tampilan, request template, atau laporan bug.\\n"
        "Pesan ini akan diteruskan ke Owner.",
        parse_mode="HTML"
    )
    await c.answer()


@dp.message(Command("saran"))
async def feedback_command(m,state):
    if not await ensure_access(m):return
    await state.set_state(FeedbackForm.collecting)
    await m.answer(
        "💡 <b>Kritik & Saran</b>\\n\\n"
        "Kirim kritik, saran fitur, ide tampilan, request template, atau laporan bug.\\n"
        "Ketik /batal untuk membatalkan.",
        parse_mode="HTML"
    )


@dp.message(StateFilter(FeedbackForm.collecting),F.text)
async def feedback_receive(m,state):
    if m.from_user.id in ADMIN_IDS:
        pass
    text_feedback=m.text.strip()
    if not text_feedback:
        await m.answer("❌ Saran tidak boleh kosong.")
        return
    for owner_id in ADMIN_IDS:
        try:
            await bot.send_message(
                owner_id,
                "💡 <b>Kritik & Saran Baru</b>\\n\\n"
                f"👤 {m.from_user.full_name}\\n"
                f"🆔 <code>{m.from_user.id}</code>\\n\\n"
                f"{text_feedback}",
                parse_mode="HTML"
            )
        except Exception:
            pass
    await state.clear()
    await m.answer("✅ Kritik & saran kamu sudah dikirim ke Owner. Terima kasih! 💜")


@dp.message(Command("admin"))
async def admin_panel(m):
    if m.from_user.id not in ADMIN_IDS:return
    kb=InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📢 Pengumuman",callback_data="adm:broadcast")],
        [InlineKeyboardButton(text="📊 Statistik",callback_data="adm:stats")],
        [InlineKeyboardButton(text="🎨 Template",callback_data="adm:templates")],
        [InlineKeyboardButton(text="➕ Tambah Template",callback_data="adm:add_template")],
        [InlineKeyboardButton(text="🗑️ Hapus Template",callback_data="adm:delete_template")],
        [InlineKeyboardButton(text="🚀 Redeploy",callback_data="adm:redeploy")]])
    await m.answer("👑 <b>Admin Panel</b>",reply_markup=kb,parse_mode="HTML")

@dp.callback_query(F.data=="adm:stats")
async def adm_stats(c):
    if c.from_user.id not in ADMIN_IDS:return
    u,a,w=stats();await c.message.answer(f"📊 <b>Statistik</b>\n\n👥 User: {u}\n🟢 Aktif: {a or 0}\n🌐 Website: {w}",parse_mode="HTML")


@dp.callback_query(F.data=="adm:redeploy")
async def adm_redeploy(c):
    if c.from_user.id not in ADMIN_IDS:return
    await c.answer("⏳ Redeploy...")
    try:await deploy_directory(build_all());await c.message.answer("✅ Semua website berhasil di-redeploy.")
    except Exception as e:await c.message.answer(f"❌ Redeploy gagal: <code>{str(e)[:1000]}</code>",parse_mode="HTML")

@dp.callback_query(F.data=="adm:broadcast")
async def adm_broadcast(c,state):
    if c.from_user.id not in ADMIN_IDS:return
    await state.set_state(AdminForm.broadcast)
    await c.message.answer("📢 Kirim teks update. Gunakan HTML Telegram jika diperlukan.")

@dp.message(StateFilter(AdminForm.broadcast),F.text)
async def broadcast(m,state):
    if m.from_user.id not in ADMIN_IDS:return
    bid=add_broadcast(m.from_user.id,m.text);sent=failed=0;us=get_users(True)
    await m.answer(f"📤 Mengirim ke {len(us)} pengguna...")
    for u in us:
        try:
            await bot.send_message(u["telegram_id"],m.text,parse_mode="HTML")
            sent+=1
        except Exception:
            failed+=1;deactivate_user(u["telegram_id"])
        await asyncio.sleep(0.08)
    finish_broadcast(bid,sent,failed);await state.clear()
    await m.answer(f"✅ Broadcast selesai.\n\n📤 {sent}\n🚫 {failed}")

@dp.callback_query(F.data=="back")
async def back(c,state):
    await state.clear()
    await c.message.edit_text("🌐 <b>Web Creator</b>\n\nPilih jenis website:",reply_markup=main_menu(),parse_mode="HTML")
    await track_message(state,c.message.message_id)



# ===== OWNER TEMPLATE MANAGEMENT (latest definitions) =====

def remove_template(category, template_id):
    target=_template_folder(category,template_id)
    if not target or not target.is_dir():
        return False
    shutil.rmtree(target)
    return True


@dp.callback_query(F.data=="adm:templates")
async def adm_templates_new(c):
    if c.from_user.id not in ADMIN_IDS:return
    ts=list_templates()
    if not ts:
        await c.message.answer("🎨 Belum ada template.")
        return
    text="🎨 <b>Template Aktif</b>\n\n"
    for t in ts:
        text += f"• <b>{t['category_name']}</b> / {t['name']}\n"
        if t.get("description"):
            text += f"  {t['description']}\n"
    await c.message.answer(text,parse_mode="HTML")


@dp.callback_query(F.data=="adm:add_template")
async def adm_add_template_start(c,state):
    if c.from_user.id not in ADMIN_IDS:return
    await state.set_state(AdminForm.add_template)
    await c.message.answer(
        "➕ <b>Tambah Template</b>\n\n"
        "Kirim dalam format:\n"
        "<code>kategori | nama template</code>\n\n"
        "Contoh:\n"
        "<code>anniversary | Forever With You</code>",
        parse_mode="HTML"
    )


@dp.message(StateFilter(AdminForm.add_template),F.text)
async def adm_add_template_meta(m,state):
    if m.from_user.id not in ADMIN_IDS:return
    if "|" not in m.text:
        await m.answer("❌ Format salah. Gunakan: <code>kategori | nama template</code>",parse_mode="HTML")
        return
    category,name=[x.strip() for x in m.text.split("|",1)]
    if not _safe_template_folder(category,name):
        await m.answer("❌ Nama kategori/template tidak valid.")
        return
    await state.update_data(template_category=category,template_name=name)
    await state.set_state(AdminForm.add_template_zip)
    await m.answer(
        f"📦 Kategori: <b>{category}</b>\n"
        f"🎨 Nama: <b>{name}</b>\n\n"
        "Sekarang kirim <b>ZIP template</b>.",
        parse_mode="HTML"
    )


@dp.message(StateFilter(AdminForm.add_template_zip),F.document)
async def adm_add_template_zip(m,state):
    if m.from_user.id not in ADMIN_IDS:return
    name=(m.document.file_name or "").lower()
    if not name.endswith(".zip"):
        await m.answer("❌ Kirim file .zip.")
        return

    d=await state.get_data()
    category=d.get("template_category")
    template_name=d.get("template_name")
    TEMPLATE_INBOX.mkdir(exist_ok=True)
    tmp=TEMPLATE_INBOX/f"{m.from_user.id}_{m.message_id}.zip"

    try:
        tg=await bot.get_file(m.document.file_id)
        await bot.download_file(tg.file_path,destination=tmp)
        target=_install_template_zip(tmp,category,template_name)
        await state.clear()
        await m.answer(
            f"✅ <b>Template berhasil ditambahkan.</b>\n\n"
            f"📁 Kategori: <code>{category}</code>\n"
            f"🎨 Nama: <b>{template_name}</b>\n"
            f"📂 Folder: <code>{target.as_posix()}</code>\n\n"
            "Template langsung tersedia di menu pembuatan website.",
            parse_mode="HTML"
        )
    except Exception as e:
        await m.answer(
            f"❌ <b>Template gagal ditambahkan.</b>\n\n<code>{str(e)[:1000]}</code>",
            parse_mode="HTML"
        )
    finally:
        tmp.unlink(missing_ok=True)


@dp.message(Command("addtemplate"))
async def add_template_command(m,state):
    if m.from_user.id not in ADMIN_IDS:return
    await state.set_state(AdminForm.add_template)
    await m.answer(
        "➕ <b>Tambah Template</b>\n\n"
        "Kirim dalam format:\n"
        "<code>kategori | nama template</code>\n\n"
        "Contoh:\n"
        "<code>anniversary | Forever With You</code>",
        parse_mode="HTML"
    )


@dp.message(Command("hapustemplate"))
async def delete_template_command(m,state):
    if m.from_user.id not in ADMIN_IDS:return
    ts=list_templates()
    if not ts:
        await m.answer("🎨 Belum ada template yang bisa dihapus.")
        return
    rows=[]
    for t in ts:
        rows.append([
            InlineKeyboardButton(
                text=f"🗑️ {t['category_name']} / {t['name']}",
                callback_data=f"adm:del:{t['key']}"
            )
        ])
    await m.answer(
        "🗑️ <b>Hapus Template</b>\n\nPilih template:",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=rows),
        parse_mode="HTML"
    )



@dp.callback_query(F.data=="adm:delete_template")
async def adm_delete_template_new(c):
    if c.from_user.id not in ADMIN_IDS:return
    ts=list_templates()
    if not ts:
        await c.answer("Tidak ada template.",show_alert=True)
        return
    rows=[]
    for t in ts:
        rows.append([
            InlineKeyboardButton(
                text=f"🗑️ {t['category_name']} / {t['name']}",
                callback_data=f"adm:del:{t['key']}"
            )
        ])
    rows.append([InlineKeyboardButton(text="⬅️ Panel Owner",callback_data="adm:back")])
    await c.message.edit_text(
        "🗑️ <b>Hapus Template</b>\n\nPilih template:",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=rows),
        parse_mode="HTML"
    )


@dp.callback_query(F.data.startswith("adm:del:"))
async def adm_delete_template_confirm_new(c):
    if c.from_user.id not in ADMIN_IDS:return
    key=c.data.split(":",2)[2]
    t=get_template_by_key(key)
    if not t:
        await c.answer("Template tidak ditemukan.",show_alert=True)
        return
    await c.message.edit_text(
        f"⚠️ <b>Konfirmasi Hapus</b>\n\n"
        f"📁 {t['category_name']}\n"
        f"🎨 {t['name']}\n\n"
        "Website yang sudah dibuat <b>tetap aman</b>.\n"
        "Yang dihapus hanya template sumber.",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [
                InlineKeyboardButton(text="✅ Ya, Hapus",callback_data=f"adm:delconfirm:{t['key']}"),
                InlineKeyboardButton(text="❌ Batal",callback_data="adm:delete_template")
            ]
        ]),
        parse_mode="HTML"
    )


@dp.callback_query(F.data.startswith("adm:delconfirm:"))
async def adm_delete_template_execute_new(c):
    if c.from_user.id not in ADMIN_IDS:return
    key=c.data.split(":",2)[2]
    t=get_template_by_key(key)
    if not t:
        await c.answer("Template tidak ditemukan.",show_alert=True)
        return
    try:
        ok=remove_template(t["category"],t["id"])
    except Exception as e:
        await c.answer("Gagal menghapus template.",show_alert=True)
        return
    if not ok:
        await c.answer("Template sudah tidak ada.",show_alert=True)
        return
    await c.answer("Template dihapus.")
    await c.message.edit_text(
        f"✅ <b>Template berhasil dihapus.</b>\n\n"
        f"📂 <code>{t['category']}/{t['id']}</code>\n\n"
        "Website yang sudah dibuat sebelumnya tetap aman.",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🗑️ Hapus Template Lain",callback_data="adm:delete_template")],
            [InlineKeyboardButton(text="⬅️ Panel Owner",callback_data="adm:back")]
        ]),
        parse_mode="HTML"
    )


# Durable-session fallbacks. These are intentionally registered after all normal
# handlers, so they only run when MemoryStorage has lost the FSM state.
@dp.message(Command("skip"))
async def durable_skip_fallback(m, state):
    if await restore_wizard(state, m.from_user.id):
        await track_message(state, m.message_id)
        await skip_current(m, state)

@dp.message(F.photo)
async def durable_photo_fallback(m, state):
    if await restore_wizard(state, m.from_user.id):
        await track_message(state, m.message_id)
        await collect_photo(m, state)

@dp.message(F.audio)
@dp.message(F.voice)
@dp.message(F.document)
async def durable_audio_fallback(m, state):
    if await restore_wizard(state, m.from_user.id):
        await track_message(state, m.message_id)
        await collect_audio(m, state)

@dp.message(F.text)
async def durable_text_fallback(m, state):
    if m.text.startswith("/"):
        return
    if await restore_wizard(state, m.from_user.id):
        await collect_text(m, state)
