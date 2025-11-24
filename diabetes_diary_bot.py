#!/usr/bin/env python3
"""
Shared Diabetes Diary Telegram Bot
- For families or caregivers managing a child with diabetes.
- Works in a private Telegram group (invite-only).
- All members can log entries; reports include all entries from that group.
"""

import os
import io
import sqlite3
import logging
import datetime
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from telegram import (
    Update,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
    constants as tg_constants,
)
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    ConversationHandler,
    ContextTypes,
    filters,
)
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.units import mm
from reportlab.pdfgen import canvas
from reportlab.lib import colors
import matplotlib.pyplot as plt
from PIL import Image

# --- Logging ---
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# --- Config ---
BOT_TOKEN = os.environ.get("BOT_TOKEN")
if not BOT_TOKEN:
    raise RuntimeError("Set BOT_TOKEN environment variable")

DB_PATH = os.environ.get("DB_PATH", "diary.db")
DAILY_REPORT_TIME = os.environ.get("DAILY_REPORT_TIME")  # e.g. "21:00"
DAILY_REPORT_CHAT_ID = os.environ.get("DAILY_REPORT_CHAT_ID")
TIMEZONE = os.environ.get("TIMEZONE", None)

# --- Conversation states ---
MEAL, PRE_BGL, POST_BGL, CARBS, RATIO, UNITS, NOTES, CONFIRM = range(8)

# --- Database ---
def init_db():
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS entries (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            chat_id INTEGER NOT NULL,
            user_id INTEGER NOT NULL,
            username TEXT,
            timestamp TEXT NOT NULL,
            local_date TEXT NOT NULL,
            meal TEXT,
            pre_bgl REAL,
            post_bgl REAL,
            carbs REAL,
            ratio TEXT,
            insulin_units REAL,
            notes TEXT
        )
        """
    )
    conn.commit()
    conn.close()

def save_entry(chat_id, user_id, username, meal, pre, post, carbs, ratio, units, notes):
    ts = datetime.datetime.now()
    local_date = ts.date().isoformat()
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute(
        """
        INSERT INTO entries (chat_id, user_id, username, timestamp, local_date, meal, pre_bgl, post_bgl, carbs, ratio, insulin_units, notes)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            chat_id,
            user_id,
            username,
            ts.isoformat(),
            local_date,
            meal,
            pre,
            post,
            carbs,
            ratio,
            units,
            notes,
        ),
    )
    conn.commit()
    conn.close()

def fetch_entries_for_chat(chat_id, since_date):
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    cur.execute(
        "SELECT * FROM entries WHERE chat_id=? AND date(local_date) >= date(?) ORDER BY timestamp ASC",
        (chat_id, since_date.isoformat()),
    )
    rows = cur.fetchall()
    conn.close()
    return rows

# --- PDF generation ---
def generate_pdf_buffer(chat_title, rows, start_date, end_date):
    buf = io.BytesIO()

    timestamps, pre_vals, post_vals, authors = [], [], [], []
    for r in rows:
        ts = datetime.datetime.fromisoformat(r["timestamp"])
        timestamps.append(ts)
        pre_vals.append(r["pre_bgl"] if r["pre_bgl"] is not None else float("nan"))
        post_vals.append(r["post_bgl"] if r["post_bgl"] is not None else float("nan"))
        authors.append(r["username"] or "")

    plot_buf = None
    if timestamps:
        plt.figure(figsize=(8, 3))
        plt.plot(timestamps, pre_vals, "o-", label="Pre-meal BGL")
        plt.plot(timestamps, post_vals, "o--", label="Post-meal BGL")
        plt.xticks(rotation=45)
        plt.title(f"BGL Trend — {start_date.isoformat()} to {end_date.isoformat()}")
        plt.legend()
        plt.tight_layout()
        plot_buf = io.BytesIO()
        plt.savefig(plot_buf, format="PNG", bbox_inches="tight")
        plt.close()
        plot_buf.seek(0)

    c = canvas.Canvas(buf, pagesize=landscape(A4))
    width, height = landscape(A4)
    margin = 15 * mm
    x, y = margin, height - margin

    c.setFont("Helvetica-Bold", 16)
    c.drawString(x, y, f"Diabetes Diary — {chat_title}")
    y -= 10 * mm
    c.setFont("Helvetica", 10)
    c.drawString(x, y, f"Report period: {start_date.isoformat()} to {end_date.isoformat()}")
    y -= 8 * mm

    if plot_buf:
        pil_im = Image.open(plot_buf)
        max_h = 60 * mm
        aspect = pil_im.width / pil_im.height
        plot_w = width - 2 * margin
        plot_h = min(max_h, plot_w / aspect)
        c.drawInlineImage(pil_im, x, y - plot_h, width=plot_w, height=plot_h)
        y -= plot_h + 8 * mm

    # Table
    c.setFont("Helvetica-Bold", 9)
    headers = ["DateTime", "Meal", "Pre", "Post", "Carbs", "Ratio", "Units", "By", "Notes"]
    col_w = [35*mm, 25*mm, 15*mm, 15*mm, 15*mm, 20*mm, 15*mm, 20*mm, width - 2*margin - 160*mm]
    tx = x
    for h, w in zip(headers, col_w):
        c.drawString(tx + 2, y, h)
        tx += w
    y -= 6 * mm
    c.setFont("Helvetica", 8)

    for r in rows:
        ts = datetime.datetime.fromisoformat(r["timestamp"]).strftime("%Y-%m-%d %H:%M")
        data = [
            ts,
            r["meal"] or "",
            r["pre_bgl"] or "",
            r["post_bgl"] or "",
            r["carbs"] or "",
            r["ratio"] or "",
            r["insulin_units"] or "",
            r["username"] or "",
            (r["notes"] or "")[:150],
        ]
        tx = x
        for d, w in zip(data, col_w):
            c.drawString(tx + 2, y, str(d))
            tx += w
        y -= 6 * mm
        if y < margin + 20 * mm:
            c.showPage()
            y = height - margin
            c.setFont("Helvetica", 8)

    c.save()
    buf.seek(0)
    return buf

# --- Conversation Handlers ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = (
        "👋 Hi! I'm your shared Diabetes Diary bot.\n\n"
        "📘 Use /entry to add a meal (BGL, carbs, ratio, insulin, notes).\n"
        "📊 Use /report 7 to generate a PDF for the last 7 days.\n"
        "❌ Use /cancel to stop an entry in progress.\n"
        "💡 All entries are shared in this private group."
    )
    await update.message.reply_text(msg)

async def entry_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "📝 Let's add a new entry!\n\n"
        "What meal is this? (e.g. breakfast, lunch, dinner)\n\n"
        "💡 Type /cancel anytime to stop."
    )
    return MEAL

async def meal_received(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not update.message.text:
        await update.message.reply_text("❌ Please send text only. Use /cancel to stop.")
        return MEAL
    context.user_data["meal"] = update.message.text.strip()
    await update.message.reply_text(
        "Enter pre-meal BGL (or '-' if not measured):\n\n"
        "💡 Type /cancel anytime to stop this entry."
    )
    return PRE_BGL

async def pre_bgl(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not update.message.text:
        await update.message.reply_text("❌ Please send text only. Use /cancel to stop.")
        return PRE_BGL
    t = update.message.text.strip()
    try:
        context.user_data["pre_bgl"] = None if t == "-" else float(t)
    except ValueError:
        await update.message.reply_text("❌ Invalid number. Please enter a number or '-'. Use /cancel to stop.")
        return PRE_BGL
    await update.message.reply_text("Enter post-meal BGL (or '-' if not measured):")
    return POST_BGL

async def post_bgl(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not update.message.text:
        await update.message.reply_text("❌ Please send text only. Use /cancel to stop.")
        return POST_BGL
    t = update.message.text.strip()
    try:
        context.user_data["post_bgl"] = None if t == "-" else float(t)
    except ValueError:
        await update.message.reply_text("❌ Invalid number. Please enter a number or '-'. Use /cancel to stop.")
        return POST_BGL
    await update.message.reply_text("Enter carbs (g):")
    return CARBS

async def carbs(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not update.message.text:
        await update.message.reply_text("❌ Please send text only. Use /cancel to stop.")
        return CARBS
    t = update.message.text.strip()
    try:
        context.user_data["carbs"] = None if t == "-" else float(t)
    except ValueError:
        await update.message.reply_text("❌ Invalid number. Please enter a number or '-'. Use /cancel to stop.")
        return CARBS
    await update.message.reply_text("Enter ratio (e.g. 1:10):")
    return RATIO

async def ratio(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not update.message.text:
        await update.message.reply_text("❌ Please send text only. Use /cancel to stop.")
        return RATIO
    context.user_data["ratio"] = update.message.text.strip()
    await update.message.reply_text("Enter insulin units given (or '-'): ")
    return UNITS

async def units(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not update.message.text:
        await update.message.reply_text("❌ Please send text only. Use /cancel to stop.")
        return UNITS
    t = update.message.text.strip()
    try:
        context.user_data["insulin_units"] = None if t == "-" else float(t)
    except ValueError:
        await update.message.reply_text("❌ Invalid number. Please enter a number or '-'. Use /cancel to stop.")
        return UNITS
    await update.message.reply_text("Any notes? (or '-' for none)")
    return NOTES

async def notes(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not update.message.text:
        await update.message.reply_text("❌ Please send text only. Use /cancel to stop.")
        return NOTES
    context.user_data["notes"] = None if update.message.text.strip() == "-" else update.message.text.strip()
    data = context.user_data
    summary = (
        f"✅ Confirm entry:\n\n"
        f"Meal: {data['meal']}\nPre BGL: {data['pre_bgl']}\nPost BGL: {data['post_bgl']}\n"
        f"Carbs: {data['carbs']}\nRatio: {data['ratio']}\nInsulin: {data['insulin_units']}\n"
        f"Notes: {data['notes']}\n\nType 'yes' to save or 'no' to cancel."
    )
    await update.message.reply_text(summary)
    return CONFIRM

async def confirm(update: Update, context: ContextTypes.DEFAULT_TYPE):
    txt = update.message.text.lower()
    if txt not in ("yes", "y"):
        await update.message.reply_text("❌ Entry cancelled.")
        return ConversationHandler.END

    chat = update.effective_chat
    user = update.effective_user
    data = context.user_data
    save_entry(
        chat.id,
        user.id,
        user.username or user.full_name,
        data["meal"],
        data["pre_bgl"],
        data["post_bgl"],
        data["carbs"],
        data["ratio"],
        data["insulin_units"],
        data["notes"],
    )
    await update.message.reply_text("✅ Saved!")
    context.user_data.clear()
    return ConversationHandler.END

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear()
    await update.message.reply_text(
        "❌ Entry cancelled.\n\n"
        "Use /entry to start a new entry."
    )
    return ConversationHandler.END

# --- Reports ---
def parse_days(arg):
    if not arg:
        return 7
    a = arg.lower()
    if a in ("week", "7", "lastweek"):
        return 7
    if a in ("today", "1"):
        return 1
    try:
        return int(a)
    except:
        return 7

async def report_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat = update.effective_chat
    args = context.args
    days = parse_days(args[0]) if args else 7
    end_date = datetime.date.today()
    start_date = end_date - datetime.timedelta(days=days - 1)
    rows = fetch_entries_for_chat(chat.id, start_date)
    if not rows:
        await update.message.reply_text(f"No entries for last {days} days.")
        return
    pdf = generate_pdf_buffer(chat.title or "Diary", rows, start_date, end_date)
    await update.message.reply_chat_action(action=tg_constants.ChatAction.UPLOAD_DOCUMENT)
    await update.message.reply_document(
        document=pdf,
        filename=f"diary_{start_date}_to_{end_date}.pdf",
        caption=f"📊 Report for {chat.title or 'Group'} ({days} days)",
    )

# --- Daily auto-report ---
async def send_daily_report(bot):
    if not DAILY_REPORT_CHAT_ID:
        return
    chat_id = int(DAILY_REPORT_CHAT_ID)
    today = datetime.date.today()
    rows = fetch_entries_for_chat(chat_id, today)
    if not rows:
        await bot.send_message(chat_id, f"No entries for {today.isoformat()}.")
        return
    pdf = generate_pdf_buffer("Daily Report", rows, today, today)
    await bot.send_document(chat_id, pdf, filename=f"daily_{today}.pdf", caption="📘 Daily Diabetes Report")

# --- Main ---
def main():
    init_db()
    app = ApplicationBuilder().token(BOT_TOKEN).build()

    conv = ConversationHandler(
        entry_points=[CommandHandler("entry", entry_command)],
        states={
            MEAL: [MessageHandler(filters.TEXT & ~filters.COMMAND, meal_received)],
            PRE_BGL: [MessageHandler(filters.TEXT & ~filters.COMMAND, pre_bgl)],
            POST_BGL: [MessageHandler(filters.TEXT & ~filters.COMMAND, post_bgl)],
            CARBS: [MessageHandler(filters.TEXT & ~filters.COMMAND, carbs)],
            RATIO: [MessageHandler(filters.TEXT & ~filters.COMMAND, ratio)],
            UNITS: [MessageHandler(filters.TEXT & ~filters.COMMAND, units)],
            NOTES: [MessageHandler(filters.TEXT & ~filters.COMMAND, notes)],
            CONFIRM: [MessageHandler(filters.TEXT & ~filters.COMMAND, confirm)],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
    )

    app.add_handler(CommandHandler("start", start))
    app.add_handler(conv)
    app.add_handler(CommandHandler("report", report_command))

    if DAILY_REPORT_TIME and DAILY_REPORT_CHAT_ID:
        hh, mm = [int(x) for x in DAILY_REPORT_TIME.split(":")]
        scheduler = AsyncIOScheduler(timezone=TIMEZONE)
        scheduler.add_job(lambda: app.create_task(send_daily_report(app.bot)), "cron", hour=hh, minute=mm)
        scheduler.start()

    logger.info("Bot started.")
    app.run_polling()

if __name__ == "__main__":
    main()
