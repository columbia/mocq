<?php
$conn = new mysqli("localhost", "u", "p", "app");
$id = intval($_GET["id"]);
$conn->query("SELECT name FROM users WHERE id = " . $id);
