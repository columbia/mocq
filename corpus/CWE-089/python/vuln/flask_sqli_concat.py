import sqlite3
from flask import Flask, request

app = Flask(__name__)

@app.route("/login", methods=["POST"])
def login():
    name = request.form["username"]
    conn = sqlite3.connect("app.db")
    cur = conn.cursor()
    query = "SELECT * FROM accounts WHERE username = '" + name + "'"
    cur.execute(query)
    return {"ok": cur.fetchone() is not None}
