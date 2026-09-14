import sqlite3
from flask import Flask, request

app = Flask(__name__)

@app.route("/user")
def user():
    uid = request.args["id"]
    conn = sqlite3.connect("app.db")
    cur = conn.cursor()
    cur.execute("SELECT name, email FROM users WHERE id = '%s'" % uid)
    return {"rows": cur.fetchall()}
