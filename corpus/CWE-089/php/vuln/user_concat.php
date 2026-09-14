<?php
$conn = new mysqli("localhost", "u", "p", "app");
$id = $_GET["id"];
$res = $conn->query("SELECT name, email FROM users WHERE id = '" . $id . "'");
while ($row = $res->fetch_assoc()) {
    echo $row["name"];
}
