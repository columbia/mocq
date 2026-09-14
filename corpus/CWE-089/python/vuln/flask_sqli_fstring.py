import sqlite3
from flask import Flask, request

app = Flask(__name__)

@app.route("/search")
def search():
    term = request.args.get("q", "")
    conn = sqlite3.connect("app.db")
    cur = conn.cursor()
    cur.execute(f"SELECT id FROM products WHERE title LIKE '%{term}%'")
    return {"ids": [r[0] for r in cur.fetchall()]}
