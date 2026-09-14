<?php
$conn = new mysqli("localhost", "u", "p", "app");
$id = $_GET["id"];
$stmt = $conn->prepare("SELECT name, email FROM users WHERE id = ?");
$stmt->bind_param("s", $id);
$stmt->execute();
