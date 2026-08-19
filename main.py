import asyncio, json, shutil
from pathlib import Path
from urllib.parse import quote
from html import escape
from aiogram import Bot, Dispatcher, F
from aiogram.filters import CommandStart, Command
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import StatesGroup, State
from aiogram.filters import StateFilter
from config import BOT_TOKEN, FORCE_JOIN_CHANNEL, PUBLIC_BASE_URL, ADMIN_IDS
from db import *
from templates import list_templates,get_template
from builder import build_all
from netlify import deploy_directory

bot=Bot(BOT_TOKEN);dp=Dispatcher()

def website_url(website_id):
    """Return the canonical URL for a generated website."""
    safe_id=quote(str(website_id), safe="")
    return f"{PUBLIC_BASE_URL.rstrip('/')}/w/{safe_id}/"

class Form(StatesGroup):
    collecting=State()
class AdminForm(StatesGroup):
    broadcast=State()

class FeedbackForm(StatesGroup):
    waiting=State()

# Pesan yang terkait sesi wizard disimpan di FSM agar /batal dapat
# membersihkannya tanpa menyentuh website yang sudah tersimpan di database.
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
    typ=f.get("type","text")
    type_hint={
        "text":"Kirim teks biasa.",
        "photos":"Kirim foto satu per satu, lalu ketik /selesai.",
        "audio":"Kirim audio atau voice; setelah diterima bot lanjut otomatis."
    }.get(typ,"Kirim data sesuai contoh/template.")
    required="\n\n⚠️ Field ini wajib diisi." if f.get("required") else "\n\nField ini boleh dilewati dengan /skip."
    text=f"ℹ️ <b>Keterangan: {label}</b>\n\n{desc or type_hint}\n\n{type_hint}{required}\n\nGunakan /batal untuk membatalkan proses."
    msg=await m.answer(text,parse_mode="HTML")
    await track_message(state,msg.message_id)

CATEGORIES={
"kenangan":("💌 Web Kenangan","Abadikan foto, chat, cerita, dan momen berharga dalam satu website interaktif."),
"confess":("💭 Web Confess","Sampaikan perasaan secara personal dengan website penuh kejutan."),
"birthday":("🎂 Web Ulang Tahun","Buat ucapan ulang tahun interaktif berisi foto, pesan, musik, dan kejutan."),
"anniversary":("❤️ Web Anniversary","Rayakan perjalanan hubungan dengan timeline, foto, cerita, dan pesan spesial."),
"farewell":("💐 Web Perpisahan","Simpan pesan, kenangan, dan ucapan terakhir dalam satu website."),
"surprise":("🎁 Web Surprise","Buat website kejutan dengan pesan, animasi, musik, dan efek interaktif.")
}

def main_menu():
    rows=[[InlineKeyboardButton(text=v[0],callback_data=f"cat:{k}")] for k,v in CATEGORIES.items()]
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
    cat=c.data.split(":",1)[1];ts=list_templates(cat)
    if not ts:
        await c.answer("Belum ada template untuk kategori ini.",show_alert=True);return
    buttons=[[InlineKeyboardButton(text=f"🎨 {t['name']}",callback_data=f"tpl:{t['id']}")] for t in ts]
    buttons.append([InlineKeyboardButton(text="⬅️ Kembali",callback_data="back")])
    await state.update_data(category=cat)
    await c.message.edit_text(f"<b>{CATEGORIES[cat][0]}</b>\n\n{CATEGORIES[cat][1]}\n\n<b>Pilih template:</b>",reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons),parse_mode="HTML")
    await track_message(state,c.message.message_id)
    await c.answer()

@dp.callback_query(F.data.startswith("tpl:"))
async def choose_template(c,state):
    if not await ensure_access(c):return
    d=await state.get_data();t=get_template(d.get("category",""),c.data.split(":",1)[1])
    if not t:await c.answer("Template tidak ditemukan.",show_alert=True);return
    await state.update_data(template_id=t["id"],fields=t.get("fields",[]),index=0,data={})
    await track_message(state,c.message.message_id)
    await state.set_state(Form.collecting);await c.answer()
    await ask_next(c.message,state)

async def ask_next(m,state):
    d=await state.get_data()
    fields=d.get("fields",[])
    i=d.get("index",0)

    if i >= len(fields):
        await finish_creation(m,state)
        return

    f=fields[i]
    typ=f.get("type","text")
    label=f.get("label",f["id"])
    desc=f.get("description","").strip()
    required=f.get("required",False)

    if typ=="photos":
        suffix="Kirim foto satu per satu."
        if not required:
            suffix+=" Ketik /skip jika kosong."
        suffix+=" Ketik /selesai jika sudah selesai."
    elif typ=="audio":
        suffix="Kirim audio/voice."
        if not required:
            suffix+=" Ketik /skip jika kosong."
    else:
        suffix=desc or "Masukkan data untuk field ini."
        if not required:
            suffix+="\n\nKetik /skip jika ingin melewati field ini."

    required_text="\n\n⚠️ Field ini wajib diisi." if required else ""
    msg=await m.answer(
        f"✏️ <b>{label}</b>\n{suffix}{required_text}\n\n💡 Ketik /keterangan jika belum paham field ini.\n🛑 Ketik /batal untuk membatalkan.",
        parse_mode="HTML"
    )
    await track_message(state,msg.message_id)

async def skip_current(m,state):
    """Advance the current wizard field when /skip is sent."""
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

    data=d.get("data",{})
    data[f["id"]] = [] if f.get("type")=="photos" else ""
    await state.update_data(data=data,index=i+1)
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

@dp.callback_query(F.data=="feedback")
async def feedback_button(c,state):
    if not await ensure_access(c):
        return
    await state.clear()
    await state.set_state(FeedbackForm.waiting)
    msg=await c.message.answer(
        "💡 <b>Kritik &amp; Saran</b>\n\n"
        "Ada masukan untuk fitur, tampilan, template, atau bug?\n"
        "Kirim kritik/saran kamu dalam satu pesan. Pesan ini akan diteruskan ke owner.\n\n"
        "🛑 Ketik /batal untuk membatalkan.",
        parse_mode="HTML"
    )
    await track_message(state,msg.message_id)
    await c.answer()

@dp.message(Command("saran"))
async def feedback_command(m,state):
    if not await ensure_access(m):
        return
    await state.clear()
    await state.set_state(FeedbackForm.waiting)
    await track_message(state,m.message_id)
    msg=await m.answer(
        "💡 <b>Kritik &amp; Saran</b>\n\n"
        "Tulis kritik, saran update, ide tampilan, atau laporan bug yang ingin kamu sampaikan kepada owner.\n\n"
        "🛑 Ketik /batal untuk membatalkan.",
        parse_mode="HTML"
    )
    await track_message(state,msg.message_id)

@dp.message(StateFilter(FeedbackForm.waiting),F.text)
async def receive_feedback(m,state):
    if m.text.startswith("/"):
        return
    await track_message(state,m.message_id)
    user=m.from_user
    username=f"@{user.username}" if user.username else "(tanpa username)"
    feedback=(
        "💡 <b>Kritik &amp; Saran Baru</b>\n\n"
        f"👤 Nama: {escape(user.full_name)}\n"
        f"🆔 ID: <code>{user.id}</code>\n"
        f"🔗 Username: {escape(username)}\n\n"
        f"📝 <b>Pesan:</b>\n{escape(m.text)}"
    )
    sent=0
    for admin_id in ADMIN_IDS:
        try:
            await bot.send_message(admin_id,feedback,parse_mode="HTML")
            sent+=1
        except Exception:
            pass
    await state.clear()
    if sent:
        await m.answer("✅ Kritik/saran kamu sudah dikirim ke owner. Terima kasih! 🙏",reply_markup=main_menu())
    else:
        await m.answer("⚠️ Kritik/saran gagal dikirim. Silakan coba lagi nanti.",reply_markup=main_menu())

@dp.message(Command("batal"))
async def cancel_command(m,state):
    if not await ensure_access(m):
        return
    active=await state.get_state()
    if active:
        await track_message(state,m.message_id)
        await delete_session_messages(m,state)
        await state.clear()
        menu=await m.answer("🌐 <b>Web Creator</b>\n\nPilih jenis website:",reply_markup=main_menu(),parse_mode="HTML")
        await state.update_data(session_message_ids=[menu.message_id])
    else:
        await m.answer("ℹ️ Tidak ada proses pembuatan yang sedang berjalan.",reply_markup=main_menu())

@dp.message(Command("skip"), StateFilter(Form.collecting))
async def skip_command(m,state):
    await track_message(state,m.message_id)
    await skip_current(m,state)

async def collect_photo(m,state):
    d=await state.get_data()
    fields=d.get("fields",[])
    i=d.get("index",0)
    if i >= len(fields): return
    f=fields[i]
    if f.get("type")!="photos":
        await m.answer("⚠️ Saat ini bot sedang meminta teks.")
        return
    Path("uploads").mkdir(exist_ok=True)
    fn=f"{m.from_user.id}_{m.message_id}.jpg"
    p=Path("uploads")/fn
    tg=await bot.get_file(m.photo[-1].file_id)
    await bot.download_file(tg.file_path,destination=p)
    data=d.get("data",{})
    data.setdefault(f["id"],[]).append(str(p))
    await state.update_data(data=data)
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
    if f.get("type")!="audio":
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
    # Handler ini HARUS terdaftar sebelum handler F.text agar /selesai
    # tidak dianggap sebagai teks biasa saat field foto sedang aktif.
    await track_message(state,m.message_id)
    d=await state.get_data()
    fields=d.get("fields",[])
    i=d.get("index",0)

    if i >= len(fields):
        await finish_creation(m,state)
        return

    f=fields[i]
    if f.get("type")=="photos":
        photos=d.get("data",{}).get(f["id"],[])
        if f.get("required") and not photos:
            await m.answer("❌ Foto wajib diisi.")
            return
        await state.update_data(index=i+1)
        await ask_next(m,state)
    else:
        await m.answer("ℹ️ /selesai hanya digunakan untuk menyelesaikan upload foto. Untuk musik, cukup kirim audio/voice atau gunakan /skip.")

@dp.message(StateFilter(Form.collecting),F.text)
async def collect_text(m,state):
    # Command tidak boleh diproses sebagai jawaban field teks.
    if m.text.startswith("/"):
        return
    await track_message(state,m.message_id)
    d=await state.get_data()
    fields=d.get("fields",[])
    i=d.get("index",0)
    if i >= len(fields): return
    f=fields[i]
    typ=f.get("type","text")
    if typ in {"photos","audio"}:
        await m.answer("⚠️ Kirim file yang diminta, atau gunakan /skip.")
        return

    data=d.get("data",{})
    data[f["id"]]=m.text
    await state.update_data(data=data,index=i+1)
    await ask_next(m,state)

def normalize_submission(category, template_id, data):
    """Keep canonical media keys while preserving the manifest field ids."""
    template=get_template(category,template_id)
    if not template:
        return data
    fields=template.get("fields",[]) or []
    result=dict(data or {})
    for f in fields:
        if not isinstance(f,dict):
            continue
        fid=str(f.get("id","")).strip()
        typ=str(f.get("type","text")).lower().strip()
        if not fid:
            continue
        if typ in {"photos","photo","images","image","gallery"}:
            result.setdefault("gallery",result.get(fid,[]))
        elif typ in {"audio","music","song","voice"}:
            result.setdefault("music",result.get(fid,""))
    return result

async def finish_creation(m,state):
    d=await state.get_data()
    category=d.get("category")
    template_id=d.get("template_id")
    data=normalize_submission(category,template_id,d.get("data") or {})
    if not category or not template_id:
        raise RuntimeError("Data sesi template tidak lengkap. Silakan mulai lagi dengan /start.")
    wid=create_website(m.from_user.id,category,template_id,data)
    progress=await m.answer("⏳ <b>Data lengkap.</b> Website sedang dibuat dan dipublikasikan...",parse_mode="HTML")
    try:
        published=build_all()
        await deploy_directory(published)
        await progress.edit_text(
            f"✨ <b>Website berhasil dibuat!</b>\n\n"
            f"🔗 <a href='{website_url(wid)}'>Buka Website</a>\n"
            f"🆔 <code>{wid}</code>",
            parse_mode="HTML"
        )
    except Exception as e:
        await progress.edit_text(
            f"⚠️ Data website tersimpan, tetapi deploy gagal.\n\n<code>{str(e)[:1000]}</code>",
            parse_mode="HTML"
        )
    await state.clear()

@dp.callback_query(F.data=="mine")
async def mine(c):
    if not await ensure_access(c):return
    items=get_user_websites(c.from_user.id)
    if not items:await c.message.answer("📁 Kamu belum memiliki website.");return
    kb=[]
    for x in items[:30]:
        kb.append([InlineKeyboardButton(text=f"🌐 {x['id']} · {x['template_id']}",url=website_url(x["id"]))])
    await c.message.answer("📁 <b>Web Saya</b>",reply_markup=InlineKeyboardMarkup(inline_keyboard=kb),parse_mode="HTML")

@dp.message(Command("admin"))
async def admin_panel(m):
    if m.from_user.id not in ADMIN_IDS:return
    kb=InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📢 Pengumuman",callback_data="adm:broadcast")],
        [InlineKeyboardButton(text="📊 Statistik",callback_data="adm:stats")],
        [InlineKeyboardButton(text="🎨 Template",callback_data="adm:templates")],
        [InlineKeyboardButton(text="🚀 Redeploy",callback_data="adm:redeploy")]])
    await m.answer("👑 <b>Admin Panel</b>",reply_markup=kb,parse_mode="HTML")

@dp.callback_query(F.data=="adm:stats")
async def adm_stats(c):
    if c.from_user.id not in ADMIN_IDS:return
    u,a,w=stats();await c.message.answer(f"📊 <b>Statistik</b>\n\n👥 User: {u}\n🟢 Aktif: {a or 0}\n🌐 Website: {w}",parse_mode="HTML")

@dp.callback_query(F.data=="adm:templates")
async def adm_templates(c):
    if c.from_user.id not in ADMIN_IDS:return
    ts=list_templates();text="🎨 <b>Template</b>\n\n"
    for t in ts:text+=f"• {t['category']} / {t['name']}\n  {t.get('description','')}\n"
    await c.message.answer(text,parse_mode="HTML")

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

