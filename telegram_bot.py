# -*- coding: utf-8 -*-
from telebot import TeleBot, types
import json, os, time, sqlite3
from typing import Dict, Any, List

# ===== Конфиг =====
try:
    from config import TOKEN
except Exception:
    TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "ВАШ_ТОКЕН_ОТ_BOTFATHER")

# ===== Оплата (эмуляция только) =====
PAYMENT_MODE = "EMULATED_ONLY"  # учебный проект, реальные платежи запрещены


class MockPaymentProvider:
    @staticmethod
    def charge(order_id: str, amount: int, outcome: str = "ok"):
        assert PAYMENT_MODE == "EMULATED_ONLY"
        if outcome == "ok":
            return {
                "status": "Succeeded",
                "provider": "MockPay",
                "order_id": order_id,
                "amount": amount,
            }
        return {
            "status": "Failed",
            "provider": "MockPay",
            "order_id": order_id,
            "amount": amount,
        }


DATA_DIR = os.path.join(os.path.dirname(__file__), "data")
DB_PATH = os.path.join(DATA_DIR, "app.db")  # теперь SQLite-файл, раньше был db.json
STORES_PATH = os.path.join(DATA_DIR, "stores.json")
MENU_PATH = os.path.join(DATA_DIR, "menu.json")

os.makedirs(DATA_DIR, exist_ok=True)


# ===== БД на SQLite вместо JSON =====
class DB:
    def __init__(self, path: str):
        self.path = path
        self._init_db()

    def _connect(self):
        return sqlite3.connect(self.path)

    def _init_db(self):
        conn = self._connect()
        cur = conn.cursor()

        # таблица пользователей
        cur.execute(
            """
        CREATE TABLE IF NOT EXISTS users (
            id TEXT PRIMARY KEY,
            username TEXT,
            first_name TEXT,
            real_name TEXT,
            address TEXT,
            age INTEGER
        )
        """
        )

        # корзина (позиции)
        cur.execute(
            """
        CREATE TABLE IF NOT EXISTS cart_items (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id TEXT NOT NULL,
            item_id TEXT,
            item_name TEXT,
            store_id TEXT,
            size TEXT,
            qty INTEGER,
            price INTEGER
        )
        """
        )

        # заказы
        cur.execute(
            """
        CREATE TABLE IF NOT EXISTS orders (
            id TEXT PRIMARY KEY,
            user_id TEXT NOT NULL,
            store_id TEXT NOT NULL,
            total INTEGER NOT NULL,
            status TEXT NOT NULL,
            created_at INTEGER NOT NULL
        )
        """
        )

        # позиции заказа
        cur.execute(
            """
        CREATE TABLE IF NOT EXISTS order_items (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            order_id TEXT NOT NULL,
            item_id TEXT,
            item_name TEXT,
            size TEXT,
            qty INTEGER,
            price INTEGER
        )
        """
        )

        conn.commit()
        conn.close()

    # --- Users ---
    def get_user(self, uid: str) -> Dict[str, Any]:
        conn = self._connect()
        cur = conn.cursor()
        cur.execute(
            "SELECT id, username, first_name, real_name, address, age FROM users WHERE id = ?",
            (uid,),
        )
        row = cur.fetchone()
        conn.close()
        if not row:
            return {}
        return {
            "id": row[0],
            "username": row[1],
            "first_name": row[2],
            "real_name": row[3],
            "address": row[4],
            "age": row[5],
        }

    def upsert_user(self, uid: str, **fields):
        # читаем текущего пользователя
        current = self.get_user(uid)
        # обновляем словарь новыми полями
        current.update(fields)
        current["id"] = uid

        conn = self._connect()
        cur = conn.cursor()
        cur.execute(
            """
            INSERT INTO users (id, username, first_name, real_name, address, age)
            VALUES (:id, :username, :first_name, :real_name, :address, :age)
            ON CONFLICT(id) DO UPDATE SET
                username=excluded.username,
                first_name=excluded.first_name,
                real_name=excluded.real_name,
                address=excluded.address,
                age=excluded.age
            """,
            {
                "id": uid,
                "username": current.get("username"),
                "first_name": current.get("first_name"),
                "real_name": current.get("real_name"),
                "address": current.get("address"),
                "age": current.get("age"),
            },
        )
        conn.commit()
        conn.close()

    # --- Cart ---
    def get_cart(self, uid: str) -> List[Dict[str, Any]]:
        conn = self._connect()
        cur = conn.cursor()
        cur.execute(
            "SELECT item_id, item_name, store_id, size, qty, price FROM cart_items WHERE user_id = ?",
            (uid,),
        )
        rows = cur.fetchall()
        conn.close()
        cart = []
        for item_id, item_name, store_id, size, qty, price in rows:
            cart.append(
                {
                    "item_id": item_id,
                    "item_name": item_name,
                    "store_id": store_id,
                    "size": size,
                    "qty": qty,
                    "price": price,
                }
            )
        return cart

    def set_cart(self, uid: str, items: List[Dict[str, Any]]):
        conn = self._connect()
        cur = conn.cursor()
        # очищаем корзину пользователя и записываем заново
        cur.execute("DELETE FROM cart_items WHERE user_id = ?", (uid,))
        for it in items:
            cur.execute(
                """
                INSERT INTO cart_items (user_id, item_id, item_name, store_id, size, qty, price)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    uid,
                    it.get("item_id"),
                    it.get("item_name"),
                    it.get("store_id"),
                    it.get("size"),
                    it.get("qty"),
                    it.get("price"),
                ),
            )
        conn.commit()
        conn.close()

    def clear_cart(self, uid: str):
        conn = self._connect()
        cur = conn.cursor()
        cur.execute("DELETE FROM cart_items WHERE user_id = ?", (uid,))
        conn.commit()
        conn.close()

    # --- Orders ---
    def create_order(
        self, uid: str, store_id: str, items: List[Dict[str, Any]], total: int
    ) -> str:
        order_id = str(int(time.time()))
        created_at = int(time.time())
        conn = self._connect()
        cur = conn.cursor()

        # создаём заказ
        cur.execute(
            """
            INSERT INTO orders (id, user_id, store_id, total, status, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (order_id, uid, store_id, total, "Pending", created_at),
        )

        # сохраняем позиции заказа
        for it in items:
            cur.execute(
                """
                INSERT INTO order_items (order_id, item_id, item_name, size, qty, price)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    order_id,
                    it.get("item_id"),
                    it.get("item_name"),
                    it.get("size"),
                    it.get("qty"),
                    it.get("price"),
                ),
            )

        conn.commit()
        conn.close()
        return order_id

    def get_order(self, order_id: str) -> Dict[str, Any]:
        conn = self._connect()
        cur = conn.cursor()
        cur.execute(
            "SELECT id, user_id, store_id, total, status, created_at FROM orders WHERE id = ?",
            (order_id,),
        )
        row = cur.fetchone()
        if not row:
            conn.close()
            return {}
        order = {
            "id": row[0],
            "user_id": row[1],
            "store_id": row[2],
            "total": row[3],
            "status": row[4],
            "created_at": row[5],
        }
        # подтянем позиции заказа
        cur.execute(
            "SELECT item_id, item_name, size, qty, price FROM order_items WHERE order_id = ?",
            (order_id,),
        )
        items_rows = cur.fetchall()
        conn.close()
        items = []
        for item_id, item_name, size, qty, price in items_rows:
            items.append(
                {
                    "item_id": item_id,
                    "item_name": item_name,
                    "size": size,
                    "qty": qty,
                    "price": price,
                }
            )
        order["items"] = items
        return order

    def get_last_order_of(self, uid: str) -> Dict[str, Any]:
        conn = self._connect()
        cur = conn.cursor()
        cur.execute(
            "SELECT id, user_id, store_id, total, status, created_at "
            "FROM orders WHERE user_id = ? ORDER BY created_at DESC LIMIT 1",
            (uid,),
        )
        row = cur.fetchone()
        conn.close()
        if not row:
            return {}
        return {
            "id": row[0],
            "user_id": row[1],
            "store_id": row[2],
            "total": row[3],
            "status": row[4],
            "created_at": row[5],
        }

    def set_order_status(self, order_id: str, status: str):
        conn = self._connect()
        cur = conn.cursor()
        cur.execute("UPDATE orders SET status = ? WHERE id = ?", (status, order_id))
        conn.commit()
        conn.close()


# ===== Утилиты =====
def load_json(path):
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def find_store(store_id: str, stores: list):
    return next((s for s in stores if s["id"] == store_id), None)


def format_rub(v: int) -> str:
    return f"{v} ₽"


# ===== Логика из заданий 2 и 3 (условия + функции) =====
def validate_name(name: str) -> str:
    """
    Проверка имени:
    - если пустое -> просим ввести;
    - если есть цифры -> ошибка;
    - если есть спецсимволы (кроме пробела и дефиса) -> ошибка;
    - иначе — имя считается корректным.
    """
    name = name.strip()
    if not name:
        return "Имя не может быть пустым. Введите хотя бы один символ."

    if any(ch.isdigit() for ch in name):
        return "В имени не должны быть цифры. Попробуйте ещё раз."

    if any(not (ch.isalpha() or ch in "- ") for ch in name):
        return "В имени обнаружены спецсимволы. Допустимы только буквы, пробел и дефис."

    return "ok"  # признак, что всё хорошо


def check_age(age: int) -> str:
    """
    Проверка возраста по примеру из задания:
    - младше 18: закрыть доступ;
    - больше 100: отправить на сайт пенсионного фонда;
    - иначе — продолжить регистрацию.
    """
    if age < 18:
        return "Вам меньше 18 лет — доступ к взрослым разделам приложения закрыт."
    elif age > 100:
        return "Вам больше 100 лет — перенаправляем на сайт пенсионного фонда 🙂"
    else:
        return "Возраст подходит, можно продолжать регистрацию и пользоваться приложением."


# ===== Инициализация =====
db = DB(DB_PATH)
STORES = load_json(STORES_PATH)
MENU = load_json(MENU_PATH)
bot = TeleBot(TOKEN)


# ===== Команды =====
@bot.message_handler(commands=["start", "help"])
def cmd_start(m):
    text = (
        "Привет! Я PizzaFlow бот 🍕\n\n"
        "Команды:\n"
        "/register — регистрация\n"
        "/name <имя> — проверить и сохранить имя\n"
        "/age <число> — проверить возраст\n"
        "/address — задать адрес (строкой)\n"
        "/stores — список пиццерий\n"
        "/menu <store_id> — меню пиццерии\n"
        "/add <item_id> <size> <qty> — добавить одну позицию в корзину\n"
        "/add_batch <список> — добавить сразу несколько позиций\n"
        "/cart — показать корзину\n"
        "/confirm <store_id> — оформить заказ\n"
        "/pay — оплата (эмуляция) | /pay fail — отказ\n"
        "/status — статус последнего заказа\n"
        "/cancel — очистить корзину"
    )
    bot.reply_to(m, text)


@bot.message_handler(commands=["register"])
def cmd_register(m):
    uid = str(m.from_user.id)
    db.upsert_user(
        uid,
        username=m.from_user.username or "",
        first_name=m.from_user.first_name or "",
    )
    bot.reply_to(m, "✅ Регистрация выполнена. Введите адрес командой /address")


@bot.message_handler(commands=["name"])
def cmd_name(m):
    """
    /name Иван-Петров
    Проверяем имя на цифры и спецсимволы.
    """
    parts = m.text.split(" ", 1)
    if len(parts) < 2:
        bot.reply_to(m, "Использование: /name <имя>\nНапример: /name Иван")
        return

    name = parts[1]
    result = validate_name(name)

    if result == "ok":
        uid = str(m.from_user.id)
        db.upsert_user(uid, real_name=name.strip())
        bot.reply_to(m, f"✅ Имя «{name.strip()}» принято. Продолжайте регистрацию.")
    else:
        bot.reply_to(m, f"❌ {result}")


@bot.message_handler(commands=["age"])
def cmd_age(m):
    """
    /age 25
    Проверяем возраст пользователя по правилам из задания.
    """
    parts = m.text.split(" ", 1)
    if len(parts) < 2:
        bot.reply_to(m, "Использование: /age <возраст>\nНапример: /age 25")
        return

    try:
        age = int(parts[1])
    except ValueError:
        bot.reply_to(m, "Возраст должен быть целым числом. Попробуйте ещё раз.")
        return

    if age <= 0:
        bot.reply_to(m, "Возраст должен быть положительным числом.")
        return

    message = check_age(age)  # внутри функции — if / elif / else
    uid = str(m.from_user.id)
    db.upsert_user(uid, age=age)
    bot.reply_to(m, message)


@bot.message_handler(commands=["address"])
def cmd_address(m):
    uid = str(m.from_user.id)
    rest = m.text.split(" ", 1)
    if len(rest) < 2 or not rest[1].strip():
        bot.reply_to(m, "Отправьте адрес вот так:\n/address Город, Улица, Дом")
        return
    db.upsert_user(uid, address=rest[1].strip())
    bot.reply_to(m, f"📍 Адрес сохранён: {rest[1].strip()}")


@bot.message_handler(commands=["stores"])
def cmd_stores(m):
    uid = str(m.from_user.id)
    user = db.get_user(uid)
    city = (
        user.get("address", "").split(",")[0].strip()
        if user.get("address")
        else None
    )
    lines = []
    for s in STORES:
        if city and s["city"] != city:
            continue
        lines.append(f"- {s['name']} [{s['id']}] — {s['city']}, {s['address']}")
    if not lines:
        lines = [
            f"- {s['name']} [{s['id']}] — {s['city']}, {s['address']}"
            for s in STORES
        ]
        bot.reply_to(m, "По адресу город не распознан, покажу все пиццерии:\n" + "\n".join(lines))
    else:
        bot.reply_to(m, "Доступные пиццерии:\n" + "\n".join(lines))


@bot.message_handler(commands=["menu"])
def cmd_menu(m):
    parts = m.text.split()
    if len(parts) < 2:
        bot.reply_to(
            m,
            "Использование: /menu <store_id>\nНапример: /menu msk-1",
        )
        return
    store_id = parts[1]
    store = find_store(store_id, STORES)
    if not store:
        bot.reply_to(m, "Пиццерия не найдена.")
        return
    items = [x for x in MENU if x["store_id"] == store_id]
    if not items:
        bot.reply_to(m, "Меню пусто.")
        return
    lines = [
        f"{i['name']} — {i['id']} | цены: "
        + ", ".join([f"{sz}:{price}₽" for sz, price in i["sizes"].items()])
        for i in items
    ]
    bot.reply_to(
        m,
        f"Меню {store['name']}:\n"
        + "\n".join(lines)
        + "\n\nДобавьте позицию: /add <item_id> <size> <qty> или /add_batch ...",
    )


@bot.message_handler(commands=["add"])
def cmd_add(m):
    parts = m.text.split()
    if len(parts) != 4:
        bot.reply_to(
            m,
            "Использование: /add <item_id> <size> <qty>\nНапример: /add pepperoni M 2",
        )
        return
    item_id, size, qty_s = parts[1], parts[2].upper(), parts[3]
    try:
        qty = int(qty_s)
        if qty <= 0:
            raise ValueError
    except Exception:
        bot.reply_to(m, "Количество должно быть положительным числом.")
        return

    candidate = next(
        (i for i in MENU if i["id"] == item_id and size in i["sizes"]), None
    )
    if not candidate:
        bot.reply_to(m, "Такого товара/размера нет в меню.")
        return

    uid = str(m.from_user.id)
    cart = db.get_cart(uid)
    price = int(candidate["sizes"][size])
    cart.append(
        {
            "item_id": item_id,
            "item_name": candidate["name"],
            "store_id": candidate["store_id"],
            "size": size,
            "qty": qty,
            "price": price,
        }
    )
    db.set_cart(uid, cart)
    bot.reply_to(
        m,
        f"✅ Добавлено: {candidate['name']} {size} x{qty} — {price * qty} ₽",
    )


@bot.message_handler(commands=["add_batch"])
def cmd_add_batch(m):
    """
    /add_batch pepperoni M 2, margherita L 1
    Добавляет сразу несколько позиций в корзину.
    Формат: /add_batch <item_id> <size> <qty>, <item_id> <size> <qty>, ...
    Пример: /add_batch pepperoni M 2, margherita L 1
    """
    uid = str(m.from_user.id)

    parts = m.text.split(" ", 1)
    if len(parts) < 2 or not parts[1].strip():
        bot.reply_to(
            m,
            "Использование:\n"
            "/add_batch pepperoni M 2, margherita L 1\n"
            "где через запятую перечислены позиции: <item_id> <size> <qty>.",
        )
        return

    raw_items = parts[1].split(",")  # список строк по запятым
    cart = db.get_cart(uid)

    added_lines: List[str] = []
    error_lines: List[str] = []

    # === ЦИКЛ по позициям (пример использования for) ===
    for raw in raw_items:
        chunk = raw.strip()
        if not chunk:
            continue

        pieces = chunk.split()
        if len(pieces) != 3:
            error_lines.append(f"«{chunk}» — ожидалось: <item_id> <size> <qty>")
            continue

        item_id, size, qty_s = pieces[0], pieces[1].upper(), pieces[2]

        # проверяем количество
        try:
            qty = int(qty_s)
            if qty <= 0:
                raise ValueError
        except ValueError:
            error_lines.append(
                f"«{chunk}» — количество должно быть положительным числом."
            )
            continue

        # ищем товар в меню
        candidate = next(
            (i for i in MENU if i["id"] == item_id and size in i["sizes"]),
            None,
        )
        if not candidate:
            error_lines.append(
                f"«{chunk}» — такого товара/размера нет в меню."
            )
            continue

        price = int(candidate["sizes"][size])
        cart.append(
            {
                "item_id": item_id,
                "item_name": candidate["name"],
                "store_id": candidate["store_id"],
                "size": size,
                "qty": qty,
                "price": price,
            }
        )
        added_lines.append(
            f"{candidate['name']} {size} x{qty} — {price * qty} ₽"
        )

    if added_lines:
        db.set_cart(uid, cart)

    if not added_lines and not error_lines:
        bot.reply_to(
            m,
            "Не удалось распознать ни одной позиции. "
            "Проверьте формат команды /add_batch.",
        )
        return

    reply_parts: List[str] = []
    if added_lines:
        reply_parts.append("✅ Добавлены позиции:\n- " + "\n- ".join(added_lines))
    if error_lines:
        reply_parts.append("\n⚠ Ошибки:\n- " + "\n- ".join(error_lines))

    bot.reply_to(m, "\n".join(reply_parts))


@bot.message_handler(commands=["cart"])
def cmd_cart(m):
    uid = str(m.from_user.id)
    cart = db.get_cart(uid)
    if not cart:
        bot.reply_to(
            m,
            "Корзина пуста. Добавьте позиции командой /add или /add_batch",
        )
        return
    total = sum(p["price"] * p["qty"] for p in cart)
    lines = [
        f"- {p['item_name']} {p['size']} x{p['qty']} — {p['price'] * p['qty']} ₽ (store:{p['store_id']})"
        for p in cart
    ]
    bot.reply_to(
        m,
        "🧺 Корзина:\n" + "\n".join(lines) + f"\nИтого: {total} ₽",
    )


@bot.message_handler(commands=["confirm"])
def cmd_confirm(m):
    parts = m.text.split()
    if len(parts) < 2:
        bot.reply_to(
            m,
            "Укажите магазин: /confirm <store_id>\nПример: /confirm msk-1",
        )
        return
    store_id = parts[1]
    uid = str(m.from_user.id)
    cart = db.get_cart(uid)
    if not cart:
        bot.reply_to(m, "Корзина пуста.")
        return
    if any(p["store_id"] != store_id for p in cart):
        bot.reply_to(
            m,
            "Все позиции в заказе должны быть из одной пиццерии. "
            "Очистите корзину или добавьте позиции из одного магазина.",
        )
        return
    total = sum(p["price"] * p["qty"] for p in cart)
    order_id = db.create_order(uid, store_id, cart, total)
    db.clear_cart(uid)
    bot.reply_to(
        m,
        f"🧾 Заказ создан #{order_id}. Сумма: {total} ₽\n"
        f"Перейдите к оплате: /pay (или /pay fail — отказ)",
    )


@bot.message_handler(commands=["pay"])
def cmd_pay(m):
    # /pay        -> успешная "оплата" (эмуляция)
    # /pay fail   -> принудительный отказ
    parts = m.text.split()
    outcome = "fail" if (len(parts) > 1 and parts[1].lower() == "fail") else "ok"

    uid = str(m.from_user.id)
    order = db.get_last_order_of(uid)
    if not order:
        bot.reply_to(m, "Нет заказов для оплаты.")
        return
    if order["status"] in ("Delivered",):
        bot.reply_to(m, "Этот заказ уже завершён.")
        return

    result = MockPaymentProvider.charge(
        order["id"], order["total"], outcome=outcome
    )
    if result["status"] == "Succeeded":
        db.set_order_status(order["id"], "Confirmed")
        bot.reply_to(
            m,
            f"✅ Оплата (эмуляция) прошла: {result['amount']} ₽. "
            f"Статус заказа #{order['id']}: Confirmed\nПроверьте статус: /status",
        )
    else:
        db.set_order_status(order["id"], "Pending")
        bot.reply_to(
            m,
            f"❌ Оплата (эмуляция) отклонена. "
            f"Статус заказа #{order['id']}: Pending",
        )


@bot.message_handler(commands=["status"])
def cmd_status(m):
    uid = str(m.from_user.id)
    order = db.get_last_order_of(uid)
    if not order:
        bot.reply_to(m, "У вас ещё нет заказов.")
        return
    bot.reply_to(m, f"Статус заказа #{order['id']}: {order['status']}")


@bot.message_handler(commands=["cancel"])
def cmd_cancel(m):
    uid = str(m.from_user.id)
    db.clear_cart(uid)
    bot.reply_to(m, "🗑 Корзина очищена.")


# ===== Запуск =====
if __name__ == "__main__":
    print("PizzaFlow bot is running...")
    bot.infinity_polling(skip_pending=True)
