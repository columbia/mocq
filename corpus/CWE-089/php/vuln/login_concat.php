<?php
function check_login($db, $username) {
    $q = "SELECT * FROM accounts WHERE username = '" . $username . "'";
    return mysqli_query($db, $q);
}
$db = mysqli_connect("localhost", "u", "p", "app");
check_login($db, $_POST["username"]);
