const express = require("express");
const mysql = require("mysql");
const app = express();
const db = mysql.createConnection({ database: "app" });

app.get("/stats", (req, res) => {
  db.query("SELECT count(*) AS n FROM users", (err, rows) => {
    res.json(rows);
  });
});
