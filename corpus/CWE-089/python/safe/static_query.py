import sqlite3
from flask import Flask

app = Flask(__name__)

@app.route("/stats")
def stats():
    conn = sqlite3.connect("app.db")
    cur = conn.cursor()
    cur.execute("SELECT count(*) FROM users")
    return {"users": cur.fetchone()[0]}
