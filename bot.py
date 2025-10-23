# bot.py
import os
import logging
import random
from flask import Flask, request, Response
from sqlalchemy import create_engine, Column, Integer, String, UniqueConstraint
from sqlalchemy.orm import sessionmaker, declarative_base
from telegram import Bot, Update, ReplyKeyboardMarkup, KeyboardButton
from telegram.ext import Dispatcher, CommandHandler, MessageHandler, Filters, ConversationHandler

# --- SETTINGS ---
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")
DATABASE_URL = os.environ.get("DATABASE_URL")  # ex: postgres://... or leave None to use sqlite
NUM_TEAMS = int(os.environ.get("NUM_TEAMS", "10"))  # 10-12 as you requested
BOT_BASE_URL = os.environ.get("BOT_BASE_URL")  # optional, e.g. https://your-app.onrender.com

if not TELEGRAM_TOKEN:
    raise RuntimeError("Set TELEGRAM_TOKEN environment variable")

# --- Logging ---
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# --- DB setup ---
Base = declarative_base()

class Player(Base):
    __tablename__ = "players"
    id = Column(Integer, primary_key=True)
    tg_id = Column(Integer, nullable=False, unique=True)  # telegram user id
    full_name = Column(String, nullable=False)
    team = Column(Integer, nullable=False)

# Choose DB engine
if DATABASE_URL:
    engine = create_engine(DATABASE_URL, echo=False, connect_args={} )
else:
    engine = create_engine("sqlite:///teams.db", echo=False, connect_args={"check_same_thread": False})

Base.metadata.create_all(engine)
Session = sessionmaker(bind=engine)

# --- Telegram/Flask setup ---
app = Flask(__name__)
bot = Bot(token=TELEGRAM_TOKEN)
dispatcher = Dispatcher(bot, None, workers=0, use_context=True)

# Conversation states
ASK_NAME = 1

# Helper functions
def get_team_counts(session):
    # returns dict team_num -> count
    counts = {i: 0 for i in range(1, NUM_TEAMS+1)}
    rows = session.query(Player.team).all()
    for (t,) in rows:
        if t in counts:
            counts[t] += 1
    return counts

def assign_team_random_balanced(session):
    counts = get_team_counts(session)
    min_count = min(counts.values())
    candidates = [team for team, c in counts.items() if c == min_count]
    return random.choice(candidates)

def format_team_list(session):
    teams = {i: [] for i in range(1, NUM_TEAMS+1)}
    for p in session.query(Player).order_by(Player.team, Player.full_name).all():
        teams[p.team].append(p.full_name)
    s = []
    for i in range(1, NUM_TEAMS+1):
        s.append(f"Команда {i} ({len(teams[i])}):")
        if teams[i]:
            for name in teams[i]:
                s.append(f"  - {name}")
        else:
            s.append("  (пусто)")
    return "\n".join(s)

# Handlers
def start(update, context):
    tg_user = update.effective_user
    session = Session()
    p = session.query(Player).filter_by(tg_id=tg_user.id).first()
    if p:
        # already registered -> show team
        keyboard = [[KeyboardButton("Моя команда")]]
        reply = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
        update.message.reply_text(
            f"Вы уже в Команде {p.team}.\nВаше ФИО: {p.full_name}",
            reply_markup=reply
        )
    else:
        # ask for FIO
        keyboard = [[KeyboardButton("Моя команда")]]
        reply = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
        update.message.reply_text("Привет! Введите, пожалуйста, своё полное ФИО (Фамилия Имя Отчество):", reply_markup=reply)
        return ASK_NAME
    return ConversationHandler.END

def ask_name_handler(update, context):
    tg_user = update.effective_user
    text = update.message.text.strip()
    # Basic validation
    if len(text.split()) < 2:
        update.message.reply_text("Пожалуйста, введите ФИО полностью (минимум фамилия и имя).")
        return ASK_NAME
    session = Session()
    existing = session.query(Player).filter_by(tg_id=tg_user.id).first()
    if existing:
        update.message.reply_text(f"Вы уже зарегистрированы в Команде {existing.team} (ФИО: {existing.full_name}).")
        return ConversationHandler.END

    team_num = assign_team_random_balanced(session)
    player = Player(tg_id=tg_user.id, full_name=text, team=team_num)
    session.add(player)
    session.commit()

    update.message.reply_text(f"Готово — вы распределены в Команду {team_num}.\nФИО: {text}")
    return ConversationHandler.END

def my_team_button(update, context):
    tg_user = update.effective_user
    session = Session()
    p = session.query(Player).filter_by(tg_id=tg_user.id).first()
    if p:
        # show team and roster
        teammates = session.query(Player).filter_by(team=p.team).order_by(Player.full_name).all()
        lines = [f"Ваша команда: {p.team}", f"Ваше ФИО: {p.full_name}", "", "Состав команды:"]
        for t in teammates:
            lines.append(f" - {t.full_name}")
        update.message.reply_text("\n".join(lines))
    else:
        update.message.reply_text("Вы ещё не зарегистрированы. Отправьте /start чтобы зарегистрироваться и ввести ФИО.")

def teams_command(update, context):
    session = Session()
    text = format_team_list(session)
    update.message.reply_text(text)

def cancel(update, context):
    update.message.reply_text("Отменено.")
    return ConversationHandler.END

# Dispatcher setup
conv_handler = ConversationHandler(
    entry_points=[CommandHandler('start', start)],
    states={
        ASK_NAME: [MessageHandler(Filters.text & ~Filters.command, ask_name_handler)]
    },
    fallbacks=[CommandHandler('cancel', cancel)],
    allow_reentry=True
)

dispatcher.add_handler(conv_handler)
dispatcher.add_handler(CommandHandler('teams', teams_command))
dispatcher.add_handler(MessageHandler(Filters.regex('^Моя команда$'), my_team_button))

# Flask route for Telegram webhook
@app.route(f"/webhook/{TELEGRAM_TOKEN}", methods=["POST"])
def webhook():
    update = Update.de_json(request.get_json(force=True), bot)
    dispatcher.process_update(update)
    return Response("ok", status=200)

@app.route("/")
def index():
    return "Bot is running"

# Utility: set webhook (run manually once)
def set_webhook():
    if not BOT_BASE_URL:
        print("Set BOT_BASE_URL environment variable to set webhook automatically.")
        return
    url = f"{BOT_BASE_URL}/webhook/{TELEGRAM_TOKEN}"
    ok = bot.set_webhook(url)
    print("set_webhook:", ok)

if __name__ == "__main__":
    # If running locally for dev, you can set webhook manually or run a polling loop.
    # For production deployment (Render/Railway) we rely on webhook route and external set_webhook step.
    set_webhook()
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))
