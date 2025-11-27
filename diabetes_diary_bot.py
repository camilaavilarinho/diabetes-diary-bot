import os
import sqlite3
from datetime import datetime, date, timedelta
from dotenv import load_dotenv
import os

from telegram import Update
from telegram.ext import (
    ApplicationBuilder, CommandHandler, ContextTypes
)

# Load environment variables from .env file
load_dotenv()
BOT_TOKEN = os.environ.get("BOT_TOKEN")
if not BOT_TOKEN:
    raise RuntimeError("Set BOT_TOKEN env var")

# =========================
#  DATABASE
# =========================

DB_PATH = os.environ.get("DB_PATH", "/data/diary.db")

def init_db():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()

    c.execute("""
    CREATE TABLE IF NOT EXISTS entries (
        id INTEGER PRIMARY KEY,
        chat_id INTEGER,
        entry_date TEXT,
        meal TEXT,
        field TEXT,
        value TEXT,
        created_at TEXT
    )
    """)

    c.execute("""
    CREATE TABLE IF NOT EXISTS notes (
        id INTEGER PRIMARY KEY,
        chat_id INTEGER,
        entry_date TEXT,
        note TEXT,
        created_at TEXT
    )
    """)

    conn.commit()
    conn.close()

def save_entry(chat_id, entry_date, meal, field, value):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("""
      INSERT INTO entries (chat_id, entry_date, meal, field, value, created_at)
      VALUES (?, ?, ?, ?, ?, ?)
    """, (chat_id, entry_date, meal, field, value, datetime.now().isoformat()))
    conn.commit()
    conn.close()

def save_note(chat_id, entry_date, text):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("""
      INSERT INTO notes (chat_id, entry_date, note, created_at)
      VALUES (?, ?, ?, ?)
    """, (chat_id, entry_date, text, datetime.now().isoformat()))
    conn.commit()
    conn.close()

def get_entries(chat_id, start_date, end_date):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()

    c.execute("""
      SELECT entry_date, meal, field, value
      FROM entries
      WHERE chat_id = ?
      AND entry_date BETWEEN ? AND ?
      ORDER BY entry_date
    """, (chat_id, start_date, end_date))

    rows = c.fetchall()
    conn.close()
    return rows

def get_notes(chat_id, start_date, end_date):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()

    c.execute("""
      SELECT entry_date, note, created_at
      FROM notes
      WHERE chat_id = ?
      AND entry_date BETWEEN ? AND ?
      ORDER BY created_at
    """, (chat_id, start_date, end_date))

    rows = c.fetchall()
    conn.close()
    return rows


# =========================
#  PDF GENERATOR
# =========================

from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.lib import colors

def generate_pdf(chat_id, start_date, end_date, file_path):
    entries = get_entries(chat_id, start_date, end_date)
    notes = get_notes(chat_id, start_date, end_date)

    # Organize by date → meals → fields
    data = {}

    for d, meal, field, value in entries:
        data.setdefault(d, {})
        data[d].setdefault(meal, {})
        data[d][meal][field] = value

    for d, note, created_at in notes:
        data.setdefault(d, {})
        data[d].setdefault("notes", [])
        # Format timestamp to show only time (HH:MM)
        try:
            timestamp = datetime.fromisoformat(created_at).strftime("%H:%M")
            note_with_time = f"[{timestamp}] {note}"
        except:
            note_with_time = note
        data[d]["notes"].append(note_with_time)

    styles = getSampleStyleSheet()

    # Set up PDF with minimal margins
    pdf = SimpleDocTemplate(
        file_path,
        pagesize=A4,
        leftMargin=10,
        rightMargin=10,
        topMargin=20,
        bottomMargin=20
    )
    elements = []

    # Build header row
    header = [
        Paragraph("<b>Date</b>", styles['Normal']),
        Paragraph("<b>Breakfast</b>", styles['Normal']),
        Paragraph("<b>Lunch</b>", styles['Normal']),
        Paragraph("<b>Dinner</b>", styles['Normal']),
        Paragraph("<b>Basal</b>", styles['Normal']),
        Paragraph("<b>Notes</b>", styles['Normal'])
    ]

    table_data = [header]

    MEALS = ["breakfast", "lunch", "dinner"]

    for day in sorted(data.keys()):
        # Convert date from YYYY-MM-DD to DD-MM-YYYY format
        try:
            date_obj = datetime.strptime(day, "%Y-%m-%d")
            formatted_date = date_obj.strftime("%d-%m-%Y")
        except:
            formatted_date = day

        row = [Paragraph(formatted_date, styles['Normal'])]

        for meal in MEALS:
            m = data[day].get(meal, {})
            cell_text = (
                f"Before: {m.get('before','-')}<br/>"
                f"After: {m.get('after','-')}<br/>"
                f"Carbs: {m.get('carbs','-')}<br/>"
                f"Ratio: {m.get('ratio','-')}<br/>"
                f"Insulin: {m.get('insulin','-')}"
            )
            row.append(Paragraph(cell_text, styles['Normal']))

        # Add basal insulin column
        basal_data = data[day].get("basal", {})
        basal_text = (
            f"AM: {basal_data.get('am', '-')}<br/>"
            f"PM: {basal_data.get('pm', '-')}"
        )
        row.append(Paragraph(basal_text, styles['Normal']))

        notes_txt = "<br/>".join(data[day].get("notes", [])) or "-"
        row.append(Paragraph(notes_txt, styles['Normal']))

        table_data.append(row)

    # Define column widths: Date, 3 meals, Basal, Notes
    # A4 width = 595 points, with 20 total margin = 575 points available
    col_widths = [45, 80, 80, 80, 50, 240]  

    table = Table(table_data, colWidths=col_widths, repeatRows=1)
    table.setStyle(TableStyle([
        ('BACKGROUND', (0,0), (-1,0), colors.lightblue),
        ('GRID', (0,0), (-1,-1), 0.5, colors.grey),
        ('VALIGN', (0,0), (-1,-1), 'TOP'),
        ('ALIGN', (0,0), (0,-1), 'CENTER'),
        ('FONTSIZE', (0,0), (-1,-1), 8),
        ('WORDWRAP', (0,0), (-1,-1), True),
    ]))

    elements.append(table)
    pdf.build(elements)


# =========================
#  COMMAND HELPERS
# =========================

MEALS = ["breakfast", "lunch", "dinner"]

def parse_meal(value):
    v = value.lower().strip()
    if v not in MEALS:
        raise ValueError("Meal must be one of: breakfast, lunch, dinner")
    return v

def today_str():
    return date.today().isoformat()


# =========================
#  COMMAND HANDLERS
# =========================

async def before(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if len(ctx.args) < 2:
        return await update.message.reply_text("Usage: /before <meal> <value>")

    meal = parse_meal(ctx.args[0])
    value = ctx.args[1]

    save_entry(update.effective_chat.id, today_str(), meal, "before", value)
    await update.message.reply_text(f"Saved: BEFORE {meal} = {value}")

async def after(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if len(ctx.args) < 2:
        return await update.message.reply_text("Usage: /after <meal> <value>")

    meal = parse_meal(ctx.args[0])
    value = ctx.args[1]

    save_entry(update.effective_chat.id, today_str(), meal, "after", value)
    await update.message.reply_text(f"Saved: AFTER {meal} = {value}")

async def carbs(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if len(ctx.args) < 2:
        return await update.message.reply_text("Usage: /carbs <meal> <grams>")
    meal = parse_meal(ctx.args[0])
    grams = ctx.args[1]

    save_entry(update.effective_chat.id, today_str(), meal, "carbs", grams)
    await update.message.reply_text(f"Saved: Carbs for {meal}: {grams}g")

async def ratio(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if len(ctx.args) < 2:
        return await update.message.reply_text("Usage: /ratio <meal> <1:xx>")
    meal = parse_meal(ctx.args[0])
    ratio = ctx.args[1]

    save_entry(update.effective_chat.id, today_str(), meal, "ratio", ratio)
    await update.message.reply_text(f"Saved: Ratio for {meal}: {ratio}")

async def insulin(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if len(ctx.args) < 2:
        return await update.message.reply_text("Usage: /insulin <meal> <units>")
    meal = parse_meal(ctx.args[0])
    units = ctx.args[1]

    save_entry(update.effective_chat.id, today_str(), meal, "insulin", units)
    await update.message.reply_text(f"Saved: Insulin for {meal}: {units}")

async def note(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    text = " ".join(ctx.args)
    if not text:
        return await update.message.reply_text("Usage: /note <text>")

    save_note(update.effective_chat.id, today_str(), text)
    await update.message.reply_text("Note added.")

async def basal(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if len(ctx.args) < 2:
        return await update.message.reply_text("Usage: /basal <am|pm> <units>")

    period = ctx.args[0].lower()
    units = ctx.args[1]

    if period not in ["am", "pm"]:
        return await update.message.reply_text("Period must be 'am' or 'pm'")

    save_entry(update.effective_chat.id, today_str(), "basal", period, units)
    await update.message.reply_text(f"Saved: Basal {period.upper()} = {units} units")

async def report(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if len(ctx.args) == 0:
        return await update.message.reply_text("Usage: /report <today | days>")

    chat_id = update.effective_chat.id

    arg = ctx.args[0]
    if arg == "today":
        start = end = today_str()
    else:
        days = int(arg)
        end = today_str()
        start = (date.today() - timedelta(days=days-1)).isoformat()

    # Generate timestamp for unique filename
    timestamp = datetime.now().strftime("%H%M%S")

    # Create a user-friendly filename
    display_filename = f"diabetes-diary-{end.replace('-', '')}-{timestamp}.pdf"
    temp_file_path = f"report_{chat_id}_{start}_{end}.pdf"

    generate_pdf(chat_id, start, end, temp_file_path)

    # Send with proper filename
    with open(temp_file_path, 'rb') as pdf_file:
        await update.message.reply_document(
            document=pdf_file,
            filename=display_filename
        )

    # Clean up temporary file
    os.remove(temp_file_path)


# =========================
#  BOT LAUNCH
# =========================

def main():
    print("Starting bot...")
    init_db()
    print("db initialized")
    token = os.getenv("BOT_TOKEN")
    print("token loaded")

    app = ApplicationBuilder().token(token).build()
    print("app built")

    app.add_handler(CommandHandler("before", before))
    app.add_handler(CommandHandler("after", after))
    app.add_handler(CommandHandler("carbs", carbs))
    app.add_handler(CommandHandler("ratio", ratio))
    app.add_handler(CommandHandler("insulin", insulin))
    app.add_handler(CommandHandler("basal", basal))
    app.add_handler(CommandHandler("note", note))
    app.add_handler(CommandHandler("report", report))

    app.run_polling()

if __name__ == "__main__":
    main()
