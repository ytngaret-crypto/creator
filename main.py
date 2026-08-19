import asyncio, json, shutil
from pathlib import Path
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

class Form(StatesGroup):
    collecting=State()
class AdminForm(StatesGroup):
    broadcast=State()

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
    if await is_joined(uid):
        upsert_user(event.from_user);return True
    if isinstance(event,CallbackQuery):
        await event.answer("❌ Join channel terlebih dahulu.",show_alert=True)
        await event.message.answer("🔒 Akses terbatas. Silakan join channel terlebih dahulu.",reply_markup=join_kb())
    else:
        await event.answer("🔒 <b>Akses Terbatas</b>\n\nIkuti channel kami terlebih dahulu.",reply_markup=join_kb(),parse_mode="HTML")
    return False

@dp.message(CommandStart())
async def start(m,state):
    await state.clear()
    if await ensure_access(m):
        await m.answer("🌐 <b>Web Creator</b>\n\nPilih jenis website:",reply_markup=main_menu(),parse_mode="HTML")

@dp.callback_query(F.data=="joincheck")
async def joincheck(c,state):
    if not await is_joined(c.from_user.id):
        await c.answer("❌ Kamu belum join channel.",show_alert=True);return
    upsert_user(c.from_user);await state.clear();await c.answer("✅ Verifikasi berhasil!")
    await c.message.edit_text("🌐 <b>Web Creator</b>\n\nPilih jenis website:",reply_markup=main_menu(),parse_mode="HTML")

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
    await c.answer()

@dp.callback_query(F.data.startswith("tpl:"))
async def choose_template(c,state):
    if not await ensure_access(c):return
    d=await state.get_data();t=get_template(d.get("category",""),c.data.split(":",1)[1])
    if not t:await c.answer("Template tidak ditemukan.",show_alert=True);return
    await state.update_data(template_id=t["id"],fields=t.get("fields",[]),index=0,data={})
    await state.set_state(Form.collecting);await c.answer()
    await ask_next(c.message,state)

async def ask_next(m,state):
    d=await state.get_data();fields=d["fields"];i=d["index"]
    if i>=len(fields):
        await finish_creation(m,state);return
    f=fields[i];typ=f.get("type","text");label=f.get("label",f["id"])
    if typ=="photos":
        await m.answer(f"📸 <b>{label}</b>\nKirim foto satu per satu. Ketik /skip jika kosong atau /selesai jika sudah selesai.",parse_mode="HTML")
    elif typ=="audio":
        await m.answer(f"🎵 <b>{label}</b>\nKirim audio/voice. Ketik /skip jika kosong.",parse_mode="HTML")
    else:
        await m.answer(f"✏️ <b>{label}</b>\n{f.get('description','')}\n\nKetik /skip jika field ini boleh dikosongkan.",parse_mode="HTML")

def next_index(d):
    d["index"]+=1;return d

@dp.message(StateFilter(Form.collecting),F.photo)
async def collect_photo(m,state):
    d=await state.get_data();f=d["fields"][d["index"]]
    if f.get("type")!="photos":
        await m.answer("⚠️ Saat ini bot sedang meminta teks.");return
    Path("uploads").mkdir(exist_ok=True)
    fn=f"{m.from_user.id}_{m.message_id}.jpg";p=Path("uploads")/fn
    tg=await bot.get_file(m.photo[-1].file_id);await bot.download_file(tg.file_path,destination=p)
    data=d["data"];data.setdefault(f["id"],[]).append(str(p));await state.update_data(data=data)
    await m.answer("📸 Foto diterima. Kirim lagi atau /selesai.")

@dp.message(StateFilter(Form.collecting),F.audio)
@dp.message(StateFilter(Form.collecting),F.voice)
async def collect_audio(m,state):
    d=await state.get_data();f=d["fields"][d["index"]]
    if f.get("type")!="audio":await m.answer("⚠️ Saat ini bot tidak meminta audio.");return
    Path("uploads").mkdir(exist_ok=True)
    obj=m.audio or m.voice;ext=".mp3" if m.audio else ".ogg";p=Path("uploads")/f"{m.from_user.id}_{m.message_id}{ext}"
    tg=await bot.get_file(obj.file_id);await bot.download_file(tg.file_path,destination=p)
    data=d["data"];data[f["id"]]=str(p);await state.update_data(data=data)
    await state.update_data(index=d["index"]+1);await ask_next(m,state)

@dp.message(StateFilter(Form.collecting),F.text)
async def collect_text(m,state):
    d=await state.get_data();f=d["fields"][d["index"]];typ=f.get("type","text")
    if typ in {"photos","audio"}:
        if m.text.lower() in {"/skip","/selesai"}:
            await state.update_data(index=d["index"]+1)
            await ask_next(m,state)
        else:await m.answer("⚠️ Kirim file yang diminta, atau gunakan /skip.")
        return
    data=d["data"]
    if m.text.lower()=="/skip":
        if f.get("required"):await m.answer("❌ Field ini wajib diisi.");return
        data[f["id"]]=""
    else:data[f["id"]]=m.text
    await state.update_data(data=data,index=d["index"]+1);await ask_next(m,state)

async def finish_creation(m,state):
    d=await state.get_data();wid=create_website(m.from_user.id,d["category"],d["template_id"],d["data"])
    try:
        published=build_all()
        await deploy_directory(published)
        await m.answer(f"✨ <b>Website berhasil dibuat!</b>\n\n🔗 <a href='{PUBLIC_BASE_URL}/w/{wid}/'>Buka Website</a>\n🆔 <code>{wid}</code>",parse_mode="HTML")
    except Exception as e:
        await m.answer(f"⚠️ Data website tersimpan, tetapi deploy gagal.\n\n<code>{str(e)[:1000]}</code>",parse_mode="HTML")
    await state.clear()

@dp.message(Command("selesai"),StateFilter(Form.collecting))
async def done(m,state):
    d=await state.get_data();f=d["fields"][d["index"]]
    if f.get("type")=="photos":
        if f.get("required") and not d["data"].get(f["id"]):await m.answer("❌ Foto wajib diisi.");return
        await state.update_data(index=d["index"]+1);await ask_next(m,state)
    else:await m.answer("ℹ️ Gunakan /skip atau kirim data yang diminta.")

@dp.callback_query(F.data=="mine")
async def mine(c):
    if not await ensure_access(c):return
    items=get_user_websites(c.from_user.id)
    if not items:await c.message.answer("📁 Kamu belum memiliki website.");return
    kb=[]
    for x in items[:30]:
        kb.append([InlineKeyboardButton(text=f"🌐 {x['id']} · {x['template_id']}",url=f"{PUBLIC_BASE_URL}/w/{x['id']}/")])
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
    await state.clear();await c.message.edit_text("🌐 <b>Web Creator</b>\n\nPilih jenis website:",reply_markup=main_menu(),parse_mode="HTML")

async def run():
    init_db();await dp.start_polling(bot)

if __name__=="__main__":asyncio.run(run())
