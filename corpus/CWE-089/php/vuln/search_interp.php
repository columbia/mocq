<?php
$conn = new mysqli("localhost", "u", "p", "app");
$term = $_REQUEST["q"];
$sql = "SELECT id FROM products WHERE title LIKE '%$term%'";
$conn->query($sql);
