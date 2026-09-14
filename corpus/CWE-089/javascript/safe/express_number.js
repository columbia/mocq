const express = require("express");
const mysql = require("mysql");
const app = express();
const db = mysql.createConnection({ database: "app" });

app.get("/user", (req, res) => {
  const id = Number(req.query.id);
  db.query("SELECT name FROM users WHERE id = " + id, (err, rows) => {
    res.json(rows);
  });
});
