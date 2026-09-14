const express = require("express");
const mysql = require("mysql");
const app = express();
const db = mysql.createConnection({ database: "app" });

app.get("/search", (req, res) => {
  const term = req.query.q;
  db.query(`SELECT id FROM products WHERE title LIKE '%${term}%'`, (err, rows) => {
    res.json(rows);
  });
});
