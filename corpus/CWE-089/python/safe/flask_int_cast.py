import sqlite3
from flask import Flask, request

app = Flask(__name__)

@app.route("/user")
def user():
    uid = int(request.args["id"])
    conn = sqlite3.connect("app.db")
    cur = conn.cursor()
    cur.execute("SELECT name FROM users WHERE id = %d" % uid)
    return {"rows": cur.fetchall()}
