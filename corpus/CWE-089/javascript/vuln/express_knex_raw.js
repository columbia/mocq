const express = require("express");
const knex = require("knex")({ client: "pg" });
const app = express();

app.get("/orders/:cust", (req, res) => {
  const cust = req.params.cust;
  knex
    .raw("SELECT * FROM orders WHERE customer = '" + cust + "'")
    .then((rows) => res.json(rows));
});
