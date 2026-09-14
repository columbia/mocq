<?php
$conn = new mysqli("localhost", "u", "p", "app");
$res = $conn->query("SELECT count(*) AS n FROM users");
$row = $res->fetch_assoc();
echo $row["n"];
